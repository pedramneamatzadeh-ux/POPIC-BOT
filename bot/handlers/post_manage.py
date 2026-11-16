"""مدیریت پست‌های ذخیره‌شده: فهرست صفحه‌بندی‌شده، پیش‌نمایش، انتشار، ویرایش و حذف."""

import html
import logging
from typing import Any, Dict

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ..config import config
from ..constants import (
    MAX_POST_TEXT_LENGTH,
    PM_DETAIL,
    PM_EDIT_LINK,
    PM_EDIT_TEXT,
    PM_LIST,
    PM_SELECT_CHANNEL,
    Msg,
)
from ..database import db
from ..decorators import admin_only
from ..keyboards import (
    input_back_keyboard,
    post_channels_keyboard,
    post_manage_detail_keyboard,
    post_manage_list_keyboard,
    settings_confirm_keyboard,
)
from ..services.publisher import (
    deserialize_buttons,
    edit_post_in_chat,
    send_post_to_chat,
    serialize_entities,
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
    pos = "بالای متن ⬆️" if post.get("show_above_text") else "پایین متن ⬇️"
    size = "بزرگ 🖼" if post.get("prefer_large_media") else "کوچک 🖼"
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
        f"• لینک عکس: {html.escape(post.get('photo_url') or '')}",
        f"• پیش‌نمایش: {pos} | اندازه: {size}",
        f"• دکمهٔ شیشه‌ای: {len(buttons)}",
        f"• تاریخ ثبت: {html.escape(fmt_tehran(post.get('created_at')))}",
    ]

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


async def _render_list(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    page: int = 0,
    message_id: int | None = None,
) -> int:
    total = db.count_posts()
    pages = total_pages(total, config.PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    offset = page * config.PAGE_SIZE
    posts = db.list_posts(limit=config.PAGE_SIZE, offset=offset)

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
    chat_id: int,
    post_id: int,
    message_id: int | None = None,
) -> int:
    post = db.get_post(post_id)
    pm = context.user_data.setdefault("pm", {})
    if not post:
        return await _render_list(
            context, chat_id, pm.get("page", 0), message_id=message_id
        )

    pm["post_id"] = post_id
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_detail_text(post),
        reply_markup=post_manage_detail_keyboard(post),
        message_id=message_id,
        parse_mode="HTML",
    )
    pm["msg_id"] = mid
    return PM_DETAIL


@admin_only
async def start_post_manage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data["pm"] = {"page": 0}
    return await _render_list(context, update.message.chat_id, page=0)


async def _publish_saved_post(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    post_id: int,
    channel_id: int,
    user_id: int,
) -> tuple[int, str]:
    post = db.get_post(post_id)
    channel = db.get_target_channel(channel_id)
    if not post or not channel:
        return PM_DETAIL, "❌ پست یا کانال پیدا نشد."

    buttons = deserialize_buttons(post.get("buttons_json"))
    try:
        sent = await send_post_to_chat(
            bot=context.bot,
            chat_id=channel["chat_id"],
            text=post["post_text"],
            entities_json=post.get("entities_json"),
            photo_url=post["photo_url"],
            show_above_text=bool(post.get("show_above_text")),
            prefer_large_media=bool(post.get("prefer_large_media")),
            buttons=buttons,
        )
    except TelegramError as exc:
        return PM_DETAIL, Msg.CHANNEL_SEND_FAILED.format(error=exc)

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

    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else None
    await _render_detail(context, chat_id, post_id, message_id=msg_id)
    return PM_DETAIL, "✅ پست در کانال منتشر شد!"


