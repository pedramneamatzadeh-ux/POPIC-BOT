"""باز کردن پنل + dispatcher تنظیمات فضای کاری."""

import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ...constants import (
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    Msg,
)
from ...database import get_workspace_db, registry
from ...decorators import workspace_owner_only
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


@workspace_owner_only
async def open_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    owner_id = update.effective_user.id
    context.user_data.pop("st_msg", None)
    context.user_data.pop("st_chat", None)

    sent = await update.message.reply_text(
        _panel_text(owner_id),
        reply_markup=settings_panel_keyboard(),
    )
    register_panel_message(context, sent.message_id)
    return SETTINGS_PANEL


@workspace_owner_only
async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    owner_id = update.effective_user.id
    state, alert_text = await _dispatch_settings(context, query, owner_id)
    try:
        if alert_text:
            await query.answer(alert_text, show_alert=True)
        else:
            await query.answer()
    except Exception:
        logger.debug("answerCallbackQuery ناموفق بود", exc_info=True)
    return state


async def _dispatch_settings(context, query, owner_id: int):
    data = (query.data or "").strip()
    if not data.startswith("st:"):
        return SETTINGS_PANEL, None
    action = data[3:]
    ws_db = get_workspace_db(owner_id)

    if action in ("panel", "refresh", "cancel"):
        _store_prompt(context, query)
        await _safe_edit(query, _panel_text(owner_id), settings_panel_keyboard())
        return SETTINGS_PANEL, None

    if action == "back":
        context.user_data.pop("st_msg", None)
        context.user_data.pop("st_chat", None)
        await _safe_edit(query, "✅ به منوی اصلی بازگشتید.", None)
        if query.message:
            await go_to_main_menu(context, query.message.chat_id, user_id=owner_id)
        return ConversationHandler.END, None

    if action == "admins":
        state = await _show_section(
            context,
            query,
            _admins_text(owner_id),
            settings_admins_keyboard(
                registry.get_workspace_admins(owner_id),
                owner_id,
                names=_admin_names(owner_id),
            ),
        )
        return state, None

    if action == "channels":
        state = await _show_section(
            context,
            query,
            _channels_text(owner_id),
            settings_channels_keyboard(ws_db.get_target_channels()),
        )
        return state, None

    if action == "preview":
        state = await _show_section(
            context,
            query,
            _preview_text(owner_id),
            settings_preview_keyboard(
                ws_db.get_show_above_text(),
                ws_db.get_prefer_large_media(),
            ),
        )
        return state, None

    if action == "pv_pos":
        ws_db.set_setting(
            "default_show_above_text",
            not ws_db.get_show_above_text(),
        )
        state = await _show_section(
            context,
            query,
            _preview_text(owner_id),
            settings_preview_keyboard(
                ws_db.get_show_above_text(),
                ws_db.get_prefer_large_media(),
            ),
        )
        return state, None

    if action == "pv_size":
        ws_db.set_setting(
            "default_prefer_large_media",
            not ws_db.get_prefer_large_media(),
        )
        state = await _show_section(
            context,
            query,
            _preview_text(owner_id),
            settings_preview_keyboard(
                ws_db.get_show_above_text(),
                ws_db.get_prefer_large_media(),
            ),
        )
        return state, None

    if action == "btn_lbl":
        await _show_prompt(
            context,
            query,
            Msg.SET_BTN_LABEL_PROMPT.format(
                current=ws_db.get_default_button_text()
            ),
        )
        return SETTINGS_BTN_LABEL, None

    if action == "adm_add":
        await _show_prompt(context, query, Msg.ADD_ADMIN_PROMPT)
        return SETTINGS_ADMIN, None

    if action.startswith("adm_del_yes:"):
        raw = action.split(":", 1)[1]
        if raw.isdigit() and registry.remove_workspace_admin(owner_id, int(raw)):
            text = f"{Msg.ADMIN_REMOVED}\n\n{_admins_text(owner_id)}"
        else:
            text = f"{Msg.ADMIN_ID_INVALID}\n\n{_admins_text(owner_id)}"
        state = await _show_section(
            context,
            query,
            text,
            settings_admins_keyboard(
                registry.get_workspace_admins(owner_id),
                owner_id,
                names=_admin_names(owner_id),
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
        if raw.isdigit() and ws_db.delete_target_channel(int(raw)):
            text = f"{Msg.CHANNEL_REMOVED}\n\n{_channels_text(owner_id)}"
        else:
            text = f"{Msg.CHANNEL_INVALID}\n\n{_channels_text(owner_id)}"
        state = await _show_section(
            context,
            query,
            text,
            settings_channels_keyboard(ws_db.get_target_channels()),
        )
        return state, None

    if action.startswith("ch_del:"):
        raw = action.split(":", 1)[1]
        ch = ws_db.get_target_channel(int(raw)) if raw.isdigit() else None
        if not ch:
            return SETTINGS_PANEL, Msg.CHANNEL_INVALID
        label = ch.get("title") or str(ch["chat_id"])
        await _safe_edit(
            query,
            f"❓ مطمئنی می‌خواهی مقصد «{label}» حذف شود؟",
            settings_confirm_keyboard(f"st:ch_del_yes:{raw}", "st:channels"),
        )
        return SETTINGS_PANEL, None

    return SETTINGS_PANEL, Msg.INVALID_COMMAND
