"""مدیریت پست‌های ذخیره‌شده: فهرست، تب زمان‌بندی روزانه، جست‌وجو، پیش‌نمایش زنده، آپدیت چندکاناله و ویرایش."""

import html
import json
import logging
from typing import Any, Callable, Dict, List, Optional

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
    PM_SCHED_DAY,
    PM_SCHED_PANEL,
    PM_SCHED_TARGET,
    PM_SCHED_TIME,
    PM_SCHED_YEAR,
    PM_SEARCH,
    PM_SELECT_CHANNEL,
    Msg,
)
from ..decorators import workspace_access
from ..keyboards import (
    confirm_action_keyboard,
    input_back_keyboard,
    main_menu_for,
    post_button_inspector_keyboard,
    post_button_type_keyboard,
    post_buttons_manage_keyboard,
    post_channels_keyboard,
    post_manage_detail_keyboard,
    post_manage_list_keyboard,
    post_manage_update_targets_keyboard,
    post_media_choice_keyboard,
    settings_confirm_keyboard,
)
from ..services.button_ui import (
    build_button_inspector_text,
    build_buttons_overview_text,
)
from ..services.publisher import (
    build_buttons_markup,
    deserialize_buttons,
    edit_post_in_chat,
    is_media_type_mismatch_error,
    move_button_row,
    move_button_side,
    send_post_to_chat,
    serialize_buttons,
    serialize_entities,
    split_button_new_row,
)
from ..services.scheduler import (
    fmt_schedule_tehran,
    tehran_date_key,
)
from ..utils import (
    PANEL_MSG_KEY,
    build_telegram_message_link,
    extract_forward_post_link,
    fmt_tehran,
    normalize_photo_url,
    register_panel_message,
    safe_edit_or_send,
    shorten_text,
    total_pages,
)
from .schedule_ui import (
    handle_schedule_callback,
    handle_schedule_day_input,
    handle_schedule_time_input,
    handle_schedule_year_input,
    init_schedule_state,
    render_schedule_panel,
)

logger = logging.getLogger(__name__)


