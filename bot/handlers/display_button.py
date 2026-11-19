"""پاسخ به کلیک کاربران روی دکمه‌های نمایشی (پاپ‌آپ) و دکمه‌های لایک/نظرسنجی."""

import logging
from typing import Any, Dict, List

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..constants import DEFAULT_POPUP_TEXT
from ..database import get_workspace_db
from ..services.publisher import build_buttons_markup, deserialize_buttons

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


async def handle_react_button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """مدیریت کلیک روی دکمه‌های لایک (شمارنده) و نظرسنجی تک‌انتخابی."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    data = (query.data or "").strip()
    parts = data.split(":")
    user_id = int(user.id)

    # حالت پیش‌نمایش موقت قبل از ذخیره در دیتابیس
    if len(parts) == 3 and parts[1] == "pv":
        btn_id = parts[2]
        pb = context.user_data.get("pb") or {}
        buttons: List[Dict[str, Any]] = pb.get("buttons") or []
        target_btn = next((b for b in buttons if b.get("btn_id") == btn_id), None)
        if not target_btn:
            try:
                await query.answer("👁 پیش‌نمایش دکمه واکنش")
            except TelegramError:
                pass
            return

        pv_votes: set = pb.setdefault("_pv_votes", set())
        if btn_id in pv_votes:
            pv_votes.remove(btn_id)
            toast = "💔 پیش‌نمایش: رأی برداشته شد"
        else:
            if target_btn.get("type") == "vote":
                for b in buttons:
                    if b.get("type") == "vote" and b.get("btn_id"):
                        pv_votes.discard(b["btn_id"])
            pv_votes.add(btn_id)
            toast = "❤️ پیش‌نمایش: رأی ثبت شد (پس از انتشار واقعی شمارش می‌شود)"

        counts = {str(bid): 1 for bid in pv_votes}
        markup = build_buttons_markup(buttons, vote_counts=counts)
        if query.message and markup:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    reply_markup=markup,
                )
            except TelegramError:
                pass
        try:
            await query.answer(toast[:200])
        except TelegramError:
            pass
        return

    scope: str
    ws_id: int
    target_id: int
    btn_id: str
    old_set_id = None

    if (
        len(parts) == 5
        and parts[1] == "old"
        and parts[2].isdigit()
        and parts[3].isdigit()
    ):
        scope = "old"
        ws_id = int(parts[2])
        target_id = int(parts[3])
        old_set_id = target_id
        btn_id = parts[4]
    elif (
        len(parts) == 4
        and parts[1].isdigit()
        and parts[2].isdigit()
    ):
        scope = "post"
        ws_id = int(parts[1])
        target_id = int(parts[2])
        btn_id = parts[3]
    else:
        try:
            await query.answer("⚠️ دکمه نامعتبر است.")
        except TelegramError:
            pass
        return

    try:
        ws_db = get_workspace_db(ws_id)
        if scope == "old":
            raw_json = ws_db.get_old_button_set(target_id)
            buttons = deserialize_buttons(raw_json)
        else:
            post = ws_db.get_post(target_id)
            buttons = deserialize_buttons(post.get("buttons_json")) if post else []

        target_btn = next((b for b in buttons if b.get("btn_id") == btn_id), None)
        if not target_btn:
            await query.answer("⚠️ این دکمه دیگر فعال نیست.")
            return

        exclusive_ids = (
            [
                str(b["btn_id"])
                for b in buttons
                if b.get("type") == "vote" and b.get("btn_id")
            ]
            if target_btn.get("type") == "vote"
            else None
        )

        action, counts = ws_db.toggle_button_vote(
            scope=scope,
            target_id=target_id,
            btn_id=btn_id,
            user_id=user_id,
            exclusive_btn_ids=exclusive_ids,
        )

        new_markup = build_buttons_markup(
            buttons,
            workspace_id=ws_id,
            post_id=None if scope == "old" else target_id,
            vote_counts=counts,
            old_set_id=old_set_id,
        )

        if query.message and new_markup:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    reply_markup=new_markup,
                )
            except TelegramError as exc:
                if "not modified" not in str(exc).lower():
                    logger.debug("به‌روزرسانی دکمه واکنش ناموفق: %s", exc)

        cur_count = counts.get(btn_id, 0)
        label = target_btn.get("text") or "❤️"
        if action == "removed":
            toast = f"💔 رأی شما برداشته شد! ({label}: {cur_count})"
        elif action == "switched":
            toast = f"🔄 رأی شما به «{label}» تغییر کرد! ({cur_count})"
        else:
            toast = f"✅ رأی شما ثبت شد! ({label}: {cur_count})"

        await query.answer(toast[:200])
    except TelegramError:
        logger.debug("خطا در پاسخ به دکمه واکنش", exc_info=True)
    except Exception:
        logger.exception("خطای غیرمنتظره در دکمه واکنش")
