from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import discord
from discord import app_commands

from . import db, ui
from .render import build_raid_embed, update_raid_message
from .timeparse import RAID_TIMEZONE, format_date, format_time, parse_event_time

RAID_CATEGORY_NAME = os.getenv('RAID_CATEGORY_NAME', 'Raid Sign-up')

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _slugify(text: str) -> str:
    return _SLUG_RE.sub('-', text.lower()).strip('-') or 'raid'


def _raid_channel_name(title: str, event_at: datetime | None) -> str:
    date_part = event_at.astimezone(RAID_TIMEZONE).strftime('%d-%b-%H-%M').lower() if event_at else 'tbd'
    return f'{_slugify(title)}-{date_part}'[:100]


async def _get_raid_category(guild: discord.Guild) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=RAID_CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(RAID_CATEGORY_NAME)
    return category


async def _position_raid_channel(
    category: discord.CategoryChannel, channel: discord.abc.GuildChannel, event_at: datetime | None
) -> None:
    raids = await db.get_all_raid_events()
    raid_by_channel = {r.channel_id: r for r in raids}

    def sort_key(c: discord.abc.GuildChannel) -> datetime:
        return raid_by_channel[c.id].event_at or datetime.max.replace(tzinfo=UTC)

    siblings = [c for c in category.channels if c.id != channel.id and c.id in raid_by_channel]
    siblings.sort(key=sort_key)
    my_key = event_at or datetime.max.replace(tzinfo=UTC)
    insert_before = next((c for c in siblings if sort_key(c) > my_key), None)
    if insert_before is not None:
        await channel.move(category=category, before=insert_before, sync_permissions=False)
    elif siblings:
        await channel.move(category=category, after=siblings[-1], sync_permissions=False)
    else:
        await channel.move(category=category, beginning=True, sync_permissions=False)


async def _sync_raid_channel(client: discord.Client, raid: db.RaidEvent) -> None:
    channel = client.get_channel(raid.channel_id) or await client.fetch_channel(raid.channel_id)
    new_name = _raid_channel_name(raid.title, raid.event_at)
    if channel.name != new_name:
        await channel.edit(name=new_name)
    if channel.category is not None:
        await _position_raid_channel(channel.category, channel, raid.event_at)


STATUS_STYLE = {
    'present': discord.ButtonStyle.success,
    'late': discord.ButtonStyle.primary,
    'absent': discord.ButtonStyle.danger,
}


class SignupButton(discord.ui.Button):
    def __init__(self, status: str, raid_id: int):
        super().__init__(
            label=status.capitalize(),
            style=STATUS_STYLE[status],
            custom_id=f'raid_signup:{status}:{raid_id}',
        )
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await db.get_raid_event(self.raid_id)
        if raid is None or raid.cancelled:
            await interaction.response.send_message('This raid has been cancelled.', ephemeral=True)
            return
        await ui.start_signup(interaction, self.status, self.raid_id)


class ManageCharactersEntryButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            emoji='⚙️', style=discord.ButtonStyle.secondary, custom_id=f'raid_signup:manage:{raid_id}'
        )

    async def callback(self, interaction: discord.Interaction):
        await ui.start_character_management(interaction)


class RaidSignupView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=None)
        for status in ('present', 'late', 'absent'):
            self.add_item(SignupButton(status, raid_id))
        self.add_item(ManageCharactersEntryButton(raid_id))


