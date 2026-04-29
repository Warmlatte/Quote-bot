import base64
import json
import os
from dataclasses import dataclass


def _decode_service_account_json(raw: str) -> str:
    """回傳原始 JSON 字串；若輸入為 Base64，先解碼再回傳。"""
    try:
        json.loads(raw)
        return raw
    except (json.JSONDecodeError, ValueError):
        # 補齊 Base64 padding（env var 常省略結尾 '='）
        padded = raw + "=" * (-len(raw) % 4)
        return base64.b64decode(padded).decode("utf-8")


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    member_role_id: int
    google_sheets_id: str
    google_service_account_json: str
    db_path: str = "/data/quote_bot.db"

    @classmethod
    def load(cls) -> "Config":
        required = [
            "DISCORD_TOKEN",
            "GUILD_ID",
            "MEMBER_ROLE_ID",
            "GOOGLE_SHEETS_ID",
            "GOOGLE_SERVICE_ACCOUNT_JSON",
        ]
        for var in required:
            if not os.environ.get(var):
                raise EnvironmentError(f"Missing required environment variable: {var}")

        return cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            guild_id=int(os.environ["GUILD_ID"]),
            member_role_id=int(os.environ["MEMBER_ROLE_ID"]),
            google_sheets_id=os.environ["GOOGLE_SHEETS_ID"],
            google_service_account_json=_decode_service_account_json(
                os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
            ),
            db_path=os.environ.get("DB_PATH", "/data/quote_bot.db"),
        )