def _group_scheduled_by_day(
    posts: List[Dict[str, Any]],
) -> List[tuple[str, List[Dict[str, Any]]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in posts:
        key = tehran_date_key(p.get("scheduled_at"))
        groups.setdefault(key, []).append(p)
    return list(groups.items())


def _list_text(
    *,
    tab: str,
    total: int,
    page: int,
    pages: int,
    search_query: str = "",
    day_key: Optional[str] = None,
    day_count: int = 0,
) -> str:
    q_note = (
        f"\n🔍 فیلتر جست‌وجو: «<code>{html.escape(search_query)}</code>»"
        if search_query
        else ""
    )
    if tab == "sched":
        if total == 0:
            return f"⏰ <b>پست‌های زمان‌بندی‌شده</b>{q_note}\n\nهیچ پست زمان‌بندی‌شده‌ای در صف نیست."
        return (
            f"⏰ <b>پست‌های زمان‌بندی‌شده</b> ({total} پست){q_note}\n"
            f"📅 روز: <b>{html.escape(day_key or '—')}</b> ({day_count} پست) — صفحه {page + 1} از {pages}"
        )

    if total == 0:
        if search_query:
            return f"📂 <b>پست‌های ذخیره‌شده</b>{q_note}\n\nنتیجه‌ای یافت نشد."
        return "📂 <b>پست‌های ذخیره‌شده</b>\n\nهنوز پستی ثبت نشده است."
    return (
        f"📂 <b>پست‌های ذخیره‌شده</b> ({total} پست){q_note}\n"
        f"📄 صفحه {page + 1} از {pages}"
    )


def _detail_text(
    post: Dict[str, Any],
    published_targets: Optional[List[Dict[str, Any]]] = None,
) -> str:
    media_type = post.get("media_type") or "link"
    buttons = deserialize_buttons(post.get("buttons_json"))
    status_label = (
        "✅ منتشرشده در کانال/گروه"
        if post.get("status") == "published"
        else "📝 پیش‌نویس / فقط پی‌وی"
    )

    lines = [
        f"🎛 <b>کنترل و جزئیات پست #{post['id']}</b>",
    ]
    if post.get("title"):
        lines.append(f"• نام پست: <b>{html.escape(post['title'])}</b>")
    lines.extend(
        [
            f"• وضعیت: {status_label}",
            f"• طول متن: {len(post.get('post_text') or '')} کاراکتر",
        ]
    )
    if media_type == "none":
        lines.append("• تصویر: بدون عکس")
    elif media_type == "photo":
        lines.append("• تصویر: 📷 عکس عادی (روی پیام)")
    else:
        pos = "بالای متن ⬆️" if post.get("show_above_text") else "پایین متن ⬇️"
        size = "بزرگ 🖼" if post.get("prefer_large_media") else "کوچک 🖼"
        lines.append(f"• لینک عکس: {html.escape(post.get('photo_url') or '')}")
        lines.append(f"• پیش‌نمایش: {pos} | اندازه: {size}")

    if post.get("schedule_status") == "pending" and post.get("scheduled_at"):
        lines.append(
            f"• ⏰ زمان‌بندی: <b>{html.escape(fmt_schedule_tehran(post['scheduled_at']))}</b>"
        )
    elif post.get("schedule_status") == "failed":
        lines.append(
            f"• ⚠️ خطای زمان‌بندی: <code>{html.escape(post.get('schedule_error') or 'نامشخص')}</code>"
        )

    lines.extend(
        [
            f"• دکمه‌های شیشه‌ای: {len(buttons)}",
            f"• تاریخ ثبت: {html.escape(fmt_tehran(post.get('created_at')))}",
        ]
    )

    if published_targets:
        lines.append(f"• منتشرشده در {len(published_targets)} مقصد:")
        for t in published_targets[:4]:
            ch_name = html.escape(t.get("title") or str(t["chat_id"]))
            link = build_telegram_message_link(
                t.get("chat_id"),
                t.get("username"),
                t.get("message_id"),
            )
            lines.append(f"   - {ch_name}" + (f" ({html.escape(link)})" if link else ""))

    lines.extend(
        [
            "",
            "⚠️ توجه: «📤 ارسال به کانال/گروه» پست را همین لحظه ارسال می‌کند؛ اگر پست زمان‌بندی شده، فقط زمان و مقصد را در «⏰ زمان‌بندی پست» تنظیم کنید.",
        ]
    )

    return "\n".join(lines)


async def _delete_preview_message(context: Any, chat_id: int, pm: Dict[str, Any]) -> None:
    pv_mid = pm.pop("preview_msg_id", None)
    if pv_mid:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=pv_mid)
        except TelegramError:
            pass


async def _sync_pm_preview_buttons(
    context: Any,
    chat_id: int,
    ws_id: int,
    post: Dict[str, Any],
    pm: Dict[str, Any],
) -> None:
    pv_mid = pm.get("preview_msg_id")
    if not pv_mid:
        return
    markup = build_buttons_markup(
        deserialize_buttons(post.get("buttons_json")),
        workspace_id=ws_id,
        post_id=post["id"],
    )
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=pv_mid,
            reply_markup=markup,
        )
    except TelegramError:
        pass


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
    page: Optional[int] = None,
    message_id: int | None = None,
) -> int:
    pm = context.user_data.setdefault("pm", {})
    await _delete_preview_message(context, chat_id, pm)

    tab = pm.get("tab") or "all"
    search_query = (pm.get("search") or "").strip()
    if page is None:
        page = pm.get("page", 0)

    all_count = (
        ws_db.count_search_posts(search_query) if search_query else ws_db.count_posts()
    )
    sched_all = ws_db.list_pending_scheduled_posts(search_query=search_query)
    sched_count = len(sched_all)

    if tab == "sched":
        groups = _group_scheduled_by_day(sched_all)
        pages = max(len(groups), 1)
        page = max(0, min(page, pages - 1))
        if groups:
            day_key, day_posts = groups[page]
        else:
            day_key, day_posts = None, []
        posts = day_posts
        offset = 0
        text = _list_text(
            tab="sched",
            total=sched_count,
            page=page,
            pages=pages,
            search_query=search_query,
            day_key=day_key,
            day_count=len(day_posts),
        )
    else:
        pages = total_pages(all_count, config.PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        offset = page * config.PAGE_SIZE
        posts = (
            ws_db.search_posts(search_query, limit=config.PAGE_SIZE, offset=offset)
            if search_query
            else ws_db.list_posts(limit=config.PAGE_SIZE, offset=offset)
        )
        text = _list_text(
            tab="all",
            total=all_count,
            page=page,
            pages=pages,
            search_query=search_query,
        )

    pm["page"] = page
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=text,
        reply_markup=post_manage_list_keyboard(
            posts,
            page,
            pages,
            offset,
            tab=tab,
            all_count=all_count,
            sched_count=sched_count,
            search_query=search_query,
        ),
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
    refresh_preview: bool = True,
) -> int:
    post = ws_db.get_post(post_id)
    pm = context.user_data.setdefault("pm", {})
    if not post:
        return await _render_list(
            context, ws_db, chat_id, pm.get("page", 0), message_id=message_id
        )

    ws_id = context.user_data.get("_ws_id") or chat_id
    pm["post_id"] = post_id
    buttons = deserialize_buttons(post.get("buttons_json"))
    targets = ws_db.get_published_targets(post_id)

    ctrl_mid = message_id or pm.get("msg_id")

    if refresh_preview:
        # دقیقاً مثل post_builder: پیش‌نمایش واقعی بالای سر پنل کنترل قرار بگیرد
        await _delete_preview_message(context, chat_id, pm)
        if ctrl_mid:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=ctrl_mid)
            except TelegramError:
                pass
            ctrl_mid = None

        try:
            pv_msg = await send_post_to_chat(
                bot=context.bot,
                chat_id=chat_id,
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
            pm["preview_msg_id"] = pv_msg.message_id
        except TelegramError as exc:
            logger.warning("ارسال پیش‌نمایش پست %s ناموفق بود: %s", post_id, exc)

    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=_detail_text(post, published_targets=targets),
        reply_markup=post_manage_detail_keyboard(
            post,
            buttons_count=len(buttons),
            published_count=len(targets),
        ),
        message_id=ctrl_mid,
        parse_mode="HTML",
        disable_web_page_preview=True,
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
            back_callback=f"pm:detail_panel:{post_id}",
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
    context.user_data["pm"] = {"page": 0, "tab": "all", "search": ""}
    return await _render_list(context, ws_db, update.message.chat_id, page=0)


async def _publish_saved_post(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    ws_id: int,
    post_id: int,
    channel_id: int,
    user_id: int,
) -> tuple[int, str]:
    post = ws_db.get_post(post_id)
    channel = ws_db.get_target_channel(channel_id)
    if not post or not channel:
        return PM_DETAIL, "❌ پست یا مقصد پیدا نشد."

    buttons = deserialize_buttons(post.get("buttons_json"))
    media_type = post.get("media_type") or "link"
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
            media_type=media_type,
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
        media_type=media_type,
    )
    return PM_DETAIL, f"✅ پست در «{channel.get('title') or channel['chat_id']}» منتشر شد!"


