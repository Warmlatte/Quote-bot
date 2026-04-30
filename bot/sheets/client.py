import json

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    def __init__(self, service_account_json: str, spreadsheet_id: str) -> None:
        info = json.loads(service_account_json)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        gc = gspread.authorize(creds)
        self._spreadsheet = gc.open_by_key(spreadsheet_id)

    def append_accepted_quote(
        self,
        created_at: str,
        quote_number: str,
        customer_name: str,
        drive_folder_url: str | None,
        final_total: int,
    ) -> None:
        row = [
            "",                              # 0  編號（由試算表公式填入）
            created_at,                      # 1  時間戳
            quote_number,                    # 2  估價單編號
            customer_name,                   # 3  客戶名稱
            drive_folder_url or "",          # 4  Drive 資料夾連結
            final_total,                     # 5  最終總價
        ]
        ws = self._spreadsheet.worksheet("報價紀錄")
        ws.append_row(row)

    def append_rejected_quote(
        self,
        created_at: str,
        customer_name: str,
        final_total: int,
        file_details_text: str,
        rejection_reason: str,
    ) -> None:
        row = [
            "",                 # 0  編號（由試算表公式填入）
            created_at,         # 1  時間戳
            customer_name,      # 2  客戶名稱
            final_total,        # 3  最終總價
            file_details_text,  # 4  檔案明細
            rejection_reason,   # 5  拒絕理由
        ]
        ws = self._spreadsheet.worksheet("客戶管理")
        ws.append_row(row)
