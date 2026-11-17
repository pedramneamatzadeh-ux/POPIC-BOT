"""متون پنل، هلپرها و لغو گفتگوی تنظیمات فضای کاری."""

import logging
from typing import Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ...constants import (
    BOT_VERSION,
    BOT_VERSION_DATE,
    SETTINGS_PANEL,
    Msg,
)
from ...database import get_workspace_db, registry
from ...decorators import workspace_owner_only
from ...keyboards import main_menu_for, settings_input_keyboard

logger = logging.getLogger(__name__)

_CANCEL_TEXTS = ("لغو", "بازگشت به منوی اصلی")


def _panel_text(owner_id: int) -> str:
    ws_db = get_workspace_db(owner_id)
    admins = registry.get_workspace_admins(owner_id)
    channels = ws_db.get_target_channels()
    pos = "بالای متن ⬆️" if ws_db.get_show_above_text() else "پایین متن ⬇️"
    size = "بزرگ 🖼" if ws_db.get_prefer_large_media() else "کوچک 🖼"

    return "\n".join(
        [
            Msg.SETTINGS_TITLE,
            "",
            f"👨‍💼 ادمین‌های فضای شما: {len(admins)} نفر (به‌علاوه شما 👑)",
            f"📢 کانال‌های مقصد: {len(channels)}",
            f"🖼 پیش‌فرض پیش‌نمایش: {pos} | {size}",
            f"🏷 متن پیش‌فرض دکمه: «{ws_db.get_default_button_text()}»",
            f"🧩 نسخه: {BOT_VERSION} ({BOT_VERSION_DATE})",
        ]
    )


def _user_names() -> dict:
    names = {}
    try:
        for row in registry.get_all_users():
            display = " ".join(
                part
                for part in (
                    row.get("first_name") or "",
                    row.get("last_name") or "",
                )
                if part
            ).strip()
            if not display:
                display = row.get("username") or ""
            if display:
                names[int(row["user_id"])] = display
    except Exception:
        logger.debug("خواندن نام کاربران ناموفق بود", exc_info=True)
    return names


def _admin_names(owner_id: int) -> dict:
    names = _user_names()
    if owner_id in names:
        names[owner_id] = f"{names[owner_id]} 👑"
    return names


def _admins_text(owner_id: int) -> str:
    names = _user_names()
    lines = ["👨‍💼 ادمین‌های فضای شما", ""]

    owner_name = names.get(owner_id) or "—"
    lines.append(f"👑 {owner_name} — {owner_id} (مالک، ثابت)")

    others = registry.get_workspace_admins(owner_id)
    if others:
        lines.extend(
            f"👤 {names.get(int(item)) or '—'} — {item}"
            for item in others
        )
    else:
        lines.append("— ادمین دیگری ثبت نشده است")

    return "\n".join(lines)


def _channels_text(owner_id: int) -> str:
    ws_db = get_workspace_db(owner_id)
    channels = ws_db.get_target_channels()
    lines = ["📢 کانال‌های مقصد فضای شما", ""]

    if not channels:
        lines.append("— هیچ کانال مقصدی ثبت نشده است")
    else:
        for ch in channels:
            title = ch.get("title") or "—"
            uname = f" (@{ch['username']})" if ch.get("username") else ""
            lines.append(f"📢 {title}{uname} — {ch['chat_id']}")

    return "\n".join(lines)


def _preview_text(owner_id: int) -> str:
    ws_db = get_workspace_db(owner_id)
    pos = "⬆️ بالای متن" if ws_db.get_show_above_text() else "⬇️ پایین متن"
    size = "🖼 بزرگ" if ws_db.get_prefer_large_media() else "🖼 کوچک"
    return "\n".join(
        [
            "🖼 تنظیمات پیش‌فرض پیش‌نمایش و دکمه",
            "",
            f"• محل پیش‌نمایش: {pos}",
            f"• اندازه پیش‌نمایش: {size}",
            f"• متن پیش‌فرض دکمه شیشه‌ای: «{ws_db.get_default_button_text()}»",
        ]
    )


async def _safe_edit(query, text: str, reply_markup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        return
    except TelegramError as err:
        if "not modified" in str(err).lower():
            return
        logger.debug("ویرایش پیام پنل ناموفق: %s", err)

    if not query.message:
        return
    try:
        await query.message.reply_text(text, reply_markup=reply_markup)
    except TelegramError:
        logger.debug("ارسال پیام پنل ناموفق", exc_info=True)


async def _edit_by_id(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id,
    message_id,
    text: str,
    reply_markup=None,
) -> None:
    if chat_id is None or message_id is None:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramError as err:
        if "not modified" not in str(err).lower():
            logger.debug("ویرایش پیام ورودی ناموفق: %s", err)


def _store_prompt(context: ContextTypes.DEFAULT_TYPE, query) -> None:
    if query.message:
        context.user_data["st_msg"] = query.message.message_id
        context.user_data["st_chat"] = query.message.chat_id


async def _show_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    prompt_text: str,
) -> None:
    _store_prompt(context, query)
    await _safe_edit(query, prompt_text, settings_input_keyboard())


async def _retry_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    error_text: str,
    prompt_text: str,
) -> None:
    full = f"{error_text}\n\n{prompt_text}"
    chat_id = context.user_data.get("st_chat")
    message_id = context.user_data.get("st_msg")
    markup = settings_input_keyboard()

    if chat_id is not None and message_id is not None:
        await _edit_by_id(context, chat_id, message_id, full, markup)
        return

    if update.message:
        await update.message.reply_text(full, reply_markup=markup)


async def _show_section(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    text: str,
    reply_markup,
) -> int:
    if query and query.message:
        _store_prompt(context, query)
        await _safe_edit(query, text, reply_markup)
    return SETTINGS_PANEL


async def _handle_text_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[int]:
    if not update.message:
        return None
    text = (update.message.text or "").strip()
    if text in _CANCEL_TEXTS:
        return await cancel_settings(update, context)
    return None


@workspace_owner_only
async def cancel_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    chat_id = context.user_data.pop("st_chat", None)
    message_id = context.user_data.pop("st_msg", None)
    if chat_id is not None and message_id is not None:
        await _edit_by_id(context, chat_id, message_id, Msg.SETTINGS_CLOSED)

    if update.message:
        await update.message.reply_text(
            Msg.MAIN_MENU_PROMPT,
            reply_markup=main_menu_for(update.effective_user.id),
        )
    return ConversationHandler.END
