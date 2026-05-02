import asyncio
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch, call
import discord
import pytest

from bot.db.client import DBClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    return interaction


# ---------------------------------------------------------------------------
# _rename_drive_folder
# ---------------------------------------------------------------------------

class TestGenerateQuoteNumber:
    def test_first_quote_of_day_is_01(self, tmp_path):
        from bot.commands.quote import _generate_quote_number
        db = DBClient(str(tmp_path / "test.db"))
        result = _generate_quote_number(db)
        assert result.startswith("trb")
        assert result.endswith("01")
        assert len(result) == 11  # trb + YYMMDD(6) + NN(2)

    def test_second_quote_increments(self, tmp_path):
        from bot.commands.quote import _generate_quote_number
        from bot.commands.quote import _ModalData
        db = DBClient(str(tmp_path / "test.db"))
        # 先用 _generate_quote_number 的日期前綴手動插一筆接受記錄
        from datetime import datetime, timedelta, timezone
        tz = timezone(timedelta(hours=8))
        today = datetime.now(tz).strftime("%y%m%d")
        db.insert_quote_record(
            quote_number=f"trb{today}01",
            customer_name="A",
            resin_label="RPG高精度樹脂",
            body_count=1,
            material_cost=100,
            processing_fee=80,
            auto_discount="無",
            manual_discount="無",
            subtotal=180,
            final_total=180,
            order_status="正常",
            decision="接受",
        )
        result = _generate_quote_number(db)
        assert result == f"trb{today}02"

    def test_format_trb_yymmdd_nn(self, tmp_path):
        from bot.commands.quote import _generate_quote_number
        db = DBClient(str(tmp_path / "test.db"))
        result = _generate_quote_number(db)
        import re
        assert re.match(r"^trb\d{6}\d{2}$", result)


class TestRenameDriveFolder:
    @pytest.mark.asyncio
    async def test_calls_rename_folder_on_success(self):
        from bot.commands.quote import _rename_drive_folder
        config = MagicMock()
        config.google_service_account_json = '{"type": "service_account"}'
        mock_drive = MagicMock()
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive):
            await _rename_drive_folder(config, "folder_id_xyz", "測試客戶")
        mock_drive.rename_folder.assert_called_once_with("folder_id_xyz", "測試客戶")

    @pytest.mark.asyncio
    async def test_swallows_exception_and_logs_warning(self):
        from bot.commands.quote import _rename_drive_folder
        config = MagicMock()
        config.google_service_account_json = '{"type": "service_account"}'
        mock_drive = MagicMock()
        mock_drive.rename_folder.side_effect = Exception("Drive API error")
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote._logger") as mock_logger:
            await _rename_drive_folder(config, "folder_id_xyz", "測試客戶")
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _guild_check
# ---------------------------------------------------------------------------

class TestGuildCheck:
    def test_correct_guild_returns_true(self):
        from bot.commands.quote import _guild_check
        interaction = _make_interaction(guild_id=111)
        assert _guild_check(interaction, 111) is True

    def test_wrong_guild_returns_false(self):
        from bot.commands.quote import _guild_check
        interaction = _make_interaction(guild_id=999)
        assert _guild_check(interaction, 111) is False


# ---------------------------------------------------------------------------
# _role_check
# ---------------------------------------------------------------------------

class TestRoleCheck:
    def test_user_with_role_returns_true(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[42, 99])
        assert _role_check(interaction, 42) is True

    def test_user_without_role_returns_false(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[99])
        assert _role_check(interaction, 42) is False

    def test_no_roles_returns_false(self):
        from bot.commands.quote import _role_check
        interaction = _make_interaction(role_ids=[])
        assert _role_check(interaction, 42) is False


# ---------------------------------------------------------------------------
# _build_quote_embed
# ---------------------------------------------------------------------------

BASE_EMBED_KWARGS = dict(
    customer_name="測試客戶",
    resin_label="RPG高精度樹脂",
    body_count=3,
    material_cost=444,
    processing_fee=230,
    subtotal=674,
    auto_discount_amount=0,
    manual_discount="無",
    final_total=674,
    order_status="正常",
    file_details=[
        {"filename": "a.stl", "volume_ml": 100.0, "body_count": 2},
        {"filename": "b.stl", "volume_ml": 27.5, "body_count": 1},
    ],
    error_files=[],
)


