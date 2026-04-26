import os
import pytest
from bot.config import Config

REQUIRED_VARS = [
    "DISCORD_TOKEN",
    "GUILD_ID",
    "MEMBER_ROLE_ID",
    "GOOGLE_SHEETS_ID",
    "GOOGLE_CREDENTIALS_PATH",
]

VALID_ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "111111111111111111",
    "MEMBER_ROLE_ID": "222222222222222222",
    "GOOGLE_SHEETS_ID": "test-sheet-id",
    "GOOGLE_CREDENTIALS_PATH": "/app/credentials/google-credentials.json",
}


def test_loads_all_env_vars(monkeypatch):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)

    config = Config.load()

    assert config.discord_token == "test-token"
    assert config.guild_id == 111111111111111111
    assert config.member_role_id == 222222222222222222
    assert config.google_sheets_id == "test-sheet-id"
    assert config.google_credentials_path == "/app/credentials/google-credentials.json"


@pytest.mark.parametrize("missing_var", REQUIRED_VARS)
def test_raises_when_env_var_missing(monkeypatch, missing_var):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing_var)

    with pytest.raises(EnvironmentError, match=missing_var):
        Config.load()
