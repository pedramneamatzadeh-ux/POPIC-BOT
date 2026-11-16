"""کاربران."""

from typing import Any, Dict, List, Optional


class UsersMixin:
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
                    user_id,
                    username,
                    first_name,
                    last_name
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_activity = CURRENT_TIMESTAMP
                """,
                (user_id, username, first_name, last_name),
            )

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM users
                ORDER BY last_activity DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def count_users(self) -> int:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]
