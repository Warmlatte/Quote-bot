from unittest.mock import MagicMock, AsyncMock, patch
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
# QuoteCog
# ---------------------------------------------------------------------------

class TestRejectReasonModal:
    @pytest.mark.asyncio
    async def test_init_stores_action_view(self):
        from bot.commands.quote import RejectReasonModal
        action_view = MagicMock()
        modal = RejectReasonModal(action_view)
        assert modal._action_view is action_view

    @pytest.mark.asyncio
    async def test_on_submit_calls_do_reject_with_reason(self):
        from bot.commands.quote import RejectReasonModal
        action_view = MagicMock()
        action_view._do_reject = MagicMock()
        modal = RejectReasonModal(action_view)
        modal.reason_input = MagicMock()
        modal.reason_input.value = "  太貴了  "
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        await modal.on_submit(interaction)
        action_view._do_reject.assert_called_once_with("太貴了")
        interaction.followup.send.assert_called_once()


class TestQuoteModal:
    @pytest.mark.asyncio
    async def test_init_stores_config_and_cog(self):
        from bot.commands.quote import QuoteModal
        config = MagicMock()
        cog = MagicMock()
        modal = QuoteModal(config, cog)
        assert modal._config is config
        assert modal._cog is cog

    @pytest.mark.asyncio
    async def test_on_submit_invalid_url_sends_error(self):
        from bot.commands.quote import QuoteModal
        modal = QuoteModal(MagicMock(), MagicMock())
        modal.drive_url_input = MagicMock()
        modal.drive_url_input.value = "not-a-valid-url"
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "客戶"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_on_submit_valid_url_sends_resin_select(self):
        from bot.commands.quote import QuoteModal
        modal = QuoteModal(MagicMock(), MagicMock())
        modal.drive_url_input = MagicMock()
        modal.drive_url_input.value = "https://drive.google.com/drive/folders/abc123"
        modal.customer_name_input = MagicMock()
        modal.customer_name_input.value = "測試客戶"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        with patch("bot.commands.quote._rename_drive_folder", new_callable=AsyncMock), \
             patch("bot.commands.quote.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            mock_asyncio.get_event_loop = MagicMock()
            await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True


class TestQuoteCog:
    def _make_cog(self):
        from bot.commands.quote import QuoteCog
        mock_bot = MagicMock()
        config = MagicMock()
        config.db_path = ":memory:"
        return QuoteCog(mock_bot, config)

    def test_init_sets_bot_and_config(self):
        from bot.commands.quote import QuoteCog
        mock_bot = MagicMock()
        config = MagicMock()
        config.db_path = ":memory:"
        cog = QuoteCog(mock_bot, config)
        assert cog.bot is mock_bot
        assert cog.config is config

    @pytest.mark.asyncio
    async def test_quote_rejects_wrong_guild(self):
        cog = self._make_cog()
        cog.config.guild_id = 111
        interaction = _make_interaction(guild_id=999, role_ids=[42])
        interaction.response = AsyncMock()
        await cog.quote.callback(cog, interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_quote_rejects_missing_role(self):
        cog = self._make_cog()
        cog.config.guild_id = 111
        cog.config.member_role_id = 42
        interaction = _make_interaction(guild_id=111, role_ids=[99])
        interaction.response = AsyncMock()
        await cog.quote.callback(cog, interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_quote_sends_modal_when_authorized(self):
        cog = self._make_cog()
        cog.config.guild_id = 111
        cog.config.member_role_id = 42
        interaction = _make_interaction(guild_id=111, role_ids=[42])
        interaction.response = AsyncMock()
        await cog.quote.callback(cog, interaction)
        interaction.response.send_modal.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_quote_calculation_handles_exception(self):
        cog = self._make_cog()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.edit_original_response = AsyncMock()
        from bot.commands.quote import _ModalData, ResinType
        modal_data = _ModalData(
            customer_name="測試",
            drive_folder_url="https://drive.google.com/drive/folders/abc",
            folder_id="abc",
        )
        with patch.object(cog, "_sync_calculate", side_effect=ValueError("oops")):
            await cog.run_quote_calculation(interaction, modal_data, ResinType.RPG, False, "RPG")
        interaction.edit_original_response.assert_called_once()
        call_args = interaction.edit_original_response.call_args
        assert "oops" in call_args[1].get("content", "")

    @pytest.mark.asyncio
    async def test_run_quote_calculation_success_sends_embed(self):
        cog = self._make_cog()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.edit_original_response = AsyncMock()
        interaction.channel = AsyncMock()
        interaction.channel.send = AsyncMock()

        from bot.commands.quote import _ModalData, ResinType
        modal_data = _ModalData(
            customer_name="成功客戶",
            drive_folder_url="https://drive.google.com/drive/folders/abc",
            folder_id="abc",
        )
        mock_quote_result = MagicMock()
        mock_quote_result.body_count = 2
        mock_quote_result.material_cost = 200
        mock_quote_result.processing_fee = 150
        mock_quote_result.subtotal = 350
        mock_quote_result.auto_discount_amount = 0
        mock_quote_result.final_total = 350
        mock_quote_result.order_status = "正常"

        with patch.object(cog, "_sync_calculate", return_value=([], [], mock_quote_result)):
            await cog.run_quote_calculation(interaction, modal_data, ResinType.RPG, False, "RPG高精度樹脂")

        interaction.edit_original_response.assert_called_once_with(content="✅ 估價已發布至頻道。", embed=None)
        interaction.channel.send.assert_called_once()


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
        embed = self._build(auto_discount_amount=0, manual_discount_amount=0)
        field_names = [f.name or "" for f in embed.fields]
        assert not any("折扣" in n for n in field_names)

    def test_auto_discount_field_shown_when_nonzero(self):
        embed = self._build(auto_discount_amount=350)
        field_names = [f.name or "" for f in embed.fields]
        assert any("折扣" in n for n in field_names)

    def test_manual_discount_field_shown_when_set(self):
        embed = self._build(manual_discount_amount=100)
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
    from bot.pricing.engine import DiscountInput
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
    stub._manual_discount = DiscountInput(mode="none", value=0)
    stub._manual_discount_amount = 0
    stub._shipping_fee = 0
    stub._shipping_address = ""
    stub._shipping_free_label = False
    stub._compute_final_total = MagicMock(return_value=560)
    stub._compute_raw_total = MagicMock(return_value=560)
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

        with patch("bot.sheets.client.SheetsClient") as mock_sheets_cls:
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
        assert quote_records[0]["file_details_text"] == "model.stl: 3.5ml / 5件"

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

        with patch("bot.sheets.client.SheetsClient") as mock_sheets_cls:
            QuoteActionView._do_reject(stub)

        mock_sheets_cls.assert_not_called()


# ---------------------------------------------------------------------------
# _format_file_details
# ---------------------------------------------------------------------------

class TestSyncCalculateUsesRecursiveListing:
    def _make_cog(self):
        cog = MagicMock()
        cog.config = MagicMock()
        cog.config.google_service_account_json = '{"type": "service_account"}'
        cog._db = MagicMock()
        return cog

    def _make_modal_data(self):
        from bot.commands.quote import _ModalData
        return _ModalData(
            customer_name="測試",
            drive_folder_url="https://drive.google.com/drive/folders/test_folder",
            folder_id="test_folder",
        )

    def test_calls_list_model_files_recursive(self):
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = [{"id": "x1", "name": "model.stl"}]
        mock_result = MagicMock()
        mock_result.volume_ml = 1.0
        mock_result.body_count = 1
        mock_result.filename = "model.stl"
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote.read_models", new_callable=AsyncMock,
                   return_value=([mock_result], [])):
            QuoteCog._sync_calculate(self._make_cog(), self._make_modal_data(), ResinType.RPG, False)
        mock_drive.list_model_files_recursive.assert_called_once_with("test_folder")
        mock_drive.list_model_files.assert_not_called()

    def test_downloads_each_model_file(self):
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = [
            {"id": "id1", "name": "a.stl"},
            {"id": "id2", "name": "b.obj"},
        ]
        mock_model_result = MagicMock()
        mock_model_result.volume_ml = 5.0
        mock_model_result.body_count = 1
        mock_model_result.filename = "a.stl"
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote.read_models", new_callable=AsyncMock,
                   return_value=([mock_model_result], [])):
            QuoteCog._sync_calculate(self._make_cog(), self._make_modal_data(), ResinType.RPG, False)
        assert mock_drive.download_file.call_count == 2

    def test_raises_when_folder_has_no_model_files(self):
        """資料夾中找不到模型檔（可能未共享或空資料夾）應拋出明確 ValueError。"""
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = []
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive):
            with pytest.raises(ValueError, match="找不到"):
                QuoteCog._sync_calculate(
                    self._make_cog(), self._make_modal_data(), ResinType.RPG, False
                )

    def test_raises_when_all_model_files_fail_to_parse(self):
        """所有模型解析失敗（非 watertight）應拋出明確 ValueError。"""
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = [
            {"id": "id1", "name": "broken.stl"},
        ]
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote.read_models", new_callable=AsyncMock,
                   return_value=([], ["broken.stl"])):
            with pytest.raises(ValueError, match="讀取失敗"):
                QuoteCog._sync_calculate(
                    self._make_cog(), self._make_modal_data(), ResinType.RPG, False
                )

    def test_download_failure_is_graceful_and_continues(self):
        """單一檔案下載失敗（捷徑/無權限）不應中斷其他檔案的計算。"""
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = [
            {"id": "id_ok",   "name": "good.stl"},
            {"id": "id_fail", "name": "shortcut.stl"},
        ]

        def download_side_effect(file_id, dest):
            if file_id == "id_fail":
                raise Exception("403 cannotDownloadShortcut")

        mock_drive.download_file.side_effect = download_side_effect
        mock_result = MagicMock()
        mock_result.volume_ml = 5.0
        mock_result.body_count = 1
        mock_result.filename = "good.stl"

        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote.read_models", new_callable=AsyncMock,
                   return_value=([mock_result], [])):
            file_details, error_files, quote_result = QuoteCog._sync_calculate(
                self._make_cog(), self._make_modal_data(), ResinType.RPG, False
            )

        assert len(file_details) == 1
        assert "shortcut.stl" in error_files
        assert quote_result.material_cost > 0

    def test_same_filename_different_subfolders_use_unique_paths(self):
        """同名檔案來自不同子資料夾時，下載路徑必須不同（以 file ID 為子目錄）。"""
        from bot.commands.quote import ResinType, QuoteCog
        mock_drive = MagicMock()
        mock_drive.list_model_files_recursive.return_value = [
            {"id": "id_alpha", "name": "model.stl"},
            {"id": "id_beta", "name": "model.stl"},
        ]
        downloaded_paths: list[str] = []
        mock_drive.download_file.side_effect = lambda _, dest: downloaded_paths.append(dest)
        mock_result = MagicMock()
        mock_result.volume_ml = 2.0
        mock_result.body_count = 1
        mock_result.filename = "model.stl"
        with patch("bot.commands.quote.DriveClient", return_value=mock_drive), \
             patch("bot.commands.quote.read_models", new_callable=AsyncMock,
                   return_value=([mock_result], [])):
            QuoteCog._sync_calculate(self._make_cog(), self._make_modal_data(), ResinType.RPG, False)
        assert len(downloaded_paths) == 2
        assert downloaded_paths[0] != downloaded_paths[1]
        assert "id_alpha" in downloaded_paths[0]
        assert "id_beta" in downloaded_paths[1]


