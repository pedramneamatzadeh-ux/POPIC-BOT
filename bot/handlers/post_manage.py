"""مدیریت پست‌های ذخیره‌شده: فهرست، پیش‌نمایش، انتشار، تنظیمات پیشرفته دکمه‌ها، ویرایش و حذف."""

import html
import logging
from typing import Any, Callable, Dict, List

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ..config import config
from ..constants import (
    DEFAULT_POPUP_TEXT,
    MAX_BUTTON_TEXT_LENGTH,
    MAX_CAPTION_LENGTH,
    MAX_COPY_TEXT_LENGTH,
    MAX_EXTRA_BUTTONS,
    MAX_POPUP_TEXT_LENGTH,
    MAX_POST_TEXT_LENGTH,
    PM_ADD_BTN_TEXT,
    PM_ADD_BTN_URL,
    PM_BTN_EDIT_TEXT,
    PM_BTN_EDIT_VALUE,
    PM_DETAIL,
    PM_EDIT_LINK,
    PM_EDIT_PHOTO,
    PM_EDIT_TEXT,
    PM_LIST,
    PM_SELECT_CHANNEL,
    Msg,
)
from ..decorators import workspace_access
from ..keyboards import (
    input_back_keyboard,
    post_button_inspector_keyboard,
    post_button_type_keyboard,
    post_buttons_manage_keyboard,
    post_channels_keyboard,
    post_manage_detail_keyboard,
    post_manage_list_keyboard,
    post_media_choice_keyboard,
    settings_confirm_keyboard,
)
from ..services.button_ui import (
    build_button_inspector_text,
    build_buttons_overview_text,
)
from ..services.publisher import (
    deserialize_buttons,
    edit_post_in_chat,
    move_button_row,
    move_button_side,
    normalize_buttons,
    send_post_to_chat,
    serialize_buttons,
    serialize_entities,
    split_button_new_row,
)
from ..utils import (
    build_telegram_message_link,
    extract_forward_post_link,
    fmt_tehran,
    go_to_main_menu,
    normalize_photo_url,
    register_panel_message,
    safe_edit_or_send,
    shorten_text,
    total_pages,
)

logger = logging.getLogger(__name__)


def _list_text(total: int, page: int, pages: int) -> str:
    if total == 0:
        return "📂 <b>پست‌های ذخیره‌شده</b>\n\nهنوز پستی ثبت نشده است."
    return (
        f"📂 <b>پست‌های ذخیره‌شده</b> ({total} پست)\n"
        f"📄 صفحه {page + 1} از {pages}"
    )


def _detail_text(post: Dict[str, Any]) -> str:
    media_type = post.get("media_type") or "link"
    buttons = deserialize_buttons(post.get("buttons_json"))
    snippet = html.escape(shorten_text(post.get("post_text") or "", 180))
    status_label = (
        "✅ منتشرشده در کانال"
        if post.get("status") == "published"
        else "📝 پیش‌نویس / فقط پی‌وی"
    )

    lines = [
        f"📄 <b>جزئیات پست #{post['id']}</b>",
        "",
        f"• وضعیت: {status_label}",
        f"• طول متن: {len(post.get('post_text') or '')} کاراکتر",
    ]
    if media_type == "none":
        lines.append("• تصویر: بدون عکس")
    elif media_type == "photo":
        lines.append("• تصویر: 📷 عکس عادی (روی پیام)")
    else:
        pos = "بالای متن ⬆️" if post.get("show_above_text") else "پایین متن ⬇️"
        size = "بزرگ 🖼" if post.get("prefer_large_media") else "کوچک 🖼"
        lines.append(f"• لینک عکس: {html.escape(post.get('photo_url') or '')}")
        lines.append(f"• پیش‌نمایش: {pos} | اندازه: {size}")

    lines.extend(
        [
            f"• دکمه‌های شیشه‌ای: {len(buttons)}",
            f"• تاریخ ثبت: {html.escape(fmt_tehran(post.get('created_at')))}",
        ]
    )

    if post.get("target_channel_id") and post.get("channel_message_id"):
        ch_name = html.escape(
            post.get("channel_title") or str(post["target_channel_id"])
        )
        link = build_telegram_message_link(
            post.get("target_channel_id"),
            post.get("channel_username"),
            post.get("channel_message_id"),
        )
        lines.append(f"• کانال منتشرشده: {ch_name}")
        if link:
            lines.append(f"• لینک پست در کانال: {html.escape(link)}")

    lines.extend(["", f"📝 <b>خلاصه متن:</b>\n{snippet}"])
    return "\n".join(lines)


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


