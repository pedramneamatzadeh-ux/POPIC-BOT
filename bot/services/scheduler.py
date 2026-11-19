"""سرویس زمان‌بندی پست‌ها (تاریخ میلادی + ساعت تهران + اجراگر پس‌زمینه)."""

import asyncio
import calendar
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram.error import TelegramError

from ..config import config
from ..constants import SCHEDULER_INTERVAL_SECONDS, Msg
from ..database import get_workspace_db
from .publisher import deserialize_buttons, send_post_to_chat

logger = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

GREGORIAN_MONTHS_FA: Tuple[Tuple[int, str], ...] = (
    (1, "ژانویه (۱)"),
    (2, "فوریه (۲)"),
    (3, "مارس (۳)"),
    (4, "آوریل (۴)"),
    (5, "مه (۵)"),
    (6, "ژوئن (۶)"),
    (7, "ژوئیه (۷)"),
    (8, "اوت (۸)"),
    (9, "سپتامبر (۹)"),
    (10, "اکتبر (۱۰)"),
    (11, "نوامبر (۱۱)"),
    (12, "دسامبر (۱۲)"),
)


def month_name_fa(month: int) -> str:
    for num, name in GREGORIAN_MONTHS_FA:
        if num == int(month):
            return name
    return str(month)


def days_in_gregorian_month(year: int, month: int) -> int:
    return calendar.monthrange(int(year), int(month))[1]


def now_tehran(now_utc: Optional[datetime] = None) -> datetime:
    base = now_utc or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(TEHRAN_TZ)


def default_schedule_draft(now_utc: Optional[datetime] = None) -> Dict[str, int]:
    cur = now_tehran(now_utc)
    return {
        "year": cur.year,
        "month": cur.month,
        "day": cur.day,
        "hour": cur.hour,
        "minute": cur.minute,
    }


def draft_from_utc_iso(
    utc_iso: Optional[str],
    now_utc: Optional[datetime] = None,
) -> Dict[str, int]:
    if utc_iso:
        try:
            dt = datetime.strptime(
                utc_iso.replace("T", " ").split(".")[0].strip(),
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=timezone.utc)
            local = dt.astimezone(TEHRAN_TZ)
            return {
                "year": local.year,
                "month": local.month,
                "day": local.day,
                "hour": local.hour,
                "minute": local.minute,
            }
        except ValueError:
            pass
    return default_schedule_draft(now_utc)


def parse_time_input(raw_text: Optional[str]) -> Optional[Tuple[int, int]]:
    from ..utils import normalize_digits

    clean = normalize_digits((raw_text or "").strip())
    m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", clean)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) is not None else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def validate_future_tehran_dt(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    now_utc: Optional[datetime] = None,
) -> Tuple[Optional[str], Optional[str]]:
    if not (1 <= month <= 12):
        return None, Msg.INVALID_COMMAND
    max_days = days_in_gregorian_month(year, month)
    if not (1 <= day <= max_days):
        return None, Msg.SCHED_DAY_INVALID.format(max_days=max_days)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, Msg.SCHED_TIME_INVALID

    target_local = datetime(year, month, day, hour, minute, 0, tzinfo=TEHRAN_TZ)
    current_local = now_tehran(now_utc)
    if target_local <= current_local:
        return None, Msg.SCHED_PAST_ERROR

    utc_str = target_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return utc_str, None


