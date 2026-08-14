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
