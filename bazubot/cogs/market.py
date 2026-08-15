import discord
from discord import app_commands
from discord.ext import commands

from .. import cards as cards_module
from .. import economy
from .. import market as market_module
from ..database import get_db
from ..discord_utils import require_guild, resolve_display_name


class Market(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def owned_card_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        user_id = str(interaction.user.id)
        with get_db(interaction.guild_id) as conn:
            rows = conn.execute(
                """
                SELECT c.name FROM inventory i
                JOIN cards c ON c.id = i.card_id
                WHERE i.user_id = ? AND i.quantity > 0 AND c.name LIKE ?
                ORDER BY c.name
                LIMIT 25
                """,
                (user_id, f"%{current}%"),
            ).fetchall()
        return [app_commands.Choice(name=row["name"], value=row["name"]) for row in rows]

    @app_commands.command(name="판매", description="보유한 카드를 희귀도 가격에 즉시 판매합니다.")
    @app_commands.describe(카드이름="판매할 카드 이름", 수량="판매할 수량")
    async def sell(
        self,
        interaction: discord.Interaction,
        카드이름: str,
        수량: app_commands.Range[int, 1] = 1,
    ):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            card = cards_module.get_card_by_name(conn, 카드이름)
            if card is None:
                await interaction.response.send_message(
                    f"'{카드이름}' 카드를 찾을 수 없어요.", ephemeral=True
                )
                return

            owned = cards_module.get_quantity(conn, user_id, card["id"])
            if owned < 수량:
                await interaction.response.send_message(
                    f"보유 수량이 부족해요! (보유: {owned}장)", ephemeral=True
                )
                return

            price = cards_module.get_card_price(conn, card["rarity"])
            total = price * 수량
            cards_module.remove_card(conn, user_id, card["id"], 수량)
            balance = economy.ensure_wallet(conn, user_id)
            new_balance = balance + total
            economy.set_balance(conn, user_id, new_balance)

        emoji = cards_module.RARITY_EMOJI[card["rarity"]]
        await interaction.response.send_message(
            f"💰 {emoji} **{card['name']}** {수량}장을 판매해 **{total:,}달러**를 받았어요! "
            f"(잔액: {new_balance:,}달러)"
        )

    @sell.autocomplete("카드이름")
    async def sell_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.owned_card_autocomplete(interaction, current)

    @app_commands.command(name="시장", description="카드를 시장에 올려 다른 유저에게 판매합니다.")
    @app_commands.describe(카드이름="올릴 카드 이름", 가격="장당 판매 가격", 수량="올릴 수량")
    async def list_card(
        self,
        interaction: discord.Interaction,
        카드이름: str,
        가격: app_commands.Range[int, 1],
        수량: app_commands.Range[int, 1] = 1,
    ):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            card = cards_module.get_card_by_name(conn, 카드이름)
            if card is None:
                await interaction.response.send_message(
                    f"'{카드이름}' 카드를 찾을 수 없어요.", ephemeral=True
                )
                return

            owned = cards_module.get_quantity(conn, user_id, card["id"])
            if owned < 수량:
                await interaction.response.send_message(
                    f"보유 수량이 부족해요! (보유: {owned}장)", ephemeral=True
                )
                return

            cards_module.remove_card(conn, user_id, card["id"], 수량)
            listing_id = market_module.create_listing(conn, user_id, card["id"], 수량, 가격)

        emoji = cards_module.RARITY_EMOJI[card["rarity"]]
        await interaction.response.send_message(
            f"🏪 시장에 {emoji} **{card['name']}** {수량}장을 장당 **{가격:,}달러**에 올렸어요! "
            f"(거래 번호: #{listing_id})"
        )

    @list_card.autocomplete("카드이름")
    async def list_card_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.owned_card_autocomplete(interaction, current)

    @app_commands.command(name="시장목록", description="시장에 올라온 카드 목록을 확인합니다.")
    async def browse(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        with get_db(guild_id) as conn:
            listings = market_module.get_listings(conn)

        if not listings:
            await interaction.response.send_message("시장에 올라온 카드가 없어요.", ephemeral=True)
            return

        lines = []
        for listing in listings[:25]:
            emoji = cards_module.RARITY_EMOJI[listing["rarity"]]
            seller_name = await resolve_display_name(interaction.guild, listing["seller_id"])
            lines.append(
                f"`#{listing['id']}` {emoji} **{listing['name']}** ×{listing['quantity']} "
                f"— {listing['price']:,}달러/장 (판매자: {seller_name})"
            )

        embed = discord.Embed(
            title="🏪 카드 시장",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        if len(listings) > 25:
            embed.set_footer(text=f"{len(listings)}건 중 25건 표시")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="구매", description="시장에 올라온 카드를 구매합니다.")
    @app_commands.describe(거래번호="구매할 거래 번호 (/시장목록 에서 확인)", 수량="구매할 수량")
    async def buy(
        self,
        interaction: discord.Interaction,
        거래번호: int,
        수량: app_commands.Range[int, 1] = 1,
    ):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        buyer_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            listing = market_module.get_listing(conn, 거래번호)
            if listing is None:
                await interaction.response.send_message(
                    "해당 거래를 찾을 수 없어요.", ephemeral=True
                )
                return
            if listing["seller_id"] == buyer_id:
                await interaction.response.send_message(
                    "본인이 올린 거래는 구매할 수 없어요.", ephemeral=True
                )
                return
            if 수량 > listing["quantity"]:
                await interaction.response.send_message(
                    f"재고가 부족해요! (남은 수량: {listing['quantity']}장)", ephemeral=True
                )
                return

            total = listing["price"] * 수량
            buyer_balance = economy.ensure_wallet(conn, buyer_id)
            if buyer_balance < total:
                await interaction.response.send_message(
                    f"잔액이 부족해요! 필요 금액: {total:,}달러 (현재 잔액: {buyer_balance:,}달러)",
                    ephemeral=True,
                )
                return

            economy.set_balance(conn, buyer_id, buyer_balance - total)
            seller_balance = economy.ensure_wallet(conn, listing["seller_id"])
            economy.set_balance(conn, listing["seller_id"], seller_balance + total)
            cards_module.add_card(conn, buyer_id, listing["card_id"], 수량)

            remaining = listing["quantity"] - 수량
            if remaining > 0:
                market_module.update_quantity(conn, 거래번호, remaining)
            else:
                market_module.remove_listing(conn, 거래번호)

        emoji = cards_module.RARITY_EMOJI[listing["rarity"]]
        await interaction.response.send_message(
            f"✅ {emoji} **{listing['name']}** {수량}장을 **{total:,}달러**에 구매했어요!"
        )

    @app_commands.command(name="시장취소", description="내가 시장에 올린 거래를 취소합니다.")
    @app_commands.describe(거래번호="취소할 거래 번호")
    async def cancel(self, interaction: discord.Interaction, 거래번호: int):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return
        user_id = str(interaction.user.id)
        with get_db(guild_id) as conn:
            listing = market_module.get_listing(conn, 거래번호)
            if listing is None:
                await interaction.response.send_message(
                    "해당 거래를 찾을 수 없어요.", ephemeral=True
                )
                return
            if listing["seller_id"] != user_id:
                await interaction.response.send_message(
                    "본인이 올린 거래만 취소할 수 있어요.", ephemeral=True
                )
                return

            cards_module.add_card(conn, user_id, listing["card_id"], listing["quantity"])
            market_module.remove_listing(conn, 거래번호)

        await interaction.response.send_message(
            f"🔙 거래 #{거래번호}를 취소하고 카드를 돌려받았어요.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Market(bot))
