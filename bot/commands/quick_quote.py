import io
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal, cast

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Config
from bot.db.client import DBClient
from bot.pdf_gen.generator import generate_quote_pdf
from bot.pricing.engine import (
    DiscountInput,
    ResinType,
    apply_auto_discounts,
    calculate_material_cost,
    calculate_processing_fee,
    calculate_quote,
)
from bot.commands.quote import (
    DiscountSelectView,
    ShippingView,
    _build_quote_embed,
    _format_file_details,
    _guild_check,
    _role_check,
    _RESIN_BASE_OPTIONS,
)

_logger = logging.getLogger(__name__)
_TZ_TAIPEI = timezone(timedelta(hours=8))

_RESIN_CODE_MAP: dict[str, ResinType] = {
    "RPG": ResinType.RPG,
    "Aq": ResinType.CLEAR,
}


# ---------------------------------------------------------------------------
# Input parsers (private module functions)
# ---------------------------------------------------------------------------

def _parse_models_single_resin(text: str) -> tuple[list[dict], str | None]:
    lines = text.splitlines()
    results: list[dict] = []
    line_num = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        line_num += 1
        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 3:
            return [], f"第 {line_num} 行格式錯誤：需要 3 個欄位"
        filename = parts[0]
        try:
            volume_ml = float(parts[1])
        except ValueError:
            return [], f"第 {line_num} 行格式錯誤：體積格式無效"
        if volume_ml <= 0:
            return [], f"第 {line_num} 行格式錯誤：體積必須大於 0"
        raw_count = parts[2]
        try:
            if "." in raw_count:
                raise ValueError()
            body_count = int(raw_count)
        except ValueError:
            return [], f"第 {line_num} 行格式錯誤：件數必須為正整數"
        if body_count <= 0:
            return [], f"第 {line_num} 行格式錯誤：件數必須為正整數"
        results.append({"filename": filename, "volume_ml": volume_ml, "body_count": body_count})
    if not results:
        return [], "請輸入至少一個檔案"
    return results, None


def _parse_models_mixed_resin(text: str) -> tuple[list[dict], str | None]:
    lines = text.splitlines()
    results: list[dict] = []
    line_num = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        line_num += 1
        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 4:
            return [], f"第 {line_num} 行格式錯誤：需要至少 4 個欄位"
        filename = parts[0]
        try:
            volume_ml = float(parts[1])
        except ValueError:
            return [], f"第 {line_num} 行格式錯誤：體積格式無效"
        if volume_ml <= 0:
            return [], f"第 {line_num} 行格式錯誤：體積必須大於 0"
        raw_count = parts[2]
        try:
            if "." in raw_count:
                raise ValueError()
            body_count = int(raw_count)
        except ValueError:
            return [], f"第 {line_num} 行格式錯誤：件數必須為正整數"
        if body_count <= 0:
            return [], f"第 {line_num} 行格式錯誤：件數必須為正整數"
        resin_code = parts[3]
        if resin_code not in _RESIN_CODE_MAP:
            return [], f"第 {line_num} 行格式錯誤：未知樹脂代碼 '{resin_code}'"
        resin = _RESIN_CODE_MAP[resin_code]
        colored = False
        if len(parts) >= 5:
            color_flag = parts[4]
            if color_flag == "C":
                colored = True
            elif color_flag != "":
                return [], f"第 {line_num} 行格式錯誤：調色欄位只接受 'C' 或空白"
        results.append({
            "filename": filename,
            "volume_ml": volume_ml,
            "body_count": body_count,
            "resin": resin,
            "colored": colored,
        })
    if not results:
        return [], "請輸入至少一個檔案"
    return results, None


# ---------------------------------------------------------------------------
# PDF filename helper
# ---------------------------------------------------------------------------

def _build_quick_pdf_filename(db: DBClient, date_str: str, date_nodash: str, customer_name: str) -> str:
    count = db.count_quick_quotes_today(date_str)
    return f"trb{date_nodash}{count + 1:03d}-{customer_name}.pdf"


# ---------------------------------------------------------------------------
# QuickQuoteActionView — discount / shipping / confirm
# ---------------------------------------------------------------------------

