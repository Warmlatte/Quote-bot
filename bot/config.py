import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    member_role_id: int
    google_sheets_id: str
    google_service_account_json: str

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
            google_service_account_json=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"],
        )
