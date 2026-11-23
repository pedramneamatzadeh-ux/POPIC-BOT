"""فلوی اصلی ساخت پست: متن -> انتخاب حالت عکس -> پیش‌نمایش زنده -> ارسال به کانال یا پی‌وی."""

import html
import logging
from typing import Any, Callable, Dict, List

from telegram import ReplyKeyboardRemove, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ...constants import (
    DEFAULT_LIKE_TEXT,
    DEFAULT_POPUP_TEXT,
    DEFAULT_VOTE_TEXT,
    MAX_BUTTON_TEXT_LENGTH,
    MAX_CAPTION_LENGTH,
    MAX_COPY_TEXT_LENGTH,
    MAX_EXTRA_BUTTONS,
    MAX_POPUP_TEXT_LENGTH,
    MAX_POST_TEXT_LENGTH,
    MAX_POST_TITLE_LENGTH,
    POST_ADD_BTN_TEXT,
    POST_ADD_BTN_URL,
    POST_BTN_EDIT_TEXT,
    POST_BTN_EDIT_VALUE,
    POST_EDIT_LINK,
    POST_EDIT_TEXT,
    POST_MEDIA_CHOICE,
    POST_PHOTO_LINK,
    POST_PHOTO_UPLOAD,
    POST_PREVIEW,
    POST_SCHED_DAY,
    POST_SCHED_PANEL,
    POST_SCHED_TARGET,
    POST_SCHED_TIME,
    POST_SCHED_YEAR,
    POST_SELECT_CHANNEL,
    POST_TEXT,
    POST_TITLE,
    Msg,
)
from ...decorators import workspace_access
from ...keyboards import (
    POST_CANCEL_INLINE,
    POST_TITLE_INLINE,
    input_back_keyboard,
    main_menu_for,
    post_button_inspector_keyboard,
    post_button_type_keyboard,
    post_buttons_manage_keyboard,
    post_caption_too_long_keyboard,
    post_channels_keyboard,
    post_media_choice_keyboard,
    post_preview_controls_keyboard,
    post_target_picker_keyboard,
)
from ..schedule_ui import (
    handle_schedule_callback,
    handle_schedule_day_input,
    handle_schedule_time_input,
    handle_schedule_year_input,
    init_schedule_state,
    render_schedule_panel,
)
from ...services.button_ui import (
    build_button_inspector_text,
    build_buttons_overview_text,
)
from ...services.publisher import (
    move_button_row,
    move_button_side,
    normalize_buttons,
    send_post_to_chat,
    serialize_buttons,
    serialize_entities,
    split_button_new_row,
)
from ...utils import (
    PANEL_MSG_KEY,
    build_telegram_message_link,
    extract_forward_post_link,
    go_to_main_menu,
    normalize_photo_url,
    register_panel_message,
)

from .common import (
    build_control_summary,
    close_question_message,
    delete_answer_message,
    send_or_refresh_preview,
    set_question_message,
    sync_preview_buttons,
)

logger = logging.getLogger(__name__)


def _extract_photo_url_from_message(message) -> tuple[str | None, str]:
    """بررسی فوروارد کانال عمومی یا لینک متنی؛ خروجی: (url_or_None, err_code)."""
    fwd_url, fwd_status = extract_forward_post_link(message)
    if fwd_status == "ok" and fwd_url:
        return fwd_url, ""
    if fwd_status == "private_channel":
        return None, "private"

    raw_text = (getattr(message, "text", None) or "").strip()
    return normalize_photo_url(raw_text)


def _extract_button_url(message) -> str | None:
    url, err = _extract_photo_url_from_message(message)
    if not url and err == "private":
        raw_text = (getattr(message, "text", None) or "").strip()
        if raw_text.lower().startswith(("t.me/", "telegram.me/")):
            url = f"https://{raw_text}"
        elif raw_text.lower().startswith(("http://", "https://")):
            url = raw_text
    return url


def _sync_pb_target_names(pb: Dict[str, Any], channels: List[Dict[str, Any]]) -> None:
    id_set = {int(x) for x in (pb.get("target_chat_ids") or [])}
    names: List[str] = []
    for ch in channels:
        cid = int(ch["chat_id"])
        if cid in id_set:
            names.append(
                ch.get("title")
                or (f"@{ch['username']}" if ch.get("username") else str(cid))
            )
    pb["target_chat_names"] = names


def _ensure_post_saved(ws_db: Any, pb: Dict[str, Any], user_id: int) -> int:
    """پست را فقط هنگام انتشار، تحویل در پی‌وی یا ذخیره و پایان در دیتابیس ثبت می‌کند."""
    post_id = pb.get("post_id")
    buttons_json = serialize_buttons(pb.get("buttons"))
    media_type = pb.get("media_type") or "link"
    photo_url = pb.get("photo_url") or ""
    photo_file_id = pb.get("photo_file_id") or ""

    if post_id is None:
        post_id = ws_db.create_post(
            post_text=pb["text"],
            entities_json=pb["entities_json"],
            photo_url=photo_url,
            created_by=user_id,
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            buttons_json=buttons_json,
            media_type=media_type,
            photo_file_id=photo_file_id,
            title=pb.get("title") or "",
        )
        pb["post_id"] = post_id
    else:
        ws_db.update_post(
            post_id,
            title=pb.get("title") or "",
            post_text=pb["text"],
            entities_json=pb["entities_json"],
            media_type=media_type,
            photo_url=photo_url,
            photo_file_id=photo_file_id,
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            buttons_json=buttons_json,
        )
    return int(post_id)


def _value_prompt_for_type(btn_type: str, label: str) -> str:
    if btn_type == "popup":
        return Msg.POST_ASK_BTN_POPUP.format(
            label=label, default_popup=DEFAULT_POPUP_TEXT
        )
    if btn_type == "copy":
        return Msg.POST_ASK_BTN_COPY.format(label=label)
    return Msg.POST_ASK_BTN_URL.format(label=label)