class TestBuildQuoteEmbed:
    def _build(self, **overrides):
        from bot.commands.quote import _build_quote_embed
        return _build_quote_embed(**{**BASE_EMBED_KWARGS, **overrides})

    def test_returns_discord_embed(self):
        embed = self._build()
        assert isinstance(embed, discord.Embed)

    def test_title_contains_quote_number_when_provided(self):
        embed = self._build(quote_number="trb26043001")
        assert "trb26043001" in (embed.title or "")

    def test_title_without_quote_number_when_empty(self):
        embed = self._build()
        assert embed.title == "📋 估價單"

    def test_contains_customer_name(self):
        embed = self._build(customer_name="VIP客戶")
        field_values = [f.value or "" for f in embed.fields]
        assert any("VIP客戶" in v for v in field_values)

    def test_contains_final_total(self):
        embed = self._build(final_total=1234)
        field_values = [f.value or "" for f in embed.fields]
        assert any("1234" in v for v in field_values)

    def test_contains_order_status(self):
        embed = self._build(order_status="未達低消")
        field_values = [f.value or "" for f in embed.fields]
        assert any("未達低消" in v for v in field_values)

    def test_no_discount_field_when_zero(self):
        embed = self._build(auto_discount_amount=0, manual_discount="無")
        field_names = [f.name or "" for f in embed.fields]
        assert not any("折扣" in n for n in field_names)

    def test_auto_discount_field_shown_when_nonzero(self):
        embed = self._build(auto_discount_amount=350)
        field_names = [f.name or "" for f in embed.fields]
        assert any("折扣" in n for n in field_names)

    def test_manual_discount_field_shown_when_set(self):
        embed = self._build(manual_discount="九折+免運")
        field_names = [f.name or "" for f in embed.fields]
        assert any("折扣" in n for n in field_names)

    def test_error_files_field_shown(self):
        embed = self._build(error_files=["broken.stl", "bad.obj"])
        field_names = [f.name or "" for f in embed.fields]
        assert any("異常" in n for n in field_names)

    def test_no_error_files_field_when_empty(self):
        embed = self._build(error_files=[])
        field_names = [f.name or "" for f in embed.fields]
        assert not any("異常" in n for n in field_names)

    def test_file_details_shown(self):
        embed = self._build(
            file_details=[{"filename": "model.stl", "volume_ml": 50.0, "body_count": 1}]
        )
        field_values = [f.value or "" for f in embed.fields]
        assert any("model.stl" in v for v in field_values)

    def test_file_details_capped_at_10(self):
        many = [{"filename": f"m{i}.stl", "volume_ml": 1.0, "body_count": 1} for i in range(15)]
        embed = self._build(file_details=many)
        detail_fields = [f for f in embed.fields if "m" in (f.value or "").lower() and ".stl" in (f.value or "").lower()]
        shown_files = sum((f.value or "").count(".stl") for f in embed.fields)
        assert shown_files <= 10


# ---------------------------------------------------------------------------
# QuoteActionView._do_accept / _do_reject — DBClient integration
# ---------------------------------------------------------------------------

def _make_quote_result(auto_discount_amount=0, auto_free_ship=False):
    qr = MagicMock()
    qr.body_count = 3
    qr.material_cost = 350
    qr.processing_fee = 240
    qr.auto_discount_amount = auto_discount_amount
    qr.auto_free_ship = auto_free_ship
    qr.subtotal = 590
    qr.final_total = 560
    qr.order_status = "正常"
    return qr


def _make_view_stub(db: DBClient):
    """Return a MagicMock with the attributes QuoteActionView._do_* methods need."""
    from bot.commands.quote import _ModalData
    stub = MagicMock()
    stub._db = db
    stub._modal_data = _ModalData(
        customer_name="測試客戶",
        drive_folder_url="https://drive.google.com/drive/folders/abc",
        folder_id="abc",
    )
    stub._quote_result = _make_quote_result()
    stub._resin_label = "RPG高精度樹脂"
    stub._file_details = []
    stub._error_files = []
    stub._final_total = 560
    stub._final_free_shipping = False
    stub._manual_nine_ten = False
    stub._manual_free_ship = False
    stub._manual_discount_str = MagicMock(return_value="無")
    stub._config = MagicMock()
    stub._config.google_service_account_json = '{"type": "service_account"}'
    return stub


