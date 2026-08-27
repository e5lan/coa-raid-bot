from __future__ import annotations

import discord

from . import db
from .render import update_raid_message

STATUS_LABELS = {'present': 'Present', 'late': 'Late', 'absent': 'Absent'}


class SpecSelect(discord.ui.Select):
    def __init__(self, specs: list[db.Spec], character_name: str, status: str, raid_id: int):
        options = [
            discord.SelectOption(label=f'{s.name} ({s.role})', value=str(s.id)) for s in specs
        ]
        super().__init__(placeholder='Choose a spec', options=options)
        self.character_name = character_name
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        spec_id = int(self.values[0])
        character = await db.create_character(interaction.user.id, self.character_name, spec_id)
        await db.upsert_signup(self.raid_id, interaction.user.id, character.id, self.status)
        await interaction.response.edit_message(
            content=f'Signed up **{character.name}** as {STATUS_LABELS[self.status]}.',
            view=None,
        )
        await update_raid_message(interaction.client, self.raid_id)


class ClassSelect(discord.ui.Select):
    def __init__(self, classes: list[db.WowClass], character_name: str, status: str, raid_id: int):
        options = [discord.SelectOption(label=c.name, value=str(c.id)) for c in classes]
        super().__init__(placeholder='Choose a class', options=options)
        self.character_name = character_name
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        specs = await db.get_specs_for_class(int(self.values[0]))
        view = discord.ui.View(timeout=180)
        view.add_item(SpecSelect(specs, self.character_name, self.status, self.raid_id))
        await interaction.response.edit_message(
            content=f'Character **{self.character_name}** — now choose a spec:',
            view=view,
        )


class NewCharacterModal(discord.ui.Modal, title='New Character'):
    name = discord.ui.TextInput(label='Character name', max_length=32)

    def __init__(self, status: str, raid_id: int):
        super().__init__()
        self.status = status
        self.raid_id = raid_id

    async def on_submit(self, interaction: discord.Interaction):
        classes = await db.get_classes()
        view = discord.ui.View(timeout=180)
        view.add_item(ClassSelect(classes, str(self.name), self.status, self.raid_id))
        await interaction.response.send_message(
            content=f'Character **{self.name}** — now choose a class:',
            view=view,
            ephemeral=True,
        )


class CharacterSelect(discord.ui.Select):
    def __init__(self, characters: list[db.Character], status: str, raid_id: int):
        options = [
            discord.SelectOption(
                label=c.name,
                description=f'{c.class_name} - {c.spec_name} ({c.role})',
                value=str(c.id),
            )
            for c in characters
        ]
        super().__init__(placeholder='Choose a character', options=options)
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        character_id = int(self.values[0])
        await db.upsert_signup(self.raid_id, interaction.user.id, character_id, self.status)
        await interaction.response.edit_message(
            content=f'Signed up as {STATUS_LABELS[self.status]}.', view=None
        )
        await update_raid_message(interaction.client, self.raid_id)


class AddCharacterButton(discord.ui.Button):
    def __init__(self, status: str, raid_id: int):
        super().__init__(label='Add new character', style=discord.ButtonStyle.secondary)
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewCharacterModal(self.status, self.raid_id))


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Cancel', style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content='Cancelled.', view=None)


def _character_select_content(characters: list[db.Character]) -> str:
    return 'Choose a character to sign up with:' if characters else 'Add a character to sign up with:'


def _build_character_select_view(characters: list[db.Character], status: str, raid_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=180)
    if characters:
        view.add_item(CharacterSelect(characters, status, raid_id))
        view.add_item(ManageCharactersButton(status, raid_id))
    view.add_item(AddCharacterButton(status, raid_id))
    view.add_item(CancelButton())
    return view


def _build_manage_menu_view(status: str | None, raid_id: int | None) -> discord.ui.View:
    view = discord.ui.View(timeout=180)
    view.add_item(
        ManageActionButton('rename', 'Rename', discord.ButtonStyle.secondary, status, raid_id)
    )
    view.add_item(
        ManageActionButton('respec', 'Respec', discord.ButtonStyle.secondary, status, raid_id)
    )
    view.add_item(
        ManageActionButton('remove', 'Remove', discord.ButtonStyle.danger, status, raid_id)
    )
    if raid_id is None:
        view.add_item(ManageDoneButton())
    else:
        view.add_item(BackToCharacterSelectButton(status, raid_id))
    return view


async def _return_after_management(
    interaction: discord.Interaction,
    status: str | None,
    raid_id: int | None,
    *,
    prefix: str = '',
    new_message: bool = False,
) -> None:
    if raid_id is None:
        content = prefix + 'Manage your characters:'
        view = _build_manage_menu_view(status, raid_id)
    else:
        characters = await db.get_characters_for_user(interaction.user.id)
        content = prefix + _character_select_content(characters)
        view = _build_character_select_view(characters, status, raid_id)
    if new_message:
        await interaction.response.send_message(content=content, view=view, ephemeral=True)
    else:
        await interaction.response.edit_message(content=content, view=view)


class ManageDoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Done', style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content='Done managing characters.', view=None)


class BackToCharacterSelectButton(discord.ui.Button):
    def __init__(self, status: str | None, raid_id: int | None):
        super().__init__(label='Cancel' if raid_id is not None else 'Back', style=discord.ButtonStyle.danger)
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await _return_after_management(interaction, self.status, self.raid_id)


