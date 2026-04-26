import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

SERVICE_ACCOUNT_JSON = json.dumps({"type": "service_account", "project_id": "test"})
SPREADSHEET_ID = "test-spreadsheet-id"


def _make_client():
    from bot.sheets.client import SheetsClient
    with patch("bot.sheets.client.Credentials.from_service_account_info"), \
         patch("bot.sheets.client.gspread.authorize") as mock_auth:
        mock_auth.return_value = MagicMock()
        client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
    return client


class TestAppendQuoteRecord:
    def _call(self, client, **overrides):
        defaults = dict(
            quote_number="Q001",
            customer_name="測試客戶",
            resin_label="RPG高精度樹脂",
            body_count=3,
            material_cost=444,
            processing_fee=230,
            auto_discount=False,
            manual_discount="無",
            subtotal=674,
            final_total=674,
            order_status="正常",
            decision="待定",
        )
        return {**defaults, **overrides}

    def test_row_has_13_columns(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client))

        row = mock_ws.append_row.call_args[0][0]
        assert len(row) == 13

    def test_timestamp_is_utc(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client))

        row = mock_ws.append_row.call_args[0][0]
        assert row[0].endswith("Z")

    def test_field_positions(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client, quote_number="Q999", customer_name="VIP"))

        row = mock_ws.append_row.call_args[0][0]
        assert row[1] == "Q999"           # 估價單編號
        assert row[2] == "VIP"            # 客戶名稱
        assert row[3] == "RPG高精度樹脂"  # 樹脂種類
        assert row[4] == 3                # 總件數
        assert row[5] == 444              # 材料費
        assert row[6] == 230              # 加工費
        assert row[9] == 674              # 折前總價
        assert row[10] == 674             # 最終總價
        assert row[12] == "待定"          # 客戶決定

    def test_auto_discount_true_shows_95_fold(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client, auto_discount=True))

        row = mock_ws.append_row.call_args[0][0]
        assert row[7] == "95折"

    def test_auto_discount_false_shows_none(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client, auto_discount=False))

        row = mock_ws.append_row.call_args[0][0]
        assert row[7] == "無"

    def test_manual_discount_field(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client, manual_discount="九折+免運"))

        row = mock_ws.append_row.call_args[0][0]
        assert row[8] == "九折+免運"

    def test_writes_to_correct_worksheet(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_spreadsheet = mock_auth.return_value.open_by_key.return_value
            mock_ws = MagicMock()
            mock_spreadsheet.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_quote_record(**self._call(client))

        mock_spreadsheet.worksheet.assert_called_with("報價記錄")


class TestAppendCustomerRecord:
    def test_row_has_6_columns(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_customer_record(
                quote_number="Q001",
                customer_name="測試客戶",
                drive_folder_url="https://drive.google.com/drive/folders/abc",
                final_total=674,
                pdf_url="https://drive.google.com/file/d/xyz/view",
            )

        row = mock_ws.append_row.call_args[0][0]
        assert len(row) == 6

    def test_field_positions(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_ws = MagicMock()
            mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_customer_record(
                quote_number="Q999",
                customer_name="VIP客戶",
                drive_folder_url="https://drive.google.com/drive/folders/abc",
                final_total=1042,
                pdf_url="https://drive.google.com/file/d/xyz/view",
            )

        row = mock_ws.append_row.call_args[0][0]
        assert row[0] == "Q999"
        assert row[1] == "VIP客戶"
        assert row[2] == "https://drive.google.com/drive/folders/abc"
        assert row[3] == 1042
        assert row[4].endswith("Z")   # 接受時間 UTC
        assert row[5] == "https://drive.google.com/file/d/xyz/view"

    def test_writes_to_correct_worksheet(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_spreadsheet = mock_auth.return_value.open_by_key.return_value
            mock_spreadsheet.worksheet.return_value = MagicMock()
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_customer_record(
                quote_number="Q001",
                customer_name="客戶",
                drive_folder_url="https://drive.google.com/drive/folders/abc",
                final_total=674,
                pdf_url="https://drive.google.com/file/d/xyz/view",
            )

        mock_spreadsheet.worksheet.assert_called_with("客戶管理")