def _parse_button_value(
    btn_type: str,
    message: Any,
) -> tuple[Dict[str, str] | None, str | None]:
    if btn_type == "popup":
        raw = (getattr(message, "text", None) or "").strip()
        text = DEFAULT_POPUP_TEXT if raw == "-" else raw
        if not 1 <= len(text) <= MAX_POPUP_TEXT_LENGTH:
            return None, Msg.BTN_POPUP_INVALID
        return {"popup_text": text}, None

    if btn_type == "copy":
        raw = (getattr(message, "text", None) or "").strip()
        if not 1 <= len(raw) <= MAX_COPY_TEXT_LENGTH:
            return None, Msg.BTN_COPY_INVALID
        return {"copy_text": raw}, None

    url = _extract_button_url(message)
    if not url:
        return None, Msg.POST_LINK_INVALID
    return {"url": url}, None


async def _show_buttons_manager(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    pb: Dict[str, Any],
    message_id: int | None = None,
) -> None:
    buttons = normalize_buttons(pb.get("buttons") or [])
    pb["buttons"] = buttons
    text = build_buttons_overview_text(buttons)
    markup = post_buttons_manage_keyboard(buttons)
    mid = message_id or pb.get("ctrl_msg_id")
    if mid:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=mid,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
            return
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        parse_mode="HTML",
    )
    pb["ctrl_msg_id"] = sent.message_id
    register_panel_message(context, sent.message_id)


async def _show_button_inspector(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    pb: Dict[str, Any],
    idx: int,
    message_id: int | None = None,
) -> None:
    buttons = normalize_buttons(pb.get("buttons") or [])
    pb["buttons"] = buttons
    if not 0 <= idx < len(buttons):
        await _show_buttons_manager(context, chat_id, pb, message_id=message_id)
        return
    text = build_button_inspector_text(buttons, idx)
    markup = post_button_inspector_keyboard(buttons, idx, prefix="pb", back_callback="pb:btns")
    mid = message_id or pb.get("ctrl_msg_id")
    if mid:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=mid,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    pb["ctrl_msg_id"] = sent.message_id
    register_panel_message(context, sent.message_id)


def _apply_layout_transform(
    pb: Dict[str, Any],
    idx: int,
    transform: Callable[[List[Dict[str, Any]], int], List[Dict[str, Any]]],
) -> int:
    buttons = normalize_buttons(pb.get("buttons") or [])
    if not 0 <= idx < len(buttons):
        return idx
    buttons[idx]["_track"] = True
    moved = transform(buttons, idx)
    new_idx = idx
    for i, b in enumerate(moved):
        if b.pop("_track", False):
            new_idx = i
            break
    pb["buttons"] = normalize_buttons(moved)
    return new_idx


@workspace_access
async def start_post_builder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    ws_id = context.user_data["_ws_id"]
    ws_role = context.user_data["_ws_role"]
    ws_db = context.user_data["_ws_db"]
    context.user_data.clear()
    context.user_data["_ws_id"] = ws_id
    context.user_data["_ws_role"] = ws_role
    context.user_data["_ws_db"] = ws_db

    await update.message.reply_text(
        Msg.POST_START_NOTICE,
        reply_markup=ReplyKeyboardRemove(),
    )

    sent = await update.message.reply_text(
        Msg.POST_ASK_TITLE,
        reply_markup=POST_TITLE_INLINE,
    )
    register_panel_message(context, sent.message_id)
    set_question_message(context, sent.message_id)
    return POST_TITLE


@workspace_access
async def receive_post_title(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    raw = " ".join((getattr(message, "text", None) or "").split())
    if not raw or len(raw) > MAX_POST_TITLE_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TITLE_INVALID,
            reply_markup=POST_TITLE_INLINE,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_TITLE

    context.user_data["pb_title"] = raw
    await close_question_message(context, message.chat_id, f"نام پست: «{raw}»")

    sent = await message.reply_text(
        Msg.POST_ASK_TEXT,
        reply_markup=POST_CANCEL_INLINE,
    )
    register_panel_message(context, sent.message_id)
    set_question_message(context, sent.message_id)
    return POST_TEXT


@workspace_access
async def handle_skip_title_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["pb_title"] = ""
    await query.edit_message_text(
        Msg.POST_ASK_TEXT,
        reply_markup=POST_CANCEL_INLINE,
    )
    if query.message:
        set_question_message(context, query.message.message_id)
    return POST_TEXT


@workspace_access
async def receive_post_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    ws_id = context.user_data["_ws_id"]

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
        "ws_id": ws_id,
        "title": context.user_data.pop("pb_title", "") or "",
        "text": text,
        "entities_json": entities_json,
        "media_type": "none",
        "photo_url": "",
        "photo_file_id": "",
        "show_above_text": ws_db.get_show_above_text(),
        "prefer_large_media": ws_db.get_prefer_large_media(),
        "buttons": [],
        "post_id": None,
        "preview_msg_id": None,
        "ctrl_msg_id": None,
        "scheduled_at": None,
        "target_chat_ids": [],
        "target_chat_names": [],
    }

    await close_question_message(
        context,
        message.chat_id,
        f"متن پست ({len(text)} کاراکتر)",
    )

    sent = await message.reply_text(
        Msg.POST_ASK_MEDIA_CHOICE,
        reply_markup=post_media_choice_keyboard(),
    )
    register_panel_message(context, sent.message_id)
    set_question_message(context, sent.message_id)
    return POST_MEDIA_CHOICE


