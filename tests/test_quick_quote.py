from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.db.client import DBClient
from bot.pricing.engine import ResinType

_TZ_TAIPEI = timezone(timedelta(hours=8))


def _make_interaction(guild_id: int = 111, role_ids: list[int] | None = None):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    member = MagicMock(spec=discord.Member)
    roles = []
    for rid in (role_ids or []):
        r = MagicMock(spec=discord.Role)
        r.id = rid
        roles.append(r)
    member.roles = roles
    interaction.user = member
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# _parse_models_single_resin
# ---------------------------------------------------------------------------

class TestParseSingleResin:
    def _parse(self, text):
        from bot.commands.quick_quote import _parse_models_single_resin
        return _parse_models_single_resin(text)

    def test_parse_single_resin_valid_single_line(self):
        models, err = self._parse("file1, 25.9, 15")
        assert err is None
        assert len(models) == 1
        assert models[0] == {"filename": "file1", "volume_ml": 25.9, "body_count": 15}

    def test_parse_single_resin_valid_multi_line(self):
        models, err = self._parse("a.stl, 10.0, 3\nb.stl, 5.5, 2")
        assert err is None
        assert len(models) == 2
        assert models[0]["filename"] == "a.stl"
        assert models[1]["filename"] == "b.stl"

    def test_parse_single_resin_blank_lines_skipped(self):
        models, err = self._parse("a.stl, 10.0, 3\n\n   \nb.stl, 5.0, 1")
        assert err is None
        assert len(models) == 2

    def test_parse_single_resin_decimal_volume(self):
        models, err = self._parse("file2, 0.5, 1")
        assert err is None
        assert models[0]["volume_ml"] == 0.5
        assert models[0]["body_count"] == 1

    def test_parse_single_resin_body_count_non_integer_error(self):
        _, err = self._parse("file5, 10.0, 1.5")
        assert err is not None
        assert "件數必須為正整數" in err
        assert "第 1 行" in err

    def test_parse_single_resin_volume_zero_error(self):
        _, err = self._parse("file3, 0.0, 1")
        assert err is not None
        assert "體積必須大於 0" in err
        assert "第 1 行" in err

    def test_parse_single_resin_volume_negative_error(self):
        _, err = self._parse("bad, -5.0, 1")
        assert err is not None
        assert "體積必須大於 0" in err

    def test_parse_single_resin_body_count_zero_error(self):
        _, err = self._parse("file4, 10.0, 0")
        assert err is not None
        assert "件數必須為正整數" in err
        assert "第 1 行" in err

    def test_parse_single_resin_insufficient_columns_error(self):
        _, err = self._parse("file6, 10.0")
        assert err is not None
        assert "需要 3 個欄位" in err
        assert "第 1 行" in err

    def test_parse_single_resin_all_blank_error(self):
        _, err = self._parse("   \n\n  ")
        assert err is not None
        assert "請輸入至少一個檔案" in err

    def test_parse_single_resin_line_number_reflects_non_blank_lines(self):
        _, err = self._parse("\nfile, 10.0, abc")
        assert err is not None
        assert "第 1 行" in err


# ---------------------------------------------------------------------------
# _parse_models_mixed_resin
# ---------------------------------------------------------------------------

