"""ویرایش پست‌های قدیمی کانال یا گروه (پست‌های ارسالی خودِ ربات)."""

import html
import logging
from typing import Any, Callable, Dict, List

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ..constants import (
    DEFAULT_LIKE_TEXT,
    DEFAULT_POPUP_TEXT,
    DEFAULT_VOTE_TEXT,
    MAX_BUTTON_TEXT_LENGTH,
    MAX_CAPTION_LENGTH,
    MAX_COPY_TEXT_LENGTH,
    MAX_EXTRA_BUTTONS,
    MAX_POPUP_TEXT_LENGTH,
    MAX_POST_TEXT_LENGTH,
    OLD_ADD_BTN_TEXT,
    OLD_ADD_BTN_URL,
    OLD_BTN_EDIT_TEXT,
    OLD_BTN_EDIT_VALUE,
    OLD_EDIT_LINK,
    OLD_EDIT_PHOTO,
    OLD_EDIT_TEXT,
    OLD_PANEL,
    OLD_TARGET,
    Msg,
)
from ..decorators import workspace_access
from ..keyboards import (
    confirm_action_keyboard,
    input_back_keyboard,
    main_menu_for,
    old_post_edit_panel_keyboard,
    post_button_inspector_keyboard,
    post_button_type_keyboard,
    post_buttons_manage_keyboard,
    post_media_choice_keyboard,
)
from ..services.button_ui import (
    build_button_inspector_text,
    build_buttons_overview_text,
)
from ..services.publisher import (
    edit_post_in_chat,
    extract_buttons_from_markup,
    is_media_type_mismatch_error,
    move_button_row,
    move_button_side,
    normalize_buttons,
    send_post_to_chat,
    serialize_buttons,
    serialize_entities,
    split_button_new_row,
)
from ..utils import (
    PANEL_MSG_KEY,
    build_telegram_message_link,
    extract_forward_post_link,
    extract_forwarded_message_target,
    normalize_photo_url,
    parse_telegram_message_link,
    register_panel_message,
    safe_edit_or_send,
    shorten_text,
)

logger = logging.getLogger(__name__)


def _old_panel_text(old: Dict[str, Any]) -> str:
    chat_title = html.escape(old.get("chat_title") or str(old["chat_id"]))
    mid = old["message_id"]
    media_type = old.get("media_type") or "none"
    buttons = old.get("buttons") or []
    text = old.get("text") or ""

    lines = [
        "🛠 <b>ویرایش پست قدیمی کانال/گروه</b>",
        f"• مقصد: <b>{chat_title}</b> (پیام <code>#{mid}</code>)",
    ]
    link = build_telegram_message_link(
        old["chat_id"], old.get("chat_username"), mid
    )
    if link:
        lines.append(f"• لینک پیام: {html.escape(link)}")

    lines.append(
        f"• متن فعلی: {len(text)} کاراکتر"
        + (f" — «{html.escape(shorten_text(text, 70))}»" if text else " (ثبت‌نشده؛ در صورت ویرایش متن جدید وارد کن)")
    )
    if media_type == "none":
        lines.append("• وضعیت عکس: بدون عکس")
    elif media_type == "photo":
        lines.append("• وضعیت عکس: 📷 عکس عادی")
    else:
        pos = "بالای متن ⬆️" if old.get("show_above_text", True) else "پایین متن ⬇️"
        size = "بزرگ 🖼" if old.get("prefer_large_media", True) else "کوچک 🖼"
        lines.append(f"• لینک عکس: {html.escape(old.get('photo_url') or '')}")
        lines.append(f"• پیش‌نمایش: {pos} | {size}")

    lines.append(f"• دکمه‌های شیشه‌ای: {len(buttons)}")
    return "\n".join(lines)