@workspace_access
async def handle_media_choice_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_db = context.user_data["_ws_db"]
    data = (query.data or "").strip()
    pb: Dict[str, Any] = context.user_data.get("pb") or {}

    if not pb:
        await query.answer("🚫 نشست ساخت پست منقضی شده است.", show_alert=True)
        return ConversationHandler.END

    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else None

    if data == "pb:media:none":
        pb["media_type"] = "none"
        pb["photo_url"] = ""
        pb["photo_file_id"] = ""
        if pb.get("post_id"):
            ws_db.update_post(
                pb["post_id"],
                media_type="none",
                photo_url="",
                photo_file_id="",
            )
        await query.answer("✅ پست بدون عکس آماده می‌شود.")
        await close_question_message(context, chat_id, "بدون عکس")
        await send_or_refresh_preview(context, chat_id, pb)
        return POST_PREVIEW

    if data == "pb:media:photo":
        text_len = len(pb.get("text") or "")
        if text_len > MAX_CAPTION_LENGTH:
            await query.answer()
            await query.edit_message_text(
                Msg.POST_CAPTION_TOO_LONG.format(length=text_len),
                reply_markup=post_caption_too_long_keyboard(),
            )
            return POST_MEDIA_CHOICE

        await query.answer()
        if msg_id:
            set_question_message(context, msg_id)
        await query.edit_message_text(
            Msg.POST_ASK_PHOTO_UPLOAD,
            reply_markup=input_back_keyboard("pb:media_menu"),
        )
        return POST_PHOTO_UPLOAD

    if data == "pb:media:link":
        await query.answer()
        if msg_id:
            set_question_message(context, msg_id)
        await query.edit_message_text(
            Msg.POST_ASK_PHOTO_LINK,
            reply_markup=input_back_keyboard("pb:media_menu"),
            disable_web_page_preview=True,
        )
        return POST_PHOTO_LINK

    if data == "pb:media_menu":
        await query.answer()
        back_cb = "pb:back_preview" if pb.get("preview_msg_id") else None
        if msg_id and not pb.get("preview_msg_id"):
            set_question_message(context, msg_id)
        await query.edit_message_text(
            Msg.POST_ASK_MEDIA_CHOICE,
            reply_markup=post_media_choice_keyboard(back_callback=back_cb),
        )
        return POST_MEDIA_CHOICE

    if data == "pb:edit_text_pre":
        await query.answer()
        await query.edit_message_text(
            "✏️ متن کوتاه‌تر (برای عکس عادی حداکثر ۱۰۲۴ کاراکتر) را ارسال کن:",
            reply_markup=input_back_keyboard("pb:media_menu"),
        )
        return POST_EDIT_TEXT

    if data == "pb:back_preview":
        await query.answer()
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
                media_type=pb.get("media_type") or "link",
                is_scheduled=bool(pb.get("scheduled_at")),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return POST_MEDIA_CHOICE


