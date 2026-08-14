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

        # Global sync so commands work in every server the bot is invited to
        # (propagation can take up to ~1 hour for Discord to roll out).
        await self.tree.sync()

        if GUILD_ID:
            # Also push an instant copy to the dev/test guild so command
            # changes show up immediately while iterating locally. This
            # guild will see the same commands twice (global + guild copy)
            # until Discord's global rollout catches up — expected during dev.
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)


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
