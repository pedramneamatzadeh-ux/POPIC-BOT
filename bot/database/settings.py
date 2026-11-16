"""تنظیمات کلید-مقدار (جدول settings) + همگام‌سازی با config."""

import json
import logging
import sqlite3
from typing import Any, Dict, List

from ..config import config

logger = logging.getLogger(__name__)


class SettingsMixin:
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return default

        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            logger.warning("مقدار نامعتبر در settings: %s", key)
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

        self._apply_settings_to_config({key: value})

    def _import_env_settings(self, connection: sqlite3.Connection) -> None:
        env_values: Dict[str, Any] = {
            "admin_ids": config.ADMIN_IDS or None,
        }

        for key, value in env_values.items():
            if value is None or value == []:
                continue

            exists = connection.execute(
                "SELECT 1 FROM settings WHERE key = ?",
                (key,),
            ).fetchone()

            if exists:
                continue

            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            logger.info("تنظیم «%s» از env به دیتابیس منتقل شد.", key)

    def _load_settings(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT key, value FROM settings"
        ).fetchall()

        if not rows:
            return

        data: Dict[str, Any] = {}
        for row in rows:
            try:
                data[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                logger.warning("مقدار JSON نامعتبر در settings: %s", row["key"])

        self._apply_settings_to_config(data)

    def _apply_settings_to_config(self, data: Dict[str, Any]) -> None:
        admin_ids = data.get("admin_ids")

        if isinstance(admin_ids, list):
            valid_ids: List[int] = []
            invalid_ids: List[Any] = []
            for item in admin_ids:
                try:
                    value = int(str(item).strip())
                except (TypeError, ValueError):
                    invalid_ids.append(item)
                    continue
                if value > 0:
                    valid_ids.append(value)
                else:
                    invalid_ids.append(item)

            if invalid_ids:
                logger.warning(
                    "admin_ids: %s مقدار نامعتبر حذف شد.",
                    invalid_ids,
                )
            config.ADMIN_IDS = valid_ids

        if "default_show_above_text" in data:
            config.DEFAULT_SHOW_ABOVE_TEXT = bool(
                data["default_show_above_text"]
            )

        if "default_prefer_large_media" in data:
            config.DEFAULT_PREFER_LARGE_MEDIA = bool(
                data["default_prefer_large_media"]
            )

        btn_text = data.get("default_button_text")
        if isinstance(btn_text, str) and btn_text.strip():
            config.DEFAULT_BUTTON_TEXT = btn_text.strip()[:64]
