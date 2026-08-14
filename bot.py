import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bazubot import cards as cards_module
from bazubot.database import get_db, init_db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DEV_INSTANT_SYNC = os.getenv("DEV_INSTANT_SYNC") == "1"

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
        # (first-time propagation can take up to ~1 hour for Discord to roll out).
        global_synced = await self.tree.sync()
        print(
            f"글로벌 동기화: {len(global_synced)}개 커맨드 "
            f"({', '.join(c.name for c in global_synced)})"
        )

        if DEV_INSTANT_SYNC and GUILD_ID:
            # Opt-in only: also push an instant copy to the dev/test guild so
            # command changes show up immediately while iterating locally.
            # That guild will show each command twice (global + guild copy)
            # while this is on, so leave DEV_INSTANT_SYNC unset in production.
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            print(
                f"길드({GUILD_ID}) 즉시 동기화: {len(guild_synced)}개 커맨드 "
                f"({', '.join(c.name for c in guild_synced)})"
            )


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
