"""گیرنده‌های ورودی متنی پنل تنظیمات."""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ...config import config
from ...constants import (
    MAX_BUTTON_TEXT_LENGTH,
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


@owner_only
async def receive_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

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

    if config.OWNER_ID is not None and target == config.OWNER_ID:
        await _retry_input(
            update, context, Msg.ADMIN_IS_OWNER, Msg.ADD_ADMIN_PROMPT
        )
        return SETTINGS_ADMIN

    ids = list(config.ADMIN_IDS or [])
    if target in ids:
        await _retry_input(
            update, context, Msg.ADMIN_ALREADY, Msg.ADD_ADMIN_PROMPT
        )
        return SETTINGS_ADMIN

    ids.append(target)
    db.set_setting("admin_ids", ids)

    text = f"{Msg.ADMIN_ADDED} ({target})\n\n{_admins_text()}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        text,
        settings_admins_keyboard(
            list(config.ADMIN_IDS or []),
            config.OWNER_ID,
            names=_admin_names(),
        ),
    )
    return SETTINGS_PANEL


@owner_only
async def receive_channel_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

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
            update, context, error or Msg.CHANNEL_CHECK_FAILED, Msg.ADD_CHANNEL_PROMPT
        )
        return SETTINGS_CHANNEL

    if db.get_target_channel_by_chat_id(chat.id):
        await _retry_input(
            update, context, Msg.CHANNEL_ALREADY, Msg.ADD_CHANNEL_PROMPT
        )
        return SETTINGS_CHANNEL

    db.add_target_channel(
        chat_id=chat.id,
        title=getattr(chat, "title", None) or "",
        username=getattr(chat, "username", None) or "",
        added_by=update.effective_user.id,
    )

    text = f"{Msg.CHANNEL_ADDED}\n\n{_channels_text()}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        text,
        settings_channels_keyboard(db.get_target_channels()),
    )
    return SETTINGS_PANEL


@owner_only
async def receive_button_label_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    cancel_state = await _handle_text_cancel(update, context)
    if cancel_state is not None:
        return cancel_state

    text = (update.message.text or "").strip()
    if not 1 <= len(text) <= MAX_BUTTON_TEXT_LENGTH:
        await _retry_input(
            update,
            context,
            Msg.BTN_LABEL_INVALID,
            Msg.SET_BTN_LABEL_PROMPT.format(current=config.DEFAULT_BUTTON_TEXT),
        )
        return SETTINGS_BTN_LABEL

    db.set_setting("default_button_text", text)

    full = f"{Msg.BTN_LABEL_SAVED}\n\n{_preview_text()}"
    await _edit_by_id(
        context,
        context.user_data.get("st_chat"),
        context.user_data.get("st_msg"),
        full,
        settings_preview_keyboard(
            config.DEFAULT_SHOW_ABOVE_TEXT,
            config.DEFAULT_PREFER_LARGE_MEDIA,
        ),
    )
    return SETTINGS_PANEL
