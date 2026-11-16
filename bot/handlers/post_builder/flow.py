"""فلوی اصلی ساخت پست: متن -> لینک عکس -> پیش‌نمایش زنده -> ارسال به کانال یا پی‌وی."""

import html
import logging
from typing import Any, Dict

from telegram import ReplyKeyboardRemove, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ...config import config
from ...constants import (
    MAX_BUTTON_TEXT_LENGTH,
    MAX_EXTRA_BUTTONS,
    MAX_POST_TEXT_LENGTH,
    POST_ADD_BTN_TEXT,
    POST_ADD_BTN_URL,
    POST_EDIT_LINK,
    POST_EDIT_TEXT,
    POST_PHOTO_LINK,
    POST_PREVIEW,
    POST_SELECT_CHANNEL,
    POST_TEXT,
    Msg,
)
from ...database import db
from ...decorators import admin_only
from ...keyboards import (
    POST_CANCEL_INLINE,
    input_back_keyboard,
    post_buttons_manage_keyboard,
    post_channels_keyboard,
    post_preview_controls_keyboard,
)
from ...services.publisher import (
    send_post_to_chat,
    serialize_buttons,
    serialize_entities,
)
from ...utils import (
    build_telegram_message_link,
    extract_forward_post_link,
    go_to_main_menu,
    normalize_photo_url,
    register_panel_message,
    track_user,
)

from .common import (
    build_control_summary,
    close_question_message,
    delete_answer_message,
    send_or_refresh_preview,
    set_question_message,
)

logger = logging.getLogger(__name__)


def _extract_photo_url_from_message(message) -> tuple[str | None, str]:
    """بررسی فوروارد کانال عمومی یا لینک متنی؛ خروجی: (url_or_None, err_code)."""
    fwd_url, fwd_status = extract_forward_post_link(message)
    if fwd_status == "ok" and fwd_url:
        return fwd_url, ""
    if fwd_status == "private_channel":
        return None, "private"

    raw_text = (message.text or "").strip()
    return normalize_photo_url(raw_text)


@admin_only
async def start_post_builder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    track_user(update)
    context.user_data.clear()

    await update.message.reply_text(
        Msg.POST_START_NOTICE,
        reply_markup=ReplyKeyboardRemove(),
    )

    sent = await update.message.reply_text(
        Msg.POST_ASK_TEXT,
        reply_markup=POST_CANCEL_INLINE,
    )
    register_panel_message(context, sent.message_id)
    set_question_message(context, sent.message_id)
    return POST_TEXT


@admin_only
async def receive_post_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    if not message or not message.text or not message.text.strip():
        sent = await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=POST_CANCEL_INLINE,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_TEXT

    text = message.text
    if len(text) > MAX_POST_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(text)),
            reply_markup=POST_CANCEL_INLINE,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_TEXT

    entities_json = serialize_entities(message.entities)
    context.user_data["pb"] = {
        "text": text,
        "entities_json": entities_json,
        "photo_url": "",
        "show_above_text": config.DEFAULT_SHOW_ABOVE_TEXT,
        "prefer_large_media": config.DEFAULT_PREFER_LARGE_MEDIA,
        "buttons": [],
        "post_id": None,
        "preview_msg_id": None,
        "ctrl_msg_id": None,
    }

    await close_question_message(
        context,
        message.chat_id,
        f"متن پست ({len(text)} کاراکتر)",
    )

    sent = await message.reply_text(
        Msg.POST_ASK_PHOTO_LINK,
        reply_markup=POST_CANCEL_INLINE,
        disable_web_page_preview=True,
    )
    register_panel_message(context, sent.message_id)
    set_question_message(context, sent.message_id)
    return POST_PHOTO_LINK