@workspace_access
async def receive_photo_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return await start_post_builder(update, context)

    photos = getattr(message, "photo", None)
    if not photos:
        sent = await message.reply_text(
            Msg.POST_PHOTO_REQUIRED,
            reply_markup=input_back_keyboard("pb:media_menu"),
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_PHOTO_UPLOAD

    file_id = photos[-1].file_id
    pb["media_type"] = "photo"
    pb["photo_file_id"] = file_id
    pb["photo_url"] = ""
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"],
            media_type="photo",
            photo_file_id=file_id,
            photo_url="",
        )

    await close_question_message(context, message.chat_id, "عکس عادی")
    try:
        await send_or_refresh_preview(context, message.chat_id, pb)
    except TelegramError as exc:
        logger.warning("ارسال پیش‌نمایش عکس عادی ناموفق: %s", exc)
        sent = await message.reply_text(
            f"❌ خطا در ساخت پیش‌نمایش با این عکس:\n{exc}",
            reply_markup=input_back_keyboard("pb:media_menu"),
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_PHOTO_UPLOAD

    await delete_answer_message(context, message)
    return POST_PREVIEW


@workspace_access
async def receive_photo_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
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
            reply_markup=input_back_keyboard("pb:media_menu"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_PHOTO_LINK

    pb["media_type"] = "link"
    pb["photo_url"] = photo_url
    pb["photo_file_id"] = ""
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"],
            media_type="link",
            photo_url=photo_url,
            photo_file_id="",
        )

    await close_question_message(context, message.chat_id, photo_url)

    try:
        await send_or_refresh_preview(context, message.chat_id, pb)
    except TelegramError as exc:
        logger.warning("ارسال پیش‌نمایش ناموفق: %s", exc)
        sent = await message.reply_text(
            f"❌ خطا در ساخت پیش‌نمایش با این لینک:\n{exc}\n\n"
            + Msg.POST_ASK_PHOTO_LINK,
            reply_markup=input_back_keyboard("pb:media_menu"),
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
    ws_db = context.user_data["_ws_db"]
    ws_id = context.user_data["_ws_id"]
    channel = ws_db.get_target_channel(channel_id)
    if not channel:
        return POST_PREVIEW, "❌ کانال مورد نظر پیدا نشد."

    was_new = pb.get("post_id") is None
    post_id = _ensure_post_saved(ws_db, pb, user_id)

    try:
        sent = await send_post_to_chat(
            bot=context.bot,
            chat_id=channel["chat_id"],
            text=pb["text"],
            entities_json=pb["entities_json"],
            photo_url=pb.get("photo_url") or "",
            show_above_text=pb["show_above_text"],
            prefer_large_media=pb["prefer_large_media"],
            buttons=pb.get("buttons") or [],
            workspace_id=ws_id,
            post_id=post_id,
            media_type=pb.get("media_type") or "link",
            photo_file_id=pb.get("photo_file_id") or "",
        )
    except TelegramError as exc:
        if was_new:
            ws_db.delete_post(post_id)
            pb["post_id"] = None
        logger.warning("ارسال به کانال %s ناموفق: %s", channel["chat_id"], exc)
        return POST_PREVIEW, Msg.CHANNEL_SEND_FAILED.format(error=exc)

    ws_db.mark_post_published(
        post_id=post_id,
        channel_chat_id=channel["chat_id"],
        channel_message_id=sent.message_id,
        channel_title=channel.get("title") or "",
        channel_username=channel.get("username") or "",
    )
    ws_db.record_delivery(
        post_id=post_id,
        target_type="channel",
        target_chat_id=channel["chat_id"],
        message_id=sent.message_id,
        sent_by=user_id,
        media_type=pb.get("media_type") or "link",
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

    await go_to_main_menu(context, query.message.chat_id, user_id=user_id)
    context.user_data.clear()
    return ConversationHandler.END, "✅ منتشر شد!"


@workspace_access
async def handle_preview_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_db = context.user_data["_ws_db"]
    data = (query.data or "").strip()
    pb: Dict[str, Any] = context.user_data.get("pb") or {}

    if not pb:
        await query.answer("🚫 نشست ساخت پست منقضی شده است.", show_alert=True)
        return ConversationHandler.END

    chat_id = query.message.chat_id if query.message else user_id
    ctrl_mid = query.message.message_id if query.message else pb.get("ctrl_msg_id")

    if data == "pb:send_pv":
        post_id = _ensure_post_saved(ws_db, pb, user_id)
        media_type = pb.get("media_type") or "link"
        if pb.get("preview_msg_id"):
            await sync_preview_buttons(context, chat_id, pb)
            ws_db.record_delivery(
                post_id=post_id,
                target_type="pv",
                target_chat_id=chat_id,
                message_id=pb["preview_msg_id"],
                sent_by=user_id,
                media_type=media_type,
            )
        else:
            try:
                sent = await send_post_to_chat(
                    bot=context.bot,
                    chat_id=chat_id,
                    text=pb["text"],
                    entities_json=pb["entities_json"],
                    photo_url=pb.get("photo_url") or "",
                    show_above_text=pb["show_above_text"],
                    prefer_large_media=pb["prefer_large_media"],
                    buttons=pb.get("buttons") or [],
                    workspace_id=pb.get("ws_id"),
                    post_id=post_id,
                    media_type=media_type,
                    photo_file_id=pb.get("photo_file_id") or "",
                )
                ws_db.record_delivery(
                    post_id=post_id,
                    target_type="pv",
                    target_chat_id=chat_id,
                    message_id=sent.message_id,
                    sent_by=user_id,
                    media_type=media_type,
                )
            except TelegramError as exc:
                await query.answer(f"❌ ارسال ناموفق: {exc}", show_alert=True)
                return POST_PREVIEW

        await query.answer("✅ پست در پی‌وی آماده است!")
        target_ctrl_mid = pb.get("ctrl_msg_id") or ctrl_mid
        if target_ctrl_mid:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=target_ctrl_mid,
                )
            except TelegramError:
                pass
        try:
            context.chat_data.pop(PANEL_MSG_KEY, None)
        except (AttributeError, TypeError):
            pass

        await context.bot.send_message(
            chat_id=chat_id,
            text=Msg.POST_PV_DELIVERED,
            reply_markup=main_menu_for(user_id),
        )
        context.user_data.clear()
        return ConversationHandler.END

    if data == "pb:send_ch":
        channels = ws_db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return POST_PREVIEW

        if len(channels) == 1:
            next_state, alert_text = await _publish_to_channel(
                context, query, pb, channels[0]["id"], user_id
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
            context, query, pb, int(raw_id), user_id
        )
        await query.answer(
            alert_text or "",
            show_alert=bool(alert_text and next_state != ConversationHandler.END),
        )
        return next_state

    if data == "pb:toggle_pos":
        pb["show_above_text"] = not pb["show_above_text"]
        if pb.get("post_id"):
            ws_db.update_post(
                pb["post_id"], show_above_text=pb["show_above_text"]
            )
        await query.answer()
        await send_or_refresh_preview(context, chat_id, pb)
        return POST_PREVIEW

    if data == "pb:toggle_size":
        pb["prefer_large_media"] = not pb["prefer_large_media"]
        if pb.get("post_id"):
            ws_db.update_post(
                pb["post_id"], prefer_large_media=pb["prefer_large_media"]
            )
        await query.answer()
        await send_or_refresh_preview(context, chat_id, pb)
        return POST_PREVIEW

    if data == "pb:change_media":
        await query.answer()
        await query.edit_message_text(
            Msg.POST_ASK_MEDIA_CHOICE,
            reply_markup=post_media_choice_keyboard(back_callback="pb:back_preview"),
        )
        return POST_MEDIA_CHOICE

    if data == "pb:targets":
        channels = ws_db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return POST_PREVIEW

        pb["_targets_backup"] = list(pb.get("target_chat_ids") or [])
        if not pb.get("target_chat_ids") and len(channels) == 1:
            pb["target_chat_ids"] = [int(channels[0]["chat_id"])]
            _sync_pb_target_names(pb, channels)
        await query.answer()
        await query.edit_message_text(
            "🎯 کانال‌ها یا گروه‌های مقصد را برای این پست انتخاب کن (برای زمان‌بندی کافیست مقصد را انتخاب کرده و سپس دکمهٔ ذخیره را بزنی):",
            reply_markup=post_target_picker_keyboard(
                channels,
                pb.get("target_chat_ids") or [],
                prefix="pb",
                back_callback="pb:back_preview",
            ),
        )
        return POST_SELECT_CHANNEL

    if data.startswith("pb:tg_toggle:"):
        raw = data.split(":", 2)[2]
        if not raw.lstrip("-").isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_SELECT_CHANNEL
        cid = int(raw)
        channels = ws_db.get_target_channels()
        selected = [int(x) for x in (pb.get("target_chat_ids") or [])]
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)
        pb["target_chat_ids"] = selected
        _sync_pb_target_names(pb, channels)
        await query.answer()
        await query.edit_message_text(
            "🎯 کانال‌ها یا گروه‌های مقصد را برای این پست انتخاب کن (برای زمان‌بندی کافیست مقصد را انتخاب کرده و سپس دکمهٔ ذخیره را بزنی):",
            reply_markup=post_target_picker_keyboard(
                channels,
                selected,
                prefix="pb",
                back_callback="pb:back_preview",
            ),
        )
        return POST_SELECT_CHANNEL

    if data == "pb:tg_done":
        selected = [int(x) for x in (pb.get("target_chat_ids") or [])]
        if pb.get("scheduled_at") and not selected:
            await query.answer(
                "⚠️ برای پست زمان‌بندی‌شده حداقل یک مقصد باید انتخاب شود!",
                show_alert=True,
            )
            return POST_SELECT_CHANNEL
        pb.pop("_targets_backup", None)
        channels = ws_db.get_target_channels()
        _sync_pb_target_names(pb, channels)
        if isinstance(pb.get("_sched"), dict):
            pb["_sched"]["targets"] = list(selected)
        if pb.get("post_id") and pb.get("scheduled_at") and selected:
            ws_db.set_post_schedule(pb["post_id"], pb["scheduled_at"], selected)
        await query.answer("✅ مقصدهای پست ثبت شدند.")
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
                media_type=pb.get("media_type") or "link",
                is_scheduled=bool(pb.get("scheduled_at")),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    if data == "pb:sched_clear":
        if pb.get("post_id"):
            ws_db.clear_post_schedule(pb["post_id"])
        pb["scheduled_at"] = None
        if isinstance(pb.get("_sched"), dict):
            pb["_sched"]["has_existing"] = False
        await query.answer("🗑 زمان‌بندی پست حذف شد.")
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
                media_type=pb.get("media_type") or "link",
                is_scheduled=False,
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    if data == "pb:sched":
        await query.answer()
        existing_utc = pb.get("scheduled_at")
        existing_targets = list(pb.get("target_chat_ids") or [])
        if pb.get("post_id"):
            saved = ws_db.get_post(pb["post_id"])
            if saved and saved.get("schedule_status") == "pending":
                existing_utc = saved.get("scheduled_at")
                import json

                try:
                    db_targets = json.loads(saved.get("scheduled_chat_ids") or "[]")
                    if db_targets:
                        existing_targets = db_targets
                except Exception:
                    pass
        pb["_sched"] = init_schedule_state(existing_utc, existing_targets)
        pb["ctrl_msg_id"] = ctrl_mid
        await render_schedule_panel(
            context,
            chat_id,
            pb["_sched"],
            prefix="pb",
            back_callback="pb:back_preview",
            message_id=ctrl_mid,
            existing_utc_iso=existing_utc,
        )
        return POST_SCHED_PANEL

    if data == "pb:edit_text":
        await query.answer()
        max_len = (
            MAX_CAPTION_LENGTH
            if (pb.get("media_type") or "link") == "photo"
            else MAX_POST_TEXT_LENGTH
        )
        await query.edit_message_text(
            f"✏️ متن جدید پست را ارسال کن (تا {max_len} کاراکتر):",
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
        await _show_buttons_manager(context, chat_id, pb, message_id=ctrl_mid)
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
            Msg.POST_ASK_BTN_TYPE,
            reply_markup=post_button_type_keyboard("pb:btn_type:", "pb:btns"),
        )
        return POST_PREVIEW

    if data.startswith("pb:btn_type:"):
        btn_type = data.split(":", 2)[2]
        if btn_type not in ("url", "popup", "copy", "like", "vote"):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        pb["pending_btn_type"] = btn_type
        pb["ctrl_msg_id"] = ctrl_mid
        await query.answer()
        if btn_type == "like":
            prompt_text = Msg.POST_ASK_BTN_LIKE.format(default_like=DEFAULT_LIKE_TEXT)
            pmode = "HTML"
        elif btn_type == "vote":
            prompt_text = Msg.POST_ASK_BTN_VOTE.format(default_vote=DEFAULT_VOTE_TEXT)
            pmode = "HTML"
        else:
            prompt_text = Msg.POST_ASK_BTN_TEXT.format(
                default=ws_db.get_default_button_text()
            )
            pmode = None
        await query.edit_message_text(
            prompt_text,
            reply_markup=input_back_keyboard("pb:btn_add"),
            parse_mode=pmode,
        )
        return POST_ADD_BTN_TEXT

    if data.startswith("pb:btn_ins:"):
        raw_idx = data.split(":", 2)[2]
        if not raw_idx.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        await query.answer()
        await _show_button_inspector(
            context, chat_id, pb, int(raw_idx), message_id=ctrl_mid
        )
        return POST_PREVIEW

    if data.startswith("pb:btn_col:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[2].isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        idx = int(parts[2])
        token = parts[3]
        style_map = {
            "none": None,
            "primary": "primary",
            "success": "success",
            "danger": "danger",
        }
        if token not in style_map:
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        buttons = normalize_buttons(pb.get("buttons") or [])
        if not 0 <= idx < len(buttons):
            await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
            return POST_PREVIEW
        buttons[idx]["style"] = style_map[token]
        pb["buttons"] = buttons
        if pb.get("post_id"):
            ws_db.update_post(pb["post_id"], buttons_json=serialize_buttons(buttons))
        await sync_preview_buttons(context, chat_id, pb)
        await query.answer("🎨 رنگ دکمه به‌روزرسانی شد.")
        await _show_button_inspector(context, chat_id, pb, idx, message_id=ctrl_mid)
        return POST_PREVIEW

    if data.startswith(("pb:btn_row_up:", "pb:btn_row_dn:", "pb:btn_left:", "pb:btn_right:", "pb:btn_split:")):
        parts = data.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        action, idx = parts[1], int(parts[2])
        fn_map = {
            "btn_row_up": lambda bs, i: move_button_row(bs, i, -1),
            "btn_row_dn": lambda bs, i: move_button_row(bs, i, 1),
            "btn_left": lambda bs, i: move_button_side(bs, i, -1),
            "btn_right": lambda bs, i: move_button_side(bs, i, 1),
            "btn_split": split_button_new_row,
        }
        new_idx = _apply_layout_transform(pb, idx, fn_map[action])
        if pb.get("post_id"):
            ws_db.update_post(
                pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
            )
        await sync_preview_buttons(context, chat_id, pb)
        await query.answer("📐 چیدمان به‌روزرسانی شد.")
        await _show_button_inspector(context, chat_id, pb, new_idx, message_id=ctrl_mid)
        return POST_PREVIEW

    if data.startswith("pb:btn_etxt:"):
        raw_idx = data.split(":", 2)[2]
        buttons = normalize_buttons(pb.get("buttons") or [])
        if not raw_idx.isdigit() or not 0 <= int(raw_idx) < len(buttons):
            await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
            return POST_PREVIEW
        idx = int(raw_idx)
        pb["editing_btn_idx"] = idx
        pb["ctrl_msg_id"] = ctrl_mid
        await query.answer()
        await query.edit_message_text(
            f"✏️ متن جدید دکمه (فعلی: «{buttons[idx]['text']}») را ارسال کن:",
            reply_markup=input_back_keyboard(f"pb:btn_ins:{idx}"),
        )
        return POST_BTN_EDIT_TEXT

    if data.startswith("pb:btn_eval:"):
        raw_idx = data.split(":", 2)[2]
        buttons = normalize_buttons(pb.get("buttons") or [])
        if not raw_idx.isdigit() or not 0 <= int(raw_idx) < len(buttons):
            await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
            return POST_PREVIEW
        idx = int(raw_idx)
        await query.answer()
        await query.edit_message_text(
            f"🔀 نوع جدید دکمه «{buttons[idx]['text']}» را انتخاب کن:",
            reply_markup=post_button_type_keyboard(
                f"pb:btn_ctype:{idx}:",
                f"pb:btn_ins:{idx}",
            ),
        )
        return POST_PREVIEW

    if data.startswith("pb:btn_ctype:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[2].isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        idx = int(parts[2])
        btn_type = parts[3]
        buttons = normalize_buttons(pb.get("buttons") or [])
        if not 0 <= idx < len(buttons) or btn_type not in ("url", "popup", "copy", "like", "vote"):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return POST_PREVIEW
        if btn_type in ("like", "vote"):
            cur = buttons[idx]
            cur.pop("url", None)
            cur.pop("popup_text", None)
            cur.pop("copy_text", None)
            cur["type"] = btn_type
            pb["buttons"] = normalize_buttons(buttons)
            if pb.get("post_id"):
                ws_db.update_post(
                    pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
                )
            await sync_preview_buttons(context, chat_id, pb)
            await query.answer("✅ نوع دکمه تغییر کرد.")
            await _show_button_inspector(context, chat_id, pb, idx, message_id=ctrl_mid)
            return POST_PREVIEW
        pb["editing_btn_idx"] = idx
        pb["editing_btn_type"] = btn_type
        pb["ctrl_msg_id"] = ctrl_mid
        await query.answer()
        await query.edit_message_text(
            _value_prompt_for_type(btn_type, buttons[idx]["text"]),
            reply_markup=input_back_keyboard(f"pb:btn_ins:{idx}"),
            disable_web_page_preview=True,
        )
        return POST_BTN_EDIT_VALUE

    if data.startswith("pb:btn_del:"):
        raw_idx = data.split(":", 2)[2]
        buttons = normalize_buttons(pb.get("buttons") or [])
        if raw_idx.isdigit() and 0 <= int(raw_idx) < len(buttons):
            buttons.pop(int(raw_idx))
            pb["buttons"] = normalize_buttons(buttons)
            if pb.get("post_id"):
                ws_db.update_post(
                    pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
                )
            await sync_preview_buttons(context, chat_id, pb)
            await query.answer("🗑 دکمه حذف شد.")
            await _show_buttons_manager(context, chat_id, pb, message_id=ctrl_mid)
            return POST_PREVIEW
        await query.answer(Msg.INVALID_COMMAND, show_alert=True)
        return POST_PREVIEW

    if data == "pb:back_preview":
        if "_targets_backup" in pb:
            pb["target_chat_ids"] = pb.pop("_targets_backup")
            _sync_pb_target_names(pb, ws_db.get_target_channels())
        await query.answer()
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
                media_type=pb.get("media_type") or "link",
                is_scheduled=bool(pb.get("scheduled_at")),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    if data == "pb:done":
        post_id = _ensure_post_saved(ws_db, pb, user_id)
        if pb.get("scheduled_at") and pb.get("target_chat_ids"):
            ws_db.set_post_schedule(post_id, pb["scheduled_at"], pb["target_chat_ids"])
        await sync_preview_buttons(context, chat_id, pb)
        await query.answer()
        done_msg = (
            "✅ پست ذخیره و زمان‌بندی شد! در زمان تعیین‌شده به‌صورت خودکار به مقصد(ها) ارسال می‌شود و نسخهٔ پیش‌نمایش در پی‌وی باقی ماند."
            if pb.get("scheduled_at")
            else Msg.POST_DONE
        )
        try:
            await query.edit_message_text(done_msg)
        except TelegramError:
            pass
        await go_to_main_menu(context, chat_id, user_id=user_id)
        context.user_data.clear()
        return ConversationHandler.END

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return POST_PREVIEW


@workspace_access
async def receive_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    is_pre_preview = not pb.get("preview_msg_id")
    back_cb = "pb:media_menu" if is_pre_preview else "pb:back_preview"

    if not message or not message.text or not message.text.strip():
        await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=input_back_keyboard(back_cb),
        )
        return POST_EDIT_TEXT

    if len(message.text) > MAX_POST_TEXT_LENGTH:
        await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(message.text)),
            reply_markup=input_back_keyboard(back_cb),
        )
        return POST_EDIT_TEXT

    if not is_pre_preview and (pb.get("media_type") or "link") == "photo" and len(message.text) > MAX_CAPTION_LENGTH:
        await message.reply_text(
            f"❌ در حالت «عکس عادی» کپشن حداکثر {MAX_CAPTION_LENGTH} کاراکتر است "
            f"(متن شما {len(message.text)} کاراکتر است).",
            reply_markup=input_back_keyboard(back_cb),
        )
        return POST_EDIT_TEXT

    pb["text"] = message.text
    pb["entities_json"] = serialize_entities(message.entities)
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"],
            post_text=pb["text"],
            entities_json=pb["entities_json"],
        )

    if is_pre_preview:
        sent = await message.reply_text(
            Msg.POST_ASK_MEDIA_CHOICE,
            reply_markup=post_media_choice_keyboard(),
        )
        register_panel_message(context, sent.message_id)
        set_question_message(context, sent.message_id)
        return POST_MEDIA_CHOICE

    await send_or_refresh_preview(context, message.chat_id, pb)
    return POST_PREVIEW