class RenameCharacterModal(discord.ui.Modal, title='Rename Character'):
    new_name = discord.ui.TextInput(label='New name', max_length=32)

    def __init__(self, character_id: int, status: str | None, raid_id: int | None):
        super().__init__()
        self.character_id = character_id
        self.status = status
        self.raid_id = raid_id

    async def on_submit(self, interaction: discord.Interaction):
        ok = await db.rename_character(self.character_id, str(self.new_name))
        prefix = (
            f'Renamed to **{self.new_name}**.\n\n'
            if ok
            else f'You already have a character named **{self.new_name}**.\n\n'
        )
        await _return_after_management(
            interaction, self.status, self.raid_id, prefix=prefix, new_message=True
        )


class RespecSpecSelect(discord.ui.Select):
    def __init__(
        self,
        specs: list[db.Spec],
        character_id: int,
        character_name: str,
        status: str | None,
        raid_id: int | None,
    ):
        options = [
            discord.SelectOption(label=f'{s.name} ({s.role})', value=str(s.id)) for s in specs
        ]
        super().__init__(placeholder='Choose a new spec', options=options)
        self.character_id = character_id
        self.character_name = character_name
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await db.update_character_spec(self.character_id, int(self.values[0]))
        await _return_after_management(
            interaction, self.status, self.raid_id, prefix=f'**{self.character_name}** respecced.\n\n'
        )


class RespecClassSelect(discord.ui.Select):
    def __init__(
        self,
        classes: list[db.WowClass],
        character_id: int,
        character_name: str,
        status: str | None,
        raid_id: int | None,
    ):
        options = [discord.SelectOption(label=c.name, value=str(c.id)) for c in classes]
        super().__init__(placeholder='Choose a new class', options=options)
        self.character_id = character_id
        self.character_name = character_name
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        specs = await db.get_specs_for_class(int(self.values[0]))
        view = discord.ui.View(timeout=180)
        view.add_item(
            RespecSpecSelect(specs, self.character_id, self.character_name, self.status, self.raid_id)
        )
        await interaction.response.edit_message(
            content=f'**{self.character_name}** — choose a new spec:', view=view
        )


class RemoveCharacterConfirmButton(discord.ui.Button):
    def __init__(self, character_id: int, character_name: str, status: str | None, raid_id: int | None):
        super().__init__(label='Confirm delete', style=discord.ButtonStyle.danger)
        self.character_id = character_id
        self.character_name = character_name
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await db.delete_character(self.character_id)
        await _return_after_management(
            interaction, self.status, self.raid_id, prefix=f'Deleted **{self.character_name}**.\n\n'
        )


class ManageCharacterSelect(discord.ui.Select):
    def __init__(
        self, characters: list[db.Character], action: str, status: str | None, raid_id: int | None
    ):
        options = [
            discord.SelectOption(
                label=c.name,
                description=f'{c.class_name} - {c.spec_name} ({c.role})',
                value=str(c.id),
            )
            for c in characters
        ]
        super().__init__(placeholder='Choose a character', options=options)
        self.action = action
        self.status = status
        self.raid_id = raid_id
        self._characters = {c.id: c for c in characters}

    async def callback(self, interaction: discord.Interaction):
        character = self._characters[int(self.values[0])]
        if self.action == 'rename':
            await interaction.response.send_modal(
                RenameCharacterModal(character.id, self.status, self.raid_id)
            )
        elif self.action == 'respec':
            classes = await db.get_classes()
            view = discord.ui.View(timeout=180)
            view.add_item(
                RespecClassSelect(classes, character.id, character.name, self.status, self.raid_id)
            )
            await interaction.response.edit_message(
                content=f'**{character.name}** — choose a new class:', view=view
            )
        elif self.action == 'remove':
            view = discord.ui.View(timeout=180)
            view.add_item(
                RemoveCharacterConfirmButton(character.id, character.name, self.status, self.raid_id)
            )
            view.add_item(BackToCharacterSelectButton(self.status, self.raid_id))
            await interaction.response.edit_message(
                content=f'Delete **{character.name}**? This also removes their raid signups. '
                'This cannot be undone.',
                view=view,
            )


class ManageActionButton(discord.ui.Button):
    def __init__(
        self, action: str, label: str, style: discord.ButtonStyle, status: str | None, raid_id: int | None
    ):
        super().__init__(label=label, style=style)
        self.action = action
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        characters = await db.get_characters_for_user(interaction.user.id)
        view = discord.ui.View(timeout=180)
        view.add_item(ManageCharacterSelect(characters, self.action, self.status, self.raid_id))
        view.add_item(BackToCharacterSelectButton(self.status, self.raid_id))
        await interaction.response.edit_message(content='Choose a character:', view=view)


class ManageCharactersButton(discord.ui.Button):
    def __init__(self, status: str, raid_id: int):
        super().__init__(label='⚙️ Manage characters', style=discord.ButtonStyle.secondary)
        self.status = status
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        view = _build_manage_menu_view(self.status, self.raid_id)
        await interaction.response.edit_message(content='Manage your characters:', view=view)


async def start_character_management(interaction: discord.Interaction) -> None:
    view = _build_manage_menu_view(None, None)
    await interaction.response.send_message(
        content='Manage your characters:', view=view, ephemeral=True
    )


async def start_signup(interaction: discord.Interaction, status: str, raid_id: int):
    characters = await db.get_characters_for_user(interaction.user.id)
    if not characters:
        await interaction.response.send_modal(NewCharacterModal(status, raid_id))
        return

    view = _build_character_select_view(characters, status, raid_id)
    await interaction.response.send_message(
        content=_character_select_content(characters), view=view, ephemeral=True
    )