class TestParseMixedResin:
    def _parse(self, text):
        from bot.commands.quick_quote import _parse_models_mixed_resin
        return _parse_models_mixed_resin(text)

    def test_parse_mixed_resin_valid_4col_no_tint(self):
        models, err = self._parse("file1, 10.0, 3, RPG")
        assert err is None
        assert models[0]["resin"] == ResinType.RPG
        assert models[0]["colored"] is False

    def test_parse_mixed_resin_valid_5col_with_tint(self):
        models, err = self._parse("file1, 5.0, 2, Aq, C")
        assert err is None
        assert models[0]["resin"] == ResinType.CLEAR
        assert models[0]["colored"] is True

    def test_parse_mixed_resin_mixed_lines(self):
        models, err = self._parse("a.stl, 10.0, 3, RPG\nb.stl, 5.0, 2, Aq, C")
        assert err is None
        assert models[0]["resin"] == ResinType.RPG
        assert models[0]["colored"] is False
        assert models[1]["resin"] == ResinType.CLEAR
        assert models[1]["colored"] is True

    def test_parse_mixed_resin_unknown_resin_error(self):
        _, err = self._parse("file1, 10.0, 3, UV")
        assert err is not None
        assert "未知樹脂代碼" in err
        assert "'UV'" in err
        assert "第 1 行" in err

    def test_parse_mixed_resin_invalid_tint_flag_error(self):
        _, err = self._parse("file1, 10.0, 3, RPG, X")
        assert err is not None
        assert "調色欄位只接受 'C' 或空白" in err
        assert "第 1 行" in err

    def test_parse_mixed_resin_inherits_volume_zero_error(self):
        _, err = self._parse("file, 0.0, 1, RPG")
        assert err is not None
        assert "體積必須大於 0" in err

    def test_parse_mixed_resin_inherits_body_count_error(self):
        _, err = self._parse("file, 5.0, 1.5, RPG")
        assert err is not None
        assert "件數必須為正整數" in err

    def test_parse_mixed_resin_empty_5th_col_is_no_tint(self):
        models, err = self._parse("file1, 10.0, 3, RPG, ")
        assert err is None
        assert models[0]["colored"] is False

    def test_parse_mixed_resin_all_blank_error(self):
        _, err = self._parse("\n\n")
        assert err is not None
        assert "請輸入至少一個檔案" in err


# ---------------------------------------------------------------------------
# QuickModeSelectView — button routing
# ---------------------------------------------------------------------------

class TestQuickModeSelectView:
    def _make_view(self):
        from bot.commands.quick_quote import QuickModeSelectView
        db = MagicMock()
        config = MagicMock()
        config.db_path = ":memory:"
        return QuickModeSelectView(db, config)

    @pytest.mark.asyncio
    async def test_same_resin_button_edits_to_resin_select_view(self):
        from bot.commands.quick_quote import QuickResinSelectView
        view = self._make_view()
        interaction = _make_interaction()
        same_btn = next(
            b for b in view.children
            if isinstance(b, discord.ui.Button) and "同一樹脂" in (b.label or "")
        )
        await same_btn.callback(interaction)
        interaction.response.edit_message.assert_called_once()
        call_kwargs = interaction.response.edit_message.call_args[1]
        assert isinstance(call_kwargs.get("view"), QuickResinSelectView)

    @pytest.mark.asyncio
    async def test_mixed_resin_button_opens_mixed_modal(self):
        from bot.commands.quick_quote import QuickQuoteModal
        view = self._make_view()
        interaction = _make_interaction()
        mixed_btn = next(
            b for b in view.children
            if isinstance(b, discord.ui.Button) and "分別樹脂" in (b.label or "")
        )
        await mixed_btn.callback(interaction)
        interaction.response.send_modal.assert_called_once()
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, QuickQuoteModal)
        assert modal_arg.mode == "mixed"

    @pytest.mark.asyncio
    async def test_view_timeout_is_300(self):
        view = self._make_view()
        assert view.timeout == 300


# ---------------------------------------------------------------------------
# QuickResinSelectView — confirm sends modal
# ---------------------------------------------------------------------------