async def _render_list(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    chat_id: int,
    page: int = 0,
    message_id: int | None = None,
) -> int:
    total = ws_db.count_posts()
    pages = total_pages(total, config.PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    offset = page * config.PAGE_SIZE
    posts = ws_db.list_posts(limit=config.PAGE_SIZE, offset=offset)

    pm = context.user_data.setdefault("pm", {})
    pm["page"] = page

    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_list_text(total, page, pages),
        reply_markup=post_manage_list_keyboard(posts, page, pages, offset),
        message_id=message_id,
        parse_mode="HTML",
    )
    pm["msg_id"] = mid
    return PM_LIST


async def _render_detail(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    chat_id: int,
    post_id: int,
    message_id: int | None = None,
) -> int:
    post = ws_db.get_post(post_id)
    pm = context.user_data.setdefault("pm", {})
    if not post:
        return await _render_list(
            context, ws_db, chat_id, pm.get("page", 0), message_id=message_id
        )

    pm["post_id"] = post_id
    buttons = deserialize_buttons(post.get("buttons_json"))
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_detail_text(post),
        reply_markup=post_manage_detail_keyboard(post, buttons_count=len(buttons)),
        message_id=message_id,
        parse_mode="HTML",
    )
    pm["msg_id"] = mid
    return PM_DETAIL


async def _render_buttons_panel(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    chat_id: int,
    post_id: int,
    message_id: int | None = None,
) -> int:
    post = ws_db.get_post(post_id)
    pm = context.user_data.setdefault("pm", {})
    if not post:
        return await _render_list(
            context, ws_db, chat_id, pm.get("page", 0), message_id=message_id
        )

    pm["post_id"] = post_id
    buttons = deserialize_buttons(post.get("buttons_json"))
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=build_buttons_overview_text(
            buttons, header=f"🔘 مدیریت دکمه‌های شیشه‌ای پست #{post_id}"
        ),
        reply_markup=post_buttons_manage_keyboard(
            buttons,
            add_callback=f"pm:btn_add:{post_id}",
            inspect_prefix=f"pm:btn_ins:{post_id}:",
            back_callback=f"pm:detail:{post_id}",
        ),
        message_id=message_id,
        parse_mode="HTML",
    )
    pm["msg_id"] = mid
    return PM_DETAIL


async def _render_button_inspector(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    chat_id: int,
    post_id: int,
    idx: int,
    message_id: int | None = None,
) -> int:
    post = ws_db.get_post(post_id)
    if not post:
        return await _render_list(context, ws_db, chat_id, 0, message_id=message_id)

    buttons = deserialize_buttons(post.get("buttons_json"))
    if not 0 <= idx < len(buttons):
        return await _render_buttons_panel(
            context, ws_db, chat_id, post_id, message_id=message_id
        )

    pm = context.user_data.setdefault("pm", {})
    pm["post_id"] = post_id
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=build_button_inspector_text(buttons, idx),
        reply_markup=post_button_inspector_keyboard(
            buttons,
            idx,
            prefix=f"pm:{post_id}",
            back_callback=f"pm:btns:{post_id}",
        ),
        message_id=message_id,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    pm["msg_id"] = mid
    return PM_DETAIL


def _transform_saved_post_buttons(
    ws_db: Any,
    post_id: int,
    idx: int,
    transform: Callable[[List[Dict[str, Any]], int], List[Dict[str, Any]]],
) -> int | None:
    post = ws_db.get_post(post_id)
    if not post:
        return None
    buttons = deserialize_buttons(post.get("buttons_json"))
    if not 0 <= idx < len(buttons):
        return None
    buttons[idx]["_track"] = True
    moved = transform(buttons, idx)
    new_idx = idx
    for i, b in enumerate(moved):
        if b.pop("_track", False):
            new_idx = i
            break
    ws_db.update_post(post_id, buttons_json=serialize_buttons(moved))
    return new_idx


@workspace_access
async def start_post_manage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    ws_db = context.user_data["_ws_db"]
    context.user_data["pm"] = {"page": 0}
    return await _render_list(context, ws_db, update.message.chat_id, page=0)


