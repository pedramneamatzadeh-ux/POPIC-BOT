"""باز کردن پنل + dispatcher تنظیمات."""

import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ...config import config
from ...constants import (
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    Msg,
)
from ...database import db
from ...decorators import owner_only
from ...keyboards import (
    settings_admins_keyboard,
    settings_channels_keyboard,
    settings_confirm_keyboard,
    settings_panel_keyboard,
    settings_preview_keyboard,
)
from ...utils import go_to_main_menu, register_panel_message

from .common import (
    _admin_names,
    _admins_text,
    _channels_text,
    _panel_text,
    _preview_text,
    _safe_edit,
    _show_prompt,
    _show_section,
    _store_prompt,
)

logger = logging.getLogger(__name__)


@owner_only
async def open_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data.pop("st_msg", None)
    context.user_data.pop("st_chat", None)

    sent = await update.message.reply_text(
        _panel_text(),
        reply_markup=settings_panel_keyboard(),
    )
    register_panel_message(context, sent.message_id)
    return SETTINGS_PANEL


@owner_only
async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    state, alert_text = await _dispatch_settings(context, query)
    try:
        if alert_text:
            await query.answer(alert_text, show_alert=True)
        else:
            await query.answer()
    except Exception:
        logger.debug("answerCallbackQuery ناموفق بود", exc_info=True)
    return state


async def _dispatch_settings(context, query):
    data = (query.data or "").strip()
    if not data.startswith("st:"):
        return SETTINGS_PANEL, None
    action = data[3:]

    if action in ("panel", "refresh", "cancel"):
        _store_prompt(context, query)
        await _safe_edit(query, _panel_text(), settings_panel_keyboard())
        return SETTINGS_PANEL, None

    if action == "back":
        context.user_data.pop("st_msg", None)
        context.user_data.pop("st_chat", None)
        await _safe_edit(query, "✅ به منوی اصلی بازگشتید.", None)
        if query.message:
            await go_to_main_menu(context, query.message.chat_id)
        return ConversationHandler.END, None

    if action == "admins":
        state = await _show_section(
            context,
            query,
            _admins_text(),
            settings_admins_keyboard(
                list(config.ADMIN_IDS or []),
                config.OWNER_ID,
                names=_admin_names(),
            ),
        )
        return state, None

    if action == "channels":
        state = await _show_section(
            context,
            query,
            _channels_text(),
            settings_channels_keyboard(db.get_target_channels()),
        )
        return state, None

    if action == "preview":
        state = await _show_section(
            context,
            query,
            _preview_text(),
            settings_preview_keyboard(
                config.DEFAULT_SHOW_ABOVE_TEXT,
                config.DEFAULT_PREFER_LARGE_MEDIA,
            ),
        )
        return state, None

    if action == "pv_pos":
        db.set_setting(
            "default_show_above_text",
            not config.DEFAULT_SHOW_ABOVE_TEXT,
        )
        state = await _show_section(
            context,
            query,
            _preview_text(),
            settings_preview_keyboard(
                config.DEFAULT_SHOW_ABOVE_TEXT,
                config.DEFAULT_PREFER_LARGE_MEDIA,
            ),
        )
        return state, None

    if action == "pv_size":
        db.set_setting(
            "default_prefer_large_media",
            not config.DEFAULT_PREFER_LARGE_MEDIA,
        )
        state = await _show_section(
            context,
            query,
            _preview_text(),
            settings_preview_keyboard(
                config.DEFAULT_SHOW_ABOVE_TEXT,
                config.DEFAULT_PREFER_LARGE_MEDIA,
            ),
        )
        return state, None

    if action == "btn_lbl":
        await _show_prompt(
            context,
            query,
            Msg.SET_BTN_LABEL_PROMPT.format(current=config.DEFAULT_BUTTON_TEXT),
        )
        return SETTINGS_BTN_LABEL, None

    if action == "adm_add":
        await _show_prompt(context, query, Msg.ADD_ADMIN_PROMPT)
        return SETTINGS_ADMIN, None

    if action.startswith("adm_del_yes:"):
        raw = action.split(":", 1)[1]
        ids = list(config.ADMIN_IDS or [])
        if raw.isdigit() and int(raw) in ids:
            ids = [item for item in ids if item != int(raw)]
            db.set_setting("admin_ids", ids)
            text = f"{Msg.ADMIN_REMOVED}\n\n{_admins_text()}"
        else:
            text = f"{Msg.ADMIN_ID_INVALID}\n\n{_admins_text()}"
        state = await _show_section(
            context,
            query,
            text,
            settings_admins_keyboard(
                list(config.ADMIN_IDS or []),
                config.OWNER_ID,
                names=_admin_names(),
            ),
        )
        return state, None

    if action.startswith("adm_del:"):
        raw = action.split(":", 1)[1]
        if not raw.isdigit():
            return SETTINGS_PANEL, Msg.ADMIN_ID_INVALID
        await _safe_edit(
            query,
            f"❓ مطمئنی می‌خواهی ادمین {raw} حذف شود؟",
            settings_confirm_keyboard(f"st:adm_del_yes:{raw}", "st:admins"),
        )
        return SETTINGS_PANEL, None

    if action == "ch_add":
        await _show_prompt(context, query, Msg.ADD_CHANNEL_PROMPT)
        return SETTINGS_CHANNEL, None

    if action.startswith("ch_del_yes:"):
        raw = action.split(":", 1)[1]
        if raw.isdigit() and db.delete_target_channel(int(raw)):
            text = f"{Msg.CHANNEL_REMOVED}\n\n{_channels_text()}"
        else:
            text = f"{Msg.CHANNEL_INVALID}\n\n{_channels_text()}"
        state = await _show_section(
            context,
            query,
            text,
            settings_channels_keyboard(db.get_target_channels()),
        )
        return state, None

    if action.startswith("ch_del:"):
        raw = action.split(":", 1)[1]
        ch = db.get_target_channel(int(raw)) if raw.isdigit() else None
        if not ch:
            return SETTINGS_PANEL, Msg.CHANNEL_INVALID
        label = ch.get("title") or str(ch["chat_id"])
        await _safe_edit(
            query,
            f"❓ مطمئنی می‌خواهی کانال «{label}» حذف شود؟",
            settings_confirm_keyboard(f"st:ch_del_yes:{raw}", "st:channels"),
        )
        return SETTINGS_PANEL, None

    return SETTINGS_PANEL, Msg.INVALID_COMMAND
