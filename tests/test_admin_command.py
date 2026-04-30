"""Tests for AdminCog /sync_sheets command."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.sync import SyncResult


def _make_config(guild_id: int = 111) -> MagicMock:
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.db_path = ":memory:"
    cfg.google_service_account_json = '{"type": "service_account"}'
    cfg.google_sheets_id = "sheet-id"
    return cfg


def _make_interaction(guild_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


def _make_cog(guild_id: int = 111):
    from bot.commands.admin import AdminCog

    bot = MagicMock(spec=discord.ext.commands.Bot)
    config = _make_config(guild_id)
    with patch("bot.commands.admin.DBClient"):
        cog = AdminCog(bot, config)
    return cog


# ---------------------------------------------------------------------------
# Guild check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_wrong_guild():
    cog = _make_cog(guild_id=111)
    interaction = _make_interaction(guild_id=999)

    await cog.sync_sheets.callback(cog, interaction)

    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "❌" in (args[0] if args else kwargs.get("content", ""))
    interaction.response.defer.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_defers_before_sync():
    cog = _make_cog()
    interaction = _make_interaction()
    result = SyncResult(synced_quotes=2, synced_customers=1, failed_quotes=0, failed_customers=0)

    with patch("bot.commands.admin.SheetsClient"), \
         patch("bot.commands.admin.sync_records", return_value=result), \
         patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)
        await cog.sync_sheets.callback(cog, interaction)

    interaction.response.defer.assert_called_once_with(ephemeral=True)


@pytest.mark.asyncio
async def test_followup_contains_counts():
    cog = _make_cog()
    interaction = _make_interaction()
    result = SyncResult(synced_quotes=3, synced_customers=1, failed_quotes=0, failed_customers=0)

    with patch("bot.commands.admin.SheetsClient"), \
         patch("bot.commands.admin.sync_records", return_value=result), \
         patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)
        await cog.sync_sheets.callback(cog, interaction)

    followup_text = interaction.followup.send.call_args[0][0]
    assert "3" in followup_text
    assert "✅" in followup_text


@pytest.mark.asyncio
async def test_followup_shows_failures():
    cog = _make_cog()
    interaction = _make_interaction()
    result = SyncResult(synced_quotes=1, synced_customers=0, failed_quotes=2, failed_customers=1)

    with patch("bot.commands.admin.SheetsClient"), \
         patch("bot.commands.admin.sync_records", return_value=result), \
         patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=result)
        await cog.sync_sheets.callback(cog, interaction)

    followup_text = interaction.followup.send.call_args[0][0]
    assert "2" in followup_text  # failed_quotes
    assert "1" in followup_text  # failed_customers
