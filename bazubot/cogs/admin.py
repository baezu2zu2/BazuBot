import discord
from discord import app_commands
from discord.ext import commands

from ..database import get_db, reset_guild_db
from ..discord_utils import require_guild


def _collect_summary(conn) -> dict[str, int]:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "유저": count("SELECT COUNT(*) FROM wallet"),
        "보유 카드": count("SELECT COALESCE(SUM(quantity), 0) FROM inventory"),
        "시장 거래": count("SELECT COUNT(*) FROM market"),
        "보유 주식": count("SELECT COALESCE(SUM(quantity), 0) FROM stock_holding"),
    }


class ResetConfirmView(discord.ui.View):
    """실수로 서버 데이터를 날리지 않도록 한 번 더 확인받는다."""

    def __init__(self, guild_id: str, author_id: int):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.author_id = author_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "명령어를 실행한 사람만 조작할 수 있어요.", ephemeral=True
            )
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            embed = discord.Embed(
                title="초기화 취소",
                description="30초 동안 응답이 없어 초기화를 취소했어요.",
                color=discord.Color.greyple(),
            )
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="초기화", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        reset_guild_db(self.guild_id)
        self._disable_all()
        embed = discord.Embed(
            title="🧹 초기화 완료",
            description=(
                "이 서버의 잔액·카드·직업·시장·주식 데이터를 모두 삭제했어요.\n"
                "주식 시세도 기본값으로 되돌아갔습니다.\n\n"
                "다른 서버의 데이터는 그대로예요."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        embed = discord.Embed(
            title="초기화 취소",
            description="아무것도 삭제하지 않았어요.",
            color=discord.Color.greyple(),
        )
        await interaction.response.edit_message(embed=embed, view=self)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="리셋", description="[관리자] 이 서버의 봇 데이터를 모두 초기화합니다."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction):
        guild_id = await require_guild(interaction)
        if guild_id is None:
            return

        with get_db(guild_id) as conn:
            summary = _collect_summary(conn)

        guild_name = interaction.guild.name if interaction.guild else guild_id
        embed = discord.Embed(
            title="⚠️ 정말 초기화할까요?",
            description=(
                f"**{guild_name}** 서버의 데이터가 전부 삭제돼요.\n"
                "잔액, 카드, 직업, 시장 거래, 주식, 배당금이 모두 사라지고 되돌릴 수 없어요.\n"
                "다른 서버의 데이터에는 영향이 없어요."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="현재 데이터",
            value="\n".join(f"{label}: {value:,}" for label, value in summary.items()),
            inline=False,
        )
        embed.set_footer(text="30초 안에 선택하지 않으면 자동으로 취소돼요.")
        # 서버 전체 데이터가 사라지는 일이라 채널에 공개로 남긴다.
        view = ResetConfirmView(guild_id, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "이 명령어는 서버 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
