from __future__ import annotations

import discord

from . import db
from .icons import get_class_emoji
from .timeparse import format_countdown, format_date, format_time

ROLE_ORDER = ['tank', 'melee', 'ranged', 'healer', 'support']
ROLE_LABEL = {
    'tank': '🛡️ Tank',
    'healer': '❤️ Healer',
    'melee': '⚔️ Melee',
    'ranged': '🏹 Ranged',
    'support': '🎶 Support',
}


def _format_signup(signup: db.Signup) -> str:
    star = '* ' if signup.status == 'late' else ''
    icon = get_class_emoji(signup.class_name)
    prefix = f'{icon} ' if icon else ''
    return f'{star}{prefix}**{signup.character_name}**'


async def build_raid_embed(raid_id: int) -> discord.Embed:
    raid = await db.get_raid_event(raid_id)
    signups = await db.get_signups(raid_id)

    title = raid.title
    color = discord.Color.blurple()
    if raid.cancelled:
        title = f'[CANCELLED] {title}'
        color = discord.Color.red()

    embed = discord.Embed(title=title, description=raid.description or None, color=color)

    present_rows = [s for s in signups if s.status in ('present', 'late')]
    absent_rows = [s for s in signups if s.status == 'absent']
    late_count = sum(1 for s in present_rows if s.status == 'late')

    count_value = f'{len(present_rows)}'
    if late_count:
        count_value += f'({late_count})'

    if raid.event_at:
        date_value = format_date(raid.event_at)
        time_value = format_time(raid.event_at)
        countdown_value = format_countdown(raid.event_at)
    elif raid.event_time:
        date_value = raid.event_time
        time_value = '\u200b'
        countdown_value = '\u200b'
    else:
        date_value = time_value = countdown_value = '\u200b'

    embed.add_field(name='\u200b', value=f'<@{raid.created_by}>\n{date_value}', inline=True)
    embed.add_field(name='\u200b', value=f'{count_value}\n{time_value}', inline=True)
    embed.add_field(name='\u200b', value=f'\u200b\n{countdown_value}', inline=True)
    embed.add_field(name='\u200b', value='\u200b', inline=False)

    for role in ROLE_ORDER:
        rows = [s for s in present_rows if s.role == role]
        lines = [_format_signup(s) for s in rows] or ['*None*']
        embed.add_field(name=f'{ROLE_LABEL[role]} ({len(rows)})', value='\n'.join(lines)[:1024], inline=True)
    embed.add_field(name='\u200b', value='\u200b', inline=True)

    absent_lines = [_format_signup(s) for s in absent_rows] or ['*None*']
    embed.add_field(name=f'❌ Absent ({len(absent_rows)})', value='\n'.join(absent_lines)[:1024], inline=False)

    embed.set_footer(text=f'Raid #{raid_id}')
    return embed


async def update_raid_message(client: discord.Client, raid_id: int, *, clear_view: bool = False) -> None:
    raid = await db.get_raid_event(raid_id)
    if raid is None or raid.message_id is None:
        return
    channel = client.get_channel(raid.channel_id) or await client.fetch_channel(raid.channel_id)
    try:
        message = await channel.fetch_message(raid.message_id)
    except discord.NotFound:
        return
    embed = await build_raid_embed(raid_id)
    if clear_view:
        await message.edit(embed=embed, view=None)
    else:
        await message.edit(embed=embed)