@workspace_access
async def receive_edit_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
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

    pb["media_type"] = "link"
    pb["photo_url"] = photo_url
    pb["photo_file_id"] = ""
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"],
            media_type="link",
            photo_url=photo_url,
            photo_file_id="",
        )

    await send_or_refresh_preview(context, message.chat_id, pb)
    await delete_answer_message(context, message)
    return POST_PREVIEW


@workspace_access
async def receive_btn_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    btn_type = pb.get("pending_btn_type") or "url"
    if btn_type == "like":
        default_label = DEFAULT_LIKE_TEXT
    elif btn_type == "vote":
        default_label = DEFAULT_VOTE_TEXT
    else:
        default_label = ws_db.get_default_button_text()
    raw = (message.text or "").strip()
    label = default_label if raw == "-" else raw
    if not 1 <= len(label) <= MAX_BUTTON_TEXT_LENGTH:
        await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard("pb:btns"),
        )
        return POST_ADD_BTN_TEXT

    if btn_type in ("like", "vote"):
        pb.pop("pending_btn_type", None)
        pb.pop("pending_btn_label", None)
        buttons = normalize_buttons(pb.get("buttons") or [])
        next_row = max((b["row"] for b in buttons), default=-1) + 1
        buttons.append(
            {
                "text": label,
                "type": btn_type,
                "style": None,
                "row": next_row,
            }
        )
        pb["buttons"] = normalize_buttons(buttons)
        if pb.get("post_id"):
            ws_db.update_post(
                pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
            )
        await delete_answer_message(context, message)
        await sync_preview_buttons(context, message.chat_id, pb)
        await _show_buttons_manager(context, message.chat_id, pb)
        return POST_PREVIEW

    pb["pending_btn_label"] = label
    await delete_answer_message(context, message)
    ctrl_mid = pb.get("ctrl_msg_id")
    prompt = _value_prompt_for_type(btn_type, label)
    if ctrl_mid:
        try:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=ctrl_mid,
                text=prompt,
                reply_markup=input_back_keyboard("pb:btns"),
                disable_web_page_preview=True,
            )
            return POST_ADD_BTN_URL
        except TelegramError:
            pass

    sent = await message.reply_text(
        prompt,
        reply_markup=input_back_keyboard("pb:btns"),
        disable_web_page_preview=True,
    )
    pb["ctrl_msg_id"] = sent.message_id
    register_panel_message(context, sent.message_id)
    return POST_ADD_BTN_URL