class QuickQuoteActionView(discord.ui.View):
    def __init__(
        self,
        db: DBClient,
        config: Config,
        customer_name: str,
        resin_label: str,
        file_details: list[dict],
        material_cost: int,
        processing_fee: int,
        subtotal: int,
        auto_discount_amount: int,
        auto_discounted_total: int,
        auto_free_ship: bool,
        order_status: str,
    ) -> None:
        super().__init__(timeout=600)
        self._db = db
        self._config = config
        self._customer_name = customer_name
        self._resin_label = resin_label
        self._file_details = file_details
        self._material_cost = material_cost
        self._processing_fee = processing_fee
        self._subtotal = subtotal
        self._auto_discount_amount = auto_discount_amount
        self._auto_discounted_total = auto_discounted_total
        self._order_status = order_status
        self._manual_discount: DiscountInput = DiscountInput(mode="none", value=0)
        self._manual_discount_amount: int = 0
        self._shipping_fee: int = 0
        self._shipping_address: str = ""
        self._shipping_free_label: bool = False
        self._message: discord.Message | None = None
        # DiscountSelectView / ShippingView expect _quote_result.final_total / auto_free_ship
        self._quote_result = SimpleNamespace(
            final_total=auto_discounted_total,
            auto_free_ship=auto_free_ship,
        )

    def _compute_merchandise_total(self) -> int:
        return self._auto_discounted_total - self._manual_discount_amount

    def _compute_min_order_supplement(self) -> int:
        if self._order_status != "未達低消":
            return 0
        return max(0, 500 - self._compute_merchandise_total())

    def _compute_final_total(self) -> int:
        return (
            self._compute_merchandise_total()
            + self._compute_min_order_supplement()
            + self._shipping_fee
        )

    async def _refresh_embed(self) -> None:
        final_total = self._compute_final_total()
        supplement = self._compute_min_order_supplement()
        embed = _build_quote_embed(
            customer_name=self._customer_name,
            resin_label=self._resin_label,
            body_count=sum(f["body_count"] for f in self._file_details),
            material_cost=self._material_cost,
            processing_fee=self._processing_fee,
            subtotal=self._subtotal,
            auto_discount_amount=self._auto_discount_amount,
            final_total=final_total,
            order_status=self._order_status,
            file_details=self._file_details,
            error_files=[],
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
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        view = DiscountSelectView(action_view=self)
        await interaction.response.send_message("選擇折扣選項：", view=view, ephemeral=True)

    @discord.ui.button(label="🚚 運送", style=discord.ButtonStyle.secondary, row=0)
    async def shipping_btn(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        view = ShippingView(action_view=self)
        await interaction.response.send_message("設定運送選項：", view=view, ephemeral=True)

    @discord.ui.button(label="✅ 確認報價", style=discord.ButtonStyle.success, row=1)
    async def confirm_btn(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()

        final_total = self._compute_final_total()
        supplement = self._compute_min_order_supplement()

        taipei_now = datetime.now(_TZ_TAIPEI)
        date_str = taipei_now.strftime("%Y-%m-%d")
        date_nodash = taipei_now.strftime("%Y%m%d")
        pdf_filename = _build_quick_pdf_filename(self._db, date_str, date_nodash, self._customer_name)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_path = os.path.join(tmp, pdf_filename)
                generate_quote_pdf(
                    quote_number="",
                    customer_name=self._customer_name,
                    resin_label=self._resin_label,
                    file_details=self._file_details,
                    error_files=[],
                    material_cost=self._material_cost,
                    processing_fee=self._processing_fee,
                    subtotal=self._subtotal,
                    auto_discount_amount=self._auto_discount_amount,
                    manual_discount_amount=self._manual_discount_amount,
                    min_order_supplement=supplement,
                    final_total=final_total,
                    shipping_fee=self._shipping_fee,
                    shipping_address=self._shipping_address,
                    shipping_free_label=self._shipping_free_label,
                    output_path=output_path,
                )
                with open(output_path, "rb") as f:
                    pdf_bytes = f.read()
        except Exception as exc:
            await interaction.followup.send(f"❌ PDF 生成失敗：{exc}", ephemeral=True)
            return

        try:
            pdf_msg = await interaction.channel.send(
                file=discord.File(io.BytesIO(pdf_bytes), filename=pdf_filename),
            )
        except Exception as exc:
            await interaction.followup.send(f"❌ PDF 發送失敗：{exc}", ephemeral=True)
            return

        pdf_url = pdf_msg.attachments[0].url if pdf_msg.attachments else ""

        manual_discount_str = (
            f"- NT$ {self._manual_discount_amount:,}"
            if self._manual_discount_amount > 0
            else "無"
        )
        self._db.insert_quote_record(
            quote_number="",
            customer_name=self._customer_name,
            resin_label=self._resin_label,
            body_count=sum(f["body_count"] for f in self._file_details),
            material_cost=self._material_cost,
            processing_fee=self._processing_fee,
            auto_discount="95折" if self._auto_discount_amount > 0 else "無",
            manual_discount=manual_discount_str,
            subtotal=self._subtotal,
            final_total=final_total,
            order_status=self._order_status,
            decision="快速",
            drive_folder_url=None,
            file_details_text=_format_file_details(self._file_details),
            shipping_fee=self._shipping_fee,
            shipping_address=self._shipping_address,
        )
        self._db.insert_customer_record(
            quote_number="",
            customer_name=self._customer_name,
            drive_folder_url="",
            final_total=final_total,
            pdf_url=pdf_url,
        )

        self.stop()
        if self._message is not None:
            await self._message.edit(content="✅ 報價已確認。", view=None)


# ---------------------------------------------------------------------------
# Discord Views
# ---------------------------------------------------------------------------

class QuickModeSelectView(discord.ui.View):
    def __init__(self, db: DBClient, config: Config) -> None:
        super().__init__(timeout=300)
        self._db = db
        self._config = config

        same_btn = discord.ui.Button(
            label="同一樹脂",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        same_btn.callback = self._on_same_resin
        self.add_item(same_btn)

        mixed_btn = discord.ui.Button(
            label="分別樹脂",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        mixed_btn.callback = self._on_mixed_resin
        self.add_item(mixed_btn)

    async def _on_same_resin(self, interaction: discord.Interaction) -> None:
        view = QuickResinSelectView(self._db, self._config)
        await interaction.response.edit_message(content="請選擇樹脂種類：", view=view)

    async def _on_mixed_resin(self, interaction: discord.Interaction) -> None:
        modal = QuickQuoteModal(mode="mixed", resin_info=None, db=self._db, config=self._config)
        await interaction.response.send_modal(modal)


class QuickResinSelectView(discord.ui.View):
    def __init__(self, db: DBClient, config: Config) -> None:
        super().__init__(timeout=300)
        self._db = db
        self._config = config
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

        self._colored_btn: discord.ui.Button = discord.ui.Button(
            label="🎨 調色",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1,
        )
        self._colored_btn.callback = self._on_toggle_color
        self.add_item(self._colored_btn)

        confirm_btn = discord.ui.Button(
            label="✅ 確認",
            style=discord.ButtonStyle.success,
            row=1,
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self._selected_resin = ResinType(cast(Any, interaction.data)["values"][0])
        self._colored = False
        self._colored_btn.disabled = self._selected_resin != ResinType.CLEAR
        self._colored_btn.style = discord.ButtonStyle.secondary
        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        await interaction.response.edit_message(
            content=f"請選擇樹脂種類：\n✅ 已選擇：{resin_label}",
            view=self,
        )

    async def _on_toggle_color(self, interaction: discord.Interaction) -> None:
        self._colored = not self._colored
        self._colored_btn.style = (
            discord.ButtonStyle.success if self._colored else discord.ButtonStyle.secondary
        )
        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        color_suffix = "（調色）" if self._colored else ""
        await interaction.response.edit_message(
            content=f"請選擇樹脂種類：\n✅ 已選擇：{resin_label}{color_suffix}",
            view=self,
        )

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if self._selected_resin is None:
            await interaction.response.send_message("⚠️ 請先選擇樹脂種類。", ephemeral=True)
            return
        resin_label = next(label for label, r in _RESIN_BASE_OPTIONS if r == self._selected_resin)
        if self._selected_resin == ResinType.CLEAR and self._colored:
            resin_label += "（調色）"
        resin_info = {
            "resin": self._selected_resin,
            "colored": self._colored,
            "label": resin_label,
        }
        modal = QuickQuoteModal(mode="single", resin_info=resin_info, db=self._db, config=self._config)
        await interaction.response.send_modal(modal)


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class QuickQuoteModal(discord.ui.Modal):
    def __init__(
        self,
        mode: Literal["single", "mixed"],
        resin_info: dict | None,
        db: DBClient,
        config: Config,
    ) -> None:
        title = "快速估價 — 同一樹脂" if mode == "single" else "快速估價 — 分別樹脂"
        super().__init__(title=title)
        self.mode = mode
        self._resin_info = resin_info
        self._db = db
        self._config = config

        self.customer_name_input = discord.ui.TextInput(
            label="客戶名稱",
            style=discord.TextStyle.short,
            max_length=50,
            placeholder="例：骰吧王小明",
            required=True,
        )
        self.add_item(self.customer_name_input)

        if mode == "single":
            placeholder = "格式：檔案名稱, 體積(ml), 件數\n例：a.stl, 25.5, 3"
        else:
            placeholder = "格式：檔案名稱, 體積(ml), 件數, 樹脂[, 調色]\n樹脂代碼：RPG / Aq  調色：C"

        self.models_input = discord.ui.TextInput(
            label="模型列表",
            style=discord.TextStyle.paragraph,
            placeholder=placeholder,
            required=True,
        )
        self.add_item(self.models_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if self.mode == "single":
            models, err = _parse_models_single_resin(self.models_input.value)
        else:
            models, err = _parse_models_mixed_resin(self.models_input.value)

        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        customer_name = self.customer_name_input.value.strip()

        file_details = [
            {"filename": m["filename"], "volume_ml": m["volume_ml"], "body_count": m["body_count"]}
            for m in models
        ]
        total_body_count = sum(m["body_count"] for m in models)

        if self.mode == "single" and self._resin_info:
            resin: ResinType = self._resin_info["resin"]
            colored: bool = self._resin_info["colored"]
            resin_label: str = self._resin_info["label"]
            total_volume_ml = sum(m["volume_ml"] for m in models)
            quote_result = calculate_quote(resin, colored, total_volume_ml, total_body_count)
            material_cost = quote_result.material_cost
            processing_fee = quote_result.processing_fee
            subtotal = quote_result.subtotal
            auto_discounted_total = quote_result.final_total
            auto_discount_amount = quote_result.auto_discount_amount
            auto_free_ship = quote_result.auto_free_ship
            order_status = quote_result.order_status
        else:
            resin_label = "混合樹脂"
            material_cost = sum(
                calculate_material_cost(m["resin"], m["volume_ml"], m["colored"])
                for m in models
            )
            processing_fee = calculate_processing_fee(total_body_count)
            subtotal = material_cost + processing_fee
            auto_discounted_total, auto_free_ship, order_status = apply_auto_discounts(subtotal)
            auto_discount_amount = subtotal - auto_discounted_total

        # Initial display total (include any low-order supplement for display)
        initial_supplement = (
            max(0, 500 - auto_discounted_total) if order_status == "未達低消" else 0
        )
        initial_total = auto_discounted_total + initial_supplement

        embed = _build_quote_embed(
            customer_name=customer_name,
            resin_label=resin_label,
            body_count=total_body_count,
            material_cost=material_cost,
            processing_fee=processing_fee,
            subtotal=subtotal,
            auto_discount_amount=auto_discount_amount,
            final_total=initial_total,
            order_status=order_status,
            file_details=file_details,
            error_files=[],
            min_order_supplement=initial_supplement,
        )

        view = QuickQuoteActionView(
            db=self._db,
            config=self._config,
            customer_name=customer_name,
            resin_label=resin_label,
            file_details=file_details,
            material_cost=material_cost,
            processing_fee=processing_fee,
            subtotal=subtotal,
            auto_discount_amount=auto_discount_amount,
            auto_discounted_total=auto_discounted_total,
            auto_free_ship=auto_free_ship,
            order_status=order_status,
        )
        msg = await interaction.channel.send(embed=embed, view=view)
        view._message = msg


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class QuickQuoteCog(commands.Cog):
    def __init__(self, db: DBClient, config: Config) -> None:
        self._db = db
        self._config = config

    @app_commands.command(name="quick_quote", description="快速手動輸入體積與件數估價")
    async def quick_quote(self, interaction: discord.Interaction) -> None:
        if not _guild_check(interaction, self._config.guild_id):
            await interaction.response.send_message(
                "❌ 此指令僅限指定伺服器使用。", ephemeral=True
            )
            return
        if not _role_check(interaction, self._config.member_role_id):
            await interaction.response.send_message(
                "❌ 您沒有使用此指令的權限，請聯繫管理員。", ephemeral=True
            )
            return
        view = QuickModeSelectView(self._db, self._config)
        await interaction.response.send_message("請選擇估價模式：", view=view, ephemeral=True)
