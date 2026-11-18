"""هستهٔ دیتابیس اختصاصی هر فضای کاری."""

import logging
import os
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class CoreMixin:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")

        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS target_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_text TEXT NOT NULL,
                    entities_json TEXT NOT NULL DEFAULT '[]',
                    media_type TEXT NOT NULL DEFAULT 'link',
                    photo_url TEXT NOT NULL DEFAULT '',
                    photo_file_id TEXT NOT NULL DEFAULT '',
                    show_above_text INTEGER NOT NULL DEFAULT 1,
                    prefer_large_media INTEGER NOT NULL DEFAULT 1,
                    buttons_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    target_channel_id INTEGER,
                    channel_message_id INTEGER,
                    channel_title TEXT DEFAULT '',
                    channel_username TEXT DEFAULT '',
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    published_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS post_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL,
                    target_chat_id INTEGER NOT NULL,
                    message_id INTEGER,
                    sent_by INTEGER NOT NULL,
                    delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (post_id)
                        REFERENCES posts(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_posts_created
                    ON posts(created_at);

                CREATE INDEX IF NOT EXISTS idx_posts_status
                    ON posts(status);

                CREATE INDEX IF NOT EXISTS idx_deliveries_post
                    ON post_deliveries(post_id);
                """
            )
            existing_cols = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(posts)").fetchall()
            }
            if "media_type" not in existing_cols:
                connection.execute(
                    "ALTER TABLE posts ADD COLUMN media_type TEXT NOT NULL DEFAULT 'link'"
                )
            if "photo_file_id" not in existing_cols:
                connection.execute(
                    "ALTER TABLE posts ADD COLUMN photo_file_id TEXT NOT NULL DEFAULT ''"
                )
            connection.execute("PRAGMA journal_mode = WAL")

    def wal_checkpoint(self) -> None:
        """محتوای WAL را به فایل اصلی دیتابیس منتقل می‌کند (پیش از بک‌آپ)."""
        try:
            with self.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            logger.warning("wal_checkpoint ناموفق بود: %s", exc)
