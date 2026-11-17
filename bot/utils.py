import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from .constants import Msg
from .database import registry
from .keyboards import main_menu_for

logger = logging.getLogger(__name__)

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

_TEHRAN_TZ = ZoneInfo("Asia/Tehran")
PANEL_MSG_KEY = "_panel_msgs"

_PRIVATE_TME_RE = re.compile(r"^https?://(?:t\.me|telegram\.me)/c/\d+/\d+", re.I)


def normalize_digits(text: Optional[str]) -> str:
    return (text or "").translate(_DIGIT_TRANSLATION)


def is_private_tme_link(url: Optional[str]) -> bool:
    if not url:
        return False
    return bool(_PRIVATE_TME_RE.match(url.strip()))


def normalize_photo_url(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """لینک ورودی عکس/پست را بررسی و نرمال‌سازی می‌کند."""
    text = (raw or "").strip()
    if not text:
        return None, "empty"

    if any(ch.isspace() for ch in text):
        return None, "invalid"

    if text.startswith("@") and "/" in text:
        body = text[1:]
        text = f"https://t.me/{body}"

    lowered = text.lower()
    if lowered.startswith(("t.me/", "telegram.me/")):
        text = f"https://{text}"

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "invalid"

    if "." not in parsed.netloc:
        return None, "invalid"

    if is_private_tme_link(text):
        return None, "private"

    return text, ""


def extract_forward_post_link(message: Any) -> Tuple[Optional[str], str]:
    """از پیام فورواردی کانال عمومی لینک `https://t.me/<username>/<msg_id>` می‌سازد."""
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        msg_id = getattr(origin, "message_id", None)
        if chat is not None and msg_id:
            username = getattr(chat, "username", None)
            if username:
                return f"https://t.me/{username}/{msg_id}", "ok"
            return None, "private_channel"
        sender_chat = getattr(origin, "sender_chat", None)
        if sender_chat is not None:
            username = getattr(sender_chat, "username", None)
            if username and msg_id:
                return f"https://t.me/{username}/{msg_id}", "ok"
            return None, "private_channel"

    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_msg_id = getattr(message, "forward_from_message_id", None)
    if fwd_chat is not None:
        username = getattr(fwd_chat, "username", None)
        if username and fwd_msg_id:
            return f"https://t.me/{username}/{fwd_msg_id}", "ok"
        return None, "private_channel"

    return None, "not_forward"


def build_telegram_message_link(
    chat_id: Optional[int],
    username: Optional[str],
    message_id: Optional[int],
) -> Optional[str]:
    if not message_id:
        return None
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    if chat_id is not None:
        raw_id = str(chat_id)
        if raw_id.startswith("-100"):
            raw_id = raw_id[4:]
        elif raw_id.startswith("-"):
            raw_id = raw_id[1:]
        return f"https://t.me/c/{raw_id}/{message_id}"
    return None


def shorten_text(text: str, limit: int = 80) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "…"


# ---------------------------------------------------------------------------
# تاریخ شمسی به وقت تهران (بدون dependency خارجی)
# ---------------------------------------------------------------------------

def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)

    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30

    return jy, jm, jd


def fmt_tehran(value: Any) -> str:
    if value is None:
        return "نامشخص"

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return "نامشخص"
        normalized = text.replace("T", " ").split(".")[0].strip()
        try:
            dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    local = dt.astimezone(_TEHRAN_TZ)
    jy, jm, jd = _gregorian_to_jalali(local.year, local.month, local.day)
    return (
        f"{jy:04d}/{jm:02d}/{jd:02d} — "
        f"{local.hour:02d}:{local.minute:02d}"
    )


# ---------------------------------------------------------------------------
# ابزارهای مشترک Handlerها
# ---------------------------------------------------------------------------

def track_user(update: Update) -> None:
    user = update.effective_user
    if user:
        registry.save_or_update_user(
            user.id,
            user.username,
            user.first_name,
            user.last_name,
        )


def register_panel_message(
    context: ContextTypes.DEFAULT_TYPE,
    message_id: Optional[int],
) -> None:
    if not message_id:
        return
    try:
        lst = context.chat_data.setdefault(PANEL_MSG_KEY, [])
        if message_id in lst:
            return
        lst.append(message_id)
        if len(lst) > 5:
            del lst[:-5]
    except (AttributeError, TypeError):
        pass


async def safe_edit_or_send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    message_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = False,
) -> int:
    if message_id is not None:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            return message_id
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return message_id
            logger.warning("ویرایش پیام %s ناموفق: %s", message_id, exc)
        except TelegramError as exc:
            logger.warning("ویرایش پیام %s ناموفق: %s", message_id, exc)

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )
    register_panel_message(context, sent.message_id)
    return sent.message_id


def total_pages(count: int, page_size: int) -> int:
    return max((count - 1) // page_size + 1, 1)


async def go_to_main_menu(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: Optional[int] = None,
) -> None:
    try:
        context.chat_data.pop(PANEL_MSG_KEY, None)
    except (AttributeError, TypeError):
        pass
    target_uid = user_id if user_id is not None else chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=Msg.MAIN_MENU_PROMPT,
        reply_markup=main_menu_for(target_uid),
    )


def forwarded_user_id(message: Any) -> Optional[int]:
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        user = getattr(origin, "sender_user", None)
        if user is not None:
            return user.id
    if getattr(message, "forward_from", None) is not None:
        return message.forward_from.id
    return None


def resolve_chat_target(message: Any):
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            return chat.id

    if getattr(message, "forward_from_chat", None) is not None:
        return message.forward_from_chat.id

    text = normalize_digits((getattr(message, "text", None) or "").strip())
    if text.startswith("@") and len(text) > 1:
        return text
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


async def precheck_chat(
    context: ContextTypes.DEFAULT_TYPE,
    target: Any,
):
    """بررسی دسترسی ربات به کانال و ادمین بودن آن."""
    try:
        chat = await context.bot.get_chat(target)
    except TelegramError:
        return None, Msg.CHANNEL_CHECK_FAILED

    if chat.type not in ("channel", "supergroup", "group"):
        return None, Msg.CHANNEL_INVALID

    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError:
        return None, Msg.CHANNEL_CHECK_FAILED

    if member.status not in ("administrator", "creator"):
        return None, Msg.CHANNEL_BOT_NOT_ADMIN

    return chat, None
