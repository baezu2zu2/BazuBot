import discord


async def resolve_display_name(guild: discord.Guild | None, user_id: str) -> str:
    if guild is None:
        return f"유저 {user_id}"

    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except discord.HTTPException:
            member = None

    return member.display_name if member else f"유저 {user_id}"


async def require_guild(interaction: discord.Interaction) -> str | None:
    """서버 DB를 쓰는 명령어용. DM에서 쓰면 안내 후 None을 돌려준다."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "이 명령어는 서버 안에서만 사용할 수 있어요. (데이터가 서버별로 따로 관리돼요)",
            ephemeral=True,
        )
        return None
    return str(interaction.guild_id)
