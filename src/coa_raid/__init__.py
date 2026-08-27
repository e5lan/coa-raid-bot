import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from . import db, icons
from .raid import OwnerControlView, RaidSignupView, setup_raid_commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='/', intents=intents)

setup_raid_commands(bot.tree)


_initialized = False


@bot.event
async def on_ready():
    global _initialized
    if not _initialized:
        await db.init_db()
        for raid_id in await db.get_all_raid_ids():
            bot.add_view(RaidSignupView(raid_id))
            bot.add_view(OwnerControlView(raid_id))
        for guild in bot.guilds:
            await icons.sync_class_emojis(guild)
        _initialized = True

    dev_guild_id = os.getenv('DEV_GUILD_ID')
    if dev_guild_id:
        guild = discord.Object(id=int(dev_guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    print(f'Logged in as {bot.user}')


def main():
    load_dotenv()
    token = os.getenv('DISCORD_BOT_TOKEN')
    bot.run(token)