class TestFormatFileDetails:
    def test_single_file(self):
        from bot.commands.quote import _format_file_details
        result = _format_file_details([{"filename": "model.stl", "volume_ml": 3.5, "body_count": 5}])
        assert result == "model.stl: 3.5ml / 5件"

    def test_multiple_files(self):
        from bot.commands.quote import _format_file_details
        details = [
            {"filename": "a.stl", "volume_ml": 2.0, "body_count": 3},
            {"filename": "b.obj", "volume_ml": 1.5, "body_count": 2},
        ]
        result = _format_file_details(details)
        assert result == "a.stl: 2.0ml / 3件\nb.obj: 1.5ml / 2件"

    def test_empty_list(self):
        from bot.commands.quote import _format_file_details
        assert _format_file_details([]) == ""


# ---------------------------------------------------------------------------
# DiscountSelectView & DiscountCustomModal
# ---------------------------------------------------------------------------

def _make_action_view_for_discount(auto_discounted_total: int = 1000, auto_free_ship: bool = False):
    """Return a minimal stub that DiscountSelectView / DiscountCustomModal can call."""
    from bot.pricing.engine import DiscountInput
    stub = MagicMock()
    qr = MagicMock()
    qr.final_total = auto_discounted_total
    qr.auto_free_ship = auto_free_ship
    stub._quote_result = qr
    stub._manual_discount = DiscountInput(mode="none", value=0)
    stub._manual_discount_amount = 0
    stub._refresh_embed = AsyncMock()
    return stub


