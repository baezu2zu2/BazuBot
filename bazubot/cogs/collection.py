from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .. import cards as cards_module
from .. import economy
from ..checks import is_test_guild
from ..database import get_db
from ..discord_utils import resolve_display_name

DEX_PAGE_SIZE = 20


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

    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        with get_db() as conn:
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
        with get_db() as conn:
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

    @app_commands.command(name="인벤토리", description="내가 보유한 카드를 확인합니다.")
    async def inventory(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT c.name, c.rarity, i.quantity
                FROM inventory i
                JOIN cards c ON c.id = i.card_id
                WHERE i.user_id = ? AND i.quantity > 0
                """,
                (user_id,),
            ).fetchall()

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
            embed.add_field(
                name=f"{cards_module.RARITY_EMOJI[rarity]} {cards_module.RARITY_LABEL[rarity]}",
                value="\n".join(lines),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="수집률", description="카드 수집률을 확인합니다.")
    async def collection_rate(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        with get_db() as conn:
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
        user_id = str(interaction.user.id)
        with get_db() as conn:
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
        user_id = str(interaction.user.id)
        with get_db() as conn:
            balance = economy.ensure_wallet(conn, user_id)
            if balance < economy.GACHA_COST:
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

            guild_id = str(interaction.guild_id) if interaction.guild_id else None
            exclude_ids = (
                cards_module.get_claimed_unique_card_ids(conn, guild_id) if guild_id else set()
            )

            card = cards_module.pick_random_card(conn, 카테고리, exclude_ids)
            if card is None:
                await interaction.response.send_message("뽑을 수 있는 카드가 없어요.", ephemeral=True)
                return

            new_balance = balance - economy.GACHA_COST
            economy.set_balance(conn, user_id, new_balance)
            cards_module.add_card(conn, user_id, card["id"])

            is_unique = card["name"] in cards_module.UNIQUE_CARD_NAMES
            if is_unique and guild_id:
                cards_module.claim_unique_card(conn, card["id"], guild_id, user_id)

        emoji = cards_module.RARITY_EMOJI[card["rarity"]]
        label = cards_module.RARITY_LABEL[card["rarity"]]
        unique_note = "\n\n🌟 이 서버에 단 하나뿐인 카드예요!" if is_unique else ""
        embed = discord.Embed(
            title="🎴 카드뽑기 결과",
            description=(
                f"{interaction.user.display_name}님이 **{economy.GACHA_COST:,}달러**를 사용해 "
                f"카드를 뽑았어요!\n\n"
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
        with get_db() as conn:
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
        with get_db() as conn:
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
