import asyncio
import io
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.db.client import DBClient
from bot.drive.client import DriveClient, extract_folder_id
from bot.pdf_gen.generator import generate_quote_pdf
from bot.pricing.engine import DiscountInput, QuoteResult, ResinType, apply_manual_discount, calculate_quote
from bot.pricing.model_reader import read_models

_logger = logging.getLogger(__name__)
_TZ_TAIPEI = timezone(timedelta(hours=8))

def _generate_quote_number(db: DBClient) -> str:
    today = datetime.now(_TZ_TAIPEI).strftime("%y%m%d")
    count = db.count_accepted_quotes_today(today)
    return f"trb{today}{count + 1:02d}"


async def _rename_drive_folder(config: Config, folder_id: str, name: str) -> None:
    loop = asyncio.get_event_loop()
    try:
        drive = DriveClient(config.google_service_account_json)
        await loop.run_in_executor(None, drive.rename_folder, folder_id, name)
    except Exception as exc:
        _logger.warning("資料夾重命名失敗：%s", exc)


_RESIN_BASE_OPTIONS: list[tuple[str, ResinType]] = [
    ("RPG高精度樹脂", ResinType.RPG),
    ("透明樹脂", ResinType.CLEAR),
]


@dataclass
class _ModalData:
    customer_name: str
    drive_folder_url: str
    folder_id: str


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------

def _format_file_details(file_details: list[dict]) -> str:
    if not file_details:
        return ""
    lines = [
        f"{f['filename']}: {f['volume_ml']:.1f}ml / {f['body_count']}件"
        for f in file_details
    ]
    return "\n".join(lines)


def _guild_check(interaction: discord.Interaction, guild_id: int) -> bool:
    return interaction.guild_id == guild_id


def _role_check(interaction: discord.Interaction, role_id: int) -> bool:
    return any(r.id == role_id for r in cast(discord.Member, interaction.user).roles)


