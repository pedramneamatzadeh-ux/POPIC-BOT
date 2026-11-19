import html
import logging
from typing import Any, Dict

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ..config import config
from ..constants import MAX_POST_TEXT_LENGTH, STATS_MSG_INPUT, STATS_VIEW, Msg
from ..database import count_workspace_dbs, registry, resolve_user_workspace
from ..decorators import workspace_access
from ..keyboards import (
    confirm_action_keyboard,
    input_back_keyboard,
    stats_keyboard,
    stats_user_detail_keyboard,
    stats_users_keyboard,
)
from ..utils import (
    fmt_tehran,
    go_to_main_menu,
    register_panel_message,
    safe_edit_or_send,
    shorten_text,
    total_pages,
)

logger = logging.getLogger(__name__)

_USERS_PAGE_SIZE = 8


def _is_env_owner(user_id: int) -> bool:
    return config.OWNER_ID is not None and int(user_id) == int(config.OWNER_ID)


async def _safe_answer(query, text=None, show_alert: bool = False) -> None:
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except BadRequest as exc:
        if "query is too old" in str(exc).lower():
            logger.info("query آمار منقضی بود: %s", exc)
            return
        raise


def _build_stats_text(user_id: int) -> str:
    ws_id, status, ws_db = resolve_user_workspace(user_id)
    summary = ws_db.get_stats_summary()
    recent = ws_db.list_posts(limit=5, offset=0)
    ws_admins = registry.get_workspace_admins(ws_id)

    if status == "admin":
        role_line = f"👨‍💼 فضای فعال: <b>ادمینِ {html.escape(registry.get_user_display_name(ws_id))}</b>"
    else:
        role_line = "👑 فضای فعال: <b>فضای شخصی شما (مالک)</b>"

    lines = [
        "📊 <b>آمار فضای کاری</b>",
        role_line,
        "",
        f"📝 کل پست‌های ثبت‌شده: <b>{summary['total_posts']}</b>",
        f"📢 منتشرشده در کانال/گروه: <b>{summary['published_posts']}</b>",
        f"⏰ در صف زمان‌بندی: <b>{summary.get('scheduled_posts', 0)}</b>",
        f"📄 پیش‌نویس / فقط پی‌وی: <b>{summary['draft_posts']}</b>",
        f"📤 دفعات ارسال به کانال/گروه: <b>{summary['channel_deliveries']}</b>",
        f"📥 دفعات تحویل در پی‌وی: <b>{summary['pv_deliveries']}</b>",
        f"📢 کانال‌ها و گروه‌های مقصد: <b>{summary['total_channels']}</b>",
        f"👨‍💼 ادمین‌های این فضا: <b>{len(ws_admins)}</b> (+ مالک)",
    ]

    if _is_env_owner(user_id):
        g_stats = registry.get_global_stats()
        lines.extend(
            [
                "",
                "🌐 <b>آمار کل ربات (ابرمالک):</b>",
                f"• کل کاربران: <b>{g_stats['total_users']}</b>",
                f"• دیتابیس‌های ساخته‌شده: <b>{count_workspace_dbs()}</b>",
            ]
        )

    if recent:
        lines.append("")
        lines.append("🕒 <b>آخرین پست‌ها:</b>")
        for idx, post in enumerate(recent, 1):
            label = post.get("title") or post.get("post_text") or ""
            snippet = html.escape(shorten_text(label, 42))
            badge = "✅" if post.get("status") == "published" else ("⏰" if post.get("schedule_status") == "pending" else "📝")
            date_str = html.escape(fmt_tehran(post.get("created_at")))
            lines.append(f"{idx}. {badge} {snippet} — <i>{date_str}</i>")

    return "\n".join(lines)


def _user_detail_text(u: Dict[str, Any]) -> str:
    uid = int(u["user_id"])
    full = " ".join(
        p for p in (u.get("first_name") or "", u.get("last_name") or "") if p
    ).strip() or "—"
    uname = f"@{u['username']}" if u.get("username") else "—"
    status = "🚫 بن‌شده (مسدود)" if u.get("is_banned") else "✅ فعال"
    if _is_env_owner(uid):
        status = "👑 مالک اصلی ربات"
    return "\n".join(
        [
            f"👤 <b>جزئیات کاربر</b> <code>{uid}</code>",
            "",
            f"• نام: {html.escape(full)}",
            f"• نام کاربری: {html.escape(uname)}",
            f"• وضعیت: <b>{status}</b>",
            f"• اولین ورود: {html.escape(fmt_tehran(u.get('first_seen')))}",
            f"• آخرین فعالیت: {html.escape(fmt_tehran(u.get('last_activity')))}",
        ]
    )


