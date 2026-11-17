import logging
import os

from bot.application import create_application
from bot.config import config
from bot.constants import BOT_VERSION
from bot.database import get_workspace_db, registry

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def preflight() -> None:
    """اعتبارسنجی اولیه پیش از راه‌اندازی ربات."""
    config.validate()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.WORKSPACES_DIR, exist_ok=True)


def main() -> None:
    preflight()

    registry.init()
    if config.OWNER_ID is not None:
        get_workspace_db(config.OWNER_ID)

    logger.info(
        "ربات POPIC-BOT نسخه %s در حال راه‌اندازی (حالت عمومی چندکاربره، ابرمالک: %s)...",
        BOT_VERSION,
        config.OWNER_ID if config.OWNER_ID is not None else "تنظیم‌نشده",
    )

    app = create_application()
    logger.info("ربات با موفقیت آماده شد — شروع Polling.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
