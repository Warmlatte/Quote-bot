import json
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_RESIN_LABEL_MAP = {
    "RPG高精度樹脂": "RPG高精度樹脂",
    "透明樹脂": "透明樹脂",
    "透明樹脂（調色）": "透明樹脂（調色）",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SheetsClient:
    def __init__(self, service_account_json: str, spreadsheet_id: str) -> None:
        info = json.loads(service_account_json)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        gc = gspread.authorize(creds)
        self._spreadsheet = gc.open_by_key(spreadsheet_id)

    def append_quote_record(
        self,
        quote_number: str,
        customer_name: str,
        resin_label: str,
        body_count: int,
        material_cost: int,
        processing_fee: int,
        auto_discount: bool,
        manual_discount: str,
        subtotal: int,
        final_total: int,
        order_status: str,
        decision: str,
    ) -> None:
        row = [
            _utc_now(),                      # 0  時間戳
            quote_number,                    # 1  估價單編號
            customer_name,                   # 2  客戶名稱
            resin_label,                     # 3  樹脂種類
            body_count,                      # 4  總件數
            material_cost,                   # 5  材料費
            processing_fee,                  # 6  加工費
            "95折" if auto_discount else "無",  # 7  固定折扣
            manual_discount,                 # 8  手動折扣
            subtotal,                        # 9  折前總價
            final_total,                     # 10 最終總價
            order_status,                    # 11 訂單狀態
            decision,                        # 12 客戶決定
        ]
        ws = self._spreadsheet.worksheet("報價記錄")
        ws.append_row(row)

    def append_customer_record(
        self,
        quote_number: str,
        customer_name: str,
        drive_folder_url: str,
        final_total: int,
        pdf_url: str,
    ) -> None:
        row = [
            quote_number,       # 0  估價單編號
            customer_name,      # 1  客戶名稱
            drive_folder_url,   # 2  Drive 資料夾連結
            final_total,        # 3  最終報價
            _utc_now(),         # 4  接受時間
            pdf_url,            # 5  PDF 連結
        ]
        ws = self._spreadsheet.worksheet("客戶管理")
        ws.append_row(row)
