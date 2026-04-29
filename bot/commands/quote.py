import asyncio
import os
import tempfile
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.db.client import DBClient
from bot.drive.client import DriveClient, extract_folder_id
from bot.pdf_gen.generator import generate_quote_pdf
from bot.pricing.engine import ResinType, apply_manual_discounts, calculate_quote
from bot.pricing.model_reader import read_models
from bot.sheets.client import SheetsClient

_RESIN_OPTIONS: list[tuple[str, ResinType, bool]] = [
    ("RPG高精度樹脂", ResinType.RPG, False),
    ("透明樹脂（不調色）", ResinType.CLEAR, False),
    ("透明樹脂（需調色）", ResinType.CLEAR, True),
]


@dataclass
class _ModalData:
    customer_name: str
    drive_folder_url: str
    quote_number: str
    folder_id: str


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------

def _guild_check(interaction: discord.Interaction, guild_id: int) -> bool:
    return interaction.guild_id == guild_id


def _role_check(interaction: discord.Interaction, role_id: int) -> bool:
    return any(r.id == role_id for r in interaction.user.roles)


def _build_quote_embed(
    quote_number: str,
    customer_name: str,
    resin_label: str,
    body_count: int,
    material_cost: int,
    processing_fee: int,
    subtotal: int,
    auto_discount_amount: int,
    manual_discount: str,
    final_total: int,
    order_status: str,
    file_details: list[dict],
    error_files: list[str],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 估價單 {quote_number}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="客戶名稱", value=customer_name, inline=True)
    embed.add_field(name="樹脂種類", value=resin_label, inline=True)
    embed.add_field(name="總件數", value=str(body_count), inline=True)
    embed.add_field(name="材料費", value=f"${material_cost}", inline=True)
    embed.add_field(name="加工費", value=f"${processing_fee}", inline=True)
    embed.add_field(name="小計", value=f"${subtotal}", inline=True)

    has_discount = auto_discount_amount > 0 or (manual_discount and manual_discount != "無")
    if has_discount:
        parts = []
        if auto_discount_amount > 0:
            parts.append(f"自動折扣 -${auto_discount_amount}")
        if manual_discount and manual_discount != "無":
            parts.append(manual_discount)
        embed.add_field(name="折扣", value=" / ".join(parts), inline=False)

    embed.add_field(name="訂單狀態", value=order_status, inline=True)
    embed.add_field(name="**最終總價**", value=f"**${final_total}**", inline=True)

    if error_files:
        embed.add_field(
            name="⚠️ 異常檔案",
            value="\n".join(error_files),
            inline=False,
        )

    shown = file_details[:10]
    if shown:
        lines = [
            f"`{f['filename']}` — {f['volume_ml']:.2f} ml，{f['body_count']} 件"
            for f in shown
        ]
        if len(file_details) > 10:
            lines.append(f"…（共 {len(file_details)} 個檔案）")
        embed.add_field(name="檔案明細", value="\n".join(lines), inline=False)

    return embed


# ---------------------------------------------------------------------------
# Discord Views & Modal
# ---------------------------------------------------------------------------

class QuoteModal(discord.ui.Modal, title="3D 列印估價"):
    customer_name_input = discord.ui.TextInput(
        label="客戶名稱", max_length=50, placeholder="例：骰吧王小明"
    )
    drive_url_input = discord.ui.TextInput(
        label="Google Drive 資料夾連結",
        placeholder="https://drive.google.com/drive/folders/...",
    )
    quote_number_input = discord.ui.TextInput(
        label="估價單編號", max_length=30, placeholder="例：Q20260426-001"
    )

    def __init__(self, config: Config, cog: "QuoteCog") -> None:
        super().__init__()
        self._config = config
        self._cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            folder_id = extract_folder_id(self.drive_url_input.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ 無效的 Google Drive 資料夾連結，請確認格式是否正確。",
                ephemeral=True,
            )
            return

        modal_data = _ModalData(
            customer_name=self.customer_name_input.value,
            drive_folder_url=self.drive_url_input.value,
            quote_number=self.quote_number_input.value,
            folder_id=folder_id,
        )
        view = ResinSelectView(modal_data=modal_data, config=self._config, cog=self._cog)
        await interaction.response.send_message(
            "請選擇樹脂種類：", view=view, ephemeral=True
        )


