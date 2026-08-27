from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

DB_PATH = Path(os.getenv('DB_PATH', 'coa_raid.sqlite3'))
CSV_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'classes_specs.csv'

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS specs (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('melee', 'ranged', 'tank', 'healer', 'support')),
    UNIQUE(class_id, name)
);

CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    spec_id INTEGER NOT NULL REFERENCES specs(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(discord_user_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS raid_events (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    event_time TEXT,
    event_at TEXT,
    created_by INTEGER NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signups (
    id INTEGER PRIMARY KEY,
    raid_event_id INTEGER NOT NULL REFERENCES raid_events(id),
    discord_user_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    status TEXT NOT NULL CHECK(status IN ('present', 'late', 'absent')),
    signed_up_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(raid_event_id, discord_user_id)
);
"""


@dataclass
class WowClass:
    id: int
    name: str


@dataclass
class Spec:
    id: int
    class_id: int
    name: str
    role: str


@dataclass
class Character:
    id: int
    discord_user_id: int
    name: str
    spec_id: int
    spec_name: str
    role: str
    class_name: str


@dataclass
class RaidEvent:
    id: int
    channel_id: int
    message_id: int | None
    title: str
    description: str | None
    event_time: str | None
    event_at: datetime | None
    created_by: int
    cancelled: bool


@dataclass
class Signup:
    status: str
    character_id: int
    character_name: str
    spec_name: str
    role: str
    class_name: str


_connection: aiosqlite.Connection | None = None


async def get_connection() -> aiosqlite.Connection:
    global _connection
    if _connection is None:
        _connection = await aiosqlite.connect(DB_PATH)
        _connection.row_factory = aiosqlite.Row
        await _connection.execute('PRAGMA foreign_keys = ON')
    return _connection


async def init_db() -> None:
    conn = await get_connection()
    await conn.executescript(SCHEMA)
    await conn.commit()
    try:
        await conn.execute('ALTER TABLE raid_events ADD COLUMN event_at TEXT')
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    await _seed_classes_and_specs(conn)


async def _seed_classes_and_specs(conn: aiosqlite.Connection) -> None:
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    class_names = sorted({row['class'] for row in rows})
    for name in class_names:
        await conn.execute(
            'INSERT INTO classes (name) VALUES (?) ON CONFLICT(name) DO NOTHING', (name,)
        )
    await conn.commit()

    cursor = await conn.execute('SELECT id, name FROM classes')
    class_ids = {row['name']: row['id'] for row in await cursor.fetchall()}

    for row in rows:
        await conn.execute(
            """
            INSERT INTO specs (class_id, name, role) VALUES (?, ?, ?)
            ON CONFLICT(class_id, name) DO UPDATE SET role = excluded.role
            """,
            (class_ids[row['class']], row['spec'], row['role']),
        )
    await conn.commit()


async def get_classes() -> list[WowClass]:
    conn = await get_connection()
    cursor = await conn.execute('SELECT id, name FROM classes ORDER BY name')
    return [WowClass(id=row['id'], name=row['name']) for row in await cursor.fetchall()]


async def get_specs_for_class(class_id: int) -> list[Spec]:
    conn = await get_connection()
    cursor = await conn.execute(
        'SELECT id, class_id, name, role FROM specs WHERE class_id = ? ORDER BY name', (class_id,)
    )
    return [
        Spec(id=row['id'], class_id=row['class_id'], name=row['name'], role=row['role'])
        for row in await cursor.fetchall()
    ]


_CHARACTER_QUERY = """
SELECT c.id, c.discord_user_id, c.name, c.spec_id, sp.name AS spec_name, sp.role AS role,
       cl.name AS class_name
FROM characters c
JOIN specs sp ON sp.id = c.spec_id
JOIN classes cl ON cl.id = sp.class_id
"""


def _row_to_character(row: aiosqlite.Row) -> Character:
    return Character(
        id=row['id'],
        discord_user_id=row['discord_user_id'],
        name=row['name'],
        spec_id=row['spec_id'],
        spec_name=row['spec_name'],
        role=row['role'],
        class_name=row['class_name'],
    )


async def get_characters_for_user(discord_user_id: int) -> list[Character]:
    conn = await get_connection()
    cursor = await conn.execute(
        f'{_CHARACTER_QUERY} WHERE c.discord_user_id = ? ORDER BY c.name COLLATE NOCASE',
        (discord_user_id,),
    )
    return [_row_to_character(row) for row in await cursor.fetchall()]


async def get_character(character_id: int) -> Character:
    conn = await get_connection()
    cursor = await conn.execute(f'{_CHARACTER_QUERY} WHERE c.id = ?', (character_id,))
    row = await cursor.fetchone()
    return _row_to_character(row)


async def create_character(discord_user_id: int, name: str, spec_id: int) -> Character:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            'INSERT INTO characters (discord_user_id, name, spec_id) VALUES (?, ?, ?)',
            (discord_user_id, name, spec_id),
        )
        await conn.commit()
        character_id = cursor.lastrowid
    except aiosqlite.IntegrityError:
        cursor = await conn.execute(
            'SELECT id FROM characters WHERE discord_user_id = ? AND name = ? COLLATE NOCASE',
            (discord_user_id, name),
        )
        row = await cursor.fetchone()
        character_id = row['id']
        await conn.execute('UPDATE characters SET spec_id = ? WHERE id = ?', (spec_id, character_id))
        await conn.commit()
    return await get_character(character_id)


async def rename_character(character_id: int, name: str) -> bool:
    conn = await get_connection()
    try:
        await conn.execute('UPDATE characters SET name = ? WHERE id = ?', (name, character_id))
        await conn.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def update_character_spec(character_id: int, spec_id: int) -> None:
    conn = await get_connection()
    await conn.execute('UPDATE characters SET spec_id = ? WHERE id = ?', (spec_id, character_id))
    await conn.commit()


async def delete_character(character_id: int) -> None:
    conn = await get_connection()
    await conn.execute('DELETE FROM signups WHERE character_id = ?', (character_id,))
    await conn.execute('DELETE FROM characters WHERE id = ?', (character_id,))
    await conn.commit()


async def create_raid_event(
    channel_id: int,
    title: str,
    description: str | None,
    event_time: str | None,
    event_at: datetime | None,
    created_by: int,
) -> int:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        INSERT INTO raid_events (channel_id, title, description, event_time, event_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel_id, title, description, event_time, event_at.isoformat() if event_at else None, created_by),
    )
    await conn.commit()
    return cursor.lastrowid


async def set_raid_message_id(raid_id: int, message_id: int) -> None:
    conn = await get_connection()
    await conn.execute('UPDATE raid_events SET message_id = ? WHERE id = ?', (message_id, raid_id))
    await conn.commit()


_RAID_EVENT_QUERY = (
    'SELECT id, channel_id, message_id, title, description, event_time, event_at, created_by, cancelled '
    'FROM raid_events'
)


def _row_to_raid_event(row: aiosqlite.Row) -> RaidEvent:
    return RaidEvent(
        id=row['id'],
        channel_id=row['channel_id'],
        message_id=row['message_id'],
        title=row['title'],
        description=row['description'],
        event_time=row['event_time'],
        event_at=datetime.fromisoformat(row['event_at']) if row['event_at'] else None,
        created_by=row['created_by'],
        cancelled=bool(row['cancelled']),
    )


async def get_raid_event(raid_id: int) -> RaidEvent | None:
    conn = await get_connection()
    cursor = await conn.execute(f'{_RAID_EVENT_QUERY} WHERE id = ?', (raid_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_raid_event(row)


async def get_all_raid_events() -> list[RaidEvent]:
    conn = await get_connection()
    cursor = await conn.execute(f'{_RAID_EVENT_QUERY} ORDER BY id DESC')
    return [_row_to_raid_event(row) for row in await cursor.fetchall()]


async def update_raid_leader(raid_id: int, leader_id: int) -> None:
    conn = await get_connection()
    await conn.execute('UPDATE raid_events SET created_by = ? WHERE id = ?', (leader_id, raid_id))
    await conn.commit()


async def delete_raid_event(raid_id: int) -> None:
    conn = await get_connection()
    await conn.execute('DELETE FROM signups WHERE raid_event_id = ?', (raid_id,))
    await conn.execute('DELETE FROM raid_events WHERE id = ?', (raid_id,))
    await conn.commit()


async def update_raid_event(
    raid_id: int, title: str, description: str | None, event_time: str | None, event_at: datetime | None
) -> None:
    conn = await get_connection()
    await conn.execute(
        'UPDATE raid_events SET title = ?, description = ?, event_time = ?, event_at = ? WHERE id = ?',
        (title, description, event_time, event_at.isoformat() if event_at else None, raid_id),
    )
    await conn.commit()


async def cancel_raid_event(raid_id: int) -> None:
    conn = await get_connection()
    await conn.execute('UPDATE raid_events SET cancelled = 1 WHERE id = ?', (raid_id,))
    await conn.commit()


async def get_all_raid_ids() -> list[int]:
    conn = await get_connection()
    cursor = await conn.execute('SELECT id FROM raid_events')
    return [row['id'] for row in await cursor.fetchall()]


async def upsert_signup(raid_event_id: int, discord_user_id: int, character_id: int, status: str) -> None:
    conn = await get_connection()
    await conn.execute(
        """
        INSERT INTO signups (raid_event_id, discord_user_id, character_id, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(raid_event_id, discord_user_id)
        DO UPDATE SET character_id = excluded.character_id,
                      status = excluded.status,
                      signed_up_at = datetime('now')
        """,
        (raid_event_id, discord_user_id, character_id, status),
    )
    await conn.commit()


async def get_signups(raid_event_id: int) -> list[Signup]:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        SELECT s.status, s.character_id, c.name AS character_name, sp.name AS spec_name,
               sp.role AS role, cl.name AS class_name
        FROM signups s
        JOIN characters c ON c.id = s.character_id
        JOIN specs sp ON sp.id = c.spec_id
        JOIN classes cl ON cl.id = sp.class_id
        WHERE s.raid_event_id = ?
        """,
        (raid_event_id,),
    )
    return [
        Signup(
            status=row['status'],
            character_id=row['character_id'],
            character_name=row['character_name'],
            spec_name=row['spec_name'],
            role=row['role'],
            class_name=row['class_name'],
        )
        for row in await cursor.fetchall()
    ]