class TestDiscountCustomModal:
    @pytest.mark.asyncio
    async def test_percentage_80_parsed_correctly(self):
        from bot.commands.quote import DiscountCustomModal
        from bot.pricing.engine import DiscountInput
        action_view = _make_action_view_for_discount(1000)
        modal = DiscountCustomModal(action_view)
        modal.discount_input = MagicMock()
        modal.discount_input.value = "80%"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        await modal.on_submit(interaction)
        assert action_view._manual_discount == DiscountInput(mode="pct", value=0.80)
        action_view._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_fixed_minus100_parsed_correctly(self):
        from bot.commands.quote import DiscountCustomModal
        from bot.pricing.engine import DiscountInput
        action_view = _make_action_view_for_discount(1000)
        modal = DiscountCustomModal(action_view)
        modal.discount_input = MagicMock()
        modal.discount_input.value = "-100"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        await modal.on_submit(interaction)
        assert action_view._manual_discount == DiscountInput(mode="fixed", value=100)
        action_view._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_input_sends_ephemeral_error(self):
        from bot.commands.quote import DiscountCustomModal
        action_view = _make_action_view_for_discount(1000)
        modal = DiscountCustomModal(action_view)
        modal.discount_input = MagicMock()
        modal.discount_input.value = "abc"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        await modal.on_submit(interaction)
        action_view._refresh_embed.assert_not_called()
        assert action_view._manual_discount_amount == 0

    @pytest.mark.asyncio
    async def test_final_total_updates_after_percentage_discount(self):
        from bot.commands.quote import DiscountCustomModal
        import math
        action_view = _make_action_view_for_discount(1000)
        modal = DiscountCustomModal(action_view)
        modal.discount_input = MagicMock()
        modal.discount_input.value = "80%"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        await modal.on_submit(interaction)
        assert action_view._manual_discount_amount == 1000 - math.floor(1000 * 0.8)


