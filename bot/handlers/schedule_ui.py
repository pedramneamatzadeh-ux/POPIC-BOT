"""رابط مشترک زمان‌بندی پست (تاریخ میلادی + ساعت تهران) برای ساخت پست و پست‌های ذخیره‌شده."""

import html
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from telegram import Update
from telegram.error import TelegramError

from ..constants import Msg
from ..keyboards import (
    input_back_keyboard,
    schedule_months_keyboard,
    schedule_panel_keyboard,
    schedule_targets_keyboard,
)
from ..services.scheduler import (
    days_in_gregorian_month,
    default_schedule_draft,
    draft_from_utc_iso,
    fmt_schedule_tehran,
    month_name_fa,
    now_tehran,
    parse_time_input,
    validate_future_tehran_dt,
)
from ..utils import normalize_digits, register_panel_message, safe_edit_or_send


def init_schedule_state(
    existing_utc_iso: Optional[str] = None,
    existing_chat_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    return {
        "draft": draft_from_utc_iso(existing_utc_iso) if existing_utc_iso else default_schedule_draft(),
        "targets": [int(x) for x in (existing_chat_ids or [])],
        "has_existing": bool(existing_utc_iso),
    }


def build_schedule_panel_text(
    draft: Dict[str, int],
    existing_utc_iso: Optional[str] = None,
) -> str:
    cur = now_tehran()
    d, m, y = int(draft["day"]), int(draft["month"]), int(draft["year"])
    hh, mm = int(draft["hour"]), int(draft["minute"])
    lines = [
        "⏰ <b>زمان‌بندی ارسال پست</b>",
        f"• زمان فعلی (تهران): <code>{cur.year:04d}/{cur.month:02d}/{cur.day:02d} — {cur.hour:02d}:{cur.minute:02d}</code>",
        f"• زمان انتخابی: <b>{y:04d}/{m:02d}/{d:02d} ({month_name_fa(m)}) — {hh:02d}:{mm:02d}</b>",
    ]
    if existing_utc_iso:
        lines.append(f"• زمان‌بندی ثبت‌شده فعلی: <code>{html.escape(fmt_schedule_tehran(existing_utc_iso))}</code>")
    lines.extend(["", "روی هر دکمه بزن تا مقدار آن را تغییر دهی:"])
    return "\n".join(lines)


async def render_schedule_panel(
    context: Any,
    chat_id: int,
    sched: Dict[str, Any],
    prefix: str,
    back_callback: str,
    message_id: Optional[int] = None,
    existing_utc_iso: Optional[str] = None,
) -> int:
    mid = await safe_edit_or_send(
        context=context,
        chat_id=chat_id,
        text=build_schedule_panel_text(sched["draft"], existing_utc_iso=existing_utc_iso),
        reply_markup=schedule_panel_keyboard(
            sched["draft"],
            prefix=prefix,
            has_existing=bool(sched.get("has_existing")),
            back_callback=back_callback,
        ),
        message_id=message_id,
        parse_mode="HTML",
    )
    return mid


async def handle_schedule_callback(
    *,
    context: Any,
    query: Any,
    ws_db: Any,
    chat_id: int,
    msg_id: Optional[int],
    data: str,
    prefix: str,
    sched: Dict[str, Any],
    states: Dict[str, int],
    back_callback: str,
    existing_utc_iso: Optional[str] = None,
    on_save: Callable[[str, List[int]], Any],
    on_clear: Callable[[], Any],
    on_back: Callable[[], Any],
) -> int:
    """مدیریت تمام کلیک‌های پنل زمان‌بندی برای prefix ('pb' یا 'pm')."""
    draft = sched["draft"]
    action = data[len(prefix) + 1 :]  # after '<prefix>:'

    if action == "sched_panel":
        await query.answer()
        await render_schedule_panel(
            context,
            chat_id,
            sched,
            prefix=prefix,
            back_callback=back_callback,
            message_id=msg_id,
            existing_utc_iso=existing_utc_iso,
        )
        return states["panel"]

    if action == "sched_day":
        max_days = days_in_gregorian_month(draft["year"], draft["month"])
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.SCHED_DAY_PROMPT.format(
                month_name=month_name_fa(draft["month"]),
                year=draft["year"],
                max_days=max_days,
            ),
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return states["day"]

    if action == "sched_month":
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="📆 ماه میلادی موردنظر را انتخاب کن:",
            reply_markup=schedule_months_keyboard(draft["month"], prefix=prefix),
            message_id=msg_id,
        )
        return states["panel"]

    if action.startswith("sched_m:"):
        raw = action.split(":", 1)[1]
        if not raw.isdigit() or not (1 <= int(raw) <= 12):
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return states["panel"]
        new_month = int(raw)
        cur = now_tehran()
        if int(draft["year"]) == cur.year and new_month < cur.month:
            await query.answer(Msg.SCHED_PAST_ERROR, show_alert=True)
            return states["panel"]

        draft["month"] = new_month
        max_days = days_in_gregorian_month(draft["year"], new_month)
        if int(draft["day"]) > max_days:
            draft["day"] = max_days
        await query.answer(f"✅ ماه به {month_name_fa(new_month)} تغییر کرد.")
        await render_schedule_panel(
            context,
            chat_id,
            sched,
            prefix=prefix,
            back_callback=back_callback,
            message_id=msg_id,
            existing_utc_iso=existing_utc_iso,
        )
        return states["panel"]

    if action == "sched_year":
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.SCHED_YEAR_PROMPT,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return states["year"]

    if action == "sched_time":
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=Msg.SCHED_TIME_PROMPT,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return states["time"]

    if action == "sched_confirm":
        utc_str, err = validate_future_tehran_dt(
            draft["year"], draft["month"], draft["day"], draft["hour"], draft["minute"]
        )
        if err or not utc_str:
            await query.answer(err or Msg.SCHED_PAST_ERROR, show_alert=True)
            return states["panel"]

        channels = ws_db.get_target_channels()
        if not channels:
            await query.answer(Msg.NO_CHANNELS_CONFIGURED, show_alert=True)
            return states["panel"]

        if len(channels) == 1:
            target_ids = [int(channels[0]["chat_id"])]
            await query.answer("⏰ زمان‌بندی ثبت شد.")
            return await on_save(utc_str, target_ids)

        if not sched.get("targets"):
            sched["targets"] = [int(channels[0]["chat_id"])]

        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="📢 کانال‌ها یا گروه‌های مقصد را برای ارسال زمان‌بندی‌شده انتخاب کن:",
            reply_markup=schedule_targets_keyboard(
                channels, sched["targets"], prefix=prefix
            ),
            message_id=msg_id,
        )
        return states["target"]

    if action.startswith("sched_tg:"):
        raw = action.split(":", 1)[1]
        if not raw.lstrip("-").isdigit():
            await query.answer(Msg.INVALID_COMMAND, show_alert=True)
            return states["target"]
        cid = int(raw)
        selected = [int(x) for x in (sched.get("targets") or [])]
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)
        sched["targets"] = selected
        await query.answer()
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text="📢 کانال‌ها یا گروه‌های مقصد را برای ارسال زمان‌بندی‌شده انتخاب کن:",
            reply_markup=schedule_targets_keyboard(
                ws_db.get_target_channels(), selected, prefix=prefix
            ),
            message_id=msg_id,
        )
        return states["target"]

    if action == "sched_save":
        selected = [int(x) for x in (sched.get("targets") or [])]
        if not selected:
            await query.answer("⚠️ حداقل یک کانال یا گروه مقصد انتخاب کن!", show_alert=True)
            return states["target"]
        utc_str, err = validate_future_tehran_dt(
            draft["year"], draft["month"], draft["day"], draft["hour"], draft["minute"]
        )
        if err or not utc_str:
            await query.answer(err or Msg.SCHED_PAST_ERROR, show_alert=True)
            await render_schedule_panel(
                context,
                chat_id,
                sched,
                prefix=prefix,
                back_callback=back_callback,
                message_id=msg_id,
                existing_utc_iso=existing_utc_iso,
            )
            return states["panel"]

        await query.answer("⏰ زمان‌بندی با موفقیت ثبت شد!")
        return await on_save(utc_str, selected)

    if action == "sched_clear":
        await query.answer("🗑 زمان‌بندی لغو شد.")
        return await on_clear()

    if action == back_callback.split(":", 1)[1] if ":" in back_callback else False:
        await query.answer()
        return await on_back()

    await query.answer(Msg.INVALID_COMMAND, show_alert=True)
    return states["panel"]


