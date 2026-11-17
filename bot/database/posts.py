"""ذخیره، بازیابی و آمار پست‌های فضای کاری."""

from typing import Any, Dict, List, Optional


class PostsMixin:
    def create_post(
        self,
        post_text: str,
        entities_json: str,
        photo_url: str,
        created_by: int,
        show_above_text: bool = True,
        prefer_large_media: bool = True,
        buttons_json: str = "[]",
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO posts (
                    post_text,
                    entities_json,
                    photo_url,
                    show_above_text,
                    prefer_large_media,
                    buttons_json,
                    status,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)
                """,
                (
                    post_text,
                    entities_json or "[]",
                    photo_url,
                    1 if show_above_text else 0,
                    1 if prefer_large_media else 0,
                    buttons_json or "[]",
                    int(created_by),
                ),
            )
            return int(cursor.lastrowid)

    def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM posts WHERE id = ?",
                (int(post_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_posts(
        self,
        limit: int = 6,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM posts
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_posts(self) -> int:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM posts"
            ).fetchone()[0]

    def update_post(
        self,
        post_id: int,
        *,
        post_text: Optional[str] = None,
        entities_json: Optional[str] = None,
        photo_url: Optional[str] = None,
        show_above_text: Optional[bool] = None,
        prefer_large_media: Optional[bool] = None,
        buttons_json: Optional[str] = None,
    ) -> bool:
        sets: List[str] = []
        params: List[Any] = []

        if post_text is not None:
            sets.append("post_text = ?")
            params.append(post_text)
        if entities_json is not None:
            sets.append("entities_json = ?")
            params.append(entities_json)
        if photo_url is not None:
            sets.append("photo_url = ?")
            params.append(photo_url)
        if show_above_text is not None:
            sets.append("show_above_text = ?")
            params.append(1 if show_above_text else 0)
        if prefer_large_media is not None:
            sets.append("prefer_large_media = ?")
            params.append(1 if prefer_large_media else 0)
        if buttons_json is not None:
            sets.append("buttons_json = ?")
            params.append(buttons_json)

        if not sets:
            return False

        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(post_id))

        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE posts SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    def mark_post_published(
        self,
        post_id: int,
        channel_chat_id: int,
        channel_message_id: int,
        channel_title: str = "",
        channel_username: str = "",
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE posts
                SET status = 'published',
                    target_channel_id = ?,
                    channel_message_id = ?,
                    channel_title = ?,
                    channel_username = ?,
                    published_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(channel_chat_id),
                    int(channel_message_id),
                    channel_title or "",
                    channel_username or "",
                    int(post_id),
                ),
            )
            return cursor.rowcount > 0

    def delete_post(self, post_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM posts WHERE id = ?",
                (int(post_id),),
            )
            return cursor.rowcount > 0

    def record_delivery(
        self,
        post_id: int,
        target_type: str,
        target_chat_id: int,
        message_id: Optional[int],
        sent_by: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO post_deliveries (
                    post_id, target_type, target_chat_id, message_id, sent_by
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(post_id),
                    target_type,
                    int(target_chat_id),
                    message_id,
                    int(sent_by),
                ),
            )

    def get_stats_summary(self) -> Dict[str, Any]:
        with self.connect() as connection:
            total_posts = connection.execute(
                "SELECT COUNT(*) FROM posts"
            ).fetchone()[0]
            published_posts = connection.execute(
                "SELECT COUNT(*) FROM posts WHERE status = 'published'"
            ).fetchone()[0]
            draft_posts = total_posts - published_posts
            channel_deliveries = connection.execute(
                "SELECT COUNT(*) FROM post_deliveries "
                "WHERE target_type = 'channel'"
            ).fetchone()[0]
            pv_deliveries = connection.execute(
                "SELECT COUNT(*) FROM post_deliveries "
                "WHERE target_type = 'pv'"
            ).fetchone()[0]
            total_channels = connection.execute(
                "SELECT COUNT(*) FROM target_channels"
            ).fetchone()[0]

        return {
            "total_posts": total_posts,
            "published_posts": published_posts,
            "draft_posts": draft_posts,
            "channel_deliveries": channel_deliveries,
            "pv_deliveries": pv_deliveries,
            "total_channels": total_channels,
        }
