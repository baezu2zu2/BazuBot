import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bazubot import cards as cards_module
from bazubot.database import get_db, init_db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

intents = discord.Intents.default()


class BazuBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        with get_db() as conn:
            cards_module.sync_cards(conn)

        await self.load_extension("bazubot.cogs.collection")
        await self.load_extension("bazubot.cogs.economy")
        await self.load_extension("bazubot.cogs.market")

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            # Clear any leftover globally-synced commands so they don't show up
            # duplicated alongside the guild-scoped ones.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
        else:
            await self.tree.sync()


bot = BazuBot()


@bot.event
async def on_ready():
    print(f"{bot.user}로 로그인했습니다.")


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN 환경변수를 설정해주세요 (.env 파일 참고).")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
