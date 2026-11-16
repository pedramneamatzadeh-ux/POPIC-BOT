import logging
import os
from typing import List, Optional

from dotenv import load_dotenv


# ریشه پروژه (پوشه‌ای که main.py در آن است) — مستقل از مسیر اجرای دستور
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)


class Config:
    DB_PATH: str = os.path.join(_PROJECT_ROOT, "popic.db")
    PAGE_SIZE: int = 6

    def __init__(self) -> None:
        self.BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
        self.OWNER_ID: Optional[int] = self._parse_optional_id(
            os.getenv("OWNER_ID")
        )

        raw_admins = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS: List[int] = self._parse_admin_ids(raw_admins)
        self.INVALID_ADMIN_IDS: List[str] = self._invalid_admin_tokens(
            raw_admins
        )

        # پیش‌فرض‌های پیش‌نمایش و دکمه (در db.init() از settings همگام می‌شوند)
        self.DEFAULT_SHOW_ABOVE_TEXT: bool = True
        self.DEFAULT_PREFER_LARGE_MEDIA: bool = True
        self.DEFAULT_BUTTON_TEXT: str = "🖼 مشاهده تصویر"

    @staticmethod
    def _invalid_admin_tokens(raw: str) -> List[str]:
        return [
            part.strip()
            for part in (raw or "").split(",")
            if part.strip() and not part.strip().isdigit()
        ]

    @staticmethod
    def _parse_admin_ids(raw: str) -> List[int]:
        return [
            int(part.strip())
            for part in raw.split(",")
            if part.strip().isdigit()
        ]

    @staticmethod
    def _parse_optional_id(raw: Optional[str]) -> Optional[int]:
        value = (raw or "").strip()
        if value.isdigit():
            return int(value)
        return None

    def validate(self) -> None:
        if self.INVALID_ADMIN_IDS:
            logger.warning(
                "⚠️ ADMIN_IDS شامل مقدار نامعتبر بود و این مقادیر حذف "
                "شدند: %s — فقط آیدی عددی تلگرام معتبر است.",
                ", ".join(self.INVALID_ADMIN_IDS),
            )

        if (
            not self.BOT_TOKEN
            or self.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE"
        ):
            raise ValueError(
                "لطفاً BOT_TOKEN را در فایل .env تنظیم کنید."
            )

        if self.OWNER_ID is None:
            raise ValueError(
                "لطفاً OWNER_ID (آیدی عددی تلگرام مالک) را "
                "در فایل .env تنظیم کنید."
            )


config = Config()