@admin_only
async def handle_post_manage_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    data = (query.data or "").strip()
    pm = context.user_data.setdefault("pm", {})
    chat_id = query.message.chat_id if query.message else update.effective_user.id
    msg_id = query.message.message_id if query.message else pm.get("msg_id")

    if data == "pm:close":
        await query.answer()
        try:
            await query.edit_message_text("✅ پنل مدیریت پست‌ها بسته شد.")
        except TelegramError:
            pass
        await go_to_main_menu(context, chat_id)
        context.user_data.pop("pm", None)
        return ConversationHandler.END

    if data == "pm:list":
        await query.answer()
        return await _render_list(
            context, chat_id, pm.get("page", 0), message_id=msg_id
        )

    if data.startswith("pm:page:"):
        raw = data.split(":", 2)[2]
        page = int(raw) if raw.lstrip("-").isdigit() else 0
        await query.answer()
        return await _render_list(context, chat_id, page, message_id=msg_id)

    if data.startswith("pm:detail:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
        await query.answer()
        return await _render_detail(context, chat_id, int(raw), message_id=msg_id)

    if data.startswith("pm:pv:"):
        raw = data.split(":", 2)[2]
        post = db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        try:
            sent = await send_post_to_chat(
                bot=context.bot,
                chat_id=chat_id,
                text=post["post_text"],
                entities_json=post.get("entities_json"),
                photo_url=post["photo_url"],
                show_above_text=bool(post.get("show_above_text")),
                prefer_large_media=bool(post.get("prefer_large_media")),
                buttons=deserialize_buttons(post.get("buttons_json")),
            )
            db.record_delivery(
                post_id=post["id"],
                target_type="pv",
                target_chat_id=chat_id,
                message_id=sent.message_id,
                sent_by=update.effective_user.id,
            )
            await query.answer("✅ پست در پی‌وی ارسال شد.")
        except TelegramError as exc:
            await query.answer(f"❌ ارسال ناموفق: {exc}", show_alert=True)
        return PM_DETAIL

    if data.startswith("pm:send_ch:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not db.get_post(int(raw)):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        post_id = int(raw)
        channels = db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return PM_DETAIL
        if len(channels) == 1:
            next_state, alert = await _publish_saved_post(
                context, query, post_id, channels[0]["id"], update.effective_user.id
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
            query,
            int(parts[2]),
            int(parts[3]),
            update.effective_user.id,
        )
        await query.answer(alert, show_alert=True)
        return next_state

    if data.startswith("pm:update_ch:"):
        raw = data.split(":", 2)[2]
        post = db.get_post(int(raw)) if raw.isdigit() else None
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
                photo_url=post["photo_url"],
                show_above_text=bool(post.get("show_above_text")),
                prefer_large_media=bool(post.get("prefer_large_media")),
                buttons=deserialize_buttons(post.get("buttons_json")),
            )
            await query.answer("✅ پست در کانال به‌روزرسانی شد!", show_alert=True)
        except TelegramError as exc:
            await query.answer(f"❌ به‌روزرسانی ناموفق: {exc}", show_alert=True)
        return PM_DETAIL

    if data.startswith("pm:toggle_pos:"):
        raw = data.split(":", 2)[2]
        post = db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        db.update_post(
            post["id"],
            show_above_text=not bool(post.get("show_above_text")),
        )
        await query.answer("✅ جای پیش‌نمایش تغییر کرد.")
        return await _render_detail(context, chat_id, post["id"], message_id=msg_id)

    if data.startswith("pm:toggle_size:"):
        raw = data.split(":", 2)[2]
        post = db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        db.update_post(
            post["id"],
            prefer_large_media=not bool(post.get("prefer_large_media")),
        )
        await query.answer("✅ اندازه پیش‌نمایش تغییر کرد.")
        return await _render_detail(context, chat_id, post["id"], message_id=msg_id)

    if data.startswith("pm:edit_text:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not db.get_post(int(raw)):
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

    if data.startswith("pm:edit_link:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not db.get_post(int(raw)):
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
        )
        return PM_EDIT_LINK

    if data.startswith("pm:del_yes:"):
        raw = data.split(":", 2)[2]
        if raw.isdigit() and db.delete_post(int(raw)):
            await query.answer("🗑 پست حذف شد.", show_alert=True)
        else:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
        return await _render_list(
            context, chat_id, pm.get("page", 0), message_id=msg_id
        )

    if data.startswith("pm:del:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or not db.get_post(int(raw)):
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


@admin_only
async def receive_pm_edit_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
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

    db.update_post(
        post_id,
        post_text=message.text,
        entities_json=serialize_entities(message.entities),
    )
    return await _render_detail(
        context, message.chat_id, post_id, message_id=pm.get("msg_id")
    )


@admin_only
async def receive_pm_edit_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
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

    db.update_post(post_id, photo_url=photo_url)
    return await _render_detail(
        context, message.chat_id, post_id, message_id=pm.get("msg_id")
    )
