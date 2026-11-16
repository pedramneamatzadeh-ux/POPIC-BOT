import logging

from bot.application import build_application
from bot.config import config
from bot.constants import BOT_VERSION, BOT_VERSION_DATE
from bot.database import db


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# جلوگیری از چاپ شدن توکن ربات در لاگ‌های httpx/httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def preflight() -> bool:
    """چک‌های پیش‌از‌راه‌اندازی — جدا شده تا قابل تست باشد."""
    try:
        config.validate()
    except ValueError as err:
        logger.error("⚠️ خطای پیکربندی: %s", err)
        return False

    db.init()
    return _post_init_checks()


def _post_init_checks() -> bool:
    if not config.ADMIN_IDS:
        if config.OWNER_ID is None:
            logger.error(
                "⚠️ هیچ مالک یا ادمینی تنظیم نشده است.",
            )
            return False
        logger.info(
            "ℹ️ ADMIN_IDS خالی است — فعلاً فقط مالک (%s) دسترسی مدیریت "
            "دارد. ادمین‌های بعدی را از «⚙️ تنظیمات» اضافه کنید.",
            config.OWNER_ID,
        )

    channels_count = db.count_target_channels()
    if channels_count == 0:
        logger.info(
            "ℹ️ هنوز کانال مقصدی ثبت نشده است — پست‌ها فعلاً در پی‌وی "
            "قابل دریافت هستند؛ برای انتشار مستقیم، از «⚙️ تنظیمات» "
            "کانال اضافه کنید.",
        )

    return True


def main() -> None:
    if not preflight():
        return
    logger.info("مالک ربات: %s", config.OWNER_ID)
    logger.info(
        "ادمین‌ها: %s",
        ", ".join(map(str, config.ADMIN_IDS)) if config.ADMIN_IDS else "—",
    )

    application = build_application()
    logger.info(
        "ربات POPIC-BOT نسخهٔ %s (%s) در حال اجراست...",
        BOT_VERSION,
        BOT_VERSION_DATE,
    )
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
