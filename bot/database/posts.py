"""ذخیره، بازیابی، زمان‌بندی، جست‌وجو و آمار پست‌های فضای کاری."""

import json
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
        media_type: str = "link",
        photo_file_id: str = "",
        title: str = "",
        scheduled_at: Optional[str] = None,
        scheduled_chat_ids: Optional[List[int]] = None,
        schedule_status: str = "",
    ) -> int:
        chat_ids_json = json.dumps(
            [int(x) for x in (scheduled_chat_ids or [])],
            ensure_ascii=False,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO posts (
                    title,
                    post_text,
                    entities_json,
                    media_type,
                    photo_url,
                    photo_file_id,
                    show_above_text,
                    prefer_large_media,
                    buttons_json,
                    status,
                    scheduled_at,
                    scheduled_chat_ids,
                    schedule_status,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (
                    (title or "").strip(),
                    post_text,
                    entities_json or "[]",
                    media_type or "link",
                    photo_url or "",
                    photo_file_id or "",
                    1 if show_above_text else 0,
                    1 if prefer_large_media else 0,
                    buttons_json or "[]",
                    scheduled_at,
                    chat_ids_json,
                    schedule_status or "",
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
            return int(
                connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            )

    def search_posts(
        self,
        query: str,
        limit: int = 6,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        pattern = f"%{(query or '').strip()}%"
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM posts
                WHERE title LIKE ? COLLATE NOCASE
                   OR post_text LIKE ? COLLATE NOCASE
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (pattern, pattern, int(limit), int(offset)),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_search_posts(self, query: str) -> int:
        pattern = f"%{(query or '').strip()}%"
        with self.connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM posts
                    WHERE title LIKE ? COLLATE NOCASE
                       OR post_text LIKE ? COLLATE NOCASE
                    """,
                    (pattern, pattern),
                ).fetchone()[0]
            )

    def list_pending_scheduled_posts(
        self,
        search_query: str = "",
    ) -> List[Dict[str, Any]]:
        q = (search_query or "").strip()
        with self.connect() as connection:
            if q:
                pattern = f"%{q}%"
                rows = connection.execute(
                    """
                    SELECT *
                    FROM posts
                    WHERE schedule_status = 'pending'
                      AND scheduled_at IS NOT NULL
                      AND (title LIKE ? COLLATE NOCASE OR post_text LIKE ? COLLATE NOCASE)
                    ORDER BY scheduled_at ASC, id ASC
                    """,
                    (pattern, pattern),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM posts
                    WHERE schedule_status = 'pending'
                      AND scheduled_at IS NOT NULL
                    ORDER BY scheduled_at ASC, id ASC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def count_pending_scheduled_posts(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM posts WHERE schedule_status = 'pending' AND scheduled_at IS NOT NULL"
                ).fetchone()[0]
            )

    def get_due_scheduled_posts(self, now_utc_iso: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM posts
                WHERE schedule_status = 'pending'
                  AND scheduled_at IS NOT NULL
                  AND scheduled_at <= ?
                ORDER BY scheduled_at ASC, id ASC
                """,
                (now_utc_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_scheduled_post(self, post_id: int) -> bool:
        """قفل اتمیک برای جلوگیری از ارسال تکراری در زمان‌بندی."""
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE posts
                SET schedule_status = 'sending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND schedule_status = 'pending'
                """,
                (int(post_id),),
            )
            return cursor.rowcount > 0

    def finish_scheduled_post(
        self,
        post_id: int,
        success: bool,
        error: str = "",
    ) -> bool:
        new_status = "sent" if success else "failed"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE posts
                SET schedule_status = ?,
                    schedule_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, (error or "")[:500], int(post_id)),
            )
            return cursor.rowcount > 0

    def set_post_schedule(
        self,
        post_id: int,
        scheduled_at: str,
        scheduled_chat_ids: List[int],
    ) -> bool:
        chat_ids_json = json.dumps(
            [int(x) for x in scheduled_chat_ids],
            ensure_ascii=False,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE posts
                SET scheduled_at = ?,
                    scheduled_chat_ids = ?,
                    schedule_status = 'pending',
                    schedule_error = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (scheduled_at, chat_ids_json, int(post_id)),
            )
            return cursor.rowcount > 0

    def clear_post_schedule(self, post_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE posts
                SET scheduled_at = NULL,
                    scheduled_chat_ids = '[]',
                    schedule_status = '',
                    schedule_error = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(post_id),),
            )
            return cursor.rowcount > 0

    def update_post(
        self,
        post_id: int,
        *,
        title: Optional[str] = None,
        post_text: Optional[str] = None,
        entities_json: Optional[str] = None,
        media_type: Optional[str] = None,
        photo_url: Optional[str] = None,
        photo_file_id: Optional[str] = None,
        show_above_text: Optional[bool] = None,
        prefer_large_media: Optional[bool] = None,
        buttons_json: Optional[str] = None,
    ) -> bool:
        sets: List[str] = []
        params: List[Any] = []

        if title is not None:
            sets.append("title = ?")
            params.append(title.strip())
        if post_text is not None:
            sets.append("post_text = ?")
            params.append(post_text)
        if entities_json is not None:
            sets.append("entities_json = ?")
            params.append(entities_json)
        if media_type is not None:
            sets.append("media_type = ?")
            params.append(media_type)
        if photo_url is not None:
            sets.append("photo_url = ?")
            params.append(photo_url)
        if photo_file_id is not None:
            sets.append("photo_file_id = ?")
            params.append(photo_file_id)
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
        media_type: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO post_deliveries (
                    post_id, target_type, target_chat_id, message_id, media_type, sent_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(post_id),
                    target_type,
                    int(target_chat_id),
                    message_id,
                    media_type or "",
                    int(sent_by),
                ),
            )

    def get_published_targets(self, post_id: int) -> List[Dict[str, Any]]:
        """تمام کانال‌ها/گروه‌هایی که این پست در آن‌ها ارسال شده را برمی‌گرداند."""
        post = self.get_post(post_id)
        if not post:
            return []

        targets: Dict[int, Dict[str, Any]] = {}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.target_chat_id, d.message_id, d.media_type, d.id AS delivery_id,
                       c.title AS ch_title, c.username AS ch_username, c.chat_type AS ch_type
                FROM post_deliveries d
                LEFT JOIN target_channels c ON c.chat_id = d.target_chat_id
                WHERE d.post_id = ?
                  AND d.target_type = 'channel'
                  AND d.message_id IS NOT NULL
                ORDER BY d.id ASC
                """,
                (int(post_id),),
            ).fetchall()

        for row in rows:
            chat_id = int(row["target_chat_id"])
            targets[chat_id] = {
                "chat_id": chat_id,
                "message_id": int(row["message_id"]),
                "media_type": row["media_type"] or "",
                "title": row["ch_title"] or str(chat_id),
                "username": row["ch_username"] or "",
                "chat_type": row["ch_type"] or "channel",
            }

        # سازگاری با رکوردهای قدیمی که فقط در جدول posts ثبت شده‌اند
        legacy_cid = post.get("target_channel_id")
        legacy_mid = post.get("channel_message_id")
        if legacy_cid and legacy_mid and int(legacy_cid) not in targets:
            targets[int(legacy_cid)] = {
                "chat_id": int(legacy_cid),
                "message_id": int(legacy_mid),
                "media_type": "",
                "title": post.get("channel_title") or str(legacy_cid),
                "username": post.get("channel_username") or "",
                "chat_type": "channel",
            }

        return list(targets.values())

    def replace_delivery_message(
        self,
        post_id: int,
        target_chat_id: int,
        new_message_id: int,
        media_type: str = "",
        sent_by: int = 0,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE post_deliveries
                SET message_id = ?, media_type = ?, delivered_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM post_deliveries
                    WHERE post_id = ? AND target_type = 'channel' AND target_chat_id = ?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (int(new_message_id), media_type or "", int(post_id), int(target_chat_id)),
            )
            post = self.get_post(post_id)
            if post and post.get("target_channel_id") == int(target_chat_id):
                connection.execute(
                    """
                    UPDATE posts
                    SET channel_message_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(new_message_id), int(post_id)),
                )

    def save_old_popup(self, popup_text: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO popup_buttons (popup_text) VALUES (?)",
                (popup_text,),
            )
            return int(cursor.lastrowid)

    def get_old_popup(self, popup_id: int) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT popup_text FROM popup_buttons WHERE id = ?",
                (int(popup_id),),
            ).fetchone()
        return row["popup_text"] if row else None

    def get_stats_summary(self) -> Dict[str, Any]:
        with self.connect() as connection:
            total_posts = connection.execute(
                "SELECT COUNT(*) FROM posts"
            ).fetchone()[0]
            published_posts = connection.execute(
                "SELECT COUNT(*) FROM posts WHERE status = 'published'"
            ).fetchone()[0]
            draft_posts = total_posts - published_posts
            scheduled_posts = connection.execute(
                "SELECT COUNT(*) FROM posts WHERE schedule_status = 'pending'"
            ).fetchone()[0]
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
            "scheduled_posts": scheduled_posts,
            "channel_deliveries": channel_deliveries,
            "pv_deliveries": pv_deliveries,
            "total_channels": total_channels,
        }