async def _render_users_list(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    page: int,
    message_id: int | None = None,
) -> int:
    total = registry.count_users()
    pages = total_pages(total, _USERS_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    users = registry.list_users_paginated(
        limit=_USERS_PAGE_SIZE, offset=page * _USERS_PAGE_SIZE
    )
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=(
            f"👥 <b>مدیریت کاربران ربات</b> ({total} کاربر)\n"
            f"📄 صفحه {page + 1} از {pages}\n\n"
            "برای بن کردن یا ارسال پیام روی کاربر بزن:"
        ),
        reply_markup=stats_users_keyboard(
            users, page, pages, env_owner_id=config.OWNER_ID
        ),
        message_id=message_id,
        parse_mode="HTML",
    )
    context.user_data["stats_msg_id"] = mid
    return STATS_VIEW


async def _render_user_detail(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    target_uid: int,
    message_id: int | None = None,
) -> int:
    u = registry.get_user(target_uid)
    if not u:
        return await _render_users_list(context, chat_id, 0, message_id=message_id)
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_user_detail_text(u),
        reply_markup=stats_user_detail_keyboard(
            target_uid,
            is_banned=bool(u.get("is_banned")),
            is_env_owner=_is_env_owner(target_uid),
        ),
        message_id=message_id,
        parse_mode="HTML",
    )
    context.user_data["stats_msg_id"] = mid
    return STATS_VIEW


@workspace_access
async def show_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    user_id = update.effective_user.id
    sent = await update.message.reply_text(
        _build_stats_text(user_id),
        reply_markup=stats_keyboard(is_env_owner=_is_env_owner(user_id)),
        parse_mode="HTML",
    )
    register_panel_message(context, sent.message_id)
    context.user_data["stats_msg_id"] = sent.message_id
    return STATS_VIEW


