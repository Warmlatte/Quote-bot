import json
from unittest.mock import MagicMock, patch

SERVICE_ACCOUNT_JSON = json.dumps({"type": "service_account", "project_id": "test"})
SPREADSHEET_ID = "test-spreadsheet-id"


def _make_client_with_ws():
    from bot.sheets.client import SheetsClient
    with patch("bot.sheets.client.Credentials.from_service_account_info"), \
         patch("bot.sheets.client.gspread.authorize") as mock_auth:
        mock_ws = MagicMock()
        mock_auth.return_value.open_by_key.return_value.worksheet.return_value = mock_ws
        client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
    return client, mock_ws, mock_auth.return_value.open_by_key.return_value


class TestAppendAcceptedQuote:
    def _call(self, client, **overrides):
        defaults = dict(
            created_at="2026-04-30T10:00:00Z",
            quote_number="Q001",
            customer_name="測試客戶",
            drive_folder_url="https://drive.google.com/drive/folders/abc",
            final_total=674,
        )
        client.append_accepted_quote(**{**defaults, **overrides})

    def test_row_has_6_columns(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client)
        row = mock_ws.append_row.call_args[0][0]
        assert len(row) == 6

    def test_first_column_is_empty_string(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client)
        row = mock_ws.append_row.call_args[0][0]
        assert row[0] == ""

    def test_field_positions(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client, quote_number="Q999", customer_name="VIP")
        row = mock_ws.append_row.call_args[0][0]
        assert row[1] == "2026-04-30T10:00:00Z"          # created_at
        assert row[2] == "Q999"                            # quote_number
        assert row[3] == "VIP"                             # customer_name
        assert row[4] == "https://drive.google.com/drive/folders/abc"  # drive_folder_url
        assert row[5] == 674                               # final_total

    def test_null_drive_folder_url_written_as_empty_string(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client, drive_folder_url=None)
        row = mock_ws.append_row.call_args[0][0]
        assert row[4] == ""

    def test_writes_to_報價紀錄_worksheet(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_spreadsheet = mock_auth.return_value.open_by_key.return_value
            mock_spreadsheet.worksheet.return_value = MagicMock()
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_accepted_quote(
                created_at="2026-04-30T10:00:00Z",
                quote_number="Q001",
                customer_name="客戶",
                drive_folder_url=None,
                final_total=500,
            )
        mock_spreadsheet.worksheet.assert_called_with("報價紀錄")


class TestAppendRejectedQuote:
    def _call(self, client, **overrides):
        defaults = dict(
            created_at="2026-04-30T10:00:00Z",
            customer_name="測試客戶",
            final_total=674,
            file_details_text="model.stl: 3.50ml / 5件",
            rejection_reason="價格太高",
        )
        client.append_rejected_quote(**{**defaults, **overrides})

    def test_row_has_6_columns(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client)
        row = mock_ws.append_row.call_args[0][0]
        assert len(row) == 6

    def test_first_column_is_empty_string(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client)
        row = mock_ws.append_row.call_args[0][0]
        assert row[0] == ""

    def test_field_positions(self):
        client, mock_ws, _ = _make_client_with_ws()
        self._call(client, customer_name="拒絕客戶", final_total=800)
        row = mock_ws.append_row.call_args[0][0]
        assert row[1] == "2026-04-30T10:00:00Z"    # created_at
        assert row[2] == "拒絕客戶"                 # customer_name
        assert row[3] == 800                         # final_total
        assert row[4] == "model.stl: 3.50ml / 5件"  # file_details_text
        assert row[5] == "價格太高"                  # rejection_reason

    def test_writes_to_客戶管理_worksheet(self):
        from bot.sheets.client import SheetsClient
        with patch("bot.sheets.client.Credentials.from_service_account_info"), \
             patch("bot.sheets.client.gspread.authorize") as mock_auth:
            mock_spreadsheet = mock_auth.return_value.open_by_key.return_value
            mock_spreadsheet.worksheet.return_value = MagicMock()
            client = SheetsClient(SERVICE_ACCOUNT_JSON, SPREADSHEET_ID)
            client.append_rejected_quote(
                created_at="2026-04-30T10:00:00Z",
                customer_name="客戶",
                final_total=500,
                file_details_text="",
                rejection_reason="",
            )
        mock_spreadsheet.worksheet.assert_called_with("客戶管理")