async def _render_old_panel(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    old: Dict[str, Any],
    message_id: int | None = None,
) -> int:
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_old_panel_text(old),
        reply_markup=old_post_edit_panel_keyboard(
            media_type=old.get("media_type") or "none",
            buttons_count=len(old.get("buttons") or []),
        ),
        message_id=message_id,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    old["panel_msg_id"] = mid
    return OLD_PANEL


def _bind_old_popups(ws_db: Any, buttons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bound = normalize_buttons(buttons)
    for b in bound:
        if b.get("type") == "popup" and not b.get("popup_id"):
            b["popup_id"] = ws_db.save_old_popup(
                b.get("popup_text") or DEFAULT_POPUP_TEXT
            )
    return bound


def _prepare_old_buttons_for_publish(
    ws_db: Any,
    old: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], int | None, Dict[str, int] | None]:
    bound = _bind_old_popups(ws_db, old.get("buttons") or [])
    old["buttons"] = bound
    if any(b.get("type") in ("like", "vote") for b in bound):
        set_id = ws_db.save_old_button_set(
            serialize_buttons(bound), set_id=old.get("old_set_id")
        )
        old["old_set_id"] = set_id
        return bound, set_id, ws_db.get_vote_counts("old", set_id)
    return bound, None, None


def _value_prompt_for_type(btn_type: str, label: str) -> str:
    if btn_type == "popup":
        return Msg.POST_ASK_BTN_POPUP.format(
            label=label, default_popup=DEFAULT_POPUP_TEXT
        )
    if btn_type == "copy":
        return Msg.POST_ASK_BTN_COPY.format(label=label)
    return Msg.POST_ASK_BTN_URL.format(label=label)


def _extract_btn_url(message: Any) -> str | None:
    fwd_url, fwd_status = extract_forward_post_link(message)
    if fwd_status == "ok" and fwd_url:
        return fwd_url
    if fwd_status == "private_channel":
        url, err = None, "private"
    else:
        url, err = normalize_photo_url(getattr(message, "text", None))
    if not url and err == "private":
        raw_text = (getattr(message, "text", None) or "").strip()
        if raw_text.lower().startswith(("t.me/", "telegram.me/")):
            url = f"https://{raw_text}"
        elif raw_text.lower().startswith(("http://", "https://")):
            url = raw_text
    return url