class TestQuickResinSelectView:
    def _make_view(self):
        from bot.commands.quick_quote import QuickResinSelectView
        db = MagicMock()
        config = MagicMock()
        return QuickResinSelectView(db, config)

    @pytest.mark.asyncio
    async def test_confirm_without_resin_selection_sends_error(self):
        view = self._make_view()
        interaction = _make_interaction()
        confirm_btn = next(
            b for b in view.children
            if isinstance(b, discord.ui.Button) and "確認" in (b.label or "")
        )
        await confirm_btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_confirm_with_resin_sends_single_modal(self):
        from bot.commands.quick_quote import QuickQuoteModal
        view = self._make_view()
        view._selected_resin = ResinType.RPG
        view._colored = False
        interaction = _make_interaction()
        confirm_btn = next(
            b for b in view.children
            if isinstance(b, discord.ui.Button) and "確認" in (b.label or "")
        )
        await confirm_btn.callback(interaction)
        interaction.response.send_modal.assert_called_once()
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, QuickQuoteModal)
        assert modal_arg.mode == "single"
        assert modal_arg._resin_info["resin"] == ResinType.RPG


# ---------------------------------------------------------------------------
# QuickQuoteModal — placeholder text by mode
# ---------------------------------------------------------------------------

class TestQuickQuoteModalPlaceholders:
    @pytest.mark.asyncio
    async def test_single_mode_placeholder_shows_3col_format(self):
        from bot.commands.quick_quote import QuickQuoteModal
        modal = QuickQuoteModal(mode="single", resin_info=None, db=MagicMock(), config=MagicMock())
        ph = modal.models_input.placeholder or ""
        assert "體積" in ph
        assert "件數" in ph

    @pytest.mark.asyncio
    async def test_mixed_mode_placeholder_shows_4_5col_format(self):
        from bot.commands.quick_quote import QuickQuoteModal
        modal = QuickQuoteModal(mode="mixed", resin_info=None, db=MagicMock(), config=MagicMock())
        ph = modal.models_input.placeholder or ""
        assert "樹脂" in ph

    @pytest.mark.asyncio
    async def test_customer_name_max_length_is_50(self):
        from bot.commands.quick_quote import QuickQuoteModal
        modal = QuickQuoteModal(mode="single", resin_info=None, db=MagicMock(), config=MagicMock())
        assert modal.customer_name_input.max_length == 50


# ---------------------------------------------------------------------------
# QuickQuoteModal.on_submit — parse error stops flow (task 4.1 / ephemeral)
# ---------------------------------------------------------------------------

