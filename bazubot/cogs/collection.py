from datetime import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import cards as cards_module
from .. import economy
from ..checks import is_test_guild
from ..database import existing_guild_ids, get_db
from ..discord_utils import require_guild, resolve_display_name

DEX_PAGE_SIZE = 20

# 한국시간 매시 0분부터 10분 간격(:00, :10, ... :50).
# 절대 시각 기준이라 봇을 재시작해도 리셋 시점이 밀리지 않는다.
CARD_RESET_TIMES = [
    time(hour=hour, minute=minute, tzinfo=economy.KST)
    for hour in range(24)
    for minute in range(0, 60, cards_module.CARD_RESET_MINUTES)
]


class DexView(discord.ui.View):
    def __init__(
        self,
        rarities: list[str],
        pages: dict[str, list],
        owned_ids: set[int],
        author_id: int,
        category: Optional[str] = None,
    ):
        super().__init__(timeout=120)
        self.pages = pages
        self.owned_ids = owned_ids
        self.author_id = author_id
        self.category = category

        self.page_specs: list[tuple[str, list]] = []
        for rarity in rarities:
            cards = pages[rarity]
            for i in range(0, len(cards), DEX_PAGE_SIZE):
                self.page_specs.append((rarity, cards[i : i + DEX_PAGE_SIZE]))

        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index == len(self.page_specs) - 1

    def build_embed(self) -> discord.Embed:
        rarity, chunk = self.page_specs[self.index]
        tier_cards = self.pages[rarity]
        owned_count = sum(1 for c in tier_cards if c["id"] in self.owned_ids)
        total_weight = sum(cards_module.RARITY_WEIGHTS[r] for r in self.pages if self.pages[r])
        tier_prob = cards_module.RARITY_WEIGHTS[rarity] / total_weight * 100
        title_prefix = f"📖 도감 [{self.category}]" if self.category else "📖 도감"
        embed = discord.Embed(
            title=(
                f"{title_prefix} — {cards_module.RARITY_EMOJI[rarity]} "
                f"{cards_module.RARITY_LABEL[rarity]} ({tier_prob:.1f}%)"
            ),
            description=f"보유 {owned_count} / {len(tier_cards)}",
            color=discord.Color.blurple(),
        )
        lines = [
            f"{'✅' if c['id'] in self.owned_ids else '❔'} "
            f"{cards_module.RARITY_EMOJI[c['rarity']]} {c['name']}"
            for c in chunk
        ]
        embed.add_field(name="카드 목록", value="\n".join(lines) or "카드가 없습니다.", inline=False)
        embed.set_footer(text=f"{self.index + 1} / {len(self.page_specs)} 페이지")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("본인만 조작할 수 있어요.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class Collection(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.card_price_tick.start()
        self.card_price_reset.start()

    def cog_unload(self):
        self.card_price_tick.cancel()
        self.card_price_reset.cancel()

    @tasks.loop(minutes=cards_module.CARD_TICK_MINUTES)
    async def card_price_tick(self):
        # tasks.loop은 시작하자마자 한 번 도는데, 그러면 봇을 재시작할 때마다
        # 가격이 움직여 버린다. 첫 바퀴는 건너뛰고 1분 뒤부터 변동시킨다.
        if self.card_price_tick.current_loop == 0:
            return

        # 카드 판매가는 서버 DB마다 따로 움직인다.
        for guild_id in existing_guild_ids():
            with get_db(guild_id) as conn:
                for rarity in cards_module.tick_card_prices(conn):
                    label = cards_module.RARITY_LABEL[rarity]
                    print(f"[{guild_id}] {label} 카드 가격이 0달러 이하로 떨어져 초기화되었습니다.")

    @card_price_tick.before_loop
    async def before_card_price_tick(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=CARD_RESET_TIMES)
    async def card_price_reset(self):
        # 10분마다 기본가로 되돌려서 시세가 한없이 흘러가지 않게 한다.
        for guild_id in existing_guild_ids():
            with get_db(guild_id) as conn:
                cards_module.reset_card_prices(conn)
        print("카드 가격을 기본가로 초기화했습니다.")

    @card_price_reset.before_loop
    async def before_card_price_reset(self):
        await self.bot.wait_until_ready()

    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        with get_db(interaction.guild_id) as conn:
            categories = cards_module.get_categories(conn)
        return [
            app_commands.Choice(name=category, value=category)
            for category in categories
            if current.lower() in category.lower()
        ][:25]

    @app_commands.command(name="도감", description="카드 도감을 확인합니다.")
    @app_commands.describe(카테고리="확인할 카드 카테고리 (비워두면 전체)")
    @app_commands.autocomplete(카테고리=category_autocomplete)
    async def dex(self, interaction: discord.Interaction, 카테고리: Optional[str] = None):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        with get_db(guild_id) as conn:
            if 카테고리:
                categories = cards_module.get_categories(conn)
                if 카테고리 not in categories:
                    await interaction.response.send_message(
                        f"'{카테고리}' 카테고리를 찾을 수 없어요.", ephemeral=True
                    )
                    return
            rows = cards_module.get_all_cards(conn, 카테고리)
            if not rows:
                await interaction.response.send_message("등록된 카드가 없어요.", ephemeral=True)
                return
            owned_rows = conn.execute(
                "SELECT card_id FROM inventory WHERE user_id = ? AND quantity > 0",
                (str(interaction.user.id),),
            ).fetchall()
        owned_ids = {row["card_id"] for row in owned_rows}

        pages: dict[str, list] = {r: [] for r in cards_module.RARITY_ORDER}
        for row in rows:
            pages[row["rarity"]].append(row)
        rarities = [r for r in cards_module.RARITY_ORDER if pages[r]]

        view = DexView(rarities, pages, owned_ids, interaction.user.id, 카테고리)
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    @app_commands.command(name="카드시세", description="등급별 카드 판매가를 확인합니다.")
    async def card_prices(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        with get_db(guild_id) as conn:
            rows = cards_module.get_card_price_rows(conn)

        lines = []
        for row in rows:
            delta = row["price"] - row["prev_price"]
            arrow = "🔺" if delta > 0 else "🔻" if delta < 0 else "➖"
            lines.append(
                f"{cards_module.RARITY_EMOJI[row['rarity']]} "
                f"**{cards_module.RARITY_LABEL[row['rarity']]}** — "
                f"{row['price']:,}달러 {arrow} {delta:+,} "
                f"(기본가 {row['base_price']:,})"
            )

        embed = discord.Embed(
            title="🎴 카드 시세",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=(
                f"{cards_module.CARD_TICK_MINUTES}분마다 "
                f"{cards_module.CARD_DELTA_MIN}~{cards_module.CARD_DELTA_MAX}달러씩 변동하고, "
                f"{cards_module.CARD_RESET_MINUTES}분마다 기본가로 초기화돼요. "
                "/판매 는 이 가격으로 정산됩니다."
            )
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="인벤토리", description="내가 보유한 카드를 확인합니다.")
    async def inventory(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            rows = conn.execute(
                """
                SELECT c.name, c.rarity, i.quantity
                FROM inventory i
                JOIN cards c ON c.id = i.card_id
                WHERE i.user_id = ? AND i.quantity > 0
                """,
                (user_id,),
            ).fetchall()
            prices = cards_module.get_card_prices(conn)

        if not rows:
            await interaction.response.send_message(
                f"{interaction.user.display_name}님은 아직 보유한 카드가 없어요."
            )
            return

        grouped: dict[str, list] = {r: [] for r in cards_module.RARITY_ORDER}
        for row in rows:
            grouped[row["rarity"]].append(row)

        embed = discord.Embed(
            title=f"🎒 {interaction.user.display_name}님의 인벤토리",
            description=f"보유 카드 종류: {len(rows)}종 / 총 {sum(r['quantity'] for r in rows)}장",
            color=discord.Color.green(),
        )
        for rarity in cards_module.RARITY_ORDER:
            cards_in_rarity = grouped[rarity]
            if not cards_in_rarity:
                continue
            lines = [f"{c['name']} ×{c['quantity']}" for c in cards_in_rarity]
            price = prices.get(rarity, cards_module.RARITY_PRICE[rarity])
            embed.add_field(
                name=(
                    f"{cards_module.RARITY_EMOJI[rarity]} "
                    f"{cards_module.RARITY_LABEL[rarity]} (장당 {price:,}달러)"
                ),
                value="\n".join(lines),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="수집률", description="카드 수집률을 확인합니다.")
    async def collection_rate(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            all_cards = cards_module.get_all_cards(conn)
            owned_rows = conn.execute(
                "SELECT DISTINCT card_id FROM inventory WHERE user_id = ? AND quantity > 0",
                (user_id,),
            ).fetchall()
        owned_ids = {row["card_id"] for row in owned_rows}

        total = len(all_cards)
        if total == 0:
            await interaction.response.send_message("등록된 카드가 없어요.", ephemeral=True)
            return

        owned = sum(1 for c in all_cards if c["id"] in owned_ids)
        percent = owned / total * 100

        by_total: dict[str, int] = {r: 0 for r in cards_module.RARITY_ORDER}
        by_owned: dict[str, int] = {r: 0 for r in cards_module.RARITY_ORDER}
        for c in all_cards:
            by_total[c["rarity"]] += 1
            if c["id"] in owned_ids:
                by_owned[c["rarity"]] += 1

        embed = discord.Embed(
            title=f"📊 {interaction.user.display_name}님의 수집률",
            description=f"전체 **{owned:,} / {total:,}장** ({percent:.1f}%)",
            color=discord.Color.blurple(),
        )
        for rarity in cards_module.RARITY_ORDER:
            tier_total = by_total[rarity]
            if tier_total == 0:
                continue
            tier_owned = by_owned[rarity]
            tier_percent = tier_owned / tier_total * 100
            embed.add_field(
                name=f"{cards_module.RARITY_EMOJI[rarity]} {cards_module.RARITY_LABEL[rarity]}",
                value=f"{tier_owned:,} / {tier_total:,} ({tier_percent:.1f}%)",
                inline=True,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="수집률랭킹", description="카드 수집률 랭킹을 확인합니다.")
    async def collection_leaderboard(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            total = len(cards_module.get_all_cards(conn))
            if total == 0:
                await interaction.response.send_message("등록된 카드가 없어요.", ephemeral=True)
                return

            rows = conn.execute(
                """
                SELECT user_id, COUNT(DISTINCT card_id) AS owned
                FROM inventory
                WHERE quantity > 0
                GROUP BY user_id
                ORDER BY owned DESC
                """
            ).fetchall()

        if not rows:
            await interaction.response.send_message(
                "아직 카드를 수집한 사람이 없어요.", ephemeral=True
            )
            return

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for i, row in enumerate(rows[:10]):
            name = await resolve_display_name(interaction.guild, row["user_id"])
            percent = row["owned"] / total * 100
            rank_label = medals.get(i, f"{i + 1}.")
            lines.append(f"{rank_label} {name} — {row['owned']:,} / {total:,}장 ({percent:.1f}%)")

        embed = discord.Embed(
            title="🏆 카드 수집률 랭킹",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

        my_rank = next((i for i, row in enumerate(rows) if row["user_id"] == user_id), None)
        if my_rank is not None and my_rank >= 10:
            my_owned = rows[my_rank]["owned"]
            my_percent = my_owned / total * 100
            embed.set_footer(
                text=(
                    f"{interaction.user.display_name}님의 순위: {my_rank + 1}위 "
                    f"({my_owned:,} / {total:,}장, {my_percent:.1f}%)"
                )
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="카드뽑기", description=f"{economy.GACHA_COST:,}달러로 카드를 뽑습니다."
    )
    @app_commands.describe(카테고리="뽑을 카드 카테고리 (비워두면 전체에서 랜덤)")
    @app_commands.autocomplete(카테고리=category_autocomplete)
    async def gacha(self, interaction: discord.Interaction, 카테고리: Optional[str] = None):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            balance = economy.ensure_wallet(conn, user_id)
            is_free = economy.free_card_draws_left(conn, user_id) > 0
            cost = 0 if is_free else economy.GACHA_COST
            if balance < cost:
                await interaction.response.send_message(
                    f"잔액이 부족해요! 필요 금액: {economy.GACHA_COST:,}달러 "
                    f"(현재 잔액: {balance:,}달러)",
                    ephemeral=True,
                )
                return

            if 카테고리:
                categories = cards_module.get_categories(conn)
                if 카테고리 not in categories:
                    await interaction.response.send_message(
                        f"'{카테고리}' 카테고리를 찾을 수 없어요.", ephemeral=True
                    )
                    return

            exclude_ids = cards_module.get_claimed_unique_card_ids(conn)

            card = cards_module.pick_random_card(conn, 카테고리, exclude_ids)
            if card is None:
                await interaction.response.send_message("뽑을 수 있는 카드가 없어요.", ephemeral=True)
                return

            if is_free:
                economy.use_free_card_draws(conn, user_id)
            new_balance = balance - cost
            economy.set_balance(conn, user_id, new_balance)
            cards_module.add_card(conn, user_id, card["id"])

            is_unique = card["name"] in cards_module.UNIQUE_CARD_NAMES
            if is_unique:
                cards_module.claim_unique_card(conn, card["id"], user_id)

            free_left = economy.free_card_draws_left(conn, user_id)

        emoji = cards_module.RARITY_EMOJI[card["rarity"]]
        label = cards_module.RARITY_LABEL[card["rarity"]]
        unique_note = "\n\n🌟 이 서버에 단 하나뿐인 카드예요!" if is_unique else ""
        cost_line = (
            f"{interaction.user.display_name}님이 🃏 **카드 트레이더** 혜택으로 무료 뽑기를 "
            f"사용했어요! (오늘 남은 무료 뽑기: {free_left}장)"
            if is_free
            else f"{interaction.user.display_name}님이 **{economy.GACHA_COST:,}달러**를 사용해 "
            "카드를 뽑았어요!"
        )
        embed = discord.Embed(
            title="🎴 카드뽑기 결과",
            description=(
                f"{cost_line}\n\n"
                f"{emoji} **{label}** — {card['name']} ({card['category']}){unique_note}\n\n"
                f"잔액: **{new_balance:,}달러**"
            ),
            color=discord.Color.gold()
            if card["rarity"] in ("rare", "legendary")
            else discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="카드지급", description="[테스트용] 카드를 지급합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    @is_test_guild()
    async def grant_card(self, interaction: discord.Interaction, 카드이름: str, 수량: int = 1):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        with get_db(guild_id) as conn:
            row = conn.execute("SELECT id FROM cards WHERE name = ?", (카드이름,)).fetchone()
            if row is None:
                await interaction.response.send_message(
                    f"'{카드이름}' 카드를 찾을 수 없어요.", ephemeral=True
                )
                return
            cards_module.add_card(conn, str(interaction.user.id), row["id"], 수량)
        await interaction.response.send_message(f"'{카드이름}' {수량}장을 지급했어요.", ephemeral=True)

    @grant_card.autocomplete("카드이름")
    async def grant_card_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        with get_db(interaction.guild_id) as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM cards WHERE name LIKE ? LIMIT 25",
                (f"%{current}%",),
            ).fetchall()
        return [app_commands.Choice(name=row["name"], value=row["name"]) for row in rows]

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
    await bot.add_cog(Collection(bot))