def _parse_btn_value(
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
    url = _extract_btn_url(message)
    if not url:
        return None, Msg.POST_LINK_INVALID
    return {"url": url}, None


def _transform_old_buttons(
    old: Dict[str, Any],
    idx: int,
    transform: Callable[[List[Dict[str, Any]], int], List[Dict[str, Any]]],
) -> int | None:
    buttons = normalize_buttons(old.get("buttons") or [])
    if not 0 <= idx < len(buttons):
        return None
    buttons[idx]["_track"] = True
    moved = transform(buttons, idx)
    new_idx = idx
    for i, b in enumerate(moved):
        if b.pop("_track", False):
            new_idx = i
            break
    old["buttons"] = normalize_buttons(moved)
    return new_idx


@workspace_access
async def start_old_post_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["old_edit"] = {}
    sent = await update.message.reply_text(
        Msg.OLD_EDIT_ASK_TARGET,
        reply_markup=input_back_keyboard("old:close"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    register_panel_message(context, sent.message_id)
    context.user_data["old_edit"]["panel_msg_id"] = sent.message_id
    return OLD_TARGET


@workspace_access
async def receive_old_post_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.setdefault("old_edit", {})

    chat_target, msg_id = extract_forwarded_message_target(message)
    is_forward = chat_target is not None and msg_id is not None

    if not is_forward:
        chat_target, msg_id = parse_telegram_message_link(
            getattr(message, "text", None)
        )

    if chat_target is None or not msg_id:
        sent = await message.reply_text(
            Msg.OLD_EDIT_TARGET_INVALID,
            reply_markup=input_back_keyboard("old:close"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_TARGET

    try:
        chat = await context.bot.get_chat(chat_target)
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError:
        sent = await message.reply_text(
            Msg.CHANNEL_CHECK_FAILED,
            reply_markup=input_back_keyboard("old:close"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_TARGET

    if member.status not in ("administrator", "creator"):
        sent = await message.reply_text(
            Msg.CHANNEL_BOT_NOT_ADMIN,
            reply_markup=input_back_keyboard("old:close"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_TARGET

    text = ""
    entities_json = "[]"
    media_type = "none"
    photo_url = ""
    photo_file_id = ""
    show_above_text = True
    prefer_large_media = True
    buttons: List[Dict[str, Any]] = []
    orig_is_photo = None

    if is_forward:
        photos = getattr(message, "photo", None)
        if photos:
            media_type = "photo"
            photo_file_id = photos[-1].file_id
            orig_is_photo = True
            text = getattr(message, "caption", None) or ""
            entities_json = serialize_entities(getattr(message, "caption_entities", None))
        else:
            orig_is_photo = False
            text = getattr(message, "text", None) or ""
            entities_json = serialize_entities(getattr(message, "entities", None))
            lpo = getattr(message, "link_preview_options", None)
            if lpo and getattr(lpo, "url", None):
                media_type = "link"
                photo_url = lpo.url
                show_above_text = bool(getattr(lpo, "show_above_text", True))
                prefer_large_media = bool(getattr(lpo, "prefer_large_media", True))
        buttons = extract_buttons_from_markup(getattr(message, "reply_markup", None))

    old.update(
        {
            "chat_id": int(chat.id),
            "chat_title": getattr(chat, "title", None) or str(chat.id),
            "chat_username": getattr(chat, "username", None) or "",
            "message_id": int(msg_id),
            "text": text,
            "entities_json": entities_json,
            "media_type": media_type,
            "photo_url": photo_url,
            "photo_file_id": photo_file_id,
            "show_above_text": show_above_text,
            "prefer_large_media": prefer_large_media,
            "buttons": buttons,
            "orig_is_photo": orig_is_photo,
        }
    )

    return await _render_old_panel(
        context, message.chat_id, old, message_id=old.get("panel_msg_id")
    )


async def _show_old_buttons_manager(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    old: Dict[str, Any],
    message_id: int | None = None,
) -> int:
    buttons = normalize_buttons(old.get("buttons") or [])
    old["buttons"] = buttons
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=build_buttons_overview_text(
            buttons, header="🔘 دکمه‌های شیشه‌ای پست قدیمی"
        ),
        reply_markup=post_buttons_manage_keyboard(
            buttons,
            add_callback="old:btn_add",
            inspect_prefix="old:btn_ins:",
            back_callback="old:panel",
        ),
        message_id=message_id,
        parse_mode="HTML",
    )
    old["panel_msg_id"] = mid
    return OLD_PANEL


async def _show_old_button_inspector(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    old: Dict[str, Any],
    idx: int,
    message_id: int | None = None,
) -> int:
    buttons = normalize_buttons(old.get("buttons") or [])
    if not 0 <= idx < len(buttons):
        return await _show_old_buttons_manager(context, chat_id, old, message_id=message_id)
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=build_button_inspector_text(buttons, idx),
        reply_markup=post_button_inspector_keyboard(
            buttons,
            idx,
            prefix="old",
            back_callback="old:btns",
        ),
        message_id=message_id,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    old["panel_msg_id"] = mid
    return OLD_PANEL


@workspace_access
async def handle_old_edit_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_db = context.user_data["_ws_db"]
    ws_id = context.user_data["_ws_id"]
    data = (query.data or "").strip()
    old = context.user_data.setdefault("old_edit", {})
    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else old.get("panel_msg_id")

    if data == "old:close":
        await query.answer()
        try:
            await query.edit_message_text("✅ بخش ویرایش پست قدیمی بسته شد.")
        except TelegramError:
            pass
        try:
            context.chat_data.pop(PANEL_MSG_KEY, None)
        except (AttributeError, TypeError):
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=Msg.MAIN_MENU_PROMPT,
            reply_markup=main_menu_for(user_id),
        )
        context.user_data.pop("old_edit", None)
        return ConversationHandler.END

    if not old.get("chat_id"):
        await query.answer("🚫 نشست منقضی شده است.", show_alert=True)
        return ConversationHandler.END

    if data == "old:restart":
        await query.answer()
        old.clear()
        old["panel_msg_id"] = msg_id
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.OLD_EDIT_ASK_TARGET,
            reply_markup=input_back_keyboard("old:close"),
            message_id=msg_id,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return OLD_TARGET

    if data == "old:panel":
        await query.answer()
        return await _render_old_panel(context, chat_id, old, message_id=msg_id)

    if data == "old:edit_text":
        await query.answer()
        max_len = (
            MAX_CAPTION_LENGTH
            if (old.get("media_type") or "none") == "photo"
            else MAX_POST_TEXT_LENGTH
        )
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=f"✏️ متن جدید پست را ارسال کن (تا {max_len} کاراکتر):",
            reply_markup=input_back_keyboard("old:panel"),
            message_id=msg_id,
        )
        return OLD_EDIT_TEXT

    if data == "old:media_menu":
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_MEDIA_CHOICE,
            reply_markup=post_media_choice_keyboard(
                prefix="old", back_callback="old:panel", include_cancel=False
            ),
            message_id=msg_id,
        )
        return OLD_PANEL

    if data == "old:media:none":
        old["media_type"] = "none"
        old["photo_url"] = ""
        old["photo_file_id"] = ""
        await query.answer("✅ حالت تصویر به «بدون عکس» تغییر یافت.")
        return await _render_old_panel(context, chat_id, old, message_id=msg_id)

    if data == "old:media:photo":
        text_len = len(old.get("text") or "")
        if text_len > MAX_CAPTION_LENGTH:
            await query.answer(
                f"⚠️ متن فعلی {text_len} کاراکتر است؛ حداکثر کپشن عکس عادی ۱۰۲۴ کاراکتر است!",
                show_alert=True,
            )
            return OLD_PANEL
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_PHOTO_UPLOAD,
            reply_markup=input_back_keyboard("old:media_menu"),
            message_id=msg_id,
        )
        return OLD_EDIT_PHOTO

    if data == "old:media:link":
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_PHOTO_LINK,
            reply_markup=input_back_keyboard("old:media_menu"),
            message_id=msg_id,
            disable_web_page_preview=True,
        )
        return OLD_EDIT_LINK

    if data == "old:toggle_pos":
        old["show_above_text"] = not bool(old.get("show_above_text", True))
        await query.answer("✅ جای پیش‌نمایش تغییر کرد.")
        return await _render_old_panel(context, chat_id, old, message_id=msg_id)

    if data == "old:toggle_size":
        old["prefer_large_media"] = not bool(old.get("prefer_large_media", True))
        await query.answer("✅ اندازه پیش‌نمایش تغییر کرد.")
        return await _render_old_panel(context, chat_id, old, message_id=msg_id)

    if data == "old:btns":
        await query.answer()
        return await _show_old_buttons_manager(context, chat_id, old, message_id=msg_id)

    if data == "old:btn_add":
        buttons = old.get("buttons") or []
        if len(buttons) >= MAX_EXTRA_BUTTONS:
            await query.answer(
                f"❌ حداکثر {MAX_EXTRA_BUTTONS} دکمه مجاز است.",
                show_alert=True,
            )
            return OLD_PANEL
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_BTN_TYPE,
            reply_markup=post_button_type_keyboard("old:btn_type:", "old:btns"),
            message_id=msg_id,
        )
        return OLD_PANEL

    if data.startswith("old:btn_type:"):
        btn_type = data.split(":", 2)[2]
        if btn_type not in ("url", "popup", "copy", "like", "vote"):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return OLD_PANEL
        old["pending_btn_type"] = btn_type
        old["panel_msg_id"] = msg_id
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
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=prompt_text,
            reply_markup=input_back_keyboard("old:btns"),
            message_id=msg_id,
            parse_mode=pmode,
        )
        return OLD_ADD_BTN_TEXT

    if data.startswith("old:btn_ins:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return OLD_PANEL
        await query.answer()
        return await _show_old_button_inspector(
            context, chat_id, old, int(raw), message_id=msg_id
        )

    parts = data.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        action = parts[1]
        idx = int(parts[2])
        buttons = normalize_buttons(old.get("buttons") or [])
        if not 0 <= idx < len(buttons):
            await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
            return await _show_old_buttons_manager(context, chat_id, old, message_id=msg_id)

        if action == "btn_col" and len(parts) == 4:
            token = parts[3]
            if token not in ("none", "primary", "success", "danger"):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return OLD_PANEL
            buttons[idx]["style"] = None if token == "none" else token
            old["buttons"] = buttons
            await query.answer("🎨 رنگ دکمه تغییر کرد.")
            return await _show_old_button_inspector(
                context, chat_id, old, idx, message_id=msg_id
            )

        if action in ("btn_row_up", "btn_row_dn", "btn_left", "btn_right", "btn_split"):
            if action == "btn_row_up":
                new_idx = _transform_old_buttons(old, idx, lambda b, i: move_button_row(b, i, -1))
            elif action == "btn_row_dn":
                new_idx = _transform_old_buttons(old, idx, lambda b, i: move_button_row(b, i, 1))
            elif action == "btn_left":
                new_idx = _transform_old_buttons(old, idx, lambda b, i: move_button_side(b, i, -1))
            elif action == "btn_right":
                new_idx = _transform_old_buttons(old, idx, lambda b, i: move_button_side(b, i, 1))
            else:
                new_idx = _transform_old_buttons(old, idx, split_button_new_row)
            await query.answer("✅ چیدمان به‌روزرسانی شد.")
            return await _show_old_button_inspector(
                context,
                chat_id,
                old,
                idx if new_idx is None else new_idx,
                message_id=msg_id,
            )

        if action == "btn_etxt":
            old["editing_btn_idx"] = idx
            old["panel_msg_id"] = msg_id
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=f"✏️ متن جدید برای دکمه «{buttons[idx]['text']}» را بفرست:",
                reply_markup=input_back_keyboard(f"old:btn_ins:{idx}"),
                message_id=msg_id,
            )
            return OLD_BTN_EDIT_TEXT

        if action == "btn_eval":
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.POST_ASK_BTN_TYPE,
                reply_markup=post_button_type_keyboard(
                    select_prefix=f"old:btn_ctype:{idx}:",
                    back_callback=f"old:btn_ins:{idx}",
                ),
                message_id=msg_id,
            )
            return OLD_PANEL

        if action == "btn_ctype" and len(parts) == 4:
            new_type = parts[3]
            if new_type not in ("url", "popup", "copy", "like", "vote"):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return OLD_PANEL
            if new_type in ("like", "vote"):
                cur = buttons[idx]
                cur.pop("url", None)
                cur.pop("popup_text", None)
                cur.pop("copy_text", None)
                cur["type"] = new_type
                old["buttons"] = normalize_buttons(buttons)
                await query.answer("✅ نوع دکمه تغییر کرد.")
                return await _show_old_button_inspector(
                    context, chat_id, old, idx, message_id=msg_id
                )
            old["editing_btn_idx"] = idx
            old["editing_btn_type"] = new_type
            old["panel_msg_id"] = msg_id
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=_value_prompt_for_type(new_type, buttons[idx]["text"]),
                reply_markup=input_back_keyboard(f"old:btn_ins:{idx}"),
                message_id=msg_id,
                disable_web_page_preview=True,
            )
            return OLD_BTN_EDIT_VALUE

        if action == "btn_del":
            buttons.pop(idx)
            old["buttons"] = normalize_buttons(buttons)
            await query.answer("🗑 دکمه حذف شد.")
            return await _show_old_buttons_manager(context, chat_id, old, message_id=msg_id)

    if data == "old:apply":
        if not (old.get("text") or "").strip():
            await query.answer(
                "⚠️ متن پست خالی است! ابتدا «✏️ ویرایش متن / کپشن» را بزن و متن پست را وارد کن.",
                show_alert=True,
            )
            return OLD_PANEL

        media_type = old.get("media_type") or "none"
        orig_is_photo = old.get("orig_is_photo")
        if orig_is_photo is not None and bool(orig_is_photo) != (media_type == "photo"):
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.MEDIA_TYPE_CHANGE_CONFIRM,
                reply_markup=confirm_action_keyboard(
                    "✅ بله، حذف و ارسال مجدد",
                    "old:apply_replace",
                    "❌ انصراف",
                    "old:panel",
                ),
                message_id=msg_id,
            )
            return OLD_PANEL

        bound_btns, old_set_id, vote_counts = _prepare_old_buttons_for_publish(ws_db, old)
        try:
            await edit_post_in_chat(
                bot=context.bot,
                chat_id=old["chat_id"],
                message_id=old["message_id"],
                text=old["text"],
                entities_json=old.get("entities_json"),
                photo_url=old.get("photo_url") or "",
                show_above_text=bool(old.get("show_above_text", True)),
                prefer_large_media=bool(old.get("prefer_large_media", True)),
                buttons=bound_btns,
                workspace_id=ws_id,
                media_type=media_type,
                photo_file_id=old.get("photo_file_id") or "",
                vote_counts=vote_counts,
                old_set_id=old_set_id,
            )
            await query.answer("✅ تغییرات روی پیام کانال/گروه اعمال شد!", show_alert=True)
            return await _render_old_panel(context, chat_id, old, message_id=msg_id)
        except TelegramError as exc:
            if is_media_type_mismatch_error(exc):
                await query.answer()
                await safe_edit_or_send(
                    context=context,
                    chat_id=chat_id,
                    text=Msg.MEDIA_TYPE_CHANGE_CONFIRM,
                    reply_markup=confirm_action_keyboard(
                        "✅ بله، حذف و ارسال مجدد",
                        "old:apply_replace",
                        "❌ انصراف",
                        "old:panel",
                    ),
                    message_id=msg_id,
                )
                return OLD_PANEL
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.OLD_EDIT_NOT_OWNED_BY_BOT.format(error=html.escape(str(exc))),
                reply_markup=input_back_keyboard("old:panel"),
                message_id=msg_id,
                parse_mode="HTML",
            )
            return OLD_PANEL

    if data == "old:apply_replace":
        if not (old.get("text") or "").strip():
            await query.answer("⚠️ متن پست خالی است!", show_alert=True)
            return OLD_PANEL

        media_type = old.get("media_type") or "none"
        bound_btns, old_set_id, vote_counts = _prepare_old_buttons_for_publish(ws_db, old)
        try:
            await context.bot.delete_message(
                chat_id=old["chat_id"],
                message_id=old["message_id"],
            )
        except TelegramError:
            pass

        try:
            sent = await send_post_to_chat(
                bot=context.bot,
                chat_id=old["chat_id"],
                text=old["text"],
                entities_json=old.get("entities_json"),
                photo_url=old.get("photo_url") or "",
                show_above_text=bool(old.get("show_above_text", True)),
                prefer_large_media=bool(old.get("prefer_large_media", True)),
                buttons=bound_btns,
                workspace_id=ws_id,
                media_type=media_type,
                photo_file_id=old.get("photo_file_id") or "",
                vote_counts=vote_counts,
                old_set_id=old_set_id,
            )
        except TelegramError as exc:
            await query.answer(f"❌ ارسال مجدد ناموفق: {exc}", show_alert=True)
            return await _render_old_panel(context, chat_id, old, message_id=msg_id)

        old["message_id"] = int(sent.message_id)
        old["orig_is_photo"] = media_type == "photo"
        await query.answer("✅ پیام قبلی حذف و نسخه جدید ارسال شد!", show_alert=True)
        return await _render_old_panel(context, chat_id, old, message_id=msg_id)

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return OLD_PANEL


