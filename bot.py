import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bazubot.database import init_db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()


class BazuBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self._commands_synced = False

    async def setup_hook(self):
        # DB는 서버(길드)마다 하나씩 만들어지며, on_ready에서 초기화한다.
        # (주식 시세 루프가 첫 명령어를 기다리지 않고 바로 돌게 하려는 목적)
        await self.load_extension("bazubot.cogs.admin")
        await self.load_extension("bazubot.cogs.collection")
        await self.load_extension("bazubot.cogs.economy")
        await self.load_extension("bazubot.cogs.market")
        await self.load_extension("bazubot.cogs.stocks")
        # 명령어 등록은 서버 목록을 알 수 있는 on_ready에서 한다.

    async def sync_to_guild(self, guild_id: int) -> list:
        """한 서버에 명령어를 직접 등록한다. 재시작 즉시 반영된다."""
        guild = discord.Object(id=guild_id)
        self.tree.copy_global_to(guild=guild)
        return await self.tree.sync(guild=guild)

    async def sync_all_guilds(self) -> None:
        """모든 서버에 명령어를 등록한다.

        글로벌 등록은 Discord가 최대 1시간에 걸쳐 전파해서 서버마다 새 명령어가
        보이는 시점이 달라진다. 서버별로 직접 등록하면 재시작 즉시 반영된다.
        """
        if self._commands_synced:  # on_ready는 재접속 때마다 다시 불린다.
            return
        self._commands_synced = True

        for guild in self.guilds:
            synced = await self.sync_to_guild(guild.id)
            print(f"{guild.name}({guild.id}) 동기화: {len(synced)}개 커맨드")

        # 예전 글로벌 등록이 남아 있으면 같은 명령어가 두 번 보인다.
        # 서버 등록을 끝낸 뒤에 비워야 명령어가 비는 순간이 생기지 않는다.
        # 트리의 글로벌 명령어는 새 서버 초대용으로 다시 채워 둔다.
        global_commands = self.tree.get_commands()
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        for command in global_commands:
            self.tree.add_command(command)
        print("글로벌 등록을 비웠습니다. (이제 서버별 등록만 사용)")


bot = BazuBot()


@bot.event
async def on_ready():
    for guild in bot.guilds:
        init_db(guild.id)
    await bot.sync_all_guilds()
    print(f"{bot.user}로 로그인했습니다. (서버 {len(bot.guilds)}개, 서버별 DB 사용)")


@bot.event
async def on_guild_join(guild: discord.Guild):
    # 새로 초대된 서버는 빈 DB에서 새로 시작하고, 명령어도 바로 등록한다.
    init_db(guild.id)
    synced = await bot.sync_to_guild(guild.id)
    print(
        f"새 서버에 초대되어 DB를 만들고 {len(synced)}개 커맨드를 등록했습니다: "
        f"{guild.name} ({guild.id})"
    )


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN 환경변수를 설정해주세요 (.env 파일 참고).")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