class TestQuickQuoteModalParseError:
    @pytest.mark.asyncio
    async def test_single_mode_parse_error_sends_ephemeral_and_stops(self):
        from bot.commands.quick_quote import QuickQuoteModal
        modal = QuickQuoteModal(mode="single", resin_info={"resin": ResinType.RPG, "colored": False, "label": "RPG高精度樹脂"}, db=MagicMock(), config=MagicMock())
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "客戶A"
        modal.models_input = MagicMock()
        modal.models_input.value = "bad_line"  # only 1 field → parse error
        interaction = _make_interaction()
        await modal.on_submit(interaction)
        interaction.response.defer.assert_called_once_with(ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True
        # channel.send should NOT be called
        if hasattr(interaction, "channel"):
            if interaction.channel:
                interaction.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_mode_parse_error_sends_ephemeral_and_stops(self):
        from bot.commands.quick_quote import QuickQuoteModal
        modal = QuickQuoteModal(mode="mixed", resin_info=None, db=MagicMock(), config=MagicMock())
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "客戶B"
        modal.models_input = MagicMock()
        modal.models_input.value = "file, 10.0, 3, UNKNOWN_RESIN"
        interaction = _make_interaction()
        await modal.on_submit(interaction)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# Quick quote calculation — Route A (task 4.1)
# ---------------------------------------------------------------------------

class TestQuickQuoteRouteACalculation:
    @pytest.mark.asyncio
    async def test_route_a_calculation_produces_positive_final_total(self, tmp_path):
        from bot.commands.quick_quote import QuickQuoteModal, QuickQuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        config = MagicMock()
        config.guild_id = 111
        modal = QuickQuoteModal(
            mode="single",
            resin_info={"resin": ResinType.RPG, "colored": False, "label": "RPG高精度樹脂"},
            db=db, config=config,
        )
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "路線A客戶"
        modal.models_input = MagicMock()
        modal.models_input.value = "a.stl, 10.0, 3\nb.stl, 5.0, 2"

        interaction = _make_interaction(guild_id=111)
        mock_msg = MagicMock()
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock(return_value=mock_msg)

        await modal.on_submit(interaction)

        # channel.send is called with embed + QuickQuoteActionView (no file yet)
        interaction.channel.send.assert_called_once()
        call_kwargs = interaction.channel.send.call_args[1]
        assert "embed" in call_kwargs
        assert "view" in call_kwargs
        assert isinstance(call_kwargs["view"], QuickQuoteActionView)
        assert "file" not in call_kwargs


# ---------------------------------------------------------------------------
# Quick quote calculation — Route B (task 4.2)
# ---------------------------------------------------------------------------

class TestQuickQuoteRouteBCalculation:
    def test_route_b_aggregates_material_costs(self):
        from bot.pricing.engine import (
            calculate_material_cost, calculate_processing_fee, apply_auto_discounts
        )
        models = [
            {"filename": "a.stl", "volume_ml": 10.0, "body_count": 3, "resin": ResinType.RPG, "colored": False},
            {"filename": "b.stl", "volume_ml": 5.0, "body_count": 2, "resin": ResinType.CLEAR, "colored": False},
        ]
        expected_material = (
            calculate_material_cost(ResinType.RPG, 10.0, False) +
            calculate_material_cost(ResinType.CLEAR, 5.0, False)
        )
        total_body = sum(m["body_count"] for m in models)
        expected_processing = calculate_processing_fee(total_body)
        expected_subtotal = expected_material + expected_processing
        final, _, _ = apply_auto_discounts(expected_subtotal)
        assert expected_material > 0
        assert expected_processing > 0
        assert final > 0


# ---------------------------------------------------------------------------
# DB write (task 5.1)
# ---------------------------------------------------------------------------

def _make_quick_action_view(tmp_path):
    from bot.commands.quick_quote import QuickQuoteActionView
    db = DBClient(str(tmp_path / "test.db"))
    config = MagicMock()
    file_details = [{"filename": "a.stl", "volume_ml": 10.0, "body_count": 3}]
    view = QuickQuoteActionView(
        db=db, config=config,
        customer_name="測試客戶",
        resin_label="RPG高精度樹脂",
        file_details=file_details,
        material_cost=400,
        processing_fee=270,
        subtotal=670,
        auto_discount_amount=0,
        auto_discounted_total=670,
        auto_free_ship=False,
        order_status="正常",
    )
    return view, db


class TestQuickQuoteDbWrite:
    @pytest.mark.asyncio
    async def test_db_write_decision_is_quick_and_drive_url_empty(self, tmp_path):
        view, db = _make_quick_action_view(tmp_path)
        view._message = MagicMock()
        view._message.edit = AsyncMock()

        interaction = _make_interaction()
        mock_pdf_msg = MagicMock()
        mock_pdf_msg.attachments = [MagicMock(url="https://cdn.discord.com/pdf.pdf")]
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock(return_value=mock_pdf_msg)

        with patch("bot.commands.quick_quote.generate_quote_pdf"), \
             patch("bot.commands.quick_quote.tempfile.TemporaryDirectory") as mock_tmp, \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"PDF"))),
                 __exit__=MagicMock(return_value=False),
             ))):
            mock_tmp.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            await view.confirm_btn.callback(interaction)

        quote_records = db.get_unsynced_quote_records()
        customer_records = db.get_unsynced_customer_records()
        assert len(quote_records) == 1
        assert quote_records[0]["decision"] == "快速"
        assert quote_records[0]["quote_number"] == ""
        assert len(customer_records) == 1
        assert customer_records[0]["drive_folder_url"] == ""


# ---------------------------------------------------------------------------
# PDF filename sequence (task 5.2)
# ---------------------------------------------------------------------------

