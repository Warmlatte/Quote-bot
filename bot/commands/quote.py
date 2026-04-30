import asyncio
import io
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

_RESIN_BASE_OPTIONS: list[tuple[str, ResinType]] = [
    ("RPG高精度樹脂", ResinType.RPG),
    ("透明樹脂", ResinType.CLEAR),
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

def _format_file_details(file_details: list[dict]) -> str:
    if not file_details:
        return ""
    lines = [
        f"{f['filename']}: {f['volume_ml']:.2f}ml / {f['body_count']}件"
        for f in file_details
    ]
    return "\n".join(lines)


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

class RejectReasonModal(discord.ui.Modal, title="拒絕原因"):
    reason_input = discord.ui.TextInput(
        label="簡短拒絕理由",
        max_length=200,
        required=False,
    )

    def __init__(self, action_view: "QuoteActionView") -> None:
        super().__init__()
        self._action_view = action_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        reason = self.reason_input.value.strip() if self.reason_input.value else ""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._action_view._do_reject, reason)
        except Exception as exc:
            await interaction.followup.send(f"❌ 處理失敗：{exc}", ephemeral=True)
            return
        await interaction.followup.send("❌ 報價已記錄為拒絕。", ephemeral=True)


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
        self._selected_resin: ResinType | None = None
        self._colored = False

        select = discord.ui.Select(
            placeholder="選擇樹脂種類...",
            options=[
                discord.SelectOption(label=label, value=resin.value)
                for label, resin in _RESIN_BASE_OPTIONS
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

        # 取得裝飾器建立的調色按鈕引用，用於動態啟用/停用
        self._colored_btn: discord.ui.Button | None = next(
            (
                item for item in self.children
                if isinstance(item, discord.ui.Button) and item.label == "🎨 調色"
            ),
            None,
        )

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self._selected_resin = ResinType(interaction.data["values"][0])
        self._colored = False
        if self._colored_btn is not None:
            self._colored_btn.disabled = self._selected_resin != ResinType.CLEAR
            self._colored_btn.style = discord.ButtonStyle.secondary
        resin_label = next(l for l, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        await interaction.response.edit_message(
            content=f"請選擇樹脂種類：\n✅ 已選擇：{resin_label}",
            view=self,
        )

    @discord.ui.button(label="🎨 調色", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def toggle_color(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._colored = not self._colored
        button.style = (
            discord.ButtonStyle.success if self._colored else discord.ButtonStyle.secondary
        )
        resin_label = next(l for l, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        color_suffix = "（調色）" if self._colored else ""
        await interaction.response.edit_message(
            content=f"請選擇樹脂種類：\n✅ 已選擇：{resin_label}{color_suffix}",
            view=self,
        )

    @discord.ui.button(label="開始計算", style=discord.ButtonStyle.primary, row=1)
    async def start_calc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self._selected_resin is None:
            await interaction.response.send_message(
                "⚠️ 請先選擇樹脂種類。", ephemeral=True
            )
            return

        resin_label = next(l for l, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        if self._selected_resin == ResinType.CLEAR and self._colored:
            resin_label += "（調色）"

        await interaction.response.edit_message(
            content="⏳ 正在讀取模型並計算中...", view=None
        )
        await self._cog.run_quote_calculation(
            interaction, self._modal_data, self._selected_resin, self._colored, resin_label
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
            pdf_bytes = await loop.run_in_executor(None, self._generate_pdf)
        except Exception as exc:
            await interaction.followup.send(f"❌ 處理失敗：{exc}", ephemeral=True)
            return

        msg = await interaction.followup.send(
            content="✅ 報價已接受！PDF 報價單如附件。",
            file=discord.File(
                io.BytesIO(pdf_bytes),
                filename=f"{self._modal_data.quote_number}.pdf",
            ),
            ephemeral=True,
        )
        pdf_url = msg.attachments[0].url if msg.attachments else "Discord 附件"
        try:
            await loop.run_in_executor(None, self._record_acceptance, pdf_url)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ 報價已接受但記錄寫入失敗：{exc}", ephemeral=True)

    @discord.ui.button(label="❌ 拒絕報價", style=discord.ButtonStyle.danger)
    async def reject_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(RejectReasonModal(self))

    # ---- blocking helpers (run in executor) ----

    def _manual_discount_str(self) -> str:
        parts = []
        if self._manual_nine_ten:
            parts.append("九折")
        if self._manual_free_ship or self._final_free_shipping:
            parts.append("免運費")
        return " + ".join(parts) if parts else "無"

    def _generate_pdf(self) -> bytes:
        md = self._modal_data
        qr = self._quote_result
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
                manual_discount=self._manual_discount_str(),
                final_total=self._final_total,
                order_status=qr.order_status,
                output_path=pdf_path,
            )
            with open(pdf_path, "rb") as f:
                return f.read()

    def _record_acceptance(self, pdf_url: str) -> None:
        md = self._modal_data
        qr = self._quote_result
        manual_discount_str = self._manual_discount_str()
        inserted = self._db.insert_customer_record(
            quote_number=md.quote_number,
            customer_name=md.customer_name,
            drive_folder_url=md.drive_folder_url,
            final_total=self._final_total,
            pdf_url=pdf_url,
        )
        if not inserted:
            raise ValueError(f"此雲端連結已有接受記錄，無法重複提交：{md.drive_folder_url}")
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
            drive_folder_url=md.drive_folder_url,
        )

    def _do_reject(self, rejection_reason: str = "") -> None:
        md = self._modal_data
        qr = self._quote_result
        manual_discount_str = self._manual_discount_str()
        file_details_text = _format_file_details(self._file_details)

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
            file_details_text=file_details_text,
            rejection_reason=rejection_reason,
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
