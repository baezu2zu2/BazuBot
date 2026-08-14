from datetime import time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import economy
from ..checks import is_test_guild
from ..database import get_db
from ..discord_utils import resolve_display_name

KST = ZoneInfo("Asia/Seoul")
SALARY_TIME = time(hour=7, minute=0, tzinfo=KST)


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pay_salary.start()

    def cog_unload(self):
        self.pay_salary.cancel()

    @tasks.loop(time=SALARY_TIME)
    async def pay_salary(self):
        with get_db() as conn:
            rows = conn.execute("SELECT user_id, job FROM job").fetchall()
            for row in rows:
                pay = economy.pay_for_job(row["job"])
                balance = economy.ensure_wallet(conn, row["user_id"])
                economy.set_balance(conn, row["user_id"], balance + pay)

    @pay_salary.before_loop
    async def before_pay_salary(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="잔액", description="내 잔액을 확인합니다.")
    async def balance(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        with get_db() as conn:
            balance = economy.ensure_wallet(conn, user_id)
        await interaction.response.send_message(
            f"💰 {interaction.user.display_name}님의 잔액: **{balance:,}달러**"
        )

    @app_commands.command(name="돈랭킹", description="잔액 랭킹을 확인합니다.")
    async def money_leaderboard(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT user_id, balance FROM wallet ORDER BY balance DESC"
            ).fetchall()

        if not rows:
            await interaction.response.send_message("아직 잔액 기록이 없어요.", ephemeral=True)
            return

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for i, row in enumerate(rows[:10]):
            name = await resolve_display_name(interaction.guild, row["user_id"])
            rank_label = medals.get(i, f"{i + 1}.")
            lines.append(f"{rank_label} {name} — {row['balance']:,}달러")

        embed = discord.Embed(
            title="💰 잔액 랭킹",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

        my_rank = next((i for i, row in enumerate(rows) if row["user_id"] == user_id), None)
        if my_rank is not None and my_rank >= 10:
            embed.set_footer(
                text=(
                    f"{interaction.user.display_name}님의 순위: {my_rank + 1}위 "
                    f"({rows[my_rank]['balance']:,}달러)"
                )
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="룰렛", description="금액을 걸고 색을 맞추면 2배, 틀리면 전부 잃습니다."
    )
    @app_commands.describe(금액="배팅할 금액", 색상="빨강 또는 파랑 중 선택")
    @app_commands.choices(
        색상=[
            app_commands.Choice(name="🔴 빨강", value="빨강"),
            app_commands.Choice(name="🔵 파랑", value="파랑"),
        ]
    )
    async def roulette(
        self,
        interaction: discord.Interaction,
        금액: app_commands.Range[int, 1],
        색상: app_commands.Choice[str],
    ):
        user_id = str(interaction.user.id)
        with get_db() as conn:
            balance = economy.ensure_wallet(conn, user_id)
            if 금액 > balance:
                await interaction.response.send_message(
                    f"잔액이 부족해요! 현재 잔액: {balance:,}달러", ephemeral=True
                )
                return

            result = economy.spin()
            won = result == 색상.value
            new_balance = balance + 금액 if won else balance - 금액
            economy.set_balance(conn, user_id, new_balance)

        emoji = economy.COLOR_EMOJI
        outcome = "🎉 승리!" if won else "💥 패배..."
        embed = discord.Embed(
            title=f"🎰 룰렛 — {outcome}",
            description=(
                f"{interaction.user.display_name}님이 {emoji[색상.value]} **{색상.value}**에 "
                f"{금액:,}달러를 걸었어요.\n"
                f"결과: {emoji[result]} **{result}**\n\n"
                f"{'+' if won else '-'}{금액:,}달러 → 잔액: **{new_balance:,}달러**"
            ),
            color=discord.Color.gold() if won else discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="취직", description="직업을 선택합니다. 매일 한국시간 오전 7시에 급여를 받아요."
    )
    @app_commands.describe(직업="선택할 직업")
    @app_commands.choices(
        직업=[
            app_commands.Choice(
                name=f"{economy.JOB_EMOJI[job]} {job} ({economy.JOB_DESCRIPTION[job]})",
                value=job,
            )
            for job in economy.JOBS
        ]
    )
    async def get_job(self, interaction: discord.Interaction, 직업: app_commands.Choice[str]):
        user_id = str(interaction.user.id)
        with get_db() as conn:
            economy.set_job(conn, user_id, 직업.value)
        await interaction.response.send_message(
            f"{economy.JOB_EMOJI[직업.value]} {interaction.user.display_name}님이 "
            f"**{직업.value}**(으)로 취직했어요! ({economy.JOB_DESCRIPTION[직업.value]})\n"
            "매일 한국시간 오전 7시에 급여가 지급돼요.",
            ephemeral=True,
        )

    @app_commands.command(name="기부", description="내 잔액에서 다른 유저에게 돈을 보냅니다.")
    @app_commands.describe(대상="돈을 받을 유저", 금액="보낼 금액")
    async def donate(
        self,
        interaction: discord.Interaction,
        대상: discord.Member,
        금액: app_commands.Range[int, 1],
    ):
        if 대상.id == interaction.user.id:
            await interaction.response.send_message("자기 자신에게는 기부할 수 없어요.", ephemeral=True)
            return
        if 대상.bot:
            await interaction.response.send_message("봇에게는 기부할 수 없어요.", ephemeral=True)
            return

        sender_id = str(interaction.user.id)
        receiver_id = str(대상.id)
        with get_db() as conn:
            sender_balance = economy.ensure_wallet(conn, sender_id)
            if 금액 > sender_balance:
                await interaction.response.send_message(
                    f"잔액이 부족해요! 현재 잔액: {sender_balance:,}달러", ephemeral=True
                )
                return

            receiver_balance = economy.ensure_wallet(conn, receiver_id)
            new_sender_balance = sender_balance - 금액
            new_receiver_balance = receiver_balance + 금액
            economy.set_balance(conn, sender_id, new_sender_balance)
            economy.set_balance(conn, receiver_id, new_receiver_balance)

        await interaction.response.send_message(
            f"💌 {interaction.user.display_name}님이 {대상.display_name}님에게 "
            f"{금액:,}달러를 기부했어요! (내 잔액: {new_sender_balance:,}달러)"
        )

    @app_commands.command(name="돈지급", description="[테스트용] 유저에게 돈을 지급합니다.")
    @app_commands.describe(대상="돈을 지급할 유저", 금액="지급할 금액")
    @app_commands.checks.has_permissions(administrator=True)
    @is_test_guild()
    async def grant_money(
        self, interaction: discord.Interaction, 대상: discord.Member, 금액: int
    ):
        user_id = str(대상.id)
        with get_db() as conn:
            balance = economy.ensure_wallet(conn, user_id)
            new_balance = balance + 금액
            economy.set_balance(conn, user_id, new_balance)
        await interaction.response.send_message(
            f"💸 {대상.display_name}님에게 {금액:,}달러를 지급했어요. (잔액: {new_balance:,}달러)",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "이 명령어는 테스트 서버에서 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