def _build_quote_embed(
    customer_name: str,
    resin_label: str,
    body_count: int,
    material_cost: int,
    processing_fee: int,
    subtotal: int,
    auto_discount_amount: int,
    final_total: int,
    order_status: str,
    file_details: list[dict],
    error_files: list[str],
    manual_discount_amount: int = 0,
    min_order_supplement: int = 0,
    shipping_fee: int = 0,
    shipping_address: str = "",
    shipping_free_label: bool = False,
    quote_number: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 估價單 {quote_number}" if quote_number else "📋 估價單",
        color=discord.Color.blue(),
    )
    embed.add_field(name="客戶名稱", value=customer_name, inline=True)
    embed.add_field(name="樹脂種類", value=resin_label, inline=True)
    embed.add_field(name="總件數", value=str(body_count), inline=True)
    embed.add_field(name="材料費", value=f"${material_cost}", inline=True)
    embed.add_field(name="加工費", value=f"${processing_fee}", inline=True)
    embed.add_field(name="小計", value=f"${subtotal}", inline=True)

    if auto_discount_amount > 0 or manual_discount_amount > 0:
        parts = []
        if auto_discount_amount > 0:
            parts.append(f"自動 -${auto_discount_amount}")
        if manual_discount_amount > 0:
            parts.append(f"手動 -${manual_discount_amount}")
        embed.add_field(name="折扣", value=" / ".join(parts), inline=False)

    if min_order_supplement > 0:
        embed.add_field(
            name="⚠️ 低消補足",
            value=f"+${min_order_supplement}（補足至低消 NT$ 500）",
            inline=False,
        )

    if shipping_address:
        ship_val = "免運費" if shipping_free_label else f"NT$ {shipping_fee:,}"
        embed.add_field(name="運費", value=ship_val, inline=True)
        embed.add_field(name="寄送地址", value=shipping_address, inline=True)

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
            f"`{f['filename']}` — {f['volume_ml']:.1f} ml，{f['body_count']} 件"
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
            folder_id=folder_id,
        )
        view = ResinSelectView(modal_data=modal_data, config=self._config, cog=self._cog)
        await interaction.response.send_message(
            "請選擇樹脂種類：", view=view, ephemeral=True
        )
        asyncio.create_task(
            _rename_drive_folder(self._config, folder_id, modal_data.customer_name)
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
        self._selected_resin = ResinType(cast(Any, interaction.data)["values"][0])
        self._colored = False
        if self._colored_btn is not None:
            self._colored_btn.disabled = self._selected_resin != ResinType.CLEAR
            self._colored_btn.style = discord.ButtonStyle.secondary
        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
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
        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
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

        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        if self._selected_resin == ResinType.CLEAR and self._colored:
            resin_label += "（調色）"

        await interaction.response.edit_message(
            content="⏳ 正在讀取模型並計算中...", view=None
        )
        await self._cog.run_quote_calculation(
            interaction, self._modal_data, self._selected_resin, self._colored, resin_label
        )


class DiscountCustomModal(discord.ui.Modal, title="自訂折扣"):
    discount_input = discord.ui.TextInput(
        label="折扣（百分比如 80% 或固定金額如 -100）",
        max_length=20,
        placeholder='例：80% 或 -100',
    )

    def __init__(self, action_view: "QuoteActionView") -> None:
        super().__init__()
        self._action_view = action_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = (self.discount_input.value or "").strip()
        av = self._action_view
        if raw.endswith("%"):
            try:
                pct = float(raw[:-1]) / 100
                if not (0 < pct < 1):
                    raise ValueError()
            except ValueError:
                await interaction.response.send_message(
                    "❌ 無效的百分比，請輸入如 80% 的格式（1%–99%）。", ephemeral=True
                )
                return
            discount = DiscountInput(mode="pct", value=pct)
        elif raw.startswith("-"):
            try:
                amount = int(raw[1:])
                if amount <= 0:
                    raise ValueError()
            except ValueError:
                await interaction.response.send_message(
                    "❌ 無效的固定金額，請輸入如 -100 的格式（負整數）。", ephemeral=True
                )
                return
            discount = DiscountInput(mode="fixed", value=amount)
        else:
            await interaction.response.send_message(
                "❌ 無法辨識格式，請使用 80% 或 -100。", ephemeral=True
            )
            return

        _, discount_amount = apply_manual_discount(av._quote_result.final_total, discount)
        av._manual_discount = discount
        av._manual_discount_amount = discount_amount
        await interaction.response.defer()
        await av._refresh_embed()


class DiscountSelectView(discord.ui.View):
    def __init__(self, action_view: "QuoteActionView") -> None:
        super().__init__(timeout=300)
        self._action_view = action_view

        select = discord.ui.Select(
            placeholder="選擇折扣選項...",
            options=[
                discord.SelectOption(label="九折", value="九折", description="套用 90% 折扣"),
                discord.SelectOption(label="自訂", value="自訂", description="輸入百分比或固定金額"),
                discord.SelectOption(label="清除折扣", value="清除折扣", description="移除手動折扣"),
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        from typing import Any, cast
        value = cast(Any, interaction.data)["values"][0]
        av = self._action_view

        if value == "九折":
            discount = DiscountInput(mode="pct", value=0.9)
            _, amount = apply_manual_discount(av._quote_result.final_total, discount)
            av._manual_discount = discount
            av._manual_discount_amount = amount
            await interaction.response.defer()
            await av._refresh_embed()
        elif value == "清除折扣":
            av._manual_discount = DiscountInput(mode="none", value=0)
            av._manual_discount_amount = 0
            await interaction.response.defer()
            await av._refresh_embed()
        else:
            await interaction.response.send_modal(DiscountCustomModal(av))


class ShippingModal(discord.ui.Modal, title="運送資訊"):
    def __init__(self, action_view: "QuoteActionView", fee_default: int = 60, free_toggled: bool = False) -> None:
        super().__init__()
        self._action_view = action_view
        self._free_toggled = free_toggled

        self.address_field = discord.ui.TextInput(
            label="寄送地址",
            required=True,
            max_length=200,
            placeholder="例：台北市大安區忠孝東路四段",
        )
        self.fee_field = discord.ui.TextInput(
            label="運費金額",
            required=True,
            max_length=10,
            default=str(fee_default),
        )
        self.add_item(self.address_field)
        self.add_item(self.fee_field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fee_raw = (self.fee_field.value or "").strip()
        try:
            fee = int(fee_raw)
            if fee < 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message(
                "❌ 無效的運費金額，請輸入非負整數（如 0 或 60）。", ephemeral=True
            )
            return

        av = self._action_view
        av._shipping_fee = fee
        av._shipping_address = (self.address_field.value or "").strip()
        av._shipping_free_label = self._free_toggled and fee == 0
        await interaction.response.defer()
        await av._refresh_embed()


class ShippingView(discord.ui.View):
    def __init__(self, action_view: "QuoteActionView") -> None:
        super().__init__(timeout=300)
        self._action_view = action_view
        self._free_active: bool = action_view._quote_result.auto_free_ship
        self._toggle_btn: discord.ui.Button | None = None
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "🆓 免運費":
                self._toggle_btn = item
                item.style = (
                    discord.ButtonStyle.success if self._free_active else discord.ButtonStyle.secondary
                )
                break

    async def toggle_free_ship(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._free_active = not self._free_active
        button.style = (
            discord.ButtonStyle.success if self._free_active else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)

    async def fill_address(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        fee_default = 0 if self._free_active else 60
        await interaction.response.send_modal(
            ShippingModal(self._action_view, fee_default=fee_default, free_toggled=self._free_active)
        )

    @discord.ui.button(label="🆓 免運費", style=discord.ButtonStyle.secondary, row=0)
    async def _toggle_free_ship_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.toggle_free_ship(interaction, button)

    @discord.ui.button(label="📝 填寫地址與運費", style=discord.ButtonStyle.primary, row=1)
    async def _fill_address_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.fill_address(interaction, button)


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
        self._manual_discount: DiscountInput = DiscountInput(mode="none", value=0)
        self._manual_discount_amount: int = 0
        self._shipping_fee: int = 0
        self._shipping_address: str = ""
        self._shipping_free_label: bool = False
        self._message: discord.Message | None = None

    def _compute_merchandise_total(self) -> int:
        """商品小計（折扣後，不含運費）。"""
        return self._quote_result.final_total - self._manual_discount_amount

    def _compute_raw_total(self) -> int:
        """商品小計 + 運費，無低消補足（用於拒絕記錄）。"""
        return self._compute_merchandise_total() + self._shipping_fee

    def _compute_min_order_supplement(self) -> int:
        """需補足至低消 500 的金額（以商品小計為基準，運費另計）。"""
        if self._quote_result.order_status != "未達低消":
            return 0
        return max(0, 500 - self._compute_merchandise_total())

    def _compute_final_total(self) -> int:
        """顯示總價 = 商品小計 + 低消補足 + 運費。"""
        merch = self._compute_merchandise_total()
        supplement = self._compute_min_order_supplement()
        return merch + supplement + self._shipping_fee

    async def _refresh_embed(self) -> None:
        qr = self._quote_result
        final_total = self._compute_final_total()
        supplement = self._compute_min_order_supplement()
        embed = _build_quote_embed(
            customer_name=self._modal_data.customer_name,
            resin_label=self._resin_label,
            body_count=qr.body_count,
            material_cost=qr.material_cost,
            processing_fee=qr.processing_fee,
            subtotal=qr.subtotal,
            auto_discount_amount=qr.auto_discount_amount,
            final_total=final_total,
            order_status=qr.order_status,
            file_details=self._file_details,
            error_files=self._error_files,
            manual_discount_amount=self._manual_discount_amount,
            min_order_supplement=supplement,
            shipping_fee=self._shipping_fee,
            shipping_address=self._shipping_address,
            shipping_free_label=self._shipping_free_label,
        )
        if self._message is not None:
            await self._message.edit(embed=embed, view=self)

    @discord.ui.button(label="✏️ 折扣", style=discord.ButtonStyle.secondary, row=0)
    async def discount_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        view = DiscountSelectView(action_view=self)
        await interaction.response.send_message(
            "選擇折扣選項：", view=view, ephemeral=True
        )

    @discord.ui.button(label="🚚 運送", style=discord.ButtonStyle.secondary, row=0)
    async def shipping_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        view = ShippingView(action_view=self)
        await interaction.response.send_message(
            "設定運送選項：", view=view, ephemeral=True
        )

    @discord.ui.button(label="✅ 接受報價", style=discord.ButtonStyle.success, row=1)
    async def accept_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        loop = asyncio.get_event_loop()
        try:
            quote_number = await loop.run_in_executor(
                None, _generate_quote_number, self._db
            )
            pdf_bytes = await loop.run_in_executor(
                None, self._generate_pdf, quote_number
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ 處理失敗：{exc}", ephemeral=True)
            return

        msg = await interaction.followup.send(
            content=f"✅ 報價已接受！PDF 報價單如附件。（{quote_number}）",
            file=discord.File(
                io.BytesIO(pdf_bytes),
                filename=f"{quote_number}.pdf",
            ),
            wait=True,
        )
        pdf_url = msg.attachments[0].url if msg.attachments else "Discord 附件"
        try:
            await loop.run_in_executor(None, self._record_acceptance, pdf_url, quote_number)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ 報價已接受但記錄寫入失敗：{exc}", ephemeral=True)

    @discord.ui.button(label="❌ 拒絕報價", style=discord.ButtonStyle.danger, row=1)
    async def reject_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(RejectReasonModal(self))

    # ---- blocking helpers (run in executor) ----

    def _generate_pdf(self, quote_number: str) -> bytes:
        md = self._modal_data
        qr = self._quote_result
        final_total = self._compute_final_total()
        supplement = self._compute_min_order_supplement()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, f"{quote_number}.pdf")
            generate_quote_pdf(
                quote_number=quote_number,
                customer_name=md.customer_name,
                resin_label=self._resin_label,
                file_details=self._file_details,
                error_files=self._error_files,
                material_cost=qr.material_cost,
                processing_fee=qr.processing_fee,
                subtotal=qr.subtotal,
                auto_discount_amount=qr.auto_discount_amount,
                manual_discount_amount=self._manual_discount_amount,
                min_order_supplement=supplement,
                final_total=final_total,
                shipping_fee=self._shipping_fee,
                shipping_address=self._shipping_address,
                shipping_free_label=self._shipping_free_label,
                output_path=pdf_path,
            )
            with open(pdf_path, "rb") as f:
                return f.read()

    def _record_acceptance(self, pdf_url: str, quote_number: str) -> None:
        md = self._modal_data
        qr = self._quote_result
        final_total = self._compute_final_total()
        inserted = self._db.insert_customer_record(
            quote_number=quote_number,
            customer_name=md.customer_name,
            drive_folder_url=md.drive_folder_url,
            final_total=final_total,
            pdf_url=pdf_url,
        )
        if not inserted:
            raise ValueError(f"此雲端連結已有接受記錄，無法重複提交：{md.drive_folder_url}")
        manual_discount_str = f"- NT$ {self._manual_discount_amount:,}" if self._manual_discount_amount > 0 else "無"
        self._db.insert_quote_record(
            quote_number=quote_number,
            customer_name=md.customer_name,
            resin_label=self._resin_label,
            body_count=qr.body_count,
            material_cost=qr.material_cost,
            processing_fee=qr.processing_fee,
            auto_discount="95折" if qr.auto_discount_amount > 0 else "無",
            manual_discount=manual_discount_str,
            subtotal=qr.subtotal,
            final_total=final_total,
            order_status=qr.order_status,
            decision="接受",
            drive_folder_url=md.drive_folder_url,
            shipping_fee=self._shipping_fee,
            shipping_address=self._shipping_address,
        )

    def _do_reject(self, rejection_reason: str = "") -> None:
        md = self._modal_data
        qr = self._quote_result
        final_total = self._compute_raw_total()
        file_details_text = _format_file_details(self._file_details)
        manual_discount_str = f"- NT$ {self._manual_discount_amount:,}" if self._manual_discount_amount > 0 else "無"

        self._db.insert_quote_record(
            quote_number="",
            customer_name=md.customer_name,
            resin_label=self._resin_label,
            body_count=qr.body_count,
            material_cost=qr.material_cost,
            processing_fee=qr.processing_fee,
            auto_discount="95折" if qr.auto_discount_amount > 0 else "無",
            manual_discount=manual_discount_str,
            subtotal=qr.subtotal,
            final_total=final_total,
            order_status=qr.order_status,
            decision="拒絕",
            file_details_text=file_details_text,
            rejection_reason=rejection_reason,
            shipping_fee=self._shipping_fee,
            shipping_address=self._shipping_address,
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

        initial_supplement = (
            max(0, 500 - quote_result.final_total)
            if quote_result.order_status == "未達低消"
            else 0
        )
        initial_total = max(500, quote_result.final_total) if initial_supplement > 0 else quote_result.final_total
        embed = _build_quote_embed(
            customer_name=modal_data.customer_name,
            resin_label=resin_label,
            body_count=quote_result.body_count,
            material_cost=quote_result.material_cost,
            processing_fee=quote_result.processing_fee,
            subtotal=quote_result.subtotal,
            auto_discount_amount=quote_result.auto_discount_amount,
            final_total=initial_total,
            order_status=quote_result.order_status,
            file_details=file_details,
            error_files=error_files,
            min_order_supplement=initial_supplement,
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
        await interaction.edit_original_response(content="✅ 估價已發布至頻道。", embed=None)
        msg = await cast(discord.abc.Messageable, interaction.channel).send(embed=embed, view=view)
        view._message = msg

    def _sync_calculate(
        self,
        modal_data: _ModalData,
        resin: ResinType,
        colored: bool,
    ) -> tuple[list[dict], list[str], QuoteResult]:
        drive = DriveClient(self.config.google_service_account_json)
        model_files = drive.list_model_files_recursive(modal_data.folder_id)

        if not model_files:
            raise ValueError(
                "資料夾中找不到 STL/OBJ 模型檔案。\n"
                "請確認：① 資料夾連結正確 ② 資料夾已共享給 Bot 服務帳號 ③ 包含 .stl 或 .obj 檔案"
            )

        with tempfile.TemporaryDirectory() as tmp:
            paths: list[str] = []
            download_errors: list[str] = []
            for f in model_files:
                file_dir = os.path.join(tmp, f["id"])
                os.makedirs(file_dir, exist_ok=True)
                dest = os.path.join(file_dir, f["name"])
                try:
                    drive.download_file(f["id"], dest)
                    paths.append(dest)
                except Exception as exc:
                    _logger.warning("下載失敗 %s (id=%s): %s", f["name"], f["id"], exc)
                    download_errors.append(f["name"])

            results, parse_errors = asyncio.run(read_models(paths))

        error_files = download_errors + parse_errors

        if not results:
            raise ValueError(
                f"找到 {len(model_files)} 個模型檔但全部讀取失敗"
                "（可能為 Google Drive 捷徑、權限不足或格式損毀）：\n"
                + "\n".join(f"• {f}" for f in error_files[:10])
            )

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