class EditRaidModal(discord.ui.Modal, title='Edit Raid'):
    def __init__(self, raid_id: int, raid: db.RaidEvent):
        super().__init__()
        self.raid_id = raid_id
        self.title_input = discord.ui.TextInput(label='Title', default=raid.title, max_length=256)
        self.time_input = discord.ui.TextInput(
            label='Time', default=raid.event_time or '', required=False, max_length=100
        )
        self.description_input = discord.ui.TextInput(
            label='Description',
            style=discord.TextStyle.paragraph,
            default=raid.description or '',
            required=False,
            max_length=1000,
        )
        self.add_item(self.title_input)
        self.add_item(self.time_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        time_text = str(self.time_input) or None
        event_at = parse_event_time(time_text) if time_text else None
        if time_text and event_at is None:
            await interaction.response.send_message(
                f"I couldn't understand the time \"{time_text}\" — try something like "
                '"tomorrow 9pm" or "Aug 29 20:00".',
                ephemeral=True,
            )
            return
        await db.update_raid_event(
            self.raid_id,
            title=str(self.title_input),
            description=str(self.description_input) or None,
            event_time=time_text,
            event_at=event_at,
        )
        raid = await db.get_raid_event(self.raid_id)
        await _sync_raid_channel(interaction.client, raid)
        await update_raid_message(interaction.client, self.raid_id)
        await interaction.response.send_message('Raid updated.')


async def _delete_raid(client: discord.Client, raid: db.RaidEvent) -> None:
    try:
        channel = client.get_channel(raid.channel_id) or await client.fetch_channel(raid.channel_id)
        await channel.delete(reason=f'Raid #{raid.id} deleted')
    except (discord.NotFound, discord.Forbidden):
        pass
    await db.delete_raid_event(raid.id)


async def _require_current_leader(interaction: discord.Interaction, raid_id: int) -> db.RaidEvent | None:
    raid = await db.get_raid_event(raid_id)
    if raid is None:
        await interaction.response.send_message('This raid no longer exists.', ephemeral=True)
        return None
    if interaction.user.id != raid.created_by:
        await interaction.response.send_message(
            "You're no longer the leader of this raid.", ephemeral=True
        )
        return None
    return raid


class ChangeLeaderModal(discord.ui.Modal, title='Change Raid Leader'):
    leader_input = discord.ui.TextInput(label='New leader (@mention or user ID)', max_length=32)

    def __init__(self, raid_id: int):
        super().__init__()
        self.raid_id = raid_id

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(self.leader_input).strip().strip('<@!>')
        if not user_id_str.isdigit():
            await interaction.response.send_message(
                'Please provide a valid @mention or numeric user ID.', ephemeral=True
            )
            return
        try:
            new_leader = await interaction.client.fetch_user(int(user_id_str))
        except discord.NotFound:
            await interaction.response.send_message("I couldn't find that user.", ephemeral=True)
            return

        raid = await db.get_raid_event(self.raid_id)
        if raid is None:
            await interaction.response.send_message('This raid no longer exists.', ephemeral=True)
            return
        await db.update_raid_leader(self.raid_id, new_leader.id)
        await update_raid_message(interaction.client, self.raid_id)
        await interaction.response.send_message(f'Leader changed to {new_leader.mention}.')

        try:
            dm_channel = await new_leader.create_dm()
            owner_embed = discord.Embed(
                title=f'Raid Leader Controls — {raid.title}',
                description='You are now the leader of this raid. Use the buttons below to manage it.',
            )
            await dm_channel.send(embed=owner_embed, view=OwnerControlView(self.raid_id))
        except discord.Forbidden:
            pass


class OwnerEditButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            label='Edit', style=discord.ButtonStyle.primary, custom_id=f'raid_owner:edit:{raid_id}'
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await _require_current_leader(interaction, self.raid_id)
        if raid is None:
            return
        if raid.cancelled:
            await interaction.response.send_message('This raid was cancelled.', ephemeral=True)
            return
        await interaction.response.send_modal(EditRaidModal(self.raid_id, raid))


class OwnerChangeLeaderButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            label='Change Leader',
            style=discord.ButtonStyle.secondary,
            custom_id=f'raid_owner:leader:{raid_id}',
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await _require_current_leader(interaction, self.raid_id)
        if raid is None:
            return
        await interaction.response.send_modal(ChangeLeaderModal(self.raid_id))


class OwnerCancelButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            label='Cancel Raid', style=discord.ButtonStyle.danger, custom_id=f'raid_owner:cancel:{raid_id}'
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await _require_current_leader(interaction, self.raid_id)
        if raid is None:
            return
        if raid.cancelled:
            await interaction.response.send_message('This raid is already cancelled.', ephemeral=True)
            return
        await db.cancel_raid_event(self.raid_id)
        await update_raid_message(interaction.client, self.raid_id, clear_view=True)
        await interaction.response.edit_message(
            content=f'❌ Raid **{raid.title}** cancelled.', embed=None, view=None
        )


class DeleteConfirmButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(label='Confirm Delete', style=discord.ButtonStyle.danger)
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await db.get_raid_event(self.raid_id)
        if raid is None:
            await interaction.response.edit_message(content='This raid no longer exists.', view=None)
            return
        await _delete_raid(interaction.client, raid)
        await interaction.response.edit_message(
            content=f'🗑️ Raid **{raid.title}** permanently deleted.', embed=None, view=None
        )


class DeleteBackButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(label='Back', style=discord.ButtonStyle.secondary)
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=None, view=OwnerControlView(self.raid_id))