class TestDiscountSelectView:
    @pytest.mark.asyncio
    async def test_nine_ten_applies_discount_and_refreshes(self):
        from bot.commands.quote import DiscountSelectView
        from bot.pricing.engine import DiscountInput
        action_view = _make_action_view_for_discount(1000)
        view = DiscountSelectView(action_view)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"values": ["九折"]}
        interaction.response = AsyncMock()
        await view._on_select(interaction)
        assert action_view._manual_discount == DiscountInput(mode="pct", value=0.9)
        action_view._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_discount_resets_and_refreshes(self):
        from bot.commands.quote import DiscountSelectView
        from bot.pricing.engine import DiscountInput
        action_view = _make_action_view_for_discount(1000)
        action_view._manual_discount = DiscountInput(mode="pct", value=0.9)
        action_view._manual_discount_amount = 100
        view = DiscountSelectView(action_view)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"values": ["清除折扣"]}
        interaction.response = AsyncMock()
        await view._on_select(interaction)
        assert action_view._manual_discount == DiscountInput(mode="none", value=0)
        assert action_view._manual_discount_amount == 0
        action_view._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_opens_modal(self):
        from bot.commands.quote import DiscountSelectView
        action_view = _make_action_view_for_discount(1000)
        view = DiscountSelectView(action_view)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"values": ["自訂"]}
        interaction.response = AsyncMock()
        await view._on_select(interaction)
        interaction.response.send_modal.assert_called_once()


# ---------------------------------------------------------------------------
# ShippingView & ShippingModal
# ---------------------------------------------------------------------------

def _make_action_view_for_shipping(auto_free_ship: bool = False):
    """Return a minimal stub for ShippingView / ShippingModal tests."""
    stub = MagicMock()
    qr = MagicMock()
    qr.auto_free_ship = auto_free_ship
    stub._quote_result = qr
    stub._shipping_fee = 0
    stub._shipping_address = ""
    stub._shipping_free_label = False
    stub._refresh_embed = AsyncMock()
    return stub


class TestShippingView:
    @pytest.mark.asyncio
    async def test_init_with_auto_free_ship_true_sets_toggle_active(self):
        from bot.commands.quote import ShippingView
        action_view = _make_action_view_for_shipping(auto_free_ship=True)
        view = ShippingView(action_view)
        assert view._free_active is True

    @pytest.mark.asyncio
    async def test_init_without_free_ship_toggle_inactive(self):
        from bot.commands.quote import ShippingView
        action_view = _make_action_view_for_shipping(auto_free_ship=False)
        view = ShippingView(action_view)
        assert view._free_active is False

    @pytest.mark.asyncio
    async def test_toggle_free_ship_flips_state(self):
        from bot.commands.quote import ShippingView
        action_view = _make_action_view_for_shipping(auto_free_ship=False)
        view = ShippingView(action_view)
        assert view._free_active is False
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        button = MagicMock(spec=discord.ui.Button)
        await view.toggle_free_ship(interaction, button)
        assert view._free_active is True

    @pytest.mark.asyncio
    async def test_fill_address_opens_modal_with_fee_60_when_not_free(self):
        from bot.commands.quote import ShippingView
        action_view = _make_action_view_for_shipping(auto_free_ship=False)
        view = ShippingView(action_view)
        view._free_active = False
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        button = MagicMock(spec=discord.ui.Button)
        await view.fill_address(interaction, button)
        interaction.response.send_modal.assert_called_once()
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert modal_arg.fee_field.default == "60"

    @pytest.mark.asyncio
    async def test_fill_address_opens_modal_with_fee_0_when_free(self):
        from bot.commands.quote import ShippingView
        action_view = _make_action_view_for_shipping(auto_free_ship=False)
        view = ShippingView(action_view)
        view._free_active = True
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        button = MagicMock(spec=discord.ui.Button)
        await view.fill_address(interaction, button)
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert modal_arg.fee_field.default == "0"


