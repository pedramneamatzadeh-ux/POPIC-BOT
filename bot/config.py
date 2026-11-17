import os
from typing import Optional

from dotenv import load_dotenv


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


class Config:
    DATA_DIR: str = os.path.join(_PROJECT_ROOT, "data")
    REGISTRY_DB_PATH: str = os.path.join(DATA_DIR, "registry.db")
    WORKSPACES_DIR: str = os.path.join(DATA_DIR, "workspaces")
    PAGE_SIZE: int = 6

    # مقادیر پیش‌فرض تغییرناپذیر برای هر دیتابیس جدید
    DEFAULT_SHOW_ABOVE_TEXT: bool = True
    DEFAULT_PREFER_LARGE_MEDIA: bool = True
    DEFAULT_BUTTON_TEXT: str = "🖼 مشاهده تصویر"

    def __init__(self) -> None:
        self.BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
        # ابرمالک اختیاری ربات (برای گزارش خطای سیستمی و آمار کل)
        self.OWNER_ID: Optional[int] = self._parse_optional_id(
            os.getenv("OWNER_ID")
        )

    @staticmethod
    def _parse_optional_id(raw: Optional[str]) -> Optional[int]:
        value = (raw or "").strip()
        if value.isdigit():
            return int(value)
        return None

    def validate(self) -> None:
        if (
            not self.BOT_TOKEN
            or self.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE"
        ):
            raise ValueError(
                "لطفاً BOT_TOKEN را در فایل .env تنظیم کنید."
            )


config = Config()
