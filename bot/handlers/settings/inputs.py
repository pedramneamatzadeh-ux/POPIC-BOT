"""گیرنده‌های ورودی متنی پنل تنظیمات فضای کاری."""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ...constants import (
    MAX_BUTTON_TEXT_LENGTH,
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
    settings_preview_keyboard,
)
from ...utils import (
    forwarded_user_id,
    normalize_digits,
    precheck_chat,
    resolve_chat_target,
)

from .common import (
    _admin_names,
    _admins_text,
    _channels_text,
    _edit_by_id,
    _handle_text_cancel,
    _preview_text,
    _retry_input,
)

logger = logging.getLogger(__name__)


@workspace_owner_only
async def receive_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

    owner_id = update.effective_user.id
    message = update.message
    target = forwarded_user_id(message)
    if target is None:
        text = normalize_digits((message.text or "").strip())
        if re.fullmatch(r"\d+", text):
            target = int(text)

    if target is None or target <= 0:
        await _retry_input(
            update, context, Msg.ADMIN_ID_INVALID, Msg.ADD_ADMIN_PROMPT
        )
        return SETTINGS_ADMIN

    if target == owner_id:
        await _retry_input(
            update, context, Msg.ADMIN_IS_OWNER, Msg.ADD_ADMIN_PROMPT
        )
        return SETTINGS_ADMIN

    if registry.is_workspace_admin(owner_id, target):
        await _retry_input(
            update, context, Msg.ADMIN_ALREADY, Msg.ADD_ADMIN_PROMPT
        )
        return SETTINGS_ADMIN

    registry.add_workspace_admin(owner_id, target)

    text = f"{Msg.ADMIN_ADDED} ({target})\n\n{_admins_text(owner_id)}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        text,
        settings_admins_keyboard(
            registry.get_workspace_admins(owner_id),
            owner_id,
            names=_admin_names(owner_id),
        ),
    )
    return SETTINGS_PANEL


@workspace_owner_only
async def receive_channel_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

    owner_id = update.effective_user.id
    ws_db = get_workspace_db(owner_id)
    message = update.message
    target = resolve_chat_target(message)
    if target is None:
        await _retry_input(
            update, context, Msg.CHANNEL_INVALID, Msg.ADD_CHANNEL_PROMPT
        )
        return SETTINGS_CHANNEL

    chat, error = await precheck_chat(context, target)
    if error is not None or chat is None:
        await _retry_input(
            update,
            context,
            error or Msg.CHANNEL_CHECK_FAILED,
            Msg.ADD_CHANNEL_PROMPT,
        )
        return SETTINGS_CHANNEL

    if ws_db.get_target_channel_by_chat_id(chat.id):
        await _retry_input(
            update, context, Msg.CHANNEL_ALREADY, Msg.ADD_CHANNEL_PROMPT
        )
        return SETTINGS_CHANNEL

    ws_db.add_target_channel(
        chat_id=chat.id,
        title=getattr(chat, "title", None) or "",
        username=getattr(chat, "username", None) or "",
        added_by=owner_id,
    )

    text = f"{Msg.CHANNEL_ADDED}\n\n{_channels_text(owner_id)}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        text,
        settings_channels_keyboard(ws_db.get_target_channels()),
    )
    return SETTINGS_PANEL


@workspace_owner_only
async def receive_button_label_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

    owner_id = update.effective_user.id
    ws_db = get_workspace_db(owner_id)
    text = (update.message.text or "").strip()
    if not 1 <= len(text) <= MAX_BUTTON_TEXT_LENGTH:
        await _retry_input(
            update,
            context,
            Msg.BTN_LABEL_INVALID,
            Msg.SET_BTN_LABEL_PROMPT.format(
                current=ws_db.get_default_button_text()
            ),
        )
        return SETTINGS_BTN_LABEL

    ws_db.set_setting("default_button_text", text)

    full = f"{Msg.BTN_LABEL_SAVED}\n\n{_preview_text(owner_id)}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        full,
        settings_preview_keyboard(
            ws_db.get_show_above_text(),
            ws_db.get_prefer_large_media(),
        ),
    )
    return SETTINGS_PANEL