async def _try_edit_target(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    ws_id: int,
    post: Dict[str, Any],
    target: Dict[str, Any],
) -> str:
    """تلاش برای آپدیت پیام منتشرشده؛ خروجی: 'ok' | 'mismatch' | error_str."""
    current_media = post.get("media_type") or "link"
    prev_media = target.get("media_type") or ""
    if prev_media and (prev_media == "photo") != (current_media == "photo"):
        return "mismatch"

    try:
        await edit_post_in_chat(
            bot=context.bot,
            chat_id=target["chat_id"],
            message_id=target["message_id"],
            text=post["post_text"],
            entities_json=post.get("entities_json"),
            photo_url=post.get("photo_url") or "",
            show_above_text=bool(post.get("show_above_text")),
            prefer_large_media=bool(post.get("prefer_large_media")),
            buttons=deserialize_buttons(post.get("buttons_json")),
            workspace_id=ws_id,
            post_id=post["id"],
            media_type=current_media,
            photo_file_id=post.get("photo_file_id") or "",
        )
        return "ok"
    except TelegramError as exc:
        if is_media_type_mismatch_error(exc):
            return "mismatch"
        return str(exc)


async def _replace_target_message(
    context: ContextTypes.DEFAULT_TYPE,
    ws_db: Any,
    ws_id: int,
    post: Dict[str, Any],
    target: Dict[str, Any],
    user_id: int,
) -> tuple[bool, str]:
    chat_id = int(target["chat_id"])
    old_mid = int(target["message_id"])
    current_media = post.get("media_type") or "link"

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=old_mid)
    except TelegramError:
        pass

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
            media_type=current_media,
            photo_file_id=post.get("photo_file_id") or "",
        )
    except TelegramError as exc:
        return False, str(exc)

    ws_db.replace_delivery_message(
        post_id=post["id"],
        target_chat_id=chat_id,
        new_message_id=sent.message_id,
        media_type=current_media,
        sent_by=user_id,
    )
    return True, ""


_PM_SCHED_STATES = {
    "panel": PM_SCHED_PANEL,
    "day": PM_SCHED_DAY,
    "year": PM_SCHED_YEAR,
    "time": PM_SCHED_TIME,
    "target": PM_SCHED_TARGET,
}