@workspace_access
async def handle_stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    data = (query.data or "").strip()
    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else context.user_data.get("stats_msg_id")

    if data == "stats:refresh":
        await _safe_answer(query)
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=_build_stats_text(user_id),
            reply_markup=stats_keyboard(is_env_owner=_is_env_owner(user_id)),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return STATS_VIEW

    if data == "stats:close":
        await _safe_answer(query)
        if query.message:
            try:
                await query.edit_message_text("✅ پنل آمار بسته شد.")
            except Exception:
                pass
            await go_to_main_menu(context, query.message.chat_id, user_id=user_id)
        return ConversationHandler.END

    # مدیریت کاربران فقط برای مالک .env
    if data.startswith(("stats:users:", "stats:user:", "stats:ban_", "stats:msg_")):
        if not _is_env_owner(user_id):
            await _safe_answer(query, Msg.ACCESS_DENIED, show_alert=True)
            return STATS_VIEW

    if data.startswith("stats:users:"):
        raw = data.split(":", 2)[2]
        page = int(raw) if raw.lstrip("-").isdigit() else 0
        await _safe_answer(query)
        return await _render_users_list(context, chat_id, page, message_id=msg_id)

    if data.startswith("stats:user:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await _safe_answer(query, Msg.INVALID_COMMAND, show_alert=True)
            return STATS_VIEW
        await _safe_answer(query)
        return await _render_user_detail(context, chat_id, int(raw), message_id=msg_id)

    if data.startswith("stats:ban_ask:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or _is_env_owner(int(raw)):
            await _safe_answer(query, Msg.INVALID_COMMAND, show_alert=True)
            return STATS_VIEW
        target_uid = int(raw)
        u = registry.get_user(target_uid)
        if not u:
            await _safe_answer(query, "❌ کاربر پیدا نشد.", show_alert=True)
            return await _render_users_list(context, chat_id, 0, message_id=msg_id)
        is_banned = bool(u.get("is_banned"))
        prompt = (
            f"❓ مطمئنی می‌خواهی کاربر <code>{target_uid}</code> از بن خارج شود؟"
            if is_banned
            else f"🚫 مطمئنی می‌خواهی کاربر <code>{target_uid}</code> بن شود و نتواند از ربات استفاده کند؟"
        )
        await _safe_answer(query)
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=prompt,
            reply_markup=confirm_action_keyboard(
                "✅ بله، رفع بن کن" if is_banned else "🚫 بله، بن کن",
                f"stats:ban_yes:{target_uid}",
                "❌ انصراف",
                f"stats:user:{target_uid}",
                yes_style="success" if is_banned else "danger",
            ),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return STATS_VIEW

    if data.startswith("stats:ban_yes:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit() or _is_env_owner(int(raw)):
            await _safe_answer(query, Msg.INVALID_COMMAND, show_alert=True)
            return STATS_VIEW
        target_uid = int(raw)
        u = registry.get_user(target_uid)
        if not u:
            await _safe_answer(query, "❌ کاربر پیدا نشد.", show_alert=True)
            return await _render_users_list(context, chat_id, 0, message_id=msg_id)
        new_banned = not bool(u.get("is_banned"))
        registry.set_user_banned(target_uid, new_banned)
        await _safe_answer(
            query,
            "🚫 کاربر بن شد." if new_banned else "✅ کاربر رفع بن شد.",
            show_alert=True,
        )
        return await _render_user_detail(context, chat_id, target_uid, message_id=msg_id)

    if data.startswith("stats:msg_ask:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await _safe_answer(query, Msg.INVALID_COMMAND, show_alert=True)
            return STATS_VIEW
        target_uid = int(raw)
        context.user_data["stats_msg_target"] = target_uid
        context.user_data["stats_msg_id"] = msg_id
        await _safe_answer(query)
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=f"✉️ متن پیامی که می‌خواهی برای کاربر <code>{target_uid}</code> ارسال شود را بفرست:",
            reply_markup=input_back_keyboard(f"stats:user:{target_uid}"),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return STATS_MSG_INPUT

    if data.startswith("stats:msg_send:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await _safe_answer(query, Msg.INVALID_COMMAND, show_alert=True)
            return STATS_VIEW
        target_uid = int(raw)
        msg_text = context.user_data.pop("stats_msg_text", None)
        context.user_data.pop("stats_msg_target", None)
        if not msg_text:
            await _safe_answer(query, "❌ متن پیام منقضی شده است.", show_alert=True)
            return await _render_user_detail(context, chat_id, target_uid, message_id=msg_id)
        try:
            await context.bot.send_message(chat_id=target_uid, text=msg_text)
            await _safe_answer(query, "✅ پیام با موفقیت برای کاربر ارسال شد!", show_alert=True)
        except TelegramError as exc:
            await _safe_answer(query, f"❌ ارسال پیام ناموفق بود: {exc}", show_alert=True)
        return await _render_user_detail(context, chat_id, target_uid, message_id=msg_id)

    await _safe_answer(query, "⚠️ گزینه نامعتبر", show_alert=True)
    return STATS_VIEW


@workspace_access
async def receive_stats_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    user_id = update.effective_user.id
    if not _is_env_owner(user_id):
        return ConversationHandler.END
    message = update.message
    target_uid = context.user_data.get("stats_msg_target")
    msg_id = context.user_data.get("stats_msg_id")
    if not target_uid:
        return STATS_VIEW

    raw = (getattr(message, "text", None) or "").strip()
    if not raw or len(raw) > MAX_POST_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TEXT_INVALID,
            reply_markup=input_back_keyboard(f"stats:user:{target_uid}"),
        )
        register_panel_message(context, sent.message_id)
        return STATS_MSG_INPUT

    context.user_data["stats_msg_text"] = raw
    try:
        await message.delete()
    except TelegramError:
        pass

    preview = html.escape(shorten_text(raw, 500))
    mid = await safe_edit_or_send(
        context=context,
        chat_id=message.chat_id,
        text=(
            f"❓ آیا مطمئنی این پیام برای کاربر <code>{target_uid}</code> ارسال شود؟\n\n"
            f"📝 <b>متن پیام:</b>\n{preview}"
        ),
        reply_markup=confirm_action_keyboard(
            "🚀 بله، ارسال کن",
            f"stats:msg_send:{target_uid}",
            "❌ انصراف",
            f"stats:user:{target_uid}",
            yes_style="success",
        ),
        message_id=msg_id,
        parse_mode="HTML",
    )
    context.user_data["stats_msg_id"] = mid
    return STATS_VIEW