class DeleteConfirmView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=120)
        self.add_item(DeleteConfirmButton(raid_id))
        self.add_item(DeleteBackButton(raid_id))


class OwnerDeleteButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            label='Delete Raid', style=discord.ButtonStyle.danger, custom_id=f'raid_owner:delete:{raid_id}'
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        raid = await _require_current_leader(interaction, self.raid_id)
        if raid is None:
            return
        await interaction.response.edit_message(
            content='⚠️ Delete this raid permanently? This removes it and all signups — '
            'this cannot be undone.',
            view=DeleteConfirmView(self.raid_id),
        )


class OwnerControlView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=None)
        self.add_item(OwnerEditButton(raid_id))
        self.add_item(OwnerChangeLeaderButton(raid_id))
        self.add_item(OwnerCancelButton(raid_id))
        self.add_item(OwnerDeleteButton(raid_id))


def _is_officer_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if isinstance(member, discord.Member):
            role_names = {r.name.lower() for r in member.roles}
            if 'officer' in role_names or 'admin' in role_names:
                return True
        return interaction.user.guild_permissions.manage_guild

    return app_commands.check(predicate)


async def _permission_denied_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
    else:
        raise error


class AdminDeleteConfirmButton(discord.ui.Button):
    def __init__(self, raid_id: int, title: str):
        super().__init__(label='Confirm Delete', style=discord.ButtonStyle.danger)
        self.raid_id = raid_id
        self.raid_title = title

    async def callback(self, interaction: discord.Interaction):
        raid = await db.get_raid_event(self.raid_id)
        if raid is None:
            await interaction.response.edit_message(content='This raid no longer exists.', view=None)
            return
        await _delete_raid(interaction.client, raid)
        await interaction.response.edit_message(
            content=f'🗑️ Raid **{self.raid_title}** (#{self.raid_id}) permanently deleted.', view=None
        )


class AdminDeleteCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Cancel', style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content='Delete cancelled.', view=None)


class AdminDeleteConfirmView(discord.ui.View):
    def __init__(self, raid_id: int, title: str):
        super().__init__(timeout=60)
        self.add_item(AdminDeleteConfirmButton(raid_id, title))
        self.add_item(AdminDeleteCancelButton())


