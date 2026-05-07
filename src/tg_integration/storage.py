from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import time


@dataclass(frozen=True)
class ConversationLink:
    telegram_chat_id: str
    amo_conversation_id: str
    telegram_user_id: str | None
    telegram_name: str | None
    telegram_username: str | None


class BridgeStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_links (
                    telegram_chat_id TEXT PRIMARY KEY,
                    amo_conversation_id TEXT NOT NULL,
                    telegram_user_id TEXT,
                    telegram_name TEXT,
                    telegram_username TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (source, event_id)
                );

                CREATE TABLE IF NOT EXISTS message_links (
                    source TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    telegram_chat_id TEXT,
                    telegram_message_id TEXT,
                    amo_message_id TEXT,
                    amo_ref_id TEXT,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (source, source_message_id)
                );
                """
            )

    def has_processed(self, source: str, event_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM processed_events WHERE source = ? AND event_id = ?",
                (source, event_id),
            ).fetchone()
        return row is not None

    def mark_processed(self, source: str, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO processed_events (source, event_id, created_at)
                VALUES (?, ?, ?)
                """,
                (source, event_id, int(time())),
            )

    def upsert_conversation_link(
        self,
        *,
        telegram_chat_id: str,
        amo_conversation_id: str,
        telegram_user_id: str | None,
        telegram_name: str | None,
        telegram_username: str | None,
    ) -> None:
        now = int(time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_links (
                    telegram_chat_id,
                    amo_conversation_id,
                    telegram_user_id,
                    telegram_name,
                    telegram_username,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    amo_conversation_id = excluded.amo_conversation_id,
                    telegram_user_id = excluded.telegram_user_id,
                    telegram_name = excluded.telegram_name,
                    telegram_username = excluded.telegram_username,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_chat_id,
                    amo_conversation_id,
                    telegram_user_id,
                    telegram_name,
                    telegram_username,
                    now,
                    now,
                ),
            )

    def get_link_by_telegram_chat_id(self, telegram_chat_id: str) -> ConversationLink | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT telegram_chat_id, amo_conversation_id, telegram_user_id, telegram_name, telegram_username
                FROM conversation_links
                WHERE telegram_chat_id = ?
                """,
                (telegram_chat_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationLink(**dict(row))

    def get_link_by_amo_conversation_id(self, amo_conversation_id: str) -> ConversationLink | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT telegram_chat_id, amo_conversation_id, telegram_user_id, telegram_name, telegram_username
                FROM conversation_links
                WHERE amo_conversation_id = ?
                """,
                (amo_conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationLink(**dict(row))

    def save_message_link(
        self,
        *,
        source: str,
        source_message_id: str,
        telegram_chat_id: str | None = None,
        telegram_message_id: str | None = None,
        amo_message_id: str | None = None,
        amo_ref_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO message_links (
                    source,
                    source_message_id,
                    telegram_chat_id,
                    telegram_message_id,
                    amo_message_id,
                    amo_ref_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    source_message_id,
                    telegram_chat_id,
                    telegram_message_id,
                    amo_message_id,
                    amo_ref_id,
                    int(time()),
                ),
            )
