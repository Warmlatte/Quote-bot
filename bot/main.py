import logging
import ssl

import aiohttp.connector
import certifi
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from bot.config import Config
from bot.db.client import DBClient
from bot.sheets.client import SheetsClient
from bot.sync import sync_records

logger = logging.getLogger(__name__)

# macOS Python.org 安裝包不自動連結系統憑證。
# aiohttp 在模組載入時就建立 _SSL_CONTEXT_VERIFIED，必須直接替換它。
aiohttp.connector._SSL_CONTEXT_VERIFIED = ssl.create_default_context(
    cafile=certifi.where()
)


# Keep the old name as an alias so existing tests importing _sync_records still work.
_sync_records = sync_records


class TheRollBarBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        self.config = config
        self._db = DBClient(config.db_path)
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.commands.admin import AdminCog
        from bot.commands.quote import QuoteCog
        from bot.commands.quick_quote import QuickQuoteCog

        await self.add_cog(QuoteCog(self, self.config))
        await self.add_cog(AdminCog(self, self.config))
        await self.add_cog(QuickQuoteCog(self._db, self.config))
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self._daily_sync.start()

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"Logged in as {self.user} ({self.user.id})")

    @tasks.loop(hours=24)
    async def _daily_sync(self) -> None:
        logger.info("Starting daily Sheets sync")
        sheets = SheetsClient(
            self.config.google_service_account_json,
            self.config.google_sheets_id,
        )
        result = sync_records(self._db, sheets)
        logger.info(
            "Daily Sheets sync complete: quotes=%d/%d customers=%d/%d",
            result.synced_quotes,
            result.synced_quotes + result.failed_quotes,
            result.synced_customers,
            result.synced_customers + result.failed_customers,
        )

    @_daily_sync.before_loop
    async def _before_daily_sync(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    load_dotenv()
    config = Config.load()
    bot = TheRollBarBot(config)
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