async def _publish_saved_post(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    ws_id: int,
    query,
    post_id: int,
    channel_id: int,
    user_id: int,
) -> tuple[int, str]:
    post = ws_db.get_post(post_id)
    channel = ws_db.get_target_channel(channel_id)
    if not post or not channel:
        return PM_DETAIL, "❌ پست یا کانال پیدا نشد."

    buttons = deserialize_buttons(post.get("buttons_json"))
    try:
        sent = await send_post_to_chat(
            bot=context.bot,
            chat_id=channel["chat_id"],
            text=post["post_text"],
            entities_json=post.get("entities_json"),
            photo_url=post.get("photo_url") or "",
            show_above_text=bool(post.get("show_above_text")),
            prefer_large_media=bool(post.get("prefer_large_media")),
            buttons=buttons,
            workspace_id=ws_id,
            post_id=post_id,
            media_type=post.get("media_type") or "link",
            photo_file_id=post.get("photo_file_id") or "",
        )
    except TelegramError as exc:
        return PM_DETAIL, Msg.CHANNEL_SEND_FAILED.format(error=exc)

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
    )

    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else None
    await _render_detail(context, ws_db, chat_id, post_id, message_id=msg_id)
    return PM_DETAIL, "✅ پست در کانال منتشر شد!"


@workspace_access
async def handle_post_manage_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_id = context.user_data["_ws_id"]
    ws_db = context.user_data["_ws_db"]
    data = (query.data or "").strip()
    pm = context.user_data.setdefault("pm", {})
    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else pm.get("msg_id")

    if data == "pm:close":
        await query.answer()
        try:
            await query.edit_message_text("✅ پنل مدیریت پست‌ها بسته شد.")
        except TelegramError:
            pass
        await go_to_main_menu(context, chat_id, user_id=user_id)
        context.user_data.pop("pm", None)
        return ConversationHandler.END

    if data == "pm:list":
        await query.answer()
        return await _render_list(
            context, ws_db, chat_id, pm.get("page", 0), message_id=msg_id
        )

    if data.startswith("pm:page:"):
        raw = data.split(":", 2)[2]
        page = int(raw) if raw.lstrip("-").isdigit() else 0
        await query.answer()
        return await _render_list(context, ws_db, chat_id, page, message_id=msg_id)

    if data.startswith("pm:detail:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
        pm.pop("pending_btn_label", None)
        pm.pop("pending_btn_type", None)
        await query.answer()
        return await _render_detail(context, ws_db, chat_id, int(raw), message_id=msg_id)

    if data.startswith("pm:btns:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm.pop("pending_btn_label", None)
        pm.pop("pending_btn_type", None)
        await query.answer()
        return await _render_buttons_panel(
            context, ws_db, chat_id, int(raw), message_id=msg_id
        )

    if data.startswith("pm:btn_add:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        buttons = deserialize_buttons(post.get("buttons_json"))
        if len(buttons) >= MAX_EXTRA_BUTTONS:
            await query.answer(
                f"❌ حداکثر {MAX_EXTRA_BUTTONS} دکمه مجاز است.",
                show_alert=True,
            )
            return PM_DETAIL
        pm["post_id"] = post["id"]
        pm["msg_id"] = msg_id
        await query.answer()
        pm["msg_id"] = await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_BTN_TYPE,
            reply_markup=post_button_type_keyboard(
                f"pm:btn_type:{post['id']}:",
                f"pm:btns:{post['id']}",
            ),
            message_id=msg_id,
        )
        return PM_DETAIL

    if data.startswith("pm:btn_type:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[2].isdigit() or parts[3] not in ("url", "popup", "copy"):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        post_id = int(parts[2])
        if not ws_db.get_post(post_id):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = post_id
        pm["pending_btn_type"] = parts[3]
        pm["msg_id"] = msg_id
        await query.answer()
        pm["msg_id"] = await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_BTN_TEXT.format(default=ws_db.get_default_button_text()),
            reply_markup=input_back_keyboard(f"pm:btn_add:{post_id}"),
            message_id=msg_id,
        )
        return PM_ADD_BTN_TEXT

    if data.startswith("pm:btn_ins:"):
        parts = data.split(":")
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        await query.answer()
        return await _render_button_inspector(
            context, ws_db, chat_id, int(parts[2]), int(parts[3]), message_id=msg_id
        )

    # تغییر وضعیت تصویر پست: pm:<post_id>:media:<none|photo|link>
    parts = data.split(":")
    if len(parts) == 4 and parts[1].isdigit() and parts[2] == "media":
        post_id = int(parts[1])
        mode = parts[3]
        post = ws_db.get_post(post_id)
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        if mode == "none":
            ws_db.update_post(
                post_id,
                media_type="none",
                photo_url="",
                photo_file_id="",
            )
            await query.answer("✅ حالت تصویر به «بدون عکس» تغییر یافت.")
            return await _render_detail(context, ws_db, chat_id, post_id, message_id=msg_id)
        if mode == "photo":
            text_len = len(post.get("post_text") or "")
            if text_len > MAX_CAPTION_LENGTH:
                await query.answer(
                    f"⚠️ متن این پست {text_len} کاراکتر است؛ در حالت عکس عادی حداکثر ۱۰۲۴ کاراکتر مجاز است!",
                    show_alert=True,
                )
                return PM_DETAIL
            pm["post_id"] = post_id
            pm["msg_id"] = msg_id
            await query.answer()
            pm["msg_id"] = await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.POST_ASK_PHOTO_UPLOAD,
                reply_markup=input_back_keyboard(f"pm:media_menu:{post_id}"),
                message_id=msg_id,
            )
            return PM_EDIT_PHOTO
        if mode == "link":
            pm["post_id"] = post_id
            pm["msg_id"] = msg_id
            await query.answer()
            pm["msg_id"] = await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.POST_ASK_PHOTO_LINK,
                reply_markup=input_back_keyboard(f"pm:media_menu:{post_id}"),
                message_id=msg_id,
                disable_web_page_preview=True,
            )
            return PM_EDIT_LINK
        await query.answer(Msg.INVALID_COMMAND, show_alert=True)
        return PM_DETAIL

    # عملیات بازرس دکمه: pm:<post_id>:<action>:<idx>[:extra]
    if len(parts) >= 4 and parts[1].isdigit():
        post_id = int(parts[1])
        action = parts[2]
        if not parts[3].isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        idx = int(parts[3])
        post = ws_db.get_post(post_id)
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST

        if action == "btn_col" and len(parts) == 5:
            token = parts[4]
            style_map = {
                "none": None,
                "primary": "primary",
                "success": "success",
                "danger": "danger",
            }
            if token not in style_map:
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return PM_DETAIL
            buttons = deserialize_buttons(post.get("buttons_json"))
            if not 0 <= idx < len(buttons):
                await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
                return PM_DETAIL
            buttons[idx]["style"] = style_map[token]
            ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
            await query.answer("🎨 رنگ دکمه به‌روزرسانی شد.")
            return await _render_button_inspector(
                context, ws_db, chat_id, post_id, idx, message_id=msg_id
            )

        if action in ("btn_row_up", "btn_row_dn", "btn_left", "btn_right", "btn_split"):
            fn_map = {
                "btn_row_up": lambda bs, i: move_button_row(bs, i, -1),
                "btn_row_dn": lambda bs, i: move_button_row(bs, i, 1),
                "btn_left": lambda bs, i: move_button_side(bs, i, -1),
                "btn_right": lambda bs, i: move_button_side(bs, i, 1),
                "btn_split": split_button_new_row,
            }
            new_idx = _transform_saved_post_buttons(
                ws_db, post_id, idx, fn_map[action]
            )
            if new_idx is None:
                await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
                return PM_DETAIL
            await query.answer("📐 چیدمان به‌روزرسانی شد.")
            return await _render_button_inspector(
                context, ws_db, chat_id, post_id, new_idx, message_id=msg_id
            )

        if action == "btn_etxt":
            buttons = deserialize_buttons(post.get("buttons_json"))
            if not 0 <= idx < len(buttons):
                await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
                return PM_DETAIL
            pm["post_id"] = post_id
            pm["editing_btn_idx"] = idx
            pm["msg_id"] = msg_id
            await query.answer()
            pm["msg_id"] = await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=f"✏️ متن جدید دکمه (فعلی: «{buttons[idx]['text']}») را ارسال کن:",
                reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
                message_id=msg_id,
            )
            return PM_BTN_EDIT_TEXT

        if action == "btn_eval":
            buttons = deserialize_buttons(post.get("buttons_json"))
            if not 0 <= idx < len(buttons):
                await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
                return PM_DETAIL
            await query.answer()
            pm["msg_id"] = await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=f"🔀 نوع جدید دکمه «{buttons[idx]['text']}» را انتخاب کن:",
                reply_markup=post_button_type_keyboard(
                    f"pm:{post_id}:btn_ctype:{idx}:",
                    f"pm:btn_ins:{post_id}:{idx}",
                ),
                message_id=msg_id,
            )
            return PM_DETAIL

        if action == "btn_ctype" and len(parts) == 5:
            btn_type = parts[4]
            buttons = deserialize_buttons(post.get("buttons_json"))
            if not 0 <= idx < len(buttons) or btn_type not in ("url", "popup", "copy"):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return PM_DETAIL
            pm["post_id"] = post_id
            pm["editing_btn_idx"] = idx
            pm["editing_btn_type"] = btn_type
            pm["msg_id"] = msg_id
            await query.answer()
            pm["msg_id"] = await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=_value_prompt_for_type(btn_type, buttons[idx]["text"]),
                reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
                message_id=msg_id,
                disable_web_page_preview=True,
            )
            return PM_BTN_EDIT_VALUE

        if action == "btn_del":
            buttons = deserialize_buttons(post.get("buttons_json"))
            if not 0 <= idx < len(buttons):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return PM_DETAIL
            buttons.pop(idx)
            ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
            await query.answer("🗑 دکمه حذف شد.")
            return await _render_buttons_panel(
                context, ws_db, chat_id, post_id, message_id=msg_id
            )

    if data.startswith("pm:pv:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        try:
            sent = await send_post_to_chat(
                bot=context.bot,
                chat_id=chat_id,
                text=post["post_text"],
                entities_json=post.get("entities_json"),
                photo_url=post.get("photo_url") or "",
                show_above_text=bool(post.get("show_above_text")),
                prefer_large_media=bool(post.get("prefer_large_media")),
                buttons=deserialize_buttons(post.get("buttons_json")),
                workspace_id=ws_id,
                post_id=post["id"],
                media_type=post.get("media_type") or "link",
                photo_file_id=post.get("photo_file_id") or "",
            )
            ws_db.record_delivery(
                post_id=post["id"],
                target_type="pv",
                target_chat_id=chat_id,
                message_id=sent.message_id,
                sent_by=user_id,
            )
            await query.answer("✅ پست در پی‌وی ارسال شد.")
        except TelegramError as exc:
            await query.answer(f"❌ ارسال ناموفق: {exc}", show_alert=True)
        return PM_DETAIL

    if data.startswith("pm:send_ch:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        post_id = int(raw)
        channels = ws_db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return PM_DETAIL
        if len(channels) == 1:
            next_state, alert = await _publish_saved_post(
                context, ws_db, ws_id, query, post_id, channels[0]["id"], user_id
            )
            await query.answer(alert, show_alert=True)
            return next_state

        pm["post_id"] = post_id
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="📢 کانال مقصد را انتخاب کن:",
            reply_markup=post_channels_keyboard(
                channels,
                callback_prefix=f"pm:pub:{post_id}:",
                back_callback=f"pm:detail:{post_id}",
            ),
            message_id=msg_id,
        )
        return PM_SELECT_CHANNEL

    if data.startswith("pm:pub:"):
        parts = data.split(":")
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        next_state, alert = await _publish_saved_post(
            context,
            ws_db,
            ws_id,
            query,
            int(parts[2]),
            int(parts[3]),
            user_id,
        )
        await query.answer(alert, show_alert=True)
        return next_state

    if data.startswith("pm:update_ch:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if (
            not post
            or not post.get("target_channel_id")
            or not post.get("channel_message_id")
        ):
            await query.answer("❌ پستِ منتشرشده‌ای یافت نشد.", show_alert=True)
            return PM_DETAIL
        try:
            await edit_post_in_chat(
                bot=context.bot,
                chat_id=post["target_channel_id"],
                message_id=post["channel_message_id"],
                text=post["post_text"],
                entities_json=post.get("entities_json"),
                photo_url=post.get("photo_url") or "",
                show_above_text=bool(post.get("show_above_text")),
                prefer_large_media=bool(post.get("prefer_large_media")),
                buttons=deserialize_buttons(post.get("buttons_json")),
                workspace_id=ws_id,
                post_id=post["id"],
                media_type=post.get("media_type") or "link",
                photo_file_id=post.get("photo_file_id") or "",
            )
            await query.answer("✅ پست در کانال به‌روزرسانی شد!", show_alert=True)
        except TelegramError as exc:
            await query.answer(f"❌ به‌روزرسانی ناموفق: {exc}", show_alert=True)
        return PM_DETAIL

    if data.startswith("pm:toggle_pos:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        ws_db.update_post(
            post["id"],
            show_above_text=not bool(post.get("show_above_text")),
        )
        await query.answer("✅ جای پیش‌نمایش تغییر کرد.")
        return await _render_detail(context, ws_db, chat_id, post["id"], message_id=msg_id)

    if data.startswith("pm:toggle_size:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        ws_db.update_post(
            post["id"],
            prefer_large_media=not bool(post.get("prefer_large_media")),
        )
        await query.answer("✅ اندازه پیش‌نمایش تغییر کرد.")
        return await _render_detail(context, ws_db, chat_id, post["id"], message_id=msg_id)

    if data.startswith("pm:edit_text:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = int(raw)
        pm["msg_id"] = msg_id
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="✏️ متن جدید پست را ارسال کن (تا ۴۰۹۶ کاراکتر):",
            reply_markup=input_back_keyboard(f"pm:detail:{raw}"),
            message_id=msg_id,
        )
        return PM_EDIT_TEXT

    if data.startswith("pm:media_menu:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        post_id = int(raw)
        pm["post_id"] = post_id
        pm["msg_id"] = msg_id
        await query.answer()
        pm["msg_id"] = await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_MEDIA_CHOICE,
            reply_markup=post_media_choice_keyboard(
                prefix=f"pm:{post_id}",
                back_callback=f"pm:detail:{post_id}",
                include_cancel=False,
            ),
            message_id=msg_id,
        )
        return PM_DETAIL

    if data.startswith("pm:edit_link:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = int(raw)
        pm["msg_id"] = msg_id
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_PHOTO_LINK,
            reply_markup=input_back_keyboard(f"pm:detail:{raw}"),
            message_id=msg_id,
            disable_web_page_preview=True,
        )
        return PM_EDIT_LINK

    if data.startswith("pm:del_yes:"):
        raw = data.split(":", 2)[2]
        if raw.isdigit() and ws_db.delete_post(int(raw)):
            await query.answer("🗑 پست حذف شد.", show_alert=True)
        else:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
        return await _render_list(
            context, ws_db, chat_id, pm.get("page", 0), message_id=msg_id
        )

    if data.startswith("pm:del:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not ws_db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=f"❓ مطمئنی می‌خواهی پست #{raw} حذف شود؟",
            reply_markup=settings_confirm_keyboard(
                f"pm:del_yes:{raw}",
                f"pm:detail:{raw}",
            ),
            message_id=msg_id,
        )
        return PM_DETAIL

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return PM_LIST


@workspace_access
async def receive_pm_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id:
        return ConversationHandler.END

    if not message or not message.text or not message.text.strip():
        sent = await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=input_back_keyboard(f"pm:detail:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    if len(message.text) > MAX_POST_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(message.text)),
            reply_markup=input_back_keyboard(f"pm:detail:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    post = ws_db.get_post(post_id)
    if post and (post.get("media_type") or "link") == "photo" and len(message.text) > MAX_CAPTION_LENGTH:
        sent = await message.reply_text(
            f"❌ در حالت «عکس عادی» کپشن حداکثر {MAX_CAPTION_LENGTH} کاراکتر است "
            f"(متن شما {len(message.text)} کاراکتر است).",
            reply_markup=input_back_keyboard(f"pm:detail:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    ws_db.update_post(
        post_id,
        post_text=message.text,
        entities_json=serialize_entities(message.entities),
    )
    return await _render_detail(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_edit_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id:
        return ConversationHandler.END

    photos = getattr(message, "photo", None)
    if not photos:
        sent = await message.reply_text(
            Msg.POST_PHOTO_REQUIRED,
            reply_markup=input_back_keyboard(f"pm:media_menu:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_PHOTO

    ws_db.update_post(
        post_id,
        media_type="photo",
        photo_file_id=photos[-1].file_id,
        photo_url="",
    )
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_detail(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_edit_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id:
        return ConversationHandler.END

    fwd_url, fwd_status = extract_forward_post_link(message)
    if fwd_status == "ok" and fwd_url:
        photo_url, err = fwd_url, ""
    elif fwd_status == "private_channel":
        photo_url, err = None, "private"
    else:
        photo_url, err = normalize_photo_url(message.text)

    if not photo_url:
        err_text = (
            Msg.POST_LINK_PRIVATE_WARN
            if err == "private"
            else Msg.POST_LINK_INVALID
        )
        sent = await message.reply_text(
            err_text,
            reply_markup=input_back_keyboard(f"pm:detail:{post_id}"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_LINK

    ws_db.update_post(
        post_id,
        media_type="link",
        photo_url=photo_url,
        photo_file_id="",
    )
    return await _render_detail(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_btn_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id:
        return ConversationHandler.END

    default_label = ws_db.get_default_button_text()
    raw = (message.text or "").strip()
    label = default_label if raw == "-" else raw
    if not 1 <= len(label) <= MAX_BUTTON_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard(f"pm:btns:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_ADD_BTN_TEXT

    pm["pending_btn_label"] = label
    btn_type = pm.get("pending_btn_type") or "url"
    try:
        await message.delete()
    except TelegramError:
        pass
    pm["msg_id"] = await safe_edit_or_send(
        context=context,
        chat_id=message.chat_id,
        text=_value_prompt_for_type(btn_type, label),
        reply_markup=input_back_keyboard(f"pm:btns:{post_id}"),
        message_id=pm.get("msg_id"),
        disable_web_page_preview=True,
    )
    return PM_ADD_BTN_URL


@workspace_access
async def receive_pm_btn_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id:
        return ConversationHandler.END

    btn_type = pm.get("pending_btn_type") or "url"
    extra, err_msg = _parse_btn_value(btn_type, message)
    if err_msg or extra is None:
        sent = await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard(f"pm:btns:{post_id}"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return PM_ADD_BTN_URL

    post = ws_db.get_post(post_id)
    if not post:
        return await _render_list(
            context, ws_db, message.chat_id, pm.get("page", 0), message_id=pm.get("msg_id")
        )

    label = pm.pop("pending_btn_label", None) or ws_db.get_default_button_text()
    pm.pop("pending_btn_type", None)
    buttons = deserialize_buttons(post.get("buttons_json"))
    next_row = (max((b["row"] for b in buttons), default=-1) + 1)
    buttons.append(
        {
            "text": label,
            "type": btn_type,
            "style": None,
            "row": next_row,
            **extra,
        }
    )
    ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))

    try:
        await message.delete()
    except TelegramError:
        pass

    return await _render_buttons_panel(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_btn_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    idx = pm.get("editing_btn_idx")
    if not post_id or idx is None:
        return ConversationHandler.END

    post = ws_db.get_post(post_id)
    if not post:
        return PM_LIST
    buttons = deserialize_buttons(post.get("buttons_json"))
    if not 0 <= idx < len(buttons):
        return await _render_buttons_panel(
            context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
        )

    new_label = (message.text or "").strip()
    if not 1 <= len(new_label) <= MAX_BUTTON_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.BTN_LABEL_INVALID,
            reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_BTN_EDIT_TEXT

    buttons[idx]["text"] = new_label
    ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
    pm.pop("editing_btn_idx", None)
    try:
        await message.delete()
    except TelegramError:
        pass

    return await _render_button_inspector(
        context, ws_db, message.chat_id, post_id, idx, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_btn_edit_value(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    idx = pm.get("editing_btn_idx")
    btn_type = pm.get("editing_btn_type")
    if not post_id or idx is None or btn_type not in ("url", "popup", "copy"):
        return ConversationHandler.END

    extra, err_msg = _parse_btn_value(btn_type, message)
    if err_msg or extra is None:
        sent = await message.reply_text(
            err_msg or Msg.POST_LINK_INVALID,
            reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
            disable_web_page_preview=True,
        )
        register_panel_message(context, sent.message_id)
        return PM_BTN_EDIT_VALUE

    post = ws_db.get_post(post_id)
    if not post:
        return PM_LIST
    buttons = deserialize_buttons(post.get("buttons_json"))
    if not 0 <= idx < len(buttons):
        return await _render_buttons_panel(
            context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id")
        )

    cur = buttons[idx]
    cur.pop("url", None)
    cur.pop("popup_text", None)
    cur.pop("copy_text", None)
    cur["type"] = btn_type
    cur.update(extra)
    ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
    pm.pop("editing_btn_idx", None)
    pm.pop("editing_btn_type", None)
    try:
        await message.delete()
    except TelegramError:
        pass

    return await _render_button_inspector(
        context, ws_db, message.chat_id, post_id, idx, message_id=pm.get("msg_id")
    )