class ResinSelectView(discord.ui.View):
    def __init__(self, modal_data: _ModalData, config: Config, cog: "QuoteCog") -> None:
        super().__init__(timeout=300)
        self._modal_data = modal_data
        self._config = config
        self._cog = cog
        self._selected_idx: int | None = None

        select = discord.ui.Select(
            placeholder="選擇樹脂種類...",
            options=[
                discord.SelectOption(label=label, value=str(i))
                for i, (label, _, _) in enumerate(_RESIN_OPTIONS)
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self._selected_idx = int(interaction.data["values"][0])
        await interaction.response.defer()

    @discord.ui.button(label="開始計算", style=discord.ButtonStyle.primary)
    async def start_calc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self._selected_idx is None:
            await interaction.response.send_message(
                "⚠️ 請先選擇樹脂種類。", ephemeral=True
            )
            return

        label, resin, colored = _RESIN_OPTIONS[self._selected_idx]
        await interaction.response.edit_message(
            content="⏳ 正在讀取模型並計算中...", view=None
        )
        await self._cog.run_quote_calculation(
            interaction, self._modal_data, resin, colored, label
        )


class DiscountView(discord.ui.View):
    def __init__(self, action_view: "QuoteActionView") -> None:
        super().__init__(timeout=300)
        self._action_view = action_view
        self._nine_ten = False
        self._free_ship = False

    @discord.ui.button(label="九折", style=discord.ButtonStyle.secondary)
    async def toggle_nine_ten(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._nine_ten = not self._nine_ten
        button.style = (
            discord.ButtonStyle.success if self._nine_ten else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="免運費", style=discord.ButtonStyle.secondary)
    async def toggle_free_ship(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._free_ship = not self._free_ship
        button.style = (
            discord.ButtonStyle.success if self._free_ship else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="套用折扣", style=discord.ButtonStyle.primary, row=1)
    async def apply_discount(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        av = self._action_view
        base = av._quote_result.final_total
        already_free = av._quote_result.auto_free_ship

        new_total, new_free = apply_manual_discounts(
            base, self._nine_ten, self._free_ship, already_free
        )
        av._final_total = new_total
        av._final_free_shipping = new_free
        av._manual_nine_ten = self._nine_ten
        av._manual_free_ship = self._free_ship

        parts = []
        if self._nine_ten:
            parts.append("九折")
        if self._free_ship or new_free:
            parts.append("免運費")
        manual_discount_str = " + ".join(parts) if parts else "無"

        embed = _build_quote_embed(
            quote_number=av._modal_data.quote_number,
            customer_name=av._modal_data.customer_name,
            resin_label=av._resin_label,
            body_count=av._quote_result.body_count,
            material_cost=av._quote_result.material_cost,
            processing_fee=av._quote_result.processing_fee,
            subtotal=av._quote_result.subtotal,
            auto_discount_amount=av._quote_result.auto_discount_amount,
            manual_discount=manual_discount_str,
            final_total=new_total,
            order_status=av._quote_result.order_status,
            file_details=av._file_details,
            error_files=av._error_files,
        )
        await interaction.response.edit_message(embed=embed, view=av)


class QuoteActionView(discord.ui.View):
    def __init__(
        self,
        modal_data: _ModalData,
        quote_result,
        file_details: list[dict],
        error_files: list[str],
        resin_label: str,
        config: Config,
        db: DBClient,
    ) -> None:
        super().__init__(timeout=600)
        self._modal_data = modal_data
        self._quote_result = quote_result
        self._file_details = file_details
        self._error_files = error_files
        self._resin_label = resin_label
        self._config = config
        self._db = db
        self._final_total = quote_result.final_total
        self._final_free_shipping = quote_result.auto_free_ship
        self._manual_nine_ten = False
        self._manual_free_ship = False

    @discord.ui.button(label="✏️ 套用折扣", style=discord.ButtonStyle.secondary)
    async def discount_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        view = DiscountView(action_view=self)
        await interaction.response.edit_message(
            content="選擇折扣選項：", view=view
        )

    @discord.ui.button(label="✅ 接受報價", style=discord.ButtonStyle.success)
    async def accept_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_event_loop()
        try:
            pdf_url = await loop.run_in_executor(None, self._do_accept)
        except Exception as exc:
            await interaction.followup.send(f"❌ 處理失敗：{exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ 報價已接受！PDF 報價單：{pdf_url}", ephemeral=True
        )

    @discord.ui.button(label="❌ 拒絕報價", style=discord.ButtonStyle.danger)
    async def reject_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._do_reject)
        except Exception as exc:
            await interaction.followup.send(f"❌ 處理失敗：{exc}", ephemeral=True)
            return
        await interaction.followup.send("❌ 報價已記錄為拒絕。", ephemeral=True)

    # ---- blocking helpers (run in executor) ----

    def _do_accept(self) -> str:
        md = self._modal_data
        qr = self._quote_result
        cfg = self._config
        parts = []
        if self._manual_nine_ten:
            parts.append("九折")
        if self._manual_free_ship or self._final_free_shipping:
            parts.append("免運費")
        manual_discount_str = " + ".join(parts) if parts else "無"

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, f"{md.quote_number}.pdf")
            generate_quote_pdf(
                quote_number=md.quote_number,
                customer_name=md.customer_name,
                resin_label=self._resin_label,
                file_details=self._file_details,
                error_files=self._error_files,
                material_cost=qr.material_cost,
                processing_fee=qr.processing_fee,
                subtotal=qr.subtotal,
                auto_discount_amount=qr.auto_discount_amount,
                manual_discount=manual_discount_str,
                final_total=self._final_total,
                order_status=qr.order_status,
                output_path=pdf_path,
            )
            drive = DriveClient(cfg.google_service_account_json)
            pdf_url = drive.upload_file(pdf_path, md.folder_id)

        self._db.insert_customer_record(
            quote_number=md.quote_number,
            customer_name=md.customer_name,
            drive_folder_url=md.drive_folder_url,
            final_total=self._final_total,
            pdf_url=pdf_url,
        )
        self._db.insert_quote_record(
            quote_number=md.quote_number,
            customer_name=md.customer_name,
            resin_label=self._resin_label,
            body_count=qr.body_count,
            material_cost=qr.material_cost,
            processing_fee=qr.processing_fee,
            auto_discount="95折" if qr.auto_discount_amount > 0 else "無",
            manual_discount=manual_discount_str,
            subtotal=qr.subtotal,
            final_total=self._final_total,
            order_status=qr.order_status,
            decision="接受",
        )
        return pdf_url

    def _do_reject(self) -> None:
        md = self._modal_data
        qr = self._quote_result
        cfg = self._config
        parts = []
        if self._manual_nine_ten:
            parts.append("九折")
        if self._manual_free_ship or self._final_free_shipping:
            parts.append("免運費")
        manual_discount_str = " + ".join(parts) if parts else "無"

        self._db.insert_quote_record(
            quote_number=md.quote_number,
            customer_name=md.customer_name,
            resin_label=self._resin_label,
            body_count=qr.body_count,
            material_cost=qr.material_cost,
            processing_fee=qr.processing_fee,
            auto_discount="95折" if qr.auto_discount_amount > 0 else "無",
            manual_discount=manual_discount_str,
            subtotal=qr.subtotal,
            final_total=self._final_total,
            order_status=qr.order_status,
            decision="拒絕",
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class QuoteCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self.config = config
        self._db = DBClient(config.db_path)

    @app_commands.command(name="quote", description="建立 3D 列印估價單")
    async def quote(self, interaction: discord.Interaction) -> None:
        if not _guild_check(interaction, self.config.guild_id):
            await interaction.response.send_message(
                "❌ 此指令僅限指定伺服器使用。", ephemeral=True
            )
            return
        if not _role_check(interaction, self.config.member_role_id):
            await interaction.response.send_message(
                "❌ 您沒有使用此指令的權限，請聯繫管理員。", ephemeral=True
            )
            return
        await interaction.response.send_modal(QuoteModal(self.config, self))

    async def run_quote_calculation(
        self,
        interaction: discord.Interaction,
        modal_data: _ModalData,
        resin: ResinType,
        colored: bool,
        resin_label: str,
    ) -> None:
        loop = asyncio.get_event_loop()
        try:
            file_details, error_files, quote_result = await loop.run_in_executor(
                None,
                self._sync_calculate,
                modal_data,
                resin,
                colored,
            )
        except Exception as exc:
            await interaction.edit_original_response(
                content=f"❌ 計算失敗：{exc}", view=None
            )
            return

        embed = _build_quote_embed(
            quote_number=modal_data.quote_number,
            customer_name=modal_data.customer_name,
            resin_label=resin_label,
            body_count=quote_result.body_count,
            material_cost=quote_result.material_cost,
            processing_fee=quote_result.processing_fee,
            subtotal=quote_result.subtotal,
            auto_discount_amount=quote_result.auto_discount_amount,
            manual_discount="無",
            final_total=quote_result.final_total,
            order_status=quote_result.order_status,
            file_details=file_details,
            error_files=error_files,
        )
        view = QuoteActionView(
            modal_data=modal_data,
            quote_result=quote_result,
            file_details=file_details,
            error_files=error_files,
            resin_label=resin_label,
            config=self.config,
            db=self._db,
        )
        await interaction.edit_original_response(content=None, embed=embed, view=view)

    def _sync_calculate(
        self,
        modal_data: _ModalData,
        resin: ResinType,
        colored: bool,
    ) -> tuple[list[dict], list[str], object]:
        drive = DriveClient(self.config.google_service_account_json)
        model_files = drive.list_model_files(modal_data.folder_id)

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for f in model_files:
                dest = os.path.join(tmp, f["name"])
                drive.download_file(f["id"], dest)
                paths.append(dest)

            results, error_files = asyncio.run(read_models(paths))

        total_volume = sum(r.volume_ml for r in results)
        total_bodies = sum(r.body_count for r in results)

        quote_result = calculate_quote(
            resin=resin,
            colored=colored,
            volume_ml=total_volume,
            body_count=total_bodies,
        )
        file_details = [
            {
                "filename": r.filename,
                "volume_ml": r.volume_ml,
                "body_count": r.body_count,
            }
            for r in results
        ]
        return file_details, error_files, quote_result