def tehran_date_key(utc_iso: Optional[str]) -> str:
    if not utc_iso:
        return "0000-00-00"
    try:
        dt = datetime.strptime(
            utc_iso.replace("T", " ").split(".")[0].strip(),
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
        local = dt.astimezone(TEHRAN_TZ)
        return local.strftime("%Y-%m-%d")
    except ValueError:
        return str(utc_iso)[:10]


def fmt_schedule_tehran(utc_iso: Optional[str]) -> str:
    if not utc_iso:
        return "—"
    try:
        dt = datetime.strptime(
            utc_iso.replace("T", " ").split(".")[0].strip(),
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
        local = dt.astimezone(TEHRAN_TZ)
        return f"{local.year:04d}/{local.month:02d}/{local.day:02d} — {local.hour:02d}:{local.minute:02d}"
    except ValueError:
        return str(utc_iso)


def _list_workspace_ids() -> List[int]:
    if not os.path.isdir(config.WORKSPACES_DIR):
        return []
    ids: List[int] = []
    for name in os.listdir(config.WORKSPACES_DIR):
        if name.startswith("user_") and name.endswith(".db"):
            raw = name[len("user_") : -len(".db")]
            if raw.isdigit():
                ids.append(int(raw))
    return sorted(ids)


async def process_due_scheduled_posts(
    bot: Any,
    now_utc: Optional[datetime] = None,
) -> int:
    """تمام فضاهای کاری را بررسی و پست‌های زمان‌بندی‌شدهٔ سررسیده را ارسال می‌کند."""
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now_iso = current.strftime("%Y-%m-%d %H:%M:%S")

    sent_total = 0
    for ws_id in _list_workspace_ids():
        ws_db = get_workspace_db(ws_id)
        due_posts = ws_db.get_due_scheduled_posts(now_iso)
        for post in due_posts:
            post_id = int(post["id"])
            if not ws_db.claim_scheduled_post(post_id):
                continue

            try:
                raw_targets = json.loads(post.get("scheduled_chat_ids") or "[]")
            except (TypeError, ValueError):
                raw_targets = []
            target_chat_ids = [int(x) for x in raw_targets if str(x).lstrip("-").isdigit()]

            if not target_chat_ids:
                ws_db.finish_scheduled_post(
                    post_id,
                    success=False,
                    error="هیچ مقصد معتبری برای زمان‌بندی ثبت نشده بود.",
                )
                continue

            buttons = deserialize_buttons(post.get("buttons_json"))
            media_type = post.get("media_type") or "link"
            photo_url = post.get("photo_url") or ""
            photo_file_id = post.get("photo_file_id") or ""
            errors: List[str] = []
            delivered = 0

            for chat_id in target_chat_ids:
                ch_info = ws_db.get_target_channel_by_chat_id(chat_id)
                try:
                    sent = await send_post_to_chat(
                        bot=bot,
                        chat_id=chat_id,
                        text=post["post_text"],
                        entities_json=post.get("entities_json"),
                        photo_url=photo_url,
                        show_above_text=bool(post.get("show_above_text")),
                        prefer_large_media=bool(post.get("prefer_large_media")),
                        buttons=buttons,
                        workspace_id=ws_id,
                        post_id=post_id,
                        media_type=media_type,
                        photo_file_id=photo_file_id,
                    )
                    ws_db.record_delivery(
                        post_id=post_id,
                        target_type="channel",
                        target_chat_id=chat_id,
                        message_id=sent.message_id,
                        sent_by=int(post.get("created_by") or ws_id),
                        media_type=media_type,
                    )
                    ws_db.mark_post_published(
                        post_id=post_id,
                        channel_chat_id=chat_id,
                        channel_message_id=sent.message_id,
                        channel_title=(ch_info or {}).get("title") or str(chat_id),
                        channel_username=(ch_info or {}).get("username") or "",
                    )
                    delivered += 1
                    sent_total += 1
                except TelegramError as exc:
                    errors.append(f"{chat_id}: {exc}")
                    logger.warning(
                        "ارسال زمان‌بندی پست %s به %s ناموفق بود: %s",
                        post_id,
                        chat_id,
                        exc,
                    )
                except Exception as exc:
                    errors.append(f"{chat_id}: {exc}")
                    logger.exception(
                        "خطای غیرمنتظره در ارسال زمان‌بندی پست %s به %s",
                        post_id,
                        chat_id,
                    )

            ws_db.finish_scheduled_post(
                post_id,
                success=delivered > 0,
                error=" | ".join(errors),
            )

            notify_uid = int(post.get("created_by") or ws_id)
            title_part = f" («{post['title']}»)" if post.get("title") else ""
            if delivered > 0:
                msg_text = (
                    f"⏰✅ پست زمان‌بندی‌شده #{post_id}{title_part} در "
                    f"{delivered} مقصد ارسال شد."
                )
            else:
                msg_text = (
                    f"⏰❌ ارسال پست زمان‌بندی‌شده #{post_id}{title_part} ناموفق بود:\n"
                    + "\n".join(errors)
                )
            try:
                await bot.send_message(chat_id=notify_uid, text=msg_text)
            except TelegramError:
                pass

    return sent_total


async def scheduler_loop(application: Any) -> None:
    """حلقه پس‌زمینه بررسی و ارسال پست‌های زمان‌بندی‌شده."""
    while True:
        try:
            await process_due_scheduled_posts(application.bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("خطا در حلقه زمان‌بندی پست‌ها")
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
