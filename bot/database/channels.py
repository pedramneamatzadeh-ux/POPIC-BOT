"""مدیریت کانال‌های مقصد."""

from typing import Any, Dict, List, Optional


class ChannelsMixin:
    def add_target_channel(
        self,
        chat_id: int,
        title: str = "",
        username: str = "",
        added_by: Optional[int] = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO target_channels (
                    chat_id, title, username, added_by
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username
                """,
                (int(chat_id), title or "", username or "", added_by),
            )
            return int(cursor.lastrowid)

    def get_target_channels(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM target_channels
                ORDER BY id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_target_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM target_channels WHERE id = ?",
                (int(channel_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_target_channel_by_chat_id(
        self,
        chat_id: int,
    ) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM target_channels WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_target_channel(self, channel_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM target_channels WHERE id = ?",
                (int(channel_id),),
            )
            return cursor.rowcount > 0

    def count_target_channels(self) -> int:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM target_channels"
            ).fetchone()[0]