@workspace_access
async def receive_old_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.get("old_edit") or {}
    if not old.get("chat_id"):
        return ConversationHandler.END

    if not message or not message.text or not message.text.strip():
        sent = await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=input_back_keyboard("old:panel"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_EDIT_TEXT

    if len(message.text) > MAX_POST_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(message.text)),
            reply_markup=input_back_keyboard("old:panel"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_EDIT_TEXT

    if (old.get("media_type") or "none") == "photo" and len(message.text) > MAX_CAPTION_LENGTH:
        sent = await message.reply_text(
            f"❌ در حالت «عکس عادی» کپشن حداکثر {MAX_CAPTION_LENGTH} کاراکتر است.",
            reply_markup=input_back_keyboard("old:panel"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_EDIT_TEXT

    old["text"] = message.text
    old["entities_json"] = serialize_entities(message.entities)
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_old_panel(
        context, message.chat_id, old, message_id=old.get("panel_msg_id")
    )


@workspace_access
async def receive_old_edit_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.get("old_edit") or {}
    if not old.get("chat_id"):
        return ConversationHandler.END

    photos = getattr(message, "photo", None)
    if not photos:
        sent = await message.reply_text(
            Msg.POST_PHOTO_REQUIRED,
            reply_markup=input_back_keyboard("old:media_menu"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_EDIT_PHOTO

    old["media_type"] = "photo"
    old["photo_file_id"] = photos[-1].file_id
    old["photo_url"] = ""
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_old_panel(
        context, message.chat_id, old, message_id=old.get("panel_msg_id")
    )


@workspace_access
async def receive_old_edit_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.get("old_edit") or {}
    if not old.get("chat_id"):
        return ConversationHandler.END

    fwd_url, fwd_status = extract_forward_post_link(message)
    if fwd_status == "ok" and fwd_url:
        photo_url, err = fwd_url, ""
    elif fwd_status == "private_channel":
        photo_url, err = None, "private"
    else:
        photo_url, err = normalize_photo_url(getattr(message, "text", None))

    if not photo_url:
        err_text = (
            Msg.POST_LINK_PRIVATE_WARN if err == "private" else Msg.POST_LINK_INVALID
        )
        sent = await message.reply_text(
            err_text,
            reply_markup=input_back_keyboard("old:media_menu"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return OLD_EDIT_LINK

    old["media_type"] = "link"
    old["photo_url"] = photo_url
    old["photo_file_id"] = ""
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_old_panel(
        context, message.chat_id, old, message_id=old.get("panel_msg_id")
    )


@workspace_access
async def receive_old_btn_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    old = context.user_data.get("old_edit") or {}
    if not old.get("chat_id"):
        return ConversationHandler.END

    btn_type = old.get("pending_btn_type") or "url"
    if btn_type == "like":
        default_label = DEFAULT_LIKE_TEXT
    elif btn_type == "vote":
        default_label = DEFAULT_VOTE_TEXT
    else:
        default_label = ws_db.get_default_button_text()
    raw = (getattr(message, "text", None) or "").strip()
    label = default_label if raw == "-" else raw
    if not 1 <= len(label) <= MAX_BUTTON_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard("old:btns"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_ADD_BTN_TEXT

    if btn_type in ("like", "vote"):
        old.pop("pending_btn_type", None)
        old.pop("pending_btn_label", None)
        buttons = normalize_buttons(old.get("buttons") or [])
        next_row = max((b["row"] for b in buttons), default=-1) + 1
        buttons.append(
            {
                "text": label,
                "type": btn_type,
                "style": None,
                "row": next_row,
            }
        )
        old["buttons"] = normalize_buttons(buttons)
        try:
            await message.delete()
        except TelegramError:
            pass
        return await _show_old_buttons_manager(
            context, message.chat_id, old, message_id=old.get("panel_msg_id")
        )

    old["pending_btn_label"] = label
    try:
        await message.delete()
    except TelegramError:
        pass
    old["panel_msg_id"] = await safe_edit_or_send(
        context=context,
        chat_id=message.chat_id,
        text=_value_prompt_for_type(btn_type, label),
        reply_markup=input_back_keyboard("old:btns"),
        message_id=old.get("panel_msg_id"),
        disable_web_page_preview=True,
    )
    return OLD_ADD_BTN_URL


@workspace_access
async def receive_old_btn_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    old = context.user_data.get("old_edit") or {}
    if not old.get("chat_id"):
        return ConversationHandler.END

    btn_type = old.get("pending_btn_type") or "url"
    extra, err_msg = _parse_btn_value(btn_type, message)
    if err_msg or extra is None:
        sent = await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard("old:btns"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return OLD_ADD_BTN_URL

    label = old.pop("pending_btn_label", None) or ws_db.get_default_button_text()
    old.pop("pending_btn_type", None)
    buttons = normalize_buttons(old.get("buttons") or [])
    next_row = max((b["row"] for b in buttons), default=-1) + 1
    buttons.append(
        {
            "text": label,
            "type": btn_type,
            "style": None,
            "row": next_row,
            **extra,
        }
    )
    old["buttons"] = normalize_buttons(buttons)
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _show_old_buttons_manager(
        context, message.chat_id, old, message_id=old.get("panel_msg_id")
    )


@workspace_access
async def receive_old_btn_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.get("old_edit") or {}
    idx = old.get("editing_btn_idx")
    buttons = normalize_buttons(old.get("buttons") or [])
    if not old.get("chat_id") or idx is None or not 0 <= idx < len(buttons):
        return OLD_PANEL

    new_label = (getattr(message, "text", None) or "").strip()
    if not 1 <= len(new_label) <= MAX_BUTTON_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard(f"old:btn_ins:{idx}"),
        )
        register_panel_message(context, sent.message_id)
        return OLD_BTN_EDIT_TEXT

    buttons[idx]["text"] = new_label
    old["buttons"] = buttons
    old.pop("editing_btn_idx", None)
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _show_old_button_inspector(
        context, message.chat_id, old, idx, message_id=old.get("panel_msg_id")
    )


@workspace_access
async def receive_old_btn_edit_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    old = context.user_data.get("old_edit") or {}
    idx = old.get("editing_btn_idx")
    btn_type = old.get("editing_btn_type")
    buttons = normalize_buttons(old.get("buttons") or [])
    if not old.get("chat_id") or idx is None or not 0 <= idx < len(buttons) or btn_type not in ("url", "popup", "copy"):
        return OLD_PANEL

    extra, err_msg = _parse_btn_value(btn_type, message)
    if err_msg or extra is None:
        sent = await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard(f"old:btn_ins:{idx}"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return OLD_BTN_EDIT_VALUE

    cur = buttons[idx]
    cur.pop("url", None)
    cur.pop("popup_text", None)
    cur.pop("copy_text", None)
    cur["type"] = btn_type
    cur.update(extra)
    old["buttons"] = normalize_buttons(buttons)
    old.pop("editing_btn_idx", None)
    old.pop("editing_btn_type", None)
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _show_old_button_inspector(
        context, message.chat_id, old, idx, message_id=old.get("panel_msg_id")
    )
