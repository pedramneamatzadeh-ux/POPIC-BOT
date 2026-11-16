import logging
import re

from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import config
from .constants import (
    PM_DETAIL,
    PM_EDIT_LINK,
    PM_EDIT_TEXT,
    PM_LIST,
    PM_SELECT_CHANNEL,
    POST_ADD_BTN_TEXT,
    POST_ADD_BTN_URL,
    POST_EDIT_LINK,
    POST_EDIT_TEXT,
    POST_PHOTO_LINK,
    POST_PREVIEW,
    POST_SELECT_CHANNEL,
    POST_TEXT,
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_BUTTON,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    STATS_VIEW,
)
from .handlers.backup import handle_backup
from .handlers.base import start
from .handlers.post_builder import (
    cancel_builder,
    cancel_from_inline,
    confirm_cancel_no,
    confirm_cancel_yes,
    handle_preview_callback,
    receive_btn_text,
    receive_btn_url,
    receive_edit_link,
    receive_edit_text,
    receive_photo_link,
    receive_post_text,
    start_post_builder,
)
from .handlers.post_manage import (
    handle_post_manage_callback,
    receive_pm_edit_link,
    receive_pm_edit_text,
    start_post_manage,
)
from .handlers.settings import (
    cancel_settings,
    open_settings,
    receive_admin_input,
    receive_button_label_input,
    receive_channel_input,
    settings_callback,
)
from .handlers.stats import handle_stats_callback, show_stats
from .services.panels import (
    PANEL_BUTTONS_PATTERN,
    panel_gate,
    register_panels,
    stale_callback_answer,
)

logger = logging.getLogger(__name__)


def _build_post_builder_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📝 ساخت پست جدید$"),
                start_post_builder,
            ),
        ],
        states={
            POST_TEXT: [
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    receive_post_text,
                ),
            ],
            POST_PHOTO_LINK: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_photo_link,
                ),
            ],
            POST_PREVIEW: [
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
            POST_SELECT_CHANNEL: [
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
            POST_EDIT_TEXT: [
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    receive_edit_text,
                ),
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
            POST_EDIT_LINK: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_edit_link,
                ),
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
            POST_ADD_BTN_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_btn_text,
                ),
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
            POST_ADD_BTN_URL: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_btn_url,
                ),
                CallbackQueryHandler(
                    handle_preview_callback,
                    pattern=r"^pb:",
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^لغو$"), cancel_builder),
            MessageHandler(filters.COMMAND, cancel_builder),
            CallbackQueryHandler(
                cancel_from_inline,
                pattern=r"^cancel_prompt$",
            ),
            CallbackQueryHandler(
                confirm_cancel_yes,
                pattern=r"^cancel_yes$",
            ),
            CallbackQueryHandler(
                confirm_cancel_no,
                pattern=r"^cancel_no$",
            ),
        ],
        allow_reentry=True,
    )


def _build_post_manage_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📂 پست‌های ذخیره‌شده$"),
                start_post_manage,
            ),
        ],
        states={
            PM_LIST: [
                CallbackQueryHandler(
                    handle_post_manage_callback,
                    pattern=r"^pm:",
                ),
            ],
            PM_DETAIL: [
                CallbackQueryHandler(
                    handle_post_manage_callback,
                    pattern=r"^pm:",
                ),
            ],
            PM_SELECT_CHANNEL: [
                CallbackQueryHandler(
                    handle_post_manage_callback,
                    pattern=r"^pm:",
                ),
            ],
            PM_EDIT_TEXT: [
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    receive_pm_edit_text,
                ),
                CallbackQueryHandler(
                    handle_post_manage_callback,
                    pattern=r"^pm:",
                ),
            ],
            PM_EDIT_LINK: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_pm_edit_link,
                ),
                CallbackQueryHandler(
                    handle_post_manage_callback,
                    pattern=r"^pm:",
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.COMMAND, lambda u, c: ConversationHandler.END),
        ],
        allow_reentry=True,
    )


def _build_stats_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📊 آمار$"), show_stats),
        ],
        states={
            STATS_VIEW: [
                CallbackQueryHandler(
                    handle_stats_callback,
                    pattern=r"^stats:",
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.COMMAND, lambda u, c: ConversationHandler.END),
        ],
        allow_reentry=True,
    )


def _build_settings_conversation() -> ConversationHandler:
    def _st_callback() -> CallbackQueryHandler:
        return CallbackQueryHandler(settings_callback, pattern=r"^st:")

    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(SETTINGS_BUTTON)}$"),
                open_settings,
            ),
        ],
        states={
            SETTINGS_PANEL: [_st_callback()],
            SETTINGS_ADMIN: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_admin_input,
                ),
                _st_callback(),
            ],
            SETTINGS_CHANNEL: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED) & ~filters.COMMAND,
                    receive_channel_input,
                ),
                _st_callback(),
            ],
            SETTINGS_BTN_LABEL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_button_label_input,
                ),
                _st_callback(),
            ],
        },
        fallbacks=[
            MessageHandler(
                filters.Regex("^(لغو|بازگشت به منوی اصلی)$"),
                cancel_settings,
            ),
            MessageHandler(filters.COMMAND, cancel_settings),
        ],
        allow_reentry=True,
    )


async def _global_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error = context.error
    if isinstance(error, BadRequest) and (
        "query is too old" in str(error).lower()
    ):
        logger.info("callback query منقضی بود: %s", error)
        return

    logger.error("خطای غیرمنتظره", exc_info=error)
    if config.OWNER_ID:
        try:
            await context.bot.send_message(
                config.OWNER_ID,
                (
                    "⚠️ خطای غیرمنتظره در ربات:\n"
                    f"{type(error).__name__}: {error}"
                )[:4000],
            )
        except TelegramError:
            logger.warning("ارسال اطلاع خطا به مالک ممکن نشد")


def build_application() -> Application:
    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(
        MessageHandler(filters.Regex(PANEL_BUTTONS_PATTERN), panel_gate),
        group=-1,
    )

    application.add_handler(CommandHandler("start", start))

    post_builder_conv = _build_post_builder_conversation()
    post_manage_conv = _build_post_manage_conversation()
    stats_conv = _build_stats_conversation()
    settings_conv = _build_settings_conversation()

    application.add_handler(post_builder_conv)
    application.add_handler(post_manage_conv)
    application.add_handler(stats_conv)
    application.add_handler(settings_conv)

    application.add_handler(
        MessageHandler(filters.Regex("^💾 بک‌آپ$"), handle_backup)
    )

    application.add_handler(CallbackQueryHandler(stale_callback_answer))

    register_panels(
        application,
        {
            "post_builder": post_builder_conv,
            "post_manage": post_manage_conv,
            "stats": stats_conv,
            "settings": settings_conv,
        },
    )

    application.add_error_handler(_global_error_handler)

    return application