class TestShippingModal:
    @pytest.mark.asyncio
    async def test_valid_address_and_fee_updates_state(self):
        from bot.commands.quote import ShippingModal
        action_view = _make_action_view_for_shipping()
        modal = ShippingModal(action_view, fee_default=60, free_toggled=False)
        modal.address_field = MagicMock()
        modal.address_field.value = "台北市大安區"
        modal.fee_field = MagicMock()
        modal.fee_field.value = "60"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        assert action_view._shipping_fee == 60
        assert action_view._shipping_address == "台北市大安區"
        action_view._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_fee_0_with_free_toggled_sets_free_label(self):
        from bot.commands.quote import ShippingModal
        action_view = _make_action_view_for_shipping()
        modal = ShippingModal(action_view, fee_default=0, free_toggled=True)
        modal.address_field = MagicMock()
        modal.address_field.value = "台北市大安區"
        modal.fee_field = MagicMock()
        modal.fee_field.value = "0"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        assert action_view._shipping_free_label is True
        assert action_view._shipping_fee == 0

    @pytest.mark.asyncio
    async def test_invalid_fee_sends_ephemeral_error(self):
        from bot.commands.quote import ShippingModal
        action_view = _make_action_view_for_shipping()
        modal = ShippingModal(action_view, fee_default=60, free_toggled=False)
        modal.address_field = MagicMock()
        modal.address_field.value = "台北市大安區"
        modal.fee_field = MagicMock()
        modal.fee_field.value = "abc"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        action_view._refresh_embed.assert_not_called()
        assert action_view._shipping_address == ""

    @pytest.mark.asyncio
    async def test_fee_field_default_is_60_when_not_free(self):
        from bot.commands.quote import ShippingModal
        action_view = _make_action_view_for_shipping()
        modal = ShippingModal(action_view, fee_default=60, free_toggled=False)
        assert modal.fee_field.default == "60"

    @pytest.mark.asyncio
    async def test_fee_field_default_is_0_when_auto_free(self):
        from bot.commands.quote import ShippingModal
        action_view = _make_action_view_for_shipping(auto_free_ship=True)
        modal = ShippingModal(action_view, fee_default=0, free_toggled=False)
        assert modal.fee_field.default == "0"


# ---------------------------------------------------------------------------
# QuoteActionView final_total calculation
# ---------------------------------------------------------------------------

def _make_full_action_view(tmp_path, auto_discounted_total: int = 1000, auto_free_ship: bool = False):
    """Instantiate a real QuoteActionView with in-memory DB for integration tests."""
    from bot.commands.quote import QuoteActionView, _ModalData
    db = DBClient(str(tmp_path / "test.db"))
    modal_data = _ModalData(
        customer_name="測試客戶",
        drive_folder_url="https://drive.google.com/drive/folders/abc",
        folder_id="abc",
    )
    qr = MagicMock()
    qr.body_count = 3
    qr.material_cost = 700
    qr.processing_fee = 300
    qr.subtotal = 1000
    qr.auto_discount_amount = 0
    qr.auto_free_ship = auto_free_ship
    qr.final_total = auto_discounted_total
    qr.order_status = "正常"
    config = MagicMock()
    config.google_service_account_json = '{}'
    view = QuoteActionView(
        modal_data=modal_data,
        quote_result=qr,
        file_details=[],
        error_files=[],
        resin_label="RPG高精度樹脂",
        config=config,
        db=db,
    )
    return view