async def handle_schedule_day_input(
    *,
    update: Update,
    context: Any,
    sched: Dict[str, Any],
    prefix: str,
    panel_msg_id: Optional[int],
    back_callback: str,
    states: Dict[str, int],
    existing_utc_iso: Optional[str] = None,
) -> int:
    message = update.message
    draft = sched["draft"]
    max_days = days_in_gregorian_month(draft["year"], draft["month"])
    raw = normalize_digits((getattr(message, "text", None) or "").strip())

    if not raw.isdigit() or not (1 <= int(raw) <= max_days):
        sent = await message.reply_text(
            Msg.SCHED_DAY_INVALID.format(max_days=max_days),
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
        )
        register_panel_message(context, sent.message_id)
        return states["day"]

    day = int(raw)
    cur = now_tehran()
    if (
        int(draft["year"]) == cur.year
        and int(draft["month"]) == cur.month
        and day < cur.day
    ):
        sent = await message.reply_text(
            Msg.SCHED_PAST_ERROR,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
        )
        register_panel_message(context, sent.message_id)
        return states["day"]

    draft["day"] = day
    try:
        await message.delete()
    except TelegramError:
        pass
    await render_schedule_panel(
        context,
        message.chat_id,
        sched,
        prefix=prefix,
        back_callback=back_callback,
        message_id=panel_msg_id,
        existing_utc_iso=existing_utc_iso,
    )
    return states["panel"]


