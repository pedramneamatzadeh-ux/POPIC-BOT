"""متون پنل، هلپرها و لغو گفتگوی تنظیمات."""

import logging
from typing import Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ...config import config
from ...constants import (
    BOT_VERSION,
    BOT_VERSION_DATE,
    SETTINGS_PANEL,
    Msg,
)
from ...database import db
from ...decorators import owner_only
from ...keyboards import main_menu_for, settings_input_keyboard

logger = logging.getLogger(__name__)

_CANCEL_TEXTS = ("لغو", "بازگشت به منوی اصلی")


def _panel_text() -> str:
    owner_id = config.OWNER_ID
    admins = [
        item for item in (config.ADMIN_IDS or []) if item != owner_id
    ]
    channels = db.get_target_channels()
    pos = "بالای متن ⬆️" if config.DEFAULT_SHOW_ABOVE_TEXT else "پایین متن ⬇️"
    size = "بزرگ 🖼" if config.DEFAULT_PREFER_LARGE_MEDIA else "کوچک 🖼"

    return "\n".join(
        [
            Msg.SETTINGS_TITLE,
            "",
            f"👨‍💼 ادمین‌ها: {len(admins)} نفر (به‌علاوه مالک 👑)",
            f"📢 کانال‌های مقصد: {len(channels)}",
            f"🖼 پیش‌فرض پیش‌نمایش: {pos} | {size}",
            f"🏷 متن پیش‌فرض دکمه: «{config.DEFAULT_BUTTON_TEXT}»",
            f"🧩 نسخه: {BOT_VERSION} ({BOT_VERSION_DATE})",
        ]
    )


def _user_names() -> dict:
    names = {}
    try:
        for row in db.get_all_users():
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


def _admin_names() -> dict:
    names = _user_names()
    owner = config.OWNER_ID
    if owner in names:
        names[owner] = f"{names[owner]} 👑"
    return names


def _admins_text() -> str:
    owner_id = config.OWNER_ID
    names = _user_names()
    lines = ["👨‍💼 ادمین‌ها", ""]

    if owner_id is not None:
        owner_name = names.get(owner_id) or "—"
        lines.append(f"👑 {owner_name} — {owner_id} (مالک، ثابت)")

    others = [
        item for item in (config.ADMIN_IDS or []) if item != owner_id
    ]
    if others:
        lines.extend(
            f"👤 {names.get(int(item)) or '—'} — {item}"
            for item in others
        )
    else:
        lines.append("— ادمین دیگری ثبت نشده است")

    return "\n".join(lines)


def _channels_text() -> str:
    channels = db.get_target_channels()
    lines = ["📢 کانال‌های مقصد", ""]

    if not channels:
        lines.append("— هیچ کانال مقصدی ثبت نشده است")
    else:
        for ch in channels:
            title = ch.get("title") or "—"
            uname = f" (@{ch['username']})" if ch.get("username") else ""
            lines.append(f"📢 {title}{uname} — {ch['chat_id']}")

    return "\n".join(lines)


def _preview_text() -> str:
    pos = "⬆️ بالای متن" if config.DEFAULT_SHOW_ABOVE_TEXT else "⬇️ پایین متن"
    size = "🖼 بزرگ" if config.DEFAULT_PREFER_LARGE_MEDIA else "🖼 کوچک"
    return "\n".join(
        [
            "🖼 تنظیمات پیش‌فرض پیش‌نمایش و دکمه",
            "",
            f"• محل پیش‌نمایش: {pos}",
            f"• اندازه پیش‌نمایش: {size}",
            f"• متن پیش‌فرض دکمه شیشه‌ای: «{config.DEFAULT_BUTTON_TEXT}»",
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


@owner_only
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
