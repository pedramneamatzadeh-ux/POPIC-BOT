"""انحصار پنل‌ها (الگوی P16 + R15)."""

import logging
import re
from typing import Dict, Optional

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ..constants import SETTINGS_BUTTON, SWITCH_WORKSPACE_BUTTON, Msg
from ..utils import PANEL_MSG_KEY

logger = logging.getLogger(__name__)

PANEL_BUTTONS = (
    "📝 ساخت پست جدید",
    "📂 پست‌های ذخیره‌شده",
    "📊 آمار",
    SETTINGS_BUTTON,
    SWITCH_WORKSPACE_BUTTON,
)

PANEL_BUTTONS_PATTERN = (
    "^(" + "|".join(re.escape(t) for t in PANEL_BUTTONS) + ")$"
)

BOT_DATA_KEY = "panel_convs"


def register_panels(
    application,
    mapping: Dict[str, ConversationHandler],
) -> None:
    application.bot_data[BOT_DATA_KEY] = dict(mapping)


def close_panels_for(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    keep: Optional[str] = None,
) -> int:
    mapping = {}
    try:
        bot_data = context.application.bot_data
    except AttributeError:
        bot_data = None
    if bot_data:
        mapping = bot_data.get(BOT_DATA_KEY) or {}

    key = (chat_id, user_id)
    closed = 0
    for name, handler in mapping.items():
        if keep is not None and name == keep:
            continue
        convs = getattr(handler, "_conversations", None)
        if not isinstance(convs, dict):
            continue
        if key in convs:
            del convs[key]
            closed += 1

    if closed:
        logger.info(
            "P16: %d پنل برای chat=%s user=%s بسته شد.",
            closed,
            chat_id,
            user_id,
        )
    return closed


async def panel_gate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    try:
        tracked = context.chat_data.pop(PANEL_MSG_KEY, None) or []
    except (AttributeError, TypeError):
        tracked = []

    for mid in tracked:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=mid,
                text=Msg.PANEL_CLOSED,
                reply_markup=None,
            )
        except Exception:
            pass

    close_panels_for(context, chat.id, user.id)


async def stale_callback_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return
    if (query.data or "").startswith(("pb_disp:", "ws_pick:")):
        return
    try:
        await query.answer("🚫 این پنل بسته شده است.")
    except Exception:
        logger.debug("پاسخ به callback بی‌صاحب ناموفق بود.", exc_info=True)
