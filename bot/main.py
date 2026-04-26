import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.config import Config


class TheRollBarBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        self.config = config
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        from bot.commands.quote import QuoteCog

        await self.add_cog(QuoteCog(self, self.config))
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id})")


def main() -> None:
    load_dotenv()
    config = Config.load()
    bot = TheRollBarBot(config)
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