@workspace_access
async def receive_btn_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        return ConversationHandler.END

    btn_type = pb.get("pending_btn_type") or "url"
    extra, err_msg = _parse_button_value(btn_type, message)
    if err_msg or extra is None:
        await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard("pb:btns"),
            disable_web_page_preview=True,
        )
        return POST_ADD_BTN_URL

    label = pb.pop("pending_btn_label", None) or ws_db.get_default_button_text()
    pb.pop("pending_btn_type", None)

    buttons = normalize_buttons(pb.get("buttons") or [])
    next_row = (max((b["row"] for b in buttons), default=-1) + 1)
    new_btn = {
        "text": label,
        "type": btn_type,
        "style": None,
        "row": next_row,
        **extra,
    }
    buttons.append(new_btn)
    pb["buttons"] = normalize_buttons(buttons)
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
        )

    await delete_answer_message(context, message)
    await sync_preview_buttons(context, message.chat_id, pb)
    await _show_buttons_manager(context, message.chat_id, pb)
    return POST_PREVIEW


@workspace_access
async def receive_btn_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    idx = pb.get("editing_btn_idx")
    buttons = normalize_buttons(pb.get("buttons") or [])
    if idx is None or not 0 <= idx < len(buttons):
        return POST_PREVIEW

    new_label = (message.text or "").strip()
    if not 1 <= len(new_label) <= MAX_BUTTON_TEXT_LENGTH:
        await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard(f"pb:btn_ins:{idx}"),
        )
        return POST_BTN_EDIT_TEXT

    buttons[idx]["text"] = new_label
    pb["buttons"] = buttons
    pb.pop("editing_btn_idx", None)
    if pb.get("post_id"):
        ws_db.update_post(pb["post_id"], buttons_json=serialize_buttons(buttons))

    await delete_answer_message(context, message)
    await sync_preview_buttons(context, message.chat_id, pb)
    await _show_button_inspector(context, message.chat_id, pb, idx)
    return POST_PREVIEW


