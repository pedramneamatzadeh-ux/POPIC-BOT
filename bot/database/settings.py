"""تنظیمات کلید-مقدار اختصاصی هر فضای کاری (بدون تغییر config سراسری)."""

import json
import logging
from typing import Any

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

    def get_show_above_text(self) -> bool:
        val = self.get_setting("default_show_above_text", None)
        if val is None:
            return config.DEFAULT_SHOW_ABOVE_TEXT
        return bool(val)

    def get_prefer_large_media(self) -> bool:
        val = self.get_setting("default_prefer_large_media", None)
        if val is None:
            return config.DEFAULT_PREFER_LARGE_MEDIA
        return bool(val)

    def get_default_button_text(self) -> str:
        val = self.get_setting("default_button_text", None)
        if isinstance(val, str) and val.strip():
            return val.strip()[:64]
        return config.DEFAULT_BUTTON_TEXT
