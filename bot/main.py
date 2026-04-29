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

logger = logging.getLogger(__name__)

# macOS Python.org 安裝包不自動連結系統憑證。
# aiohttp 在模組載入時就建立 _SSL_CONTEXT_VERIFIED，必須直接替換它。
aiohttp.connector._SSL_CONTEXT_VERIFIED = ssl.create_default_context(
    cafile=certifi.where()
)


def _sync_records(db: DBClient, sheets: SheetsClient) -> None:
    for record in db.get_unsynced_quote_records():
        try:
            sheets.append_quote_record(
                quote_number=record["quote_number"],
                customer_name=record["customer_name"],
                resin_label=record["resin_label"],
                body_count=record["body_count"],
                material_cost=record["material_cost"],
                processing_fee=record["processing_fee"],
                auto_discount=record["auto_discount"] == "95折",
                manual_discount=record["manual_discount"],
                subtotal=record["subtotal"],
                final_total=record["final_total"],
                order_status=record["order_status"],
                decision=record["decision"],
            )
            db.mark_quote_record_synced(record["id"])
        except Exception:
            logger.exception("Failed to sync quote record id=%s", record["id"])

    for record in db.get_unsynced_customer_records():
        try:
            sheets.append_customer_record(
                quote_number=record["quote_number"],
                customer_name=record["customer_name"],
                drive_folder_url=record["drive_folder_url"],
                final_total=record["final_total"],
                pdf_url=record["pdf_url"],
            )
            db.mark_customer_record_synced(record["id"])
        except Exception:
            logger.exception("Failed to sync customer record id=%s", record["id"])


class TheRollBarBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        self.config = config
        self._db = DBClient(config.db_path)
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.commands.quote import QuoteCog

        await self.add_cog(QuoteCog(self, self.config))
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self._daily_sync.start()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id})")

    @tasks.loop(hours=24)
    async def _daily_sync(self) -> None:
        logger.info("Starting daily Sheets sync")
        sheets = SheetsClient(
            self.config.google_service_account_json,
            self.config.google_sheets_id,
        )
        _sync_records(self._db, sheets)
        logger.info("Daily Sheets sync complete")

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