class TestQuoteActionViewFinalTotal:
    @pytest.mark.asyncio
    async def test_no_discount_no_shipping(self, tmp_path):
        view = _make_full_action_view(tmp_path, auto_discounted_total=1000)
        assert view._compute_final_total() == 1000

    @pytest.mark.asyncio
    async def test_discount_only(self, tmp_path):
        from bot.pricing.engine import DiscountInput
        view = _make_full_action_view(tmp_path, auto_discounted_total=1000)
        view._manual_discount = DiscountInput(mode="pct", value=0.9)
        view._manual_discount_amount = 100
        assert view._compute_final_total() == 900

    @pytest.mark.asyncio
    async def test_shipping_only(self, tmp_path):
        view = _make_full_action_view(tmp_path, auto_discounted_total=1000)
        view._shipping_fee = 60
        assert view._compute_final_total() == 1060

    @pytest.mark.asyncio
    async def test_discount_and_shipping(self, tmp_path):
        from bot.pricing.engine import DiscountInput
        view = _make_full_action_view(tmp_path, auto_discounted_total=1000)
        view._manual_discount = DiscountInput(mode="pct", value=0.9)
        view._manual_discount_amount = 100
        view._shipping_fee = 60
        assert view._compute_final_total() == 960

    @pytest.mark.asyncio
    async def test_min_order_supplement_applied(self, tmp_path):
        view = _make_full_action_view(tmp_path, auto_discounted_total=300)
        view._quote_result.order_status = "未達低消"
        assert view._compute_min_order_supplement() == 200
        assert view._compute_final_total() == 500

    @pytest.mark.asyncio
    async def test_min_order_supplement_with_shipping(self, tmp_path):
        # 運費是額外服務費，不計入低消基準
        # 低消不足 300，補足 200 至 500，再加運費 60 = 560
        view = _make_full_action_view(tmp_path, auto_discounted_total=300)
        view._quote_result.order_status = "未達低消"
        view._shipping_fee = 60
        assert view._compute_min_order_supplement() == 200  # 補足量不受運費影響
        assert view._compute_final_total() == 560           # 500 + 60

    @pytest.mark.asyncio
    async def test_min_order_raw_total_for_reject_includes_shipping(self, tmp_path):
        # 拒絕報價存原始金額（含運費，無補足）
        view = _make_full_action_view(tmp_path, auto_discounted_total=300)
        view._quote_result.order_status = "未達低消"
        view._shipping_fee = 60
        assert view._compute_raw_total() == 360  # 300 + 60，無補足

    @pytest.mark.asyncio
    async def test_min_order_supplement_zero_when_normal(self, tmp_path):
        view = _make_full_action_view(tmp_path, auto_discounted_total=1000)
        assert view._compute_min_order_supplement() == 0
        assert view._compute_final_total() == 1000

    @pytest.mark.asyncio
    async def test_min_order_no_supplement_when_already_500(self, tmp_path):
        view = _make_full_action_view(tmp_path, auto_discounted_total=500)
        view._quote_result.order_status = "未達低消"
        assert view._compute_min_order_supplement() == 0
        assert view._compute_final_total() == 500


# ---------------------------------------------------------------------------
# BodyCountSelectView
# ---------------------------------------------------------------------------

def _make_action_view_for_body_count(file_details=None):
    from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
    stub = MagicMock()
    stub._file_details = file_details or [
        {"filename": "a.stl", "volume_ml": 5.0, "body_count": 2},
        {"filename": "b.obj", "volume_ml": 3.0, "body_count": 1},
    ]
    stub._quote_result = calculate_quote(
        resin=ResinType.RPG, colored=False, volume_ml=8.0, body_count=3
    )
    stub._manual_discount = DiscountInput(mode="none", value=0)
    stub._manual_discount_amount = 0
    stub._refresh_embed = AsyncMock()
    return stub


