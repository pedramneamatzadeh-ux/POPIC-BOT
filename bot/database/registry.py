"""دیتابیس مرکزی رجیستری: کاربران، فضای کاری فعال و عضویت ادمین‌ها."""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


class RegistryDatabase:
    def __init__(self, path: str) -> None:
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
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    active_workspace_id INTEGER,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    banned_at TIMESTAMP,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS workspace_admins (
                    workspace_owner_id INTEGER NOT NULL,
                    admin_user_id INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workspace_owner_id, admin_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_ws_admin_user
                    ON workspace_admins(admin_user_id);
                """
            )
            existing_cols = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "is_banned" not in existing_cols:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0"
                )
            if "banned_at" not in existing_cols:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN banned_at TIMESTAMP"
                )
            connection.execute("PRAGMA journal_mode = WAL")

    def save_or_update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, first_name, last_name, active_workspace_id
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_activity = CURRENT_TIMESTAMP
                """,
                (int(user_id), username, first_name, last_name, int(user_id)),
            )

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY last_activity DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users_paginated(
        self,
        limit: int = 8,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                ORDER BY last_activity DESC, user_id ASC
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_users(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            )

    def is_user_banned(self, user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT is_banned FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return bool(row and row["is_banned"])

    def set_user_banned(self, user_id: int, banned: bool) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET is_banned = ?,
                    banned_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE user_id = ?
                """,
                (1 if banned else 0, 1 if banned else 0, int(user_id)),
            )
            return cursor.rowcount > 0

    def get_user_display_name(self, user_id: int) -> str:
        user = self.get_user(user_id)
        if not user:
            return str(user_id)
        full = " ".join(
            part
            for part in (
                user.get("first_name") or "",
                user.get("last_name") or "",
            )
            if part
        ).strip()
        if full:
            return full
        if user.get("username"):
            return f"@{user['username']}"
        return str(user_id)

    def get_active_workspace_id(self, user_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT active_workspace_id FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        if row and row["active_workspace_id"]:
            return int(row["active_workspace_id"])
        return int(user_id)

    def set_active_workspace_id(
        self,
        user_id: int,
        workspace_owner_id: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (user_id, active_workspace_id)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    active_workspace_id = excluded.active_workspace_id,
                    last_activity = CURRENT_TIMESTAMP
                """,
                (int(user_id), int(workspace_owner_id)),
            )

    def add_workspace_admin(
        self,
        workspace_owner_id: int,
        admin_user_id: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO workspace_admins (
                    workspace_owner_id, admin_user_id
                )
                VALUES (?, ?)
                """,
                (int(workspace_owner_id), int(admin_user_id)),
            )

    def remove_workspace_admin(
        self,
        workspace_owner_id: int,
        admin_user_id: int,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM workspace_admins
                WHERE workspace_owner_id = ? AND admin_user_id = ?
                """,
                (int(workspace_owner_id), int(admin_user_id)),
            )
            return cursor.rowcount > 0

    def get_workspace_admins(self, workspace_owner_id: int) -> List[int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT admin_user_id
                FROM workspace_admins
                WHERE workspace_owner_id = ?
                ORDER BY added_at ASC
                """,
                (int(workspace_owner_id),),
            ).fetchall()
        return [int(row["admin_user_id"]) for row in rows]

    def get_admin_workspaces(self, admin_user_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    wa.workspace_owner_id,
                    u.username,
                    u.first_name,
                    u.last_name
                FROM workspace_admins wa
                LEFT JOIN users u ON u.user_id = wa.workspace_owner_id
                WHERE wa.admin_user_id = ?
                ORDER BY wa.added_at ASC
                """,
                (int(admin_user_id),),
            ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            full = " ".join(
                part
                for part in (
                    item.get("first_name") or "",
                    item.get("last_name") or "",
                )
                if part
            ).strip()
            if not full and item.get("username"):
                full = f"@{item['username']}"
            item["owner_display"] = full or str(item["workspace_owner_id"])
            result.append(item)
        return result

    def is_workspace_admin(
        self,
        workspace_owner_id: int,
        user_id: int,
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM workspace_admins
                WHERE workspace_owner_id = ? AND admin_user_id = ?
                """,
                (int(workspace_owner_id), int(user_id)),
            ).fetchone()
        return row is not None

    def get_global_stats(self) -> Dict[str, int]:
        with self.connect() as connection:
            total_users = connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]
            total_admin_links = connection.execute(
                "SELECT COUNT(*) FROM workspace_admins"
            ).fetchone()[0]
        return {
            "total_users": int(total_users),
            "total_admin_links": int(total_admin_links),
        }

    def wal_checkpoint(self) -> None:
        """محتوای WAL را به فایل اصلی registry.db منتقل می‌کند."""
        try:
            with self.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
