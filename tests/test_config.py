import pytest
from bot.config import Config

REQUIRED_VARS = [
    "DISCORD_TOKEN",
    "GUILD_ID",
    "MEMBER_ROLE_ID",
    "GOOGLE_SHEETS_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
]

VALID_ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "111111111111111111",
    "MEMBER_ROLE_ID": "222222222222222222",
    "GOOGLE_SHEETS_ID": "test-sheet-id",
    "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type": "service_account", "project_id": "test"}',
}


def test_loads_all_env_vars(monkeypatch):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)

    config = Config.load()

    assert config.discord_token == "test-token"
    assert config.guild_id == 111111111111111111
    assert config.member_role_id == 222222222222222222
    assert config.google_sheets_id == "test-sheet-id"
    assert config.google_service_account_json == '{"type": "service_account", "project_id": "test"}'


@pytest.mark.parametrize("missing_var", REQUIRED_VARS)
def test_raises_when_env_var_missing(monkeypatch, missing_var):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing_var)

    with pytest.raises(EnvironmentError, match=missing_var):
        Config.load()


def test_db_path_defaults_when_not_set(monkeypatch):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DB_PATH", raising=False)

    config = Config.load()

    assert config.db_path == "/data/quote_bot.db"


def test_db_path_can_be_overridden(monkeypatch):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DB_PATH", "./local.db")

    config = Config.load()

    assert config.db_path == "./local.db"


def test_loads_base64_encoded_service_account(monkeypatch):
    import base64
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    raw = VALID_ENV["GOOGLE_SERVICE_ACCOUNT_JSON"]
    encoded = base64.b64encode(raw.encode()).decode()
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", encoded)

    config = Config.load()

    assert config.google_service_account_json == raw