class TestBodyCountSelectView:
    @pytest.mark.asyncio
    async def test_body_count_select_confirm_disabled_initially(self):
        from bot.commands.quote import BodyCountSelectView
        stub = _make_action_view_for_body_count()
        view = BodyCountSelectView(stub)
        confirm_btn = next(
            (item for item in view.children
             if isinstance(item, discord.ui.Button) and "確認" in (item.label or "")),
            None,
        )
        assert confirm_btn is not None
        assert confirm_btn.disabled is True

    @pytest.mark.asyncio
    async def test_body_count_select_confirm_enabled_after_select(self):
        from bot.commands.quote import BodyCountSelectView
        stub = _make_action_view_for_body_count()
        view = BodyCountSelectView(stub)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"values": ["0"]}
        interaction.response = AsyncMock()
        await view._on_select(interaction)
        confirm_btn = next(
            (item for item in view.children
             if isinstance(item, discord.ui.Button) and "確認" in (item.label or "")),
            None,
        )
        assert confirm_btn is not None
        assert confirm_btn.disabled is False

    @pytest.mark.asyncio
    async def test_body_count_select_cancel_edits_message(self):
        from bot.commands.quote import BodyCountSelectView
        stub = _make_action_view_for_body_count()
        view = BodyCountSelectView(stub)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await view._on_cancel(interaction)
        interaction.response.edit_message.assert_called_once()
        call_kwargs = interaction.response.edit_message.call_args[1]
        assert call_kwargs.get("content") == "已取消。"

    @pytest.mark.asyncio
    async def test_body_count_select_confirm_sends_modal(self):
        from bot.commands.quote import BodyCountSelectView, BodyCountModal
        stub = _make_action_view_for_body_count()
        view = BodyCountSelectView(stub)
        view._selected_idx = 0
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await view._on_confirm(interaction)
        interaction.response.send_modal.assert_called_once()
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, BodyCountModal)

    @pytest.mark.asyncio
    async def test_body_count_select_select_options_match_file_details(self):
        from bot.commands.quote import BodyCountSelectView
        file_details = [
            {"filename": "model_a.stl", "volume_ml": 5.0, "body_count": 2},
            {"filename": "model_b.obj", "volume_ml": 3.0, "body_count": 1},
        ]
        stub = _make_action_view_for_body_count(file_details)
        view = BodyCountSelectView(stub)
        select = next(
            (item for item in view.children if isinstance(item, discord.ui.Select)),
            None,
        )
        assert select is not None
        option_labels = [opt.label for opt in select.options]
        assert "model_a.stl" in option_labels
        assert "model_b.obj" in option_labels
        option_values = [opt.value for opt in select.options]
        assert "0" in option_values
        assert "1" in option_values


# ---------------------------------------------------------------------------
# BodyCountModal — validation
# ---------------------------------------------------------------------------

class TestBodyCountModalValidation:
    def _make_stub_with_real_quote(self):
        from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
        stub = MagicMock()
        stub._file_details = [
            {"filename": "a.stl", "volume_ml": 5.0, "body_count": 2},
        ]
        stub._quote_result = calculate_quote(
            resin=ResinType.RPG, colored=False, volume_ml=5.0, body_count=2
        )
        stub._manual_discount = DiscountInput(mode="none", value=0)
        stub._manual_discount_amount = 0
        stub._refresh_embed = AsyncMock()
        return stub

    @pytest.mark.asyncio
    async def test_body_count_modal_validation_valid_5(self):
        from bot.commands.quote import BodyCountModal
        stub = self._make_stub_with_real_quote()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "5"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        stub._refresh_embed.assert_called_once()
        interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_body_count_modal_validation_zero_rejected(self):
        from bot.commands.quote import BodyCountModal
        stub = self._make_stub_with_real_quote()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "0"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        msg = call_args[0][0] if call_args[0] else ""
        assert "無效" in msg
        assert call_args[1].get("ephemeral") is True
        stub._refresh_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_body_count_modal_validation_abc_rejected(self):
        from bot.commands.quote import BodyCountModal
        stub = self._make_stub_with_real_quote()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "abc"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        msg = call_args[0][0] if call_args[0] else ""
        assert "無效" in msg
        assert call_args[1].get("ephemeral") is True
        stub._refresh_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_body_count_modal_validation_negative_rejected(self):
        from bot.commands.quote import BodyCountModal
        stub = self._make_stub_with_real_quote()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "-1"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        stub._refresh_embed.assert_not_called()


# ---------------------------------------------------------------------------
# BodyCountModal — recalculation (spec example + manual discount + shipping)
# ---------------------------------------------------------------------------

