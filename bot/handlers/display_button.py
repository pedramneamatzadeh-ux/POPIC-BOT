"""پاسخ به کلیک کاربران روی دکمه‌های نمایشی (پاپ‌آپ بدون لینک) در پی‌وی یا کانال."""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..constants import DEFAULT_POPUP_TEXT
from ..database import get_workspace_db
from ..services.publisher import deserialize_buttons

logger = logging.getLogger(__name__)


async def handle_display_button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()
    parts = data.split(":")
    popup_text = DEFAULT_POPUP_TEXT

    if len(parts) == 3 and parts[1] == "pv" and parts[2].isdigit():
        idx = int(parts[2])
        pb = context.user_data.get("pb") or {}
        buttons = pb.get("buttons") or []
        if 0 <= idx < len(buttons):
            popup_text = buttons[idx].get("popup_text") or DEFAULT_POPUP_TEXT
    elif (
        len(parts) == 4
        and parts[1] == "old"
        and parts[2].isdigit()
        and parts[3].isdigit()
    ):
        ws_id = int(parts[2])
        popup_id = int(parts[3])
        try:
            saved = get_workspace_db(ws_id).get_old_popup(popup_id)
            if saved:
                popup_text = saved
        except Exception:
            logger.debug("خطا در خواندن متن دکمه نمایشی قدیمی", exc_info=True)
    elif (
        len(parts) == 4
        and parts[1].isdigit()
        and parts[2].isdigit()
        and parts[3].isdigit()
    ):
        ws_id = int(parts[1])
        post_id = int(parts[2])
        idx = int(parts[3])
        try:
            post = get_workspace_db(ws_id).get_post(post_id)
            if post:
                buttons = deserialize_buttons(post.get("buttons_json"))
                if 0 <= idx < len(buttons):
                    popup_text = buttons[idx].get("popup_text") or DEFAULT_POPUP_TEXT
        except Exception:
            logger.debug("خطا در خواندن متن دکمه نمایشی", exc_info=True)

    try:
        await query.answer(popup_text[:200], show_alert=True)
    except TelegramError:
        logger.debug("پاسخ به دکمه نمایشی ناموفق بود", exc_info=True)
