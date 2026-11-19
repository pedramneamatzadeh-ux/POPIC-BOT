"""هلپرهای مشترک فلوی ساخت پست (R15 پنل + R18 بستن سؤال تایپی)."""

import html
import logging
from typing import Any, Dict

from telegram.error import TelegramError

from ...keyboards import post_preview_controls_keyboard
from ...services.publisher import build_buttons_markup, send_post_to_chat
from ...services.scheduler import fmt_schedule_tehran
from ...utils import PANEL_MSG_KEY, register_panel_message

logger = logging.getLogger(__name__)

QUESTION_MID_KEY = "_pb_question_mid"


def set_question_message(context: Any, message_id) -> None:
    if not message_id:
        return
    try:
        context.user_data[QUESTION_MID_KEY] = message_id
    except (AttributeError, TypeError):
        pass


def clear_question_message(context: Any) -> None:
    try:
        context.user_data.pop(QUESTION_MID_KEY, None)
    except (AttributeError, TypeError):
        pass


async def close_question_message(
    context: Any,
    chat_id: int,
    answer_display: str,
) -> None:
    try:
        mid = context.user_data.pop(QUESTION_MID_KEY, None)
    except (AttributeError, TypeError):
        mid = None
    if not mid:
        return

    try:
        lst = context.chat_data.get(PANEL_MSG_KEY)
        if lst and mid in lst:
            lst.remove(mid)
    except (AttributeError, TypeError):
        pass

    edit = getattr(getattr(context, "bot", None), "edit_message_text", None)
    if edit is None:
        return
    try:
        await edit(
            chat_id=chat_id,
            message_id=mid,
            text=f"✅ ثبت شد: {answer_display}",
            reply_markup=None,
        )
    except TelegramError:
        pass


async def delete_answer_message(context: Any, message: Any) -> None:
    if not message:
        return
    dm = getattr(getattr(context, "bot", None), "delete_message", None)
    if dm is None:
        return
    try:
        await dm(chat_id=message.chat_id, message_id=message.message_id)
    except TelegramError:
        pass


def build_control_summary(pb: Dict[str, Any]) -> str:
    media_type = pb.get("media_type") or "link"
    text_len = len(pb.get("text") or "")
    btns_count = len(pb.get("buttons") or [])
    max_len = 1024 if media_type == "photo" else 4096

    lines = [
        "🎛 <b>کنترل و انتشار پیش‌نمایش</b>",
        f"• طول متن: {text_len} / {max_len} کاراکتر",
    ]
    if pb.get("title"):
        lines.insert(1, f"• نام پست: <b>{html.escape(pb['title'])}</b>")
    if media_type == "none":
        lines.append("• وضعیت تصویر: بدون عکس")
    elif media_type == "photo":
        lines.append("• وضعیت تصویر: 📷 عکس عادی (روی پیام)")
    else:
        pos = "بالای متن ⬆️" if pb["show_above_text"] else "پایین متن ⬇️"
        size = "بزرگ 🖼" if pb["prefer_large_media"] else "کوچک 🖼"
        lines.append(f"• لینک عکس: {html.escape(pb.get('photo_url') or '')}")
        lines.append(f"• نمایش عکس: {pos} | اندازه: {size}")

    if pb.get("scheduled_at"):
        lines.append(f"• ⏰ زمان‌بندی: <b>{html.escape(fmt_schedule_tehran(pb['scheduled_at']))}</b>")

    if btns_count:
        lines.append(f"• دکمه‌های شیشه‌ای: {btns_count}")
    lines.append("")
    lines.append("یکی از گزینه‌های زیر را انتخاب کن:")
    return "\n".join(lines)


async def sync_preview_buttons(
    context: Any,
    chat_id: int,
    pb: Dict[str, Any],
) -> None:
    """دکمه‌های شیشه‌ای پیام پیش‌نمایش را بدون ارسال مجدد پیام به‌روزرسانی می‌کند."""
    preview_mid = pb.get("preview_msg_id")
    if not preview_mid:
        return
    edit_markup = getattr(
        getattr(context, "bot", None),
        "edit_message_reply_markup",
        None,
    )
    if edit_markup is None:
        return
    markup = build_buttons_markup(
        pb.get("buttons") or [],
        workspace_id=pb.get("ws_id"),
        post_id=pb.get("post_id"),
    )
    try:
        await edit_markup(
            chat_id=chat_id,
            message_id=preview_mid,
            reply_markup=markup,
        )
    except TelegramError:
        pass


async def send_or_refresh_preview(
    context: Any,
    chat_id: int,
    pb: Dict[str, Any],
) -> None:
    """پست پیش‌نمایش + پیام کنترل را می‌فرستد (پیش‌نمایش قبلی را پاک می‌کند)."""
    old_preview = pb.get("preview_msg_id")
    old_ctrl = pb.get("ctrl_msg_id")
    for mid in (old_preview, old_ctrl):
        if mid:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except TelegramError:
                pass

    media_type = pb.get("media_type") or "link"
    preview_msg = await send_post_to_chat(
        bot=context.bot,
        chat_id=chat_id,
        text=pb["text"],
        entities_json=pb["entities_json"],
        photo_url=pb.get("photo_url") or "",
        show_above_text=pb["show_above_text"],
        prefer_large_media=pb["prefer_large_media"],
        buttons=pb.get("buttons") or [],
        workspace_id=pb.get("ws_id"),
        post_id=pb.get("post_id"),
        media_type=media_type,
        photo_file_id=pb.get("photo_file_id") or "",
    )
    pb["preview_msg_id"] = preview_msg.message_id

    ctrl_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=build_control_summary(pb),
        reply_markup=post_preview_controls_keyboard(
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            extra_buttons_count=len(pb.get("buttons") or []),
            media_type=media_type,
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    pb["ctrl_msg_id"] = ctrl_msg.message_id
    register_panel_message(context, ctrl_msg.message_id)