@admin_only
async def receive_photo_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return await start_post_builder(update, context)

    photo_url, err = _extract_photo_url_from_message(message)
    if not photo_url:
        err_text = (
            Msg.POST_LINK_PRIVATE_WARN
            if err == "private"
            else Msg.POST_LINK_INVALID
        )
        sent = await message.reply_text(
            err_text,
            reply_markup=POST_CANCEL_INLINE,
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_PHOTO_LINK

    pb["photo_url"] = photo_url
    await close_question_message(context, message.chat_id, photo_url)

    post_id = db.create_post(
        post_text=pb["text"],
        entities_json=pb["entities_json"],
        photo_url=pb["photo_url"],
        created_by=update.effective_user.id,
        show_above_text=pb["show_above_text"],
        prefer_large_media=pb["prefer_large_media"],
        buttons_json=serialize_buttons(pb.get("buttons")),
    )
    pb["post_id"] = post_id

    try:
        await send_or_refresh_preview(context, message.chat_id, pb)
    except TelegramError as exc:
        logger.warning("ارسال پیش‌نمایش ناموفق: %s", exc)
        sent = await message.reply_text(
            f"❌ خطا در ساخت پیش‌نمایش با این لینک:\n{exc}\n\n"
            + Msg.POST_ASK_PHOTO_LINK,
            reply_markup=POST_CANCEL_INLINE,
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_PHOTO_LINK

    await delete_answer_message(context, message)
    return POST_PREVIEW


async def _publish_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    pb: Dict[str, Any],
    channel_id: int,
    user_id: int,
) -> tuple[int, str | None]:
    channel = db.get_target_channel(channel_id)
    if not channel:
        return POST_PREVIEW, "❌ کانال مورد نظر پیدا نشد."

    try:
        sent = await send_post_to_chat(
            bot=context.bot,
            chat_id=channel["chat_id"],
            text=pb["text"],
            entities_json=pb["entities_json"],
            photo_url=pb["photo_url"],
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            buttons=pb.get("buttons") or [],
        )
    except TelegramError as exc:
        logger.warning("ارسال به کانال %s ناموفق: %s", channel["chat_id"], exc)
        return POST_PREVIEW, Msg.CHANNEL_SEND_FAILED.format(error=exc)

    post_id = pb.get("post_id")
    if post_id:
        db.mark_post_published(
            post_id=post_id,
            channel_chat_id=channel["chat_id"],
            channel_message_id=sent.message_id,
            channel_title=channel.get("title") or "",
            channel_username=channel.get("username") or "",
        )
        db.record_delivery(
            post_id=post_id,
            target_type="channel",
            target_chat_id=channel["chat_id"],
            message_id=sent.message_id,
            sent_by=user_id,
        )

    link = build_telegram_message_link(
        channel["chat_id"],
        channel.get("username"),
        sent.message_id,
    )
    ch_name = html.escape(channel.get("title") or str(channel["chat_id"]))
    done_text = f"✅ پست با موفقیت در کانال <b>{ch_name}</b> منتشر شد!"
    if link:
        done_text += f"\n🔗 {html.escape(link)}"

    try:
        await query.edit_message_text(done_text, parse_mode="HTML")
    except TelegramError:
        pass

    await go_to_main_menu(context, query.message.chat_id)
    context.user_data.clear()
    return ConversationHandler.END, "✅ منتشر شد!"


@admin_only
async def handle_preview_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    data = (query.data or "").strip()
    pb: Dict[str, Any] = context.user_data.get("pb") or {}

    if not pb:
        await query.answer("🚫 نشست ساخت پست منقضی شده است.", show_alert=True)
        return ConversationHandler.END

    chat_id = query.message.chat_id if query.message else update.effective_user.id

    if data == "pb:send_pv":
        try:
            sent = await send_post_to_chat(
                bot=context.bot,
                chat_id=chat_id,
                text=pb["text"],
                entities_json=pb["entities_json"],
                photo_url=pb["photo_url"],
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                buttons=pb.get("buttons") or [],
            )
        except TelegramError as exc:
            await query.answer(f"❌ ارسال ناموفق: {exc}", show_alert=True)
            return POST_PREVIEW

        if pb.get("post_id"):
            db.record_delivery(
                post_id=pb["post_id"],
                target_type="pv",
                target_chat_id=chat_id,
                message_id=sent.message_id,
                sent_by=update.effective_user.id,
            )
        await query.answer(
            "✅ پست خالص در پی‌وی ارسال شد — می‌توانید فوروارد یا کپی کنید.",
            show_alert=True,
        )
        return POST_PREVIEW

    if data == "pb:send_ch":
        channels = db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return POST_PREVIEW

        if len(channels) == 1:
            next_state, alert_text = await _publish_to_channel(
                context, query, pb, channels[0]["id"], update.effective_user.id
            )
            await query.answer(
                alert_text or "",
                show_alert=bool(alert_text and next_state != ConversationHandler.END),
            )
            return next_state

        await query.answer()
        await query.edit_message_text(
            "📢 کانال مقصد برای انتشار پست را انتخاب کن:",
            reply_markup=post_channels_keyboard(
                channels,
                callback_prefix="pb:pub:",
                back_callback="pb:back_preview",
            ),
        )
        return POST_SELECT_CHANNEL

    if data.startswith("pb:pub:"):
        raw_id = data.split(":", 2)[2]
        if not raw_id.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_SELECT_CHANNEL
        next_state, alert_text = await _publish_to_channel(
            context, query, pb, int(raw_id), update.effective_user.id
        )
        await query.answer(
            alert_text or "",
            show_alert=bool(alert_text and next_state != ConversationHandler.END),
        )
        return next_state

    if data == "pb:toggle_pos":
        pb["show_above_text"] = not pb["show_above_text"]
        if pb.get("post_id"):
            db.update_post(
                pb["post_id"], show_above_text=pb["show_above_text"]
            )
        await query.answer()
        await send_or_refresh_preview(context, chat_id, pb)
        return POST_PREVIEW

    if data == "pb:toggle_size":
        pb["prefer_large_media"] = not pb["prefer_large_media"]
        if pb.get("post_id"):
            db.update_post(
                pb["post_id"], prefer_large_media=pb["prefer_large_media"]
            )
        await query.answer()
        await send_or_refresh_preview(context, chat_id, pb)
        return POST_PREVIEW

    if data == "pb:edit_text":
        await query.answer()
        await query.edit_message_text(
            "✏️ متن جدید پست را ارسال کن (تا ۴۰۹۶ کاراکتر):",
            reply_markup=input_back_keyboard("pb:back_preview"),
        )
        return POST_EDIT_TEXT

    if data == "pb:edit_link":
        await query.answer()
        await query.edit_message_text(
            Msg.POST_ASK_PHOTO_LINK,
            reply_markup=input_back_keyboard("pb:back_preview"),
            disable_web_page_preview=True,
        )
        return POST_EDIT_LINK

    if data == "pb:btns":
        await query.answer()
        await query.edit_message_text(
            "🔘 مدیریت دکمه‌های شیشه‌ای اختیاری زیر پست:",
            reply_markup=post_buttons_manage_keyboard(pb.get("buttons") or []),
        )
        return POST_PREVIEW

    if data == "pb:btn_add":
        buttons = pb.get("buttons") or []
        if len(buttons) >= MAX_EXTRA_BUTTONS:
            await query.answer(
                f"❌ حداکثر {MAX_EXTRA_BUTTONS} دکمه مجاز است.",
                show_alert=True,
            )
            return POST_PREVIEW
        await query.answer()
        await query.edit_message_text(
            Msg.POST_ASK_BTN_TEXT.format(default=config.DEFAULT_BUTTON_TEXT),
            reply_markup=input_back_keyboard("pb:btns"),
        )
        return POST_ADD_BTN_TEXT

    if data.startswith("pb:btn_del:"):
        raw_idx = data.split(":", 2)[2]
        buttons = pb.get("buttons") or []
        if raw_idx.isdigit() and 0 <= int(raw_idx) < len(buttons):
            buttons.pop(int(raw_idx))
            if pb.get("post_id"):
                db.update_post(
                    pb["post_id"], buttons_json=serialize_buttons(buttons)
                )
            await query.answer("🗑 دکمه حذف شد.")
            await send_or_refresh_preview(context, chat_id, pb)
            return POST_PREVIEW
        await query.answer(Msg.INVALID_COMMAND, show_alert=True)
        return POST_PREVIEW

    if data == "pb:back_preview":
        await query.answer()
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    if data == "pb:done":
        await query.answer()
        try:
            await query.edit_message_text(Msg.POST_DONE)
        except TelegramError:
            pass
        await go_to_main_menu(context, chat_id)
        context.user_data.clear()
        return ConversationHandler.END

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return POST_PREVIEW


@admin_only
async def receive_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    if not message or not message.text or not message.text.strip():
        await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=input_back_keyboard("pb:back_preview"),
        )
        return POST_EDIT_TEXT

    if len(message.text) > MAX_POST_TEXT_LENGTH:
        await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(message.text)),
            reply_markup=input_back_keyboard("pb:back_preview"),
        )
        return POST_EDIT_TEXT

    pb["text"] = message.text
    pb["entities_json"] = serialize_entities(message.entities)
    if pb.get("post_id"):
        db.update_post(
            pb["post_id"],
            post_text=pb["text"],
            entities_json=pb["entities_json"],
        )

    await send_or_refresh_preview(context, message.chat_id, pb)
    return POST_PREVIEW