@workspace_access
async def handle_post_manage_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    ws_db = context.user_data["_ws_db"]
    ws_id = context.user_data["_ws_id"]
    data = (query.data or "").strip()
    pm = context.user_data.setdefault("pm", {"page": 0, "tab": "all", "search": ""})
    chat_id = query.message.chat_id if query.message else user_id
    msg_id = query.message.message_id if query.message else pm.get("msg_id")

    if data == "pm:close":
        await query.answer()
        await _delete_preview_message(context, chat_id, pm)
        try:
            await query.edit_message_text("✅ پنل پست‌های ذخیره‌شده بسته شد.")
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
        context.user_data.pop("pm", None)
        return ConversationHandler.END

    if data == "pm:list":
        await query.answer()
        return await _render_list(context, ws_db, chat_id, pm.get("page", 0), message_id=msg_id)

    if data.startswith("pm:tab:"):
        tab = data.split(":", 2)[2]
        if tab not in ("all", "sched"):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
        pm["tab"] = tab
        pm["page"] = 0
        await query.answer()
        return await _render_list(context, ws_db, chat_id, 0, message_id=msg_id)

    if data == "pm:search":
        await query.answer()
        pm["msg_id"] = msg_id
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="🔍 نام پست یا بخشی از متن آن را بفرست تا جست‌وجو شود:",
            reply_markup=input_back_keyboard("pm:list"),
            message_id=msg_id,
        )
        return PM_SEARCH

    if data == "pm:search_clear":
        pm["search"] = ""
        pm["page"] = 0
        await query.answer("✅ فیلتر جست‌وجو پاک شد.")
        return await _render_list(context, ws_db, chat_id, 0, message_id=msg_id)

    if data.startswith("pm:page:"):
        raw = data.split(":", 2)[2]
        page = int(raw) if raw.lstrip("-").isdigit() else 0
        await query.answer()
        return await _render_list(context, ws_db, chat_id, page, message_id=msg_id)

    if data.startswith("pm:detail_panel:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
        await query.answer()
        return await _render_detail(
            context, ws_db, chat_id, int(raw), message_id=msg_id, refresh_preview=False
        )

    if data.startswith("pm:detail:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
        await query.answer()
        return await _render_detail(
            context, ws_db, chat_id, int(raw), message_id=msg_id, refresh_preview=True
        )

    # زمان‌بندی داخل مدیریت پست‌های ذخیره‌شده
    if data.startswith("pm:sched:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = post["id"]
        pm["msg_id"] = msg_id
        existing_utc = (
            post.get("scheduled_at")
            if post.get("schedule_status") == "pending"
            else None
        )
        try:
            existing_targets = json.loads(post.get("scheduled_chat_ids") or "[]")
        except Exception:
            existing_targets = []
        pm["_sched"] = init_schedule_state(existing_utc, existing_targets)
        await query.answer()
        await render_schedule_panel(
            context,
            chat_id,
            pm["_sched"],
            prefix="pm",
            back_callback=f"pm:detail_panel:{post['id']}",
            message_id=msg_id,
            existing_utc_iso=existing_utc,
        )
        return PM_SCHED_PANEL

    if data.startswith("pm:sched_"):
        post_id = pm.get("post_id")
        post = ws_db.get_post(post_id) if post_id else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        sched = pm.setdefault("_sched", init_schedule_state(post.get("scheduled_at")))
        existing_utc = (
            post.get("scheduled_at")
            if post.get("schedule_status") == "pending"
            else None
        )

        async def _back_pm_detail() -> int:
            return await _render_detail(
                context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=False
            )

        async def _save_pm_sched(utc_str: str, chat_ids: List[int]) -> int:
            ws_db.set_post_schedule(post_id, utc_str, chat_ids)
            return await _back_pm_detail()

        async def _clear_pm_sched() -> int:
            ws_db.clear_post_schedule(post_id)
            return await _back_pm_detail()

        return await handle_schedule_callback(
            context=context,
            query=query,
            ws_db=ws_db,
            chat_id=chat_id,
            msg_id=msg_id,
            data=data,
            prefix="pm",
            sched=sched,
            states=_PM_SCHED_STATES,
            back_callback=f"pm:detail_panel:{post_id}",
            existing_utc_iso=existing_utc,
            on_save=_save_pm_sched,
            on_clear=_clear_pm_sched,
            on_back=_back_pm_detail,
        )

    # دکمه‌های شیشه‌ای پست ذخیره‌شده
    if data.startswith("pm:btns:"):
        raw = data.split(":", 2)[2]
        if not raw.isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_LIST
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
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_BTN_TYPE,
            reply_markup=post_button_type_keyboard(
                select_prefix=f"pm:btn_type:{post['id']}:",
                back_callback=f"pm:btns:{post['id']}",
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
        btn_type = parts[3]
        if not ws_db.get_post(post_id):
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = post_id
        pm["pending_btn_type"] = btn_type
        pm["msg_id"] = msg_id
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.POST_ASK_BTN_TEXT.format(default=ws_db.get_default_button_text()),
            reply_markup=input_back_keyboard(f"pm:btns:{post_id}"),
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
            return await _render_detail(
                context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=True
            )
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
        buttons = deserialize_buttons(post.get("buttons_json"))
        if not 0 <= idx < len(buttons):
            await query.answer("❌ دکمه پیدا نشد.", show_alert=True)
            return await _render_buttons_panel(
                context, ws_db, chat_id, post_id, message_id=msg_id
            )

        if action == "btn_col" and len(parts) == 5:
            token = parts[4]
            if token not in ("none", "primary", "success", "danger"):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return PM_DETAIL
            buttons[idx]["style"] = None if token == "none" else token
            ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
            await query.answer("🎨 رنگ دکمه تغییر کرد.")
            return await _render_button_inspector(
                context, ws_db, chat_id, post_id, idx, message_id=msg_id
            )

        if action in ("btn_row_up", "btn_row_dn", "btn_left", "btn_right", "btn_split"):
            if action == "btn_row_up":
                new_idx = _transform_saved_post_buttons(
                    ws_db, post_id, idx, lambda b, i: move_button_row(b, i, -1)
                )
            elif action == "btn_row_dn":
                new_idx = _transform_saved_post_buttons(
                    ws_db, post_id, idx, lambda b, i: move_button_row(b, i, 1)
                )
            elif action == "btn_left":
                new_idx = _transform_saved_post_buttons(
                    ws_db, post_id, idx, lambda b, i: move_button_side(b, i, -1)
                )
            elif action == "btn_right":
                new_idx = _transform_saved_post_buttons(
                    ws_db, post_id, idx, lambda b, i: move_button_side(b, i, 1)
                )
            else:
                new_idx = _transform_saved_post_buttons(
                    ws_db, post_id, idx, split_button_new_row
                )
            updated_post = ws_db.get_post(post_id)
            if updated_post:
                await _sync_pm_preview_buttons(context, chat_id, ws_id, updated_post, pm)
            await query.answer("✅ چیدمان به‌روزرسانی شد.")
            return await _render_button_inspector(
                context,
                ws_db,
                chat_id,
                post_id,
                idx if new_idx is None else new_idx,
                message_id=msg_id,
            )

        if action == "btn_etxt":
            pm["post_id"] = post_id
            pm["editing_btn_idx"] = idx
            pm["msg_id"] = msg_id
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=f"✏️ متن جدید برای دکمه «{buttons[idx]['text']}» را بفرست:",
                reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
                message_id=msg_id,
            )
            return PM_BTN_EDIT_TEXT

        if action == "btn_eval":
            pm["post_id"] = post_id
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.POST_ASK_BTN_TYPE,
                reply_markup=post_button_type_keyboard(
                    select_prefix=f"pm:{post_id}:btn_ctype:{idx}:",
                    back_callback=f"pm:btn_ins:{post_id}:{idx}",
                ),
                message_id=msg_id,
            )
            return PM_DETAIL

        if action == "btn_ctype" and len(parts) == 5:
            new_type = parts[4]
            if new_type not in ("url", "popup", "copy"):
                await query.answer(Msg.INVALID_COMMAND, show_alert=True)
                return PM_DETAIL
            pm["post_id"] = post_id
            pm["editing_btn_idx"] = idx
            pm["editing_btn_type"] = new_type
            pm["msg_id"] = msg_id
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=_value_prompt_for_type(new_type, buttons[idx]["text"]),
                reply_markup=input_back_keyboard(f"pm:btn_ins:{post_id}:{idx}"),
                message_id=msg_id,
                disable_web_page_preview=True,
            )
            return PM_BTN_EDIT_VALUE

        if action == "btn_del":
            buttons.pop(idx)
            ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
            updated_post = ws_db.get_post(post_id)
            if updated_post:
                await _sync_pm_preview_buttons(context, chat_id, ws_id, updated_post, pm)
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

        if pm.get("preview_msg_id"):
            await _sync_pm_preview_buttons(context, chat_id, ws_id, post, pm)
            ws_db.record_delivery(
                post_id=post["id"],
                target_type="pv",
                target_chat_id=chat_id,
                message_id=pm["preview_msg_id"],
                sent_by=user_id,
                media_type=post.get("media_type") or "link",
            )
        else:
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
                    media_type=post.get("media_type") or "link",
                )
            except TelegramError as exc:
                await query.answer(f"❌ ارسال ناموفق: {exc}", show_alert=True)
                return PM_DETAIL

        await query.answer("✅ پست در پی‌وی آماده است!")
        if msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
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
        context.user_data.pop("pm", None)
        return ConversationHandler.END

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
                context, ws_db, ws_id, post_id, channels[0]["id"], user_id
            )
            await query.answer(alert, show_alert=True)
            return await _render_detail(
                context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=False
            )

        pm["post_id"] = post_id
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="📢 کانال یا گروه مقصد را انتخاب کن:",
            reply_markup=post_channels_keyboard(
                channels,
                callback_prefix=f"pm:pub:{post_id}:",
                back_callback=f"pm:detail_panel:{post_id}",
            ),
            message_id=msg_id,
        )
        return PM_SELECT_CHANNEL

    if data.startswith("pm:pub:"):
        parts = data.split(":")
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        post_id = int(parts[2])
        next_state, alert = await _publish_saved_post(
            context, ws_db, ws_id, post_id, int(parts[3]), user_id
        )
        await query.answer(alert, show_alert=True)
        return await _render_detail(
            context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=False
        )

    if data.startswith("pm:update_ch:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        targets = ws_db.get_published_targets(post["id"])
        if not targets:
            await query.answer("❌ پستِ منتشرشده‌ای یافت نشد.", show_alert=True)
            return PM_DETAIL
        if len(targets) == 1:
            res = await _try_edit_target(context, ws_db, ws_id, post, targets[0])
            if res == "ok":
                await query.answer("✅ پست در مقصد به‌روزرسانی شد!", show_alert=True)
                return await _render_detail(
                    context, ws_db, chat_id, post["id"], message_id=msg_id, refresh_preview=False
                )
            if res == "mismatch":
                await query.answer()
                await safe_edit_or_send(
                    context=context,
                    chat_id=chat_id,
                    text=Msg.MEDIA_TYPE_CHANGE_CONFIRM,
                    reply_markup=confirm_action_keyboard(
                        "✅ بله، حذف و ارسال مجدد",
                        f"pm:upd_replace:{post['id']}:{targets[0]['chat_id']}",
                        "❌ انصراف",
                        f"pm:detail_panel:{post['id']}",
                    ),
                    message_id=msg_id,
                )
                return PM_DETAIL
            await query.answer(f"❌ به‌روزرسانی ناموفق: {res}", show_alert=True)
            return PM_DETAIL

        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="♻️ این پست در چند مقصد منتشر شده است. کدام‌یک به‌روزرسانی شود؟",
            reply_markup=post_manage_update_targets_keyboard(post["id"], targets),
            message_id=msg_id,
        )
        return PM_DETAIL

    if data.startswith("pm:upd_pick:"):
        parts = data.split(":")
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].lstrip("-").isdigit()):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        post_id = int(parts[2])
        target_chat_id = int(parts[3])
        post = ws_db.get_post(post_id)
        targets = ws_db.get_published_targets(post_id) if post else []
        target = next((t for t in targets if t["chat_id"] == target_chat_id), None)
        if not post or not target:
            await query.answer("❌ مقصد پیدا نشد.", show_alert=True)
            return PM_DETAIL
        res = await _try_edit_target(context, ws_db, ws_id, post, target)
        if res == "ok":
            await query.answer(
                f"✅ پست در «{target['title']}» به‌روزرسانی شد!", show_alert=True
            )
            return await _render_detail(
                context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=False
            )
        if res == "mismatch":
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.MEDIA_TYPE_CHANGE_CONFIRM,
                reply_markup=confirm_action_keyboard(
                    "✅ بله، حذف و ارسال مجدد",
                    f"pm:upd_replace:{post_id}:{target_chat_id}",
                    "❌ انصراف",
                    f"pm:detail_panel:{post_id}",
                ),
                message_id=msg_id,
            )
            return PM_DETAIL
        await query.answer(f"❌ به‌روزرسانی ناموفق: {res}", show_alert=True)
        return PM_DETAIL

    if data.startswith("pm:upd_all:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        targets = ws_db.get_published_targets(post["id"])
        ok_count = 0
        mismatches = []
        errors = []
        for t in targets:
            res = await _try_edit_target(context, ws_db, ws_id, post, t)
            if res == "ok":
                ok_count += 1
            elif res == "mismatch":
                mismatches.append(t)
            else:
                errors.append(f"{t['title']}: {res}")
        if mismatches:
            await query.answer()
            await safe_edit_or_send(
                context=context,
                chat_id=chat_id,
                text=Msg.MEDIA_TYPE_CHANGE_CONFIRM,
                reply_markup=confirm_action_keyboard(
                    f"✅ بله، جایگزینی در {len(mismatches)} مقصد",
                    f"pm:upd_replace_all:{post['id']}",
                    "❌ انصراف",
                    f"pm:detail_panel:{post['id']}",
                ),
                message_id=msg_id,
            )
            return PM_DETAIL
        if errors:
            await query.answer(
                f"✅ {ok_count} مقصد آپدیت شد؛ خطاها:\n" + "\n".join(errors),
                show_alert=True,
            )
        else:
            await query.answer(f"✅ پست در همهٔ {ok_count} مقصد به‌روزرسانی شد!", show_alert=True)
        return await _render_detail(
            context, ws_db, chat_id, post["id"], message_id=msg_id, refresh_preview=False
        )

    if data.startswith("pm:upd_replace:"):
        parts = data.split(":")
        if len(parts) != 4 or not (parts[2].isdigit() and parts[3].lstrip("-").isdigit()):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return PM_DETAIL
        post_id = int(parts[2])
        target_chat_id = int(parts[3])
        post = ws_db.get_post(post_id)
        targets = ws_db.get_published_targets(post_id) if post else []
        target = next((t for t in targets if t["chat_id"] == target_chat_id), None)
        if not post or not target:
            await query.answer("❌ مقصد پیدا نشد.", show_alert=True)
            return PM_DETAIL
        ok, err = await _replace_target_message(
            context, ws_db, ws_id, post, target, user_id
        )
        if ok:
            await query.answer("✅ پیام قبلی حذف و نسخه جدید ارسال شد!", show_alert=True)
        else:
            await query.answer(f"❌ جایگزینی ناموفق: {err}", show_alert=True)
        return await _render_detail(
            context, ws_db, chat_id, post_id, message_id=msg_id, refresh_preview=False
        )

    if data.startswith("pm:upd_replace_all:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        targets = ws_db.get_published_targets(post["id"])
        ok_count = 0
        for t in targets:
            res = await _try_edit_target(context, ws_db, ws_id, post, t)
            if res == "ok":
                ok_count += 1
            elif res == "mismatch":
                ok, _ = await _replace_target_message(
                    context, ws_db, ws_id, post, t, user_id
                )
                if ok:
                    ok_count += 1
        await query.answer(f"✅ {ok_count} مقصد با موفقیت به‌روزرسانی/جایگزین شدند.", show_alert=True)
        return await _render_detail(
            context, ws_db, chat_id, post["id"], message_id=msg_id, refresh_preview=False
        )

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
        return await _render_detail(
            context, ws_db, chat_id, post["id"], message_id=msg_id, refresh_preview=True
        )

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
        return await _render_detail(
            context, ws_db, chat_id, post["id"], message_id=msg_id, refresh_preview=True
        )

    if data.startswith("pm:edit_text:"):
        raw = data.split(":", 2)[2]
        post = ws_db.get_post(int(raw)) if raw.isdigit() else None
        if not post:
            await query.answer("❌ پست پیدا نشد.", show_alert=True)
            return PM_LIST
        pm["post_id"] = int(raw)
        pm["msg_id"] = msg_id
        max_len = (
            MAX_CAPTION_LENGTH
            if (post.get("media_type") or "link") == "photo"
            else MAX_POST_TEXT_LENGTH
        )
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=f"✏️ متن جدید پست را ارسال کن (تا {max_len} کاراکتر):",
            reply_markup=input_back_keyboard(f"pm:detail_panel:{raw}"),
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
                back_callback=f"pm:detail_panel:{post_id}",
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
            reply_markup=input_back_keyboard(f"pm:detail_panel:{raw}"),
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
                f"pm:detail_panel:{raw}",
            ),
            message_id=msg_id,
        )
        return PM_DETAIL

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return PM_LIST


