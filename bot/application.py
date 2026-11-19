import asyncio
import html
import logging
import re

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from .config import config
from .constants import (
    OLD_ADD_BTN_TEXT,
    OLD_ADD_BTN_URL,
    OLD_BTN_EDIT_TEXT,
    OLD_BTN_EDIT_VALUE,
    OLD_EDIT_BUTTON,
    OLD_EDIT_LINK,
    OLD_EDIT_PHOTO,
    OLD_EDIT_TEXT,
    OLD_PANEL,
    OLD_TARGET,
    PM_ADD_BTN_TEXT,
    PM_ADD_BTN_URL,
    PM_BTN_EDIT_TEXT,
    PM_BTN_EDIT_VALUE,
    PM_DETAIL,
    PM_EDIT_LINK,
    PM_EDIT_PHOTO,
    PM_EDIT_TEXT,
    PM_LIST,
    PM_SCHED_DAY,
    PM_SCHED_PANEL,
    PM_SCHED_TARGET,
    PM_SCHED_TIME,
    PM_SCHED_YEAR,
    PM_SEARCH,
    PM_SELECT_CHANNEL,
    POST_ADD_BTN_TEXT,
    POST_ADD_BTN_URL,
    POST_BTN_EDIT_TEXT,
    POST_BTN_EDIT_VALUE,
    POST_EDIT_LINK,
    POST_EDIT_TEXT,
    POST_MEDIA_CHOICE,
    POST_PHOTO_LINK,
    POST_PHOTO_UPLOAD,
    POST_PREVIEW,
    POST_SCHED_DAY,
    POST_SCHED_PANEL,
    POST_SCHED_TARGET,
    POST_SCHED_TIME,
    POST_SCHED_YEAR,
    POST_SELECT_CHANNEL,
    POST_TEXT,
    POST_TITLE,
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_BUTTON,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    STATS_MSG_INPUT,
    STATS_VIEW,
    SWITCH_WORKSPACE_BUTTON,
)
from .handlers.backup import handle_backup
from .handlers.base import (
    open_workspace_switcher,
    start,
    workspace_pick_callback,
)
from .handlers.display_button import handle_display_button_callback
from .handlers.old_post_edit import (
    handle_old_edit_callback,
    receive_old_btn_edit_text,
    receive_old_btn_edit_value,
    receive_old_btn_text,
    receive_old_btn_value,
    receive_old_edit_link,
    receive_old_edit_photo,
    receive_old_edit_text,
    receive_old_post_target,
    start_old_post_edit,
)
from .handlers.post_builder import (
    cancel_builder,
    cancel_from_inline,
    confirm_cancel_no,
    confirm_cancel_yes,
    handle_media_choice_callback,
    handle_pb_schedule_callback,
    handle_preview_callback,
    handle_skip_title_callback,
    receive_btn_edit_text,
    receive_btn_edit_value,
    receive_btn_text,
    receive_btn_url,
    receive_edit_link,
    receive_edit_text,
    receive_pb_sched_day,
    receive_pb_sched_time,
    receive_pb_sched_year,
    receive_photo_link,
    receive_photo_upload,
    receive_post_text,
    receive_post_title,
    start_post_builder,
)
from .handlers.post_manage import (
    handle_post_manage_callback,
    receive_pm_btn_edit_text,
    receive_pm_btn_edit_value,
    receive_pm_btn_text,
    receive_pm_btn_url,
    receive_pm_edit_link,
    receive_pm_edit_photo,
    receive_pm_edit_text,
    receive_pm_sched_day,
    receive_pm_sched_time,
    receive_pm_sched_year,
    receive_pm_search,
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
from .handlers.stats import (
    handle_stats_callback,
    receive_stats_user_message,
    show_stats,
)
from .services.panels import (
    PANEL_BUTTONS_PATTERN,
    panel_gate,
    register_panels,
    stale_callback_answer,
)
from .services.scheduler import scheduler_loop

logger = logging.getLogger(__name__)


async def _error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "خطای پیش‌بینی‌نشده هنگام پردازش آپدیت %s: %s",
        getattr(update, "update_id", None),
        context.error,
        exc_info=context.error,
    )
    if config.OWNER_ID is not None:
        try:
            err_text = html.escape(str(context.error)[:500])
            await context.bot.send_message(
                chat_id=config.OWNER_ID,
                text=(
                    "⚠️ <b>خطای غیرمنتظره در POPIC-BOT:</b>\n"
                    f"<code>{err_text}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("ارسال گزارش خطا به ابرمالک ناموفق بود.", exc_info=True)


async def _post_init(application: Application) -> None:
    application.bot_data["_scheduler_task"] = asyncio.create_task(
        scheduler_loop(application)
    )


async def _post_shutdown(application: Application) -> None:
    task = application.bot_data.pop("_scheduler_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_application() -> Application:
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    panel_gate_handler = MessageHandler(
        filters.Regex(PANEL_BUTTONS_PATTERN) & filters.ChatType.PRIVATE,
        panel_gate,
    )
    application.add_handler(panel_gate_handler, group=-1)

    cancel_inline_handlers = [
        CallbackQueryHandler(cancel_from_inline, pattern=r"^cancel_prompt$"),
        CallbackQueryHandler(confirm_cancel_yes, pattern=r"^cancel_yes$"),
        CallbackQueryHandler(confirm_cancel_no, pattern=r"^cancel_no$"),
    ]
    text_input_filter = (
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(PANEL_BUTTONS_PATTERN)
    )
    forward_or_text = (
        (filters.TEXT | filters.FORWARDED)
        & ~filters.COMMAND
        & ~filters.Regex(PANEL_BUTTONS_PATTERN)
    )
    photo_input_filter = (
        (filters.PHOTO | filters.TEXT | filters.Document.ALL)
        & ~filters.COMMAND
        & ~filters.Regex(PANEL_BUTTONS_PATTERN)
    )

    post_builder_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^📝 ساخت پست جدید$") & filters.ChatType.PRIVATE,
                start_post_builder,
            ),
        ],
        states={
            POST_TITLE: [
                MessageHandler(text_input_filter, receive_post_title),
                CallbackQueryHandler(handle_skip_title_callback, pattern=r"^pb:skip_title$"),
                *cancel_inline_handlers,
            ],
            POST_TEXT: [
                MessageHandler(text_input_filter, receive_post_text),
                *cancel_inline_handlers,
            ],
            POST_MEDIA_CHOICE: [
                CallbackQueryHandler(handle_media_choice_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_PHOTO_UPLOAD: [
                MessageHandler(photo_input_filter, receive_photo_upload),
                CallbackQueryHandler(handle_media_choice_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_PHOTO_LINK: [
                MessageHandler(forward_or_text, receive_photo_link),
                CallbackQueryHandler(handle_media_choice_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_PREVIEW: [
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SELECT_CHANNEL: [
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_edit_text),
                CallbackQueryHandler(handle_media_choice_callback, pattern=r"^pb:media_menu$"),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_EDIT_LINK: [
                MessageHandler(forward_or_text, receive_edit_link),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_ADD_BTN_TEXT: [
                MessageHandler(text_input_filter, receive_btn_text),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_ADD_BTN_URL: [
                MessageHandler(forward_or_text, receive_btn_url),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_BTN_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_btn_edit_text),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_BTN_EDIT_VALUE: [
                MessageHandler(forward_or_text, receive_btn_edit_value),
                CallbackQueryHandler(handle_preview_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SCHED_PANEL: [
                CallbackQueryHandler(handle_pb_schedule_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SCHED_DAY: [
                MessageHandler(text_input_filter, receive_pb_sched_day),
                CallbackQueryHandler(handle_pb_schedule_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SCHED_YEAR: [
                MessageHandler(text_input_filter, receive_pb_sched_year),
                CallbackQueryHandler(handle_pb_schedule_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SCHED_TIME: [
                MessageHandler(text_input_filter, receive_pb_sched_time),
                CallbackQueryHandler(handle_pb_schedule_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
            POST_SCHED_TARGET: [
                CallbackQueryHandler(handle_pb_schedule_callback, pattern=r"^pb:"),
                *cancel_inline_handlers,
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_builder),
            MessageHandler(
                filters.Regex(r"^(لغو|بازگشت به منوی اصلی)$"),
                cancel_builder,
            ),
            *cancel_inline_handlers,
        ],
        per_message=False,
        allow_reentry=True,
    )

    post_manage_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^📂 پست‌های ذخیره‌شده$") & filters.ChatType.PRIVATE,
                start_post_manage,
            ),
        ],
        states={
            PM_LIST: [
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SEARCH: [
                MessageHandler(text_input_filter, receive_pm_search),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_DETAIL: [
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SELECT_CHANNEL: [
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_pm_edit_text),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_EDIT_LINK: [
                MessageHandler(forward_or_text, receive_pm_edit_link),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_EDIT_PHOTO: [
                MessageHandler(photo_input_filter, receive_pm_edit_photo),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_ADD_BTN_TEXT: [
                MessageHandler(text_input_filter, receive_pm_btn_text),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_ADD_BTN_URL: [
                MessageHandler(forward_or_text, receive_pm_btn_url),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_BTN_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_pm_btn_edit_text),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_BTN_EDIT_VALUE: [
                MessageHandler(forward_or_text, receive_pm_btn_edit_value),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SCHED_PANEL: [
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SCHED_DAY: [
                MessageHandler(text_input_filter, receive_pm_sched_day),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SCHED_YEAR: [
                MessageHandler(text_input_filter, receive_pm_sched_year),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SCHED_TIME: [
                MessageHandler(text_input_filter, receive_pm_sched_time),
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
            PM_SCHED_TARGET: [
                CallbackQueryHandler(handle_post_manage_callback, pattern=r"^pm:"),
            ],
        },
        fallbacks=[],
        per_message=False,
        allow_reentry=True,
    )

    old_edit_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(OLD_EDIT_BUTTON)}$")
                & filters.ChatType.PRIVATE,
                start_old_post_edit,
            ),
        ],
        states={
            OLD_TARGET: [
                MessageHandler(
                    (filters.TEXT | filters.FORWARDED)
                    & ~filters.COMMAND
                    & ~filters.Regex(PANEL_BUTTONS_PATTERN),
                    receive_old_post_target,
                ),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_PANEL: [
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_old_edit_text),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_EDIT_PHOTO: [
                MessageHandler(photo_input_filter, receive_old_edit_photo),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_EDIT_LINK: [
                MessageHandler(forward_or_text, receive_old_edit_link),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_ADD_BTN_TEXT: [
                MessageHandler(text_input_filter, receive_old_btn_text),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_ADD_BTN_URL: [
                MessageHandler(forward_or_text, receive_old_btn_value),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_BTN_EDIT_TEXT: [
                MessageHandler(text_input_filter, receive_old_btn_edit_text),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
            OLD_BTN_EDIT_VALUE: [
                MessageHandler(forward_or_text, receive_old_btn_edit_value),
                CallbackQueryHandler(handle_old_edit_callback, pattern=r"^old:"),
            ],
        },
        fallbacks=[],
        per_message=False,
        allow_reentry=True,
    )

    settings_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(SETTINGS_BUTTON)}$")
                & filters.ChatType.PRIVATE,
                open_settings,
            ),
        ],
        states={
            SETTINGS_PANEL: [
                CallbackQueryHandler(settings_callback, pattern=r"^st:"),
            ],
            SETTINGS_ADMIN: [
                MessageHandler(forward_or_text, receive_admin_input),
                CallbackQueryHandler(settings_callback, pattern=r"^st:"),
            ],
            SETTINGS_CHANNEL: [
                MessageHandler(forward_or_text, receive_channel_input),
                CallbackQueryHandler(settings_callback, pattern=r"^st:"),
            ],
            SETTINGS_BTN_LABEL: [
                MessageHandler(text_input_filter, receive_button_label_input),
                CallbackQueryHandler(settings_callback, pattern=r"^st:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_settings),
            MessageHandler(
                filters.Regex(r"^(لغو|بازگشت به منوی اصلی)$"),
                cancel_settings,
            ),
        ],
        per_message=False,
        allow_reentry=True,
    )

    stats_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^📊 آمار$") & filters.ChatType.PRIVATE,
                show_stats,
            ),
        ],
        states={
            STATS_VIEW: [
                CallbackQueryHandler(handle_stats_callback, pattern=r"^stats:"),
            ],
            STATS_MSG_INPUT: [
                MessageHandler(text_input_filter, receive_stats_user_message),
                CallbackQueryHandler(handle_stats_callback, pattern=r"^stats:"),
            ],
        },
        fallbacks=[],
        per_message=False,
        allow_reentry=True,
    )

    register_panels(
        application,
        {
            "post_builder": post_builder_conv,
            "post_manage": post_manage_conv,
            "old_edit": old_edit_conv,
            "settings": settings_conv,
            "stats": stats_conv,
        },
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(handle_display_button_callback, pattern=r"^pb_disp:")
    )
    application.add_handler(
        CallbackQueryHandler(workspace_pick_callback, pattern=r"^ws_pick:\d+$")
    )
    application.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(SWITCH_WORKSPACE_BUTTON)}$")
            & filters.ChatType.PRIVATE,
            open_workspace_switcher,
        )
    )
    application.add_handler(post_builder_conv)
    application.add_handler(post_manage_conv)
    application.add_handler(old_edit_conv)
    application.add_handler(settings_conv)
    application.add_handler(stats_conv)

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^💾 بک‌آپ$") & filters.ChatType.PRIVATE,
            handle_backup,
        )
    )
    application.add_handler(
        TypeHandler(Update, stale_callback_answer),
        group=1,
    )
    application.add_error_handler(_error_handler)

    return application