class TestDoAccept:
    def test_writes_to_db_not_sheets(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._record_acceptance(stub, "Discord 附件", "trb26043001")

        quote_records = db.get_unsynced_quote_records()
        customer_records = db.get_unsynced_customer_records()
        assert len(quote_records) == 1
        assert len(customer_records) == 1
        assert quote_records[0]["decision"] == "接受"
        assert customer_records[0]["pdf_url"] == "Discord 附件"

    def test_writes_quote_number_to_record(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._record_acceptance(stub, "Discord 附件", "trb26043001")

        quote_records = db.get_unsynced_quote_records()
        customer_records = db.get_unsynced_customer_records()
        assert quote_records[0]["quote_number"] == "trb26043001"
        assert customer_records[0]["quote_number"] == "trb26043001"

    def test_writes_drive_folder_url_to_quote_record(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._record_acceptance(stub, "Discord 附件", "trb26043001")

        quote_records = db.get_unsynced_quote_records()
        assert quote_records[0]["drive_folder_url"] == "https://drive.google.com/drive/folders/abc"

    def test_does_not_call_sheets_client(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        with patch("bot.commands.quote.SheetsClient") as mock_sheets_cls:
            QuoteActionView._record_acceptance(stub, "Discord 附件", "trb26043001")

        mock_sheets_cls.assert_not_called()


class TestDoReject:
    def test_writes_quote_record_to_db(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._do_reject(stub)

        quote_records = db.get_unsynced_quote_records()
        assert len(quote_records) == 1
        assert quote_records[0]["decision"] == "拒絕"

    def test_writes_rejection_reason_to_db(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._do_reject(stub, "價格太高")

        quote_records = db.get_unsynced_quote_records()
        assert quote_records[0]["rejection_reason"] == "價格太高"

    def test_writes_file_details_text_to_db(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)
        stub._file_details = [{"filename": "model.stl", "volume_ml": 3.5, "body_count": 5}]

        QuoteActionView._do_reject(stub)

        quote_records = db.get_unsynced_quote_records()
        assert quote_records[0]["file_details_text"] == "model.stl: 3.50ml / 5件"

    def test_empty_rejection_reason_stored_as_empty_string(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._do_reject(stub, "")

        quote_records = db.get_unsynced_quote_records()
        assert quote_records[0]["rejection_reason"] == ""

    def test_does_not_write_customer_record(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        QuoteActionView._do_reject(stub)

        customer_records = db.get_unsynced_customer_records()
        assert len(customer_records) == 0

    def test_does_not_call_sheets_client(self, tmp_path):
        from bot.commands.quote import QuoteActionView
        db = DBClient(str(tmp_path / "test.db"))
        stub = _make_view_stub(db)

        with patch("bot.commands.quote.SheetsClient") as mock_sheets_cls:
            QuoteActionView._do_reject(stub)

        mock_sheets_cls.assert_not_called()


# ---------------------------------------------------------------------------
# _format_file_details
# ---------------------------------------------------------------------------

class TestFormatFileDetails:
    def test_single_file(self):
        from bot.commands.quote import _format_file_details
        result = _format_file_details([{"filename": "model.stl", "volume_ml": 3.5, "body_count": 5}])
        assert result == "model.stl: 3.50ml / 5件"

    def test_multiple_files(self):
        from bot.commands.quote import _format_file_details
        details = [
            {"filename": "a.stl", "volume_ml": 2.0, "body_count": 3},
            {"filename": "b.obj", "volume_ml": 1.5, "body_count": 2},
        ]
        result = _format_file_details(details)
        assert result == "a.stl: 2.00ml / 3件\nb.obj: 1.50ml / 2件"

    def test_empty_list(self):
        from bot.commands.quote import _format_file_details
        assert _format_file_details([]) == ""
