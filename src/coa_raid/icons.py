from __future__ import annotations

import io
from pathlib import Path

import discord
from PIL import Image

ICON_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'icons'

CLASS_ICON_SLUGS = {
    'Barbarian': 'barbarian',
    'Witch Doctor': 'witchdoctor',
    'Felsworn': 'demonhunter',
    'Witch Hunter': 'witchhunter',
    'Stormbringer': 'stormbringer',
    'Knight of Xoroth': 'fleshwarden',
    'Guardian': 'guardian',
    'Templar': 'monk',
    'Bloodmage': 'sonofarugal',
    'Ranger': 'ranger',
    'Chronomancer': 'chronomancer',
    'Necromancer': 'necromancer',
    'Pyromancer': 'pyromancer',
    'Cultist': 'cultist',
    'Starcaller': 'starcaller',
    'Sun Cleric': 'suncleric',
    'Tinker': 'tinker',
    'Venomancer': 'prophet',
    'Reaper': 'reaper',
    'Primalist': 'wildwalker',
    'Runemaster': 'spiritmage',
}

_class_emojis: dict[str, str] = {}


def _emoji_name(slug: str) -> str:
    return f'coa_{slug}'


def _webp_to_png_bytes(slug: str) -> bytes:
    with Image.open(ICON_DIR / f'class-{slug}.webp') as im:
        buf = io.BytesIO()
        im.convert('RGBA').save(buf, format='PNG')
        return buf.getvalue()


async def sync_class_emojis(guild: discord.Guild) -> None:
    existing = {e.name: e for e in guild.emojis}
    for class_name, slug in CLASS_ICON_SLUGS.items():
        emoji_name = _emoji_name(slug)
        emoji = existing.get(emoji_name)
        if emoji is None:
            try:
                emoji = await guild.create_custom_emoji(
                    name=emoji_name,
                    image=_webp_to_png_bytes(slug),
                    reason='COA raid bot class icon',
                )
            except discord.HTTPException as exc:
                print(f'Failed to upload class emoji for {class_name}: {exc}')
                continue
        _class_emojis[class_name] = str(emoji)


def get_class_emoji(class_name: str) -> str:
    return _class_emojis.get(class_name, '')
