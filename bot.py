import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bazubot.database import init_db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DEV_INSTANT_SYNC = os.getenv("DEV_INSTANT_SYNC") == "1"

intents = discord.Intents.default()


class BazuBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # DB는 서버(길드)마다 하나씩 만들어지며, on_ready에서 초기화한다.
        # (주식 시세 루프가 첫 명령어를 기다리지 않고 바로 돌게 하려는 목적)
        await self.load_extension("bazubot.cogs.admin")
        await self.load_extension("bazubot.cogs.collection")
        await self.load_extension("bazubot.cogs.economy")
        await self.load_extension("bazubot.cogs.market")
        await self.load_extension("bazubot.cogs.stocks")

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
    for guild in bot.guilds:
        init_db(guild.id)
    print(f"{bot.user}로 로그인했습니다. (서버 {len(bot.guilds)}개, 서버별 DB 사용)")


@bot.event
async def on_guild_join(guild: discord.Guild):
    # 새로 초대된 서버는 빈 DB에서 새로 시작한다.
    init_db(guild.id)
    print(f"새 서버에 초대되어 DB를 만들었습니다: {guild.name} ({guild.id})")


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN 환경변수를 설정해주세요 (.env 파일 참고).")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