async def handle_schedule_year_input(
    *,
    update: Update,
    context: Any,
    sched: Dict[str, Any],
    prefix: str,
    panel_msg_id: Optional[int],
    back_callback: str,
    states: Dict[str, int],
    existing_utc_iso: Optional[str] = None,
) -> int:
    message = update.message
    draft = sched["draft"]
    cur = now_tehran()
    raw = normalize_digits((getattr(message, "text", None) or "").strip())

    if not raw.isdigit() or not (cur.year <= int(raw) <= 2100):
        err_msg = (
            Msg.SCHED_PAST_ERROR
            if (raw.isdigit() and 1900 <= int(raw) < cur.year)
            else Msg.SCHED_YEAR_INVALID.format(min_year=cur.year)
        )
        sent = await message.reply_text(
            err_msg,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
        )
        register_panel_message(context, sent.message_id)
        return states["year"]

    draft["year"] = int(raw)
    max_days = days_in_gregorian_month(draft["year"], draft["month"])
    if int(draft["day"]) > max_days:
        draft["day"] = max_days

    try:
        await message.delete()
    except TelegramError:
        pass
    await render_schedule_panel(
        context,
        message.chat_id,
        sched,
        prefix=prefix,
        back_callback=back_callback,
        message_id=panel_msg_id,
        existing_utc_iso=existing_utc_iso,
    )
    return states["panel"]


async def handle_schedule_time_input(
    *,
    update: Update,
    context: Any,
    sched: Dict[str, Any],
    prefix: str,
    panel_msg_id: Optional[int],
    back_callback: str,
    states: Dict[str, int],
    existing_utc_iso: Optional[str] = None,
) -> int:
    message = update.message
    draft = sched["draft"]
    parsed = parse_time_input(getattr(message, "text", None))
    if parsed is None:
        sent = await message.reply_text(
            Msg.SCHED_TIME_INVALID,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
            parse_mode="HTML",
        )
        register_panel_message(context, sent.message_id)
        return states["time"]

    hour, minute = parsed
    cur = now_tehran()
    if (
        int(draft["year"]) == cur.year
        and int(draft["month"]) == cur.month
        and int(draft["day"]) == cur.day
        and (hour, minute) <= (cur.hour, cur.minute)
    ):
        sent = await message.reply_text(
            Msg.SCHED_PAST_ERROR,
            reply_markup=input_back_keyboard(f"{prefix}:sched_panel"),
        )
        register_panel_message(context, sent.message_id)
        return states["time"]

    draft["hour"] = hour
    draft["minute"] = minute
    try:
        await message.delete()
    except TelegramError:
        pass
    await render_schedule_panel(
        context,
        message.chat_id,
        sched,
        prefix=prefix,
        back_callback=back_callback,
        message_id=panel_msg_id,
        existing_utc_iso=existing_utc_iso,
    )
    return states["panel"]