@workspace_access
async def receive_btn_edit_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    idx = pb.get("editing_btn_idx")
    btn_type = pb.get("editing_btn_type")
    buttons = normalize_buttons(pb.get("buttons") or [])
    if idx is None or not 0 <= idx < len(buttons) or btn_type not in ("url", "popup", "copy"):
        return POST_PREVIEW

    extra, err_msg = _parse_button_value(btn_type, message)
    if err_msg or extra is None:
        await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard(f"pb:btn_ins:{idx}"),
            disable_web_page_preview=True,
        )
        return POST_BTN_EDIT_VALUE

    cur = buttons[idx]
    cur.pop("url", None)
    cur.pop("popup_text", None)
    cur.pop("copy_text", None)
    cur["type"] = btn_type
    cur.update(extra)
    pb["buttons"] = normalize_buttons(buttons)
    pb.pop("editing_btn_idx", None)
    pb.pop("editing_btn_type", None)
    if pb.get("post_id"):
        ws_db.update_post(
            pb["post_id"], buttons_json=serialize_buttons(pb["buttons"])
        )

    await delete_answer_message(context, message)
    await sync_preview_buttons(context, message.chat_id, pb)
    await _show_button_inspector(context, message.chat_id, pb, idx)
    return POST_PREVIEW