@admin_only
async def receive_edit_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    photo_url, err = _extract_photo_url_from_message(message)
    if not photo_url:
        err_text = (
            Msg.POST_LINK_PRIVATE_WARN
            if err == "private"
            else Msg.POST_LINK_INVALID
        )
        await message.reply_text(
            err_text,
            reply_markup=input_back_keyboard("pb:back_preview"),
            disable_web_page_preview=True,
        )
        return POST_EDIT_LINK

    pb["photo_url"] = photo_url
    if pb.get("post_id"):
        db.update_post(pb["post_id"], photo_url=photo_url)

    await send_or_refresh_preview(context, message.chat_id, pb)
    await delete_answer_message(context, message)
    return POST_PREVIEW


@admin_only
async def receive_btn_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    raw = (message.text or "").strip()
    label = config.DEFAULT_BUTTON_TEXT if raw == "-" else raw
    if not 1 <= len(label) <= MAX_BUTTON_TEXT_LENGTH:
        await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard("pb:btns"),
        )
        return POST_ADD_BTN_TEXT

    pb["pending_btn_label"] = label
    sent = await message.reply_text(
        Msg.POST_ASK_BTN_URL.format(label=label),
        reply_markup=input_back_keyboard("pb:btns"),
        disable_web_page_preview=True,
    )
    register_panel_message(context, sent.message_id)
    return POST_ADD_BTN_URL


@admin_only
async def receive_btn_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    url, err = _extract_photo_url_from_message(message)
    # برای دکمهٔ شیشه‌ای، حتی لینک کانال خصوصی هم به‌عنوان URL عادی مجاز است
    if not url and err == "private":
        raw_text = (message.text or "").strip()
        if raw_text.lower().startswith(("t.me/", "telegram.me/")):
            url = f"https://{raw_text}"
        elif raw_text.lower().startswith(("http://", "https://")):
            url = raw_text

    if not url:
        await message.reply_text(
            Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard("pb:btns"),
            disable_web_page_preview=True,
        )
        return POST_ADD_BTN_URL

    label = pb.pop("pending_btn_label", None) or config.DEFAULT_BUTTON_TEXT
    pb.setdefault("buttons", []).append({"text": label, "url": url})
    if pb.get("post_id"):
        db.update_post(
            pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
        )

    await send_or_refresh_preview(context, message.chat_id, pb)
    await delete_answer_message(context, message)
    return POST_PREVIEW
