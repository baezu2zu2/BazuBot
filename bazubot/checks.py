import os

import discord
from discord import app_commands

TEST_GUILD_ID = os.getenv("GUILD_ID")


def is_test_guild():
    async def predicate(interaction: discord.Interaction) -> bool:
        return bool(TEST_GUILD_ID) and interaction.guild_id == int(TEST_GUILD_ID)

    return app_commands.check(predicate)