@workspace_access
async def receive_pm_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.message
    ws_db = context.user_data["_ws_db"]
    pm = context.user_data.setdefault("pm", {"page": 0, "tab": "all", "search": ""})
    query_str = " ".join((getattr(message, "text", None) or "").split())
    if not query_str:
        sent = await message.reply_text(
            "❌ لطفاً عبارت غیرخالی برای جست‌وجو بفرست:",
            reply_markup=input_back_keyboard("pm:list"),
        )
        register_panel_message(context, sent.message_id)
        return PM_SEARCH

    pm["search"] = query_str
    pm["page"] = 0
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_list(
        context, ws_db, message.chat_id, 0, message_id=pm.get("msg_id")
    )


@workspace_access
async def receive_pm_sched_day(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id or "_sched" not in pm:
        return ConversationHandler.END
    ws_db = context.user_data["_ws_db"]
    post = ws_db.get_post(post_id) or {}
    existing_utc = (
        post.get("scheduled_at") if post.get("schedule_status") == "pending" else None
    )
    return await handle_schedule_day_input(
        update=update,
        context=context,
        sched=pm["_sched"],
        prefix="pm",
        panel_msg_id=pm.get("msg_id"),
        back_callback=f"pm:detail_panel:{post_id}",
        states=_PM_SCHED_STATES,
        existing_utc_iso=existing_utc,
    )


@workspace_access
async def receive_pm_sched_year(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id or "_sched" not in pm:
        return ConversationHandler.END
    ws_db = context.user_data["_ws_db"]
    post = ws_db.get_post(post_id) or {}
    existing_utc = (
        post.get("scheduled_at") if post.get("schedule_status") == "pending" else None
    )
    return await handle_schedule_year_input(
        update=update,
        context=context,
        sched=pm["_sched"],
        prefix="pm",
        panel_msg_id=pm.get("msg_id"),
        back_callback=f"pm:detail_panel:{post_id}",
        states=_PM_SCHED_STATES,
        existing_utc_iso=existing_utc,
    )


@workspace_access
async def receive_pm_sched_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    pm = context.user_data.get("pm") or {}
    post_id = pm.get("post_id")
    if not post_id or "_sched" not in pm:
        return ConversationHandler.END
    ws_db = context.user_data["_ws_db"]
    post = ws_db.get_post(post_id) or {}
    existing_utc = (
        post.get("scheduled_at") if post.get("schedule_status") == "pending" else None
    )
    return await handle_schedule_time_input(
        update=update,
        context=context,
        sched=pm["_sched"],
        prefix="pm",
        panel_msg_id=pm.get("msg_id"),
        back_callback=f"pm:detail_panel:{post_id}",
        states=_PM_SCHED_STATES,
        existing_utc_iso=existing_utc,
    )


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
            reply_markup=input_back_keyboard(f"pm:detail_panel:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    if len(message.text) > MAX_POST_TEXT_LENGTH:
        sent = await message.reply_text(
            Msg.POST_TEXT_TOO_LONG.format(length=len(message.text)),
            reply_markup=input_back_keyboard(f"pm:detail_panel:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    post = ws_db.get_post(post_id)
    if post and (post.get("media_type") or "link") == "photo" and len(message.text) > MAX_CAPTION_LENGTH:
        sent = await message.reply_text(
            f"❌ در حالت «عکس عادی» کپشن حداکثر {MAX_CAPTION_LENGTH} کاراکتر است "
            f"(متن شما {len(message.text)} کاراکتر است).",
            reply_markup=input_back_keyboard(f"pm:detail_panel:{post_id}"),
        )
        register_panel_message(context, sent.message_id)
        return PM_EDIT_TEXT

    ws_db.update_post(
        post_id,
        post_text=message.text,
        entities_json=serialize_entities(message.entities),
    )
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_detail(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id"), refresh_preview=True
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
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id"), refresh_preview=True
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
            reply_markup=input_back_keyboard(f"pm:detail_panel:{post_id}"),
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
    try:
        await message.delete()
    except TelegramError:
        pass
    return await _render_detail(
        context, ws_db, message.chat_id, post_id, message_id=pm.get("msg_id"), refresh_preview=True
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
    ws_id = context.user_data["_ws_id"]
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
    ws_db.update_post(post_id, buttons_json=serialize_buttons(buttons))
    updated_post = ws_db.get_post(post_id)
    if updated_post:
        await _sync_pm_preview_buttons(context, message.chat_id, ws_id, updated_post, pm)

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
    ws_id = context.user_data["_ws_id"]
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
    updated_post = ws_db.get_post(post_id)
    if updated_post:
        await _sync_pm_preview_buttons(context, message.chat_id, ws_id, updated_post, pm)
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
    ws_id = context.user_data["_ws_id"]
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
    updated_post = ws_db.get_post(post_id)
    if updated_post:
        await _sync_pm_preview_buttons(context, message.chat_id, ws_id, updated_post, pm)
    pm.pop("editing_btn_idx", None)
    pm.pop("editing_btn_type", None)
    try:
        await message.delete()
    except TelegramError:
        pass

    return await _render_button_inspector(
        context, ws_db, message.chat_id, post_id, idx, message_id=pm.get("msg_id")
    )