class TestQuickQuotePdfFilename:
    def test_filename_seq_001_when_no_records(self, tmp_path):
        from bot.commands.quick_quote import _build_quick_pdf_filename
        db = DBClient(str(tmp_path / "test.db"))
        today = datetime.now(_TZ_TAIPEI)
        date_str = today.strftime("%Y-%m-%d")
        date_nodash = today.strftime("%Y%m%d")
        filename = _build_quick_pdf_filename(db, date_str, date_nodash, "王小明")
        assert filename == f"trb{date_nodash}001-王小明.pdf"

    def test_filename_seq_005_when_4_existing(self, tmp_path):
        from bot.commands.quick_quote import _build_quick_pdf_filename
        db = DBClient(str(tmp_path / "test.db"))
        today = datetime.now(_TZ_TAIPEI)
        date_str = today.strftime("%Y-%m-%d")
        date_slash = today.strftime("%Y/%m/%d")
        date_nodash = today.strftime("%Y%m%d")

        # Insert 4 quick quotes for today directly
        for _ in range(4):
            db._conn.execute(
                """INSERT INTO quote_records
                (created_at, quote_number, customer_name, resin_label, body_count,
                 material_cost, processing_fee, auto_discount, manual_discount,
                 subtotal, final_total, order_status, decision, shipping_fee, shipping_address)
                VALUES (?, '', 'test', 'RPG', 1, 100, 90, '無', '無', 190, 190, '正常', '快速', 0, '')""",
                (f"{date_slash} 10:00",),
            )
        db._conn.commit()

        filename = _build_quick_pdf_filename(db, date_str, date_nodash, "客戶")
        assert filename == f"trb{date_nodash}005-客戶.pdf"


# ---------------------------------------------------------------------------
# Channel send without QuoteActionView (task 5.3)
# ---------------------------------------------------------------------------

class TestQuickQuoteRouteAChannelSend:
    @pytest.mark.asyncio
    async def test_quick_quote_route_a_channel_send(self, tmp_path):
        from bot.commands.quick_quote import QuickQuoteModal, QuickQuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        config = MagicMock()
        modal = QuickQuoteModal(
            mode="single",
            resin_info={"resin": ResinType.RPG, "colored": False, "label": "RPG高精度樹脂"},
            db=db, config=config,
        )
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "頻道測試"
        modal.models_input = MagicMock()
        modal.models_input.value = "m.stl, 8.0, 2"

        interaction = _make_interaction()
        mock_msg = MagicMock()
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock(return_value=mock_msg)

        await modal.on_submit(interaction)

        interaction.channel.send.assert_called_once()
        call_kwargs = interaction.channel.send.call_args[1]
        # embed and QuickQuoteActionView (with discount/shipping) must be present
        assert "embed" in call_kwargs
        assert isinstance(call_kwargs.get("view"), QuickQuoteActionView)
        # no PDF file in this initial send
        assert "file" not in call_kwargs


# ---------------------------------------------------------------------------
# QuickQuoteActionView — discount / shipping / confirm
# ---------------------------------------------------------------------------

