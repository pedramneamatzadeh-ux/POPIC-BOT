"""هلپرهای مشترک فلوی ساخت پست (R15 پنل + R18 بستن سؤال تایپی)."""

import logging
from typing import Any, Dict

from telegram.error import TelegramError

from ...keyboards import post_preview_controls_keyboard
from ...services.publisher import send_post_to_chat
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
    pos = "بالای متن ⬆️" if pb["show_above_text"] else "پایین متن ⬇️"
    size = "بزرگ 🖼" if pb["prefer_large_media"] else "کوچک 🖼"
    text_len = len(pb.get("text") or "")
    btns_count = len(pb.get("buttons") or [])

    lines = [
        "🎛 <b>کنترل و انتشار پیش‌نمایش</b>",
        f"• طول متن: {text_len} / 4096 کاراکتر",
        f"• لینک عکس: {pb['photo_url']}",
        f"• نمایش عکس: {pos} | اندازه: {size}",
    ]
    if btns_count:
        lines.append(f"• دکمه‌های شیشه‌ای: {btns_count}")
    lines.append("")
    lines.append("یکی از گزینه‌های زیر را انتخاب کن:")
    return "\n".join(lines)


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

    preview_msg = await send_post_to_chat(
        bot=context.bot,
        chat_id=chat_id,
        text=pb["text"],
        entities_json=pb["entities_json"],
        photo_url=pb["photo_url"],
        show_above_text=pb["show_above_text"],
        prefer_large_media=pb["prefer_large_media"],
        buttons=pb.get("buttons") or [],
    )
    pb["preview_msg_id"] = preview_msg.message_id

    ctrl_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=build_control_summary(pb),
        reply_markup=post_preview_controls_keyboard(
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            extra_buttons_count=len(pb.get("buttons") or []),
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    pb["ctrl_msg_id"] = ctrl_msg.message_id
    register_panel_message(context, ctrl_msg.message_id)