_PB_SCHED_STATES = {
    "panel": POST_SCHED_PANEL,
    "day": POST_SCHED_DAY,
    "year": POST_SCHED_YEAR,
    "time": POST_SCHED_TIME,
    "target": POST_SCHED_TARGET,
}


@workspace_access
async def handle_pb_schedule_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_db = context.user_data["_ws_db"]
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb:
        await query.answer("🚫 نشست ساخت پست منقضی شده است.", show_alert=True)
        return ConversationHandler.END

    chat_id = query.message.chat_id if query.message else user_id
    ctrl_mid = query.message.message_id if query.message else pb.get("ctrl_msg_id")
    sched = pb.setdefault("_sched", init_schedule_state(pb.get("scheduled_at")))

    async def _back_to_preview() -> int:
        await query.edit_message_text(
            build_control_summary(pb),
            reply_markup=post_preview_controls_keyboard(
                show_above_text=pb["show_above_text"],
                prefer_large_media=pb["prefer_large_media"],
                extra_buttons_count=len(pb.get("buttons") or []),
                media_type=pb.get("media_type") or "link",
                is_scheduled=bool(pb.get("scheduled_at")),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return POST_PREVIEW

    async def _on_save(utc_str: str, chat_ids: List[int]) -> int:
        post_id = _ensure_post_saved(ws_db, pb, user_id)
        ws_db.set_post_schedule(post_id, utc_str, chat_ids)
        pb["scheduled_at"] = utc_str
        pb["target_chat_ids"] = list(chat_ids)
        _sync_pb_target_names(pb, ws_db.get_target_channels())
        sched["has_existing"] = True
        sched["targets"] = list(chat_ids)
        await sync_preview_buttons(context, chat_id, pb)
        return await _back_to_preview()

    async def _on_clear() -> int:
        if pb.get("post_id"):
            ws_db.clear_post_schedule(pb["post_id"])
        pb["scheduled_at"] = None
        sched["has_existing"] = False
        return await _back_to_preview()

    return await handle_schedule_callback(
        context=context,
        query=query,
        ws_db=ws_db,
        chat_id=chat_id,
        msg_id=ctrl_mid,
        data=(query.data or "").strip(),
        prefix="pb",
        sched=sched,
        states=_PB_SCHED_STATES,
        back_callback="pb:back_preview",
        existing_utc_iso=pb.get("scheduled_at"),
        on_save=_on_save,
        on_clear=_on_clear,
        on_back=_back_to_preview,
    )


@workspace_access
async def receive_pb_sched_day(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb or "_sched" not in pb:
        return ConversationHandler.END
    return await handle_schedule_day_input(
        update=update,
        context=context,
        sched=pb["_sched"],
        prefix="pb",
        panel_msg_id=pb.get("ctrl_msg_id"),
        back_callback="pb:back_preview",
        states=_PB_SCHED_STATES,
        existing_utc_iso=pb.get("scheduled_at"),
    )


@workspace_access
async def receive_pb_sched_year(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb or "_sched" not in pb:
        return ConversationHandler.END
    return await handle_schedule_year_input(
        update=update,
        context=context,
        sched=pb["_sched"],
        prefix="pb",
        panel_msg_id=pb.get("ctrl_msg_id"),
        back_callback="pb:back_preview",
        states=_PB_SCHED_STATES,
        existing_utc_iso=pb.get("scheduled_at"),
    )


@workspace_access
async def receive_pb_sched_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pb: Dict[str, Any] = context.user_data.get("pb") or {}
    if not pb or "_sched" not in pb:
        return ConversationHandler.END
    return await handle_schedule_time_input(
        update=update,
        context=context,
        sched=pb["_sched"],
        prefix="pb",
        panel_msg_id=pb.get("ctrl_msg_id"),
        back_callback="pb:back_preview",
        states=_PB_SCHED_STATES,
        existing_utc_iso=pb.get("scheduled_at"),
    )