class TestBodyCountRecalculate:
    @pytest.mark.asyncio
    async def test_body_count_recalculate_spec_example(self):
        """Spec example: file A(2) + file B(1) = 3; change A to 5 → total=6.
        processing_fee = 2*80 + 3*70 + 1*60 = 430, material_cost=ceil(10*3.5)=35, subtotal=465."""
        from bot.commands.quote import BodyCountModal
        from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
        file_details = [
            {"filename": "a.stl", "volume_ml": 5.0, "body_count": 2},
            {"filename": "b.stl", "volume_ml": 5.0, "body_count": 1},
        ]
        stub = MagicMock()
        stub._file_details = file_details
        stub._quote_result = calculate_quote(
            resin=ResinType.RPG, colored=False, volume_ml=10.0, body_count=3
        )
        stub._manual_discount = DiscountInput(mode="none", value=0)
        stub._manual_discount_amount = 0
        stub._refresh_embed = AsyncMock()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "5"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        assert stub._quote_result.body_count == 6
        assert stub._quote_result.processing_fee == 430
        assert stub._quote_result.material_cost == 35
        assert stub._quote_result.subtotal == 465
        assert stub._file_details[0]["body_count"] == 5
        assert stub._file_details[1]["body_count"] == 1
        stub._refresh_embed.assert_called_once()

    @pytest.mark.asyncio
    async def test_body_count_recalculate_file_details_not_mutated(self):
        """Old file_details dicts should not be mutated — new list with new dict."""
        from bot.commands.quote import BodyCountModal
        from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
        original_dict = {"filename": "a.stl", "volume_ml": 5.0, "body_count": 2}
        stub = MagicMock()
        stub._file_details = [original_dict]
        stub._quote_result = calculate_quote(
            resin=ResinType.RPG, colored=False, volume_ml=5.0, body_count=2
        )
        stub._manual_discount = DiscountInput(mode="none", value=0)
        stub._manual_discount_amount = 0
        stub._refresh_embed = AsyncMock()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "5"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        assert original_dict["body_count"] == 2  # original dict unchanged

    @pytest.mark.asyncio
    async def test_body_count_recalculate_manual_discount_recalculated(self):
        """Spec: 九折 active, body count change causes new final_total.
        _manual_discount_amount = new_final_total - floor(new_final_total * 0.9)."""
        import math
        from bot.commands.quote import BodyCountModal
        from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
        file_details = [
            {"filename": "a.stl", "volume_ml": 10.0, "body_count": 2},
        ]
        stub = MagicMock()
        stub._file_details = file_details
        stub._quote_result = calculate_quote(
            resin=ResinType.RPG, colored=False, volume_ml=10.0, body_count=2
        )
        stub._manual_discount = DiscountInput(mode="pct", value=0.9)
        stub._manual_discount_amount = 0
        stub._refresh_embed = AsyncMock()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "10"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        new_final_total = stub._quote_result.final_total
        expected_amount = new_final_total - math.floor(new_final_total * 0.9)
        assert stub._manual_discount_amount == expected_amount

    @pytest.mark.asyncio
    async def test_body_count_recalculate_shipping_fee_unchanged(self):
        """_shipping_fee must remain unchanged after body count override."""
        from bot.commands.quote import BodyCountModal
        from bot.pricing.engine import DiscountInput, ResinType, calculate_quote
        stub = MagicMock()
        stub._file_details = [{"filename": "a.stl", "volume_ml": 5.0, "body_count": 2}]
        stub._quote_result = calculate_quote(
            resin=ResinType.RPG, colored=False, volume_ml=5.0, body_count=2
        )
        stub._manual_discount = DiscountInput(mode="none", value=0)
        stub._manual_discount_amount = 0
        stub._shipping_fee = 60
        stub._refresh_embed = AsyncMock()
        modal = BodyCountModal(stub, 0)
        modal.count_input = MagicMock()
        modal.count_input.value = "5"
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await modal.on_submit(interaction)
        assert stub._shipping_fee == 60


# ---------------------------------------------------------------------------
# QuoteActionView — 🔢 件數 button
# ---------------------------------------------------------------------------

def _make_full_action_view_with_files(tmp_path):
    from bot.commands.quote import QuoteActionView, _ModalData
    from bot.pricing.engine import ResinType, calculate_quote
    db = DBClient(str(tmp_path / "test.db"))
    modal_data = _ModalData(
        customer_name="測試客戶",
        drive_folder_url="https://drive.google.com/drive/folders/abc",
        folder_id="abc",
    )
    qr = calculate_quote(resin=ResinType.RPG, colored=False, volume_ml=10.0, body_count=3)
    config = MagicMock()
    config.google_service_account_json = "{}"
    return QuoteActionView(
        modal_data=modal_data,
        quote_result=qr,
        file_details=[{"filename": "a.stl", "volume_ml": 10.0, "body_count": 3}],
        error_files=[],
        resin_label="RPG高精度樹脂",
        config=config,
        db=db,
    )


class TestBodyCountBtn:
    @pytest.mark.asyncio
    async def test_body_count_btn_exists(self, tmp_path):
        view = _make_full_action_view_with_files(tmp_path)
        btn = next(
            (item for item in view.children
             if isinstance(item, discord.ui.Button) and "件數" in (item.label or "")),
            None,
        )
        assert btn is not None

    @pytest.mark.asyncio
    async def test_body_count_btn_sends_ephemeral(self, tmp_path):
        view = _make_full_action_view_with_files(tmp_path)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await view.body_count_btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_quote_action_view_shows_five_buttons(self, tmp_path):
        view = _make_full_action_view_with_files(tmp_path)
        buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
        assert len(buttons) == 5
        labels = [b.label for b in buttons]
        assert "✏️ 折扣" in labels
        assert "🚚 運送" in labels
        assert "🔢 件數" in labels
        assert "✅ 接受報價" in labels
        assert "❌ 拒絕報價" in labels