class TestQuickQuoteActionView:
    @pytest.mark.asyncio
    async def test_has_discount_button(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        buttons = [b for b in view.children if isinstance(b, discord.ui.Button)]
        labels = [b.label for b in buttons]
        assert any("折扣" in (lbl or "") for lbl in labels)

    @pytest.mark.asyncio
    async def test_has_shipping_button(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        buttons = [b for b in view.children if isinstance(b, discord.ui.Button)]
        labels = [b.label for b in buttons]
        assert any("運送" in (lbl or "") or "運費" in (lbl or "") for lbl in labels)

    @pytest.mark.asyncio
    async def test_has_confirm_button(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        buttons = [b for b in view.children if isinstance(b, discord.ui.Button)]
        labels = [b.label for b in buttons]
        assert any("確認" in (lbl or "") for lbl in labels)

    @pytest.mark.asyncio
    async def test_compute_final_total_no_adjustments(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        assert view._compute_final_total() == 670

    @pytest.mark.asyncio
    async def test_compute_final_total_with_shipping(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        view._shipping_fee = 60
        assert view._compute_final_total() == 730

    @pytest.mark.asyncio
    async def test_compute_final_total_with_manual_discount(self, tmp_path):
        from bot.pricing.engine import DiscountInput
        import math
        view, _ = _make_quick_action_view(tmp_path)
        view._manual_discount = DiscountInput(mode="pct", value=0.9)
        view._manual_discount_amount = 670 - math.floor(670 * 0.9)
        assert view._compute_final_total() == math.floor(670 * 0.9)

    @pytest.mark.asyncio
    async def test_discount_btn_sends_ephemeral_view(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        interaction = _make_interaction()
        await view.discount_btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_shipping_btn_sends_ephemeral_view(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        interaction = _make_interaction()
        await view.shipping_btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_confirm_btn_sends_pdf_file_to_channel(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        view._message = MagicMock()
        view._message.edit = AsyncMock()

        interaction = _make_interaction()
        mock_pdf_msg = MagicMock()
        mock_pdf_msg.attachments = [MagicMock(url="https://cdn.discord.com/q.pdf")]
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock(return_value=mock_pdf_msg)

        with patch("bot.commands.quick_quote.generate_quote_pdf"), \
             patch("bot.commands.quick_quote.tempfile.TemporaryDirectory") as mock_tmp, \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"PDF"))),
                 __exit__=MagicMock(return_value=False),
             ))):
            mock_tmp.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            await view.confirm_btn.callback(interaction)

        call_kwargs = interaction.channel.send.call_args[1]
        assert "file" in call_kwargs

    @pytest.mark.asyncio
    async def test_confirm_btn_includes_shipping_in_pdf_call(self, tmp_path):
        view, _ = _make_quick_action_view(tmp_path)
        view._message = MagicMock()
        view._message.edit = AsyncMock()
        view._shipping_fee = 60
        view._shipping_address = "台北市大安區"

        interaction = _make_interaction()
        mock_pdf_msg = MagicMock()
        mock_pdf_msg.attachments = [MagicMock(url="https://cdn.discord.com/q.pdf")]
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock(return_value=mock_pdf_msg)

        with patch("bot.commands.quick_quote.generate_quote_pdf") as mock_gen, \
             patch("bot.commands.quick_quote.tempfile.TemporaryDirectory") as mock_tmp, \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"PDF"))),
                 __exit__=MagicMock(return_value=False),
             ))):
            mock_tmp.return_value.__enter__ = MagicMock(return_value="/tmp/fake")
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            await view.confirm_btn.callback(interaction)

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("shipping_fee") == 60
        assert call_kwargs.get("shipping_address") == "台北市大安區"


# ---------------------------------------------------------------------------
# QuickQuoteCog — guild lock and role check (task 6.1)
# ---------------------------------------------------------------------------

class TestQuickQuoteCog:
    def _make_cog(self):
        from bot.commands.quick_quote import QuickQuoteCog
        db = MagicMock()
        config = MagicMock()
        config.db_path = ":memory:"
        config.guild_id = 111
        config.member_role_id = 42
        return QuickQuoteCog(db, config)

    @pytest.mark.asyncio
    async def test_wrong_guild_rejected(self):
        cog = self._make_cog()
        interaction = _make_interaction(guild_id=999, role_ids=[42])
        await cog.quick_quote.callback(cog, interaction)
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True
        interaction.response.send_modal.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_role_rejected(self):
        cog = self._make_cog()
        interaction = _make_interaction(guild_id=111, role_ids=[99])
        await cog.quick_quote.callback(cog, interaction)
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_authorized_gets_mode_select_view(self):
        from bot.commands.quick_quote import QuickModeSelectView
        cog = self._make_cog()
        interaction = _make_interaction(guild_id=111, role_ids=[42])
        await cog.quick_quote.callback(cog, interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True
        assert isinstance(call_kwargs.get("view"), QuickModeSelectView)
