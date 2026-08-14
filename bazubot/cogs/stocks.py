import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import economy
from .. import stocks as stocks_module
from ..database import existing_guild_ids, get_db
from ..discord_utils import require_guild

PRICE_LIST_LIMIT = 25


def _cost_label(total_cost: float, quantity: int) -> str:
    if total_cost <= 0:
        return "무료 획득"
    return f"평단 {round(total_cost / quantity):,}달러"


def _profit_label(profit: float, rate: float | None) -> str:
    arrow = "🔺" if profit > 0 else "🔻" if profit < 0 else "➖"
    # 산 적이 없으면(전량 무료) 나눌 원가가 없어 수익률을 못 낸다.
    if rate is None:
        return f"{arrow} {round(profit):+,}달러"
    return f"{arrow} {round(profit):+,}달러 ({rate:+.2f}%)"


def _profit_color(profit: float) -> discord.Color:
    if profit > 0:
        return discord.Color.red()
    if profit < 0:
        return discord.Color.blue()
    return discord.Color.greyple()


class Stocks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(seconds=stocks_module.TICK_SECONDS)
    async def tick(self):
        # 시세와 배당금은 서버 DB마다 따로 굴러간다.
        market_open = stocks_module.is_market_open()
        for guild_id in existing_guild_ids():
            with get_db(guild_id) as conn:
                # 배당금은 방금 지난 10초 동안의 가격 기준으로 먼저 쌓는다.
                stocks_module.accrue_dividends(conn, stocks_module.TICK_SECONDS)
                if market_open:
                    for name in stocks_module.tick_prices(conn):
                        # 콘솔 인코딩(cp949)에서 터지지 않게 이모지 없이 출력한다.
                        print(f"[{guild_id}] {name} 주식이 0달러 이하로 떨어져 초기화되었습니다.")

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    async def stock_autocomplete(self, interaction: discord.Interaction, current: str):
        # 사업가 명의 주식이 수시로 생기므로 고정 선택지 대신 자동완성을 쓴다.
        if interaction.guild_id is None:
            return []
        with get_db(interaction.guild_id) as conn:
            names = stocks_module.stock_names(conn)
        return [
            app_commands.Choice(name=f"{stocks_module.stock_emoji(name)} {name}", value=name)
            for name in names
            if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="주식", description="주식 시세를 확인합니다.")
    async def prices(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        with get_db(guild_id) as conn:
            rows = stocks_module.get_stocks(conn)

        lines = []
        # 사업가 주식 때문에 종목 수가 계속 늘 수 있어 표시 개수를 제한한다.
        for row in rows[:PRICE_LIST_LIMIT]:
            delta = row["price"] - row["prev_price"]
            arrow = "🔺" if delta > 0 else "🔻" if delta < 0 else "➖"
            lines.append(
                f"{stocks_module.stock_emoji(row['name'])} **{row['name']}** — "
                f"{row['price']:,}달러 {arrow} {delta:+,}"
            )

        market_note = (
            "장이 열려 있어요 (10초마다 시세 변동)"
            if stocks_module.is_market_open()
            else "장이 닫혔어요 — 한국시간 12시부터 시세가 움직여요"
        )
        if len(rows) > PRICE_LIST_LIMIT:
            market_note = (
                f"{len(rows)}종목 중 상위 {PRICE_LIST_LIMIT}개 표시 · {market_note}"
            )
        embed = discord.Embed(
            title="📈 주식 시세",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=market_note)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="주식구매", description="주식을 현재 시세로 구매합니다.")
    @app_commands.describe(종목="구매할 종목", 수량="구매할 주식 수")
    @app_commands.autocomplete(종목=stock_autocomplete)
    async def buy(
        self,
        interaction: discord.Interaction,
        종목: str,
        수량: app_commands.Range[int, 1] = 1,
    ):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            stock = stocks_module.get_stock(conn, 종목)
            if stock is None:
                await interaction.response.send_message(
                    f"'{종목}' 종목을 찾을 수 없어요.", ephemeral=True
                )
                return

            # 잔액을 확인하기 전에 무료 혜택을 소모하지 않도록 먼저 계산만 한다.
            free = min(수량, economy.free_stock_buys_left(conn, user_id))
            total = stock["price"] * (수량 - free)

            balance = economy.ensure_wallet(conn, user_id)
            if total > balance:
                await interaction.response.send_message(
                    f"잔액이 부족해요! 필요 금액: {total:,}달러 (현재 잔액: {balance:,}달러)",
                    ephemeral=True,
                )
                return

            economy.use_free_stock_buys(conn, user_id, free)
            new_balance = balance - total
            economy.set_balance(conn, user_id, new_balance)
            stocks_module.add_holding(conn, user_id, 종목, 수량, total)
            owned = stocks_module.get_quantity(conn, user_id, 종목)
            free_left = economy.free_stock_buys_left(conn, user_id)

        free_note = (
            f"\n🐜 개미 혜택으로 {free}주를 무료로 샀어요! (오늘 남은 무료 구매: {free_left}주)"
            if free
            else ""
        )
        await interaction.response.send_message(
            f"📈 {stocks_module.stock_emoji(종목)} **{종목}** {수량:,}주를 "
            f"**{total:,}달러**에 구매했어요! (주당 {stock['price']:,}달러)"
            f"{free_note}\n보유: {owned:,}주 / 잔액: **{new_balance:,}달러**"
        )

    @app_commands.command(name="주식판매", description="보유한 주식을 현재 시세로 판매합니다.")
    @app_commands.describe(종목="판매할 종목", 수량="판매할 주식 수")
    @app_commands.autocomplete(종목=stock_autocomplete)
    async def sell(
        self,
        interaction: discord.Interaction,
        종목: str,
        수량: app_commands.Range[int, 1] = 1,
    ):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            stock = stocks_module.get_stock(conn, 종목)
            if stock is None:
                await interaction.response.send_message(
                    f"'{종목}' 종목을 찾을 수 없어요.", ephemeral=True
                )
                return

            owned = stocks_module.get_quantity(conn, user_id, 종목)
            if owned < 수량:
                await interaction.response.send_message(
                    f"보유 수량이 부족해요! (보유: {owned:,}주)", ephemeral=True
                )
                return

            total = stock["price"] * 수량
            stocks_module.remove_holding(conn, user_id, 종목, 수량)
            balance = economy.ensure_wallet(conn, user_id)
            new_balance = balance + total
            economy.set_balance(conn, user_id, new_balance)

        await interaction.response.send_message(
            f"📉 {stocks_module.stock_emoji(종목)} **{종목}** {수량:,}주를 "
            f"**{total:,}달러**에 판매했어요! (주당 {stock['price']:,}달러)\n"
            f"보유: {owned - 수량:,}주 / 잔액: **{new_balance:,}달러**"
        )

    @app_commands.command(name="내주식", description="내가 보유한 주식을 확인합니다.")
    async def portfolio(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            holdings = stocks_module.get_holdings(conn, user_id)
            accrued = stocks_module.get_accrued_dividend(conn, user_id)

        if not holdings:
            await interaction.response.send_message(
                f"{interaction.user.display_name}님은 아직 보유한 주식이 없어요.", ephemeral=True
            )
            return

        lines = []
        for row in holdings:
            value = row["price"] * row["quantity"]
            profit, rate = stocks_module.profit_and_rate(value, row["total_cost"])
            cost_label = _cost_label(row["total_cost"], row["quantity"])
            lines.append(
                f"{stocks_module.stock_emoji(row['stock_name'])} **{row['stock_name']}** "
                f"×{row['quantity']:,} — {value:,}달러 (주당 {row['price']:,}달러)\n"
                f"└ {cost_label} · {_profit_label(profit, rate)}"
            )

        total_value = sum(row["price"] * row["quantity"] for row in holdings)
        total_cost = sum(row["total_cost"] for row in holdings)
        total_profit, total_rate = stocks_module.profit_and_rate(total_value, total_cost)

        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}님의 주식",
            description="\n".join(lines),
            color=_profit_color(total_profit),
        )
        embed.add_field(name="평가 금액", value=f"{total_value:,}달러", inline=True)
        embed.add_field(name="매수 금액", value=f"{round(total_cost):,}달러", inline=True)
        embed.add_field(
            name="총 수익률", value=_profit_label(total_profit, total_rate), inline=True
        )
        embed.add_field(name="쌓인 배당금", value=f"{accrued:.4f}달러", inline=True)
        embed.set_footer(text="/배당금 으로 쌓인 배당금을 받을 수 있어요.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="배당금", description="주식으로 쌓인 배당금을 받습니다.")
    async def dividend(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            payout = stocks_module.claim_dividend(conn, user_id)
            remainder = stocks_module.get_accrued_dividend(conn, user_id)
            if payout <= 0:
                await interaction.response.send_message(
                    f"아직 받을 배당금이 없어요. (누적: {remainder:.4f}달러)", ephemeral=True
                )
                return

            balance = economy.ensure_wallet(conn, user_id)
            new_balance = balance + payout
            economy.set_balance(conn, user_id, new_balance)

        await interaction.response.send_message(
            f"💵 배당금 **{payout:,}달러**를 받았어요! "
            f"(남은 누적: {remainder:.4f}달러 / 잔액: **{new_balance:,}달러**)"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Stocks(bot))