def setup_raid_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name='raid', description='Create a new raid signup post (Officer/Admin only)')
    @app_commands.describe(
        title='Raid title, e.g. "Molten Core"',
        time='When the raid happens, e.g. "tomorrow 9pm" or "Aug 29 20:00"',
        description='Optional extra details',
        leader='Who leads this raid (defaults to you)',
    )
    @_is_officer_or_admin()
    async def raid_create(
        interaction: discord.Interaction,
        title: str,
        time: str,
        description: str | None = None,
        leader: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        event_at = parse_event_time(time)
        if event_at is None:
            await interaction.followup.send(
                f'I couldn\'t understand the time "{time}" — try something like '
                '"tomorrow 9pm" or "Aug 29 20:00".',
                ephemeral=True,
            )
            return
        leader_member = leader or interaction.user
        category = await _get_raid_category(interaction.guild)
        raid_channel = await interaction.guild.create_text_channel(
            _raid_channel_name(title, event_at),
            category=category,
            reason=f'Raid created by {interaction.user}',
        )
        raid_id = await db.create_raid_event(
            channel_id=raid_channel.id,
            title=title,
            description=description,
            event_time=time,
            event_at=event_at,
            created_by=leader_member.id,
        )
        embed = await build_raid_embed(raid_id)
        view = RaidSignupView(raid_id)
        message = await raid_channel.send(embed=embed, view=view)
        await db.set_raid_message_id(raid_id, message.id)
        await _position_raid_channel(category, raid_channel, event_at)
        await interaction.followup.send(f'Raid created: {raid_channel.mention}', ephemeral=True)

        try:
            dm_channel = await leader_member.create_dm()
            owner_embed = discord.Embed(
                title=f'Raid Leader Controls — {title}',
                description='Use the buttons below to edit or cancel this raid.',
            )
            await dm_channel.send(embed=owner_embed, view=OwnerControlView(raid_id))
        except discord.Forbidden:
            who = 'you' if leader_member.id == interaction.user.id else leader_member.mention
            await interaction.followup.send(
                f"I couldn't DM {who} the raid controls — check the privacy settings if "
                f'{"you want" if who == "you" else "they want"} to edit or cancel this raid later.',
                ephemeral=True,
            )

    @raid_create.error
    async def raid_create_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "You don't have permission to create a raid.", ephemeral=True
            )
        else:
            raise error

    @tree.command(name='raid-list', description='List all raids (Officer/Admin only)')
    @_is_officer_or_admin()
    async def raid_list(interaction: discord.Interaction):
        raids = await db.get_all_raid_events()
        if not raids:
            await interaction.response.send_message('No raids found.', ephemeral=True)
            return
        lines = []
        for r in raids:
            status = 'Cancelled' if r.cancelled else 'Active'
            when = f'{format_date(r.event_at)} {format_time(r.event_at)}' if r.event_at else (r.event_time or '-')
            lines.append(f'`#{r.id}` **{r.title}** — {status} — {when} — led by <@{r.created_by}>')
        embed = discord.Embed(
            title='All Raids', description='\n'.join(lines)[:4000], color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    raid_list.error(_permission_denied_error)

    @tree.command(name='raid-info', description='Show full details for a raid, past or present (Officer/Admin only)')
    @app_commands.describe(raid_id='Raid ID (shown in the raid post footer, or via /raid-list)')
    @_is_officer_or_admin()
    async def raid_info(interaction: discord.Interaction, raid_id: int):
        raid = await db.get_raid_event(raid_id)
        if raid is None:
            await interaction.response.send_message(f'No raid found with ID {raid_id}.', ephemeral=True)
            return
        embed = await build_raid_embed(raid_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    raid_info.error(_permission_denied_error)

    @tree.command(name='raid-delete', description='Permanently delete a raid (Officer/Admin only)')
    @app_commands.describe(raid_id='Raid ID to delete')
    @_is_officer_or_admin()
    async def raid_delete_command(interaction: discord.Interaction, raid_id: int):
        raid = await db.get_raid_event(raid_id)
        if raid is None:
            await interaction.response.send_message(f'No raid found with ID {raid_id}.', ephemeral=True)
            return
        await interaction.response.send_message(
            content=f'⚠️ Delete raid **{raid.title}** (#{raid_id}) permanently? '
            'This removes it and all signups.',
            view=AdminDeleteConfirmView(raid_id, raid.title),
            ephemeral=True,
        )

    raid_delete_command.error(_permission_denied_error)
