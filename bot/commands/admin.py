import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from bot.commands.quote import _guild_check
from bot.config import Config
from bot.db.client import DBClient
from bot.sheets.client import SheetsClient
from bot.sync import sync_records


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self._config = config
        self._db = DBClient(config.db_path)

    @app_commands.command(
        name="sync_sheets",
        description="立即將 SQLite 未同步的報價記錄推送至 Google Sheets",
    )
    @app_commands.default_permissions(administrator=True)
    async def sync_sheets(self, interaction: discord.Interaction) -> None:
        if not _guild_check(interaction, self._config.guild_id):
            await interaction.response.send_message(
                "❌ 此指令僅限指定伺服器使用。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        sheets = SheetsClient(
            self._config.google_service_account_json,
            self._config.google_sheets_id,
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, sync_records, self._db, sheets)

        lines = [
            "✅ 同步完成！",
            f"報價記錄：同步 {result.synced_quotes} 筆，失敗 {result.failed_quotes} 筆",
            f"客戶記錄：同步 {result.synced_customers} 筆，失敗 {result.failed_customers} 筆",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)
