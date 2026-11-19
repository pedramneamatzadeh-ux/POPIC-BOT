from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .config import config
from .constants import OLD_EDIT_BUTTON, SETTINGS_BUTTON, SWITCH_WORKSPACE_BUTTON
from .database import registry, resolve_user_workspace
from .services.publisher import (
    STYLE_ICONS,
    TYPE_ICONS,
    group_buttons_by_row,
    normalize_buttons,
)
from .services.scheduler import GREGORIAN_MONTHS_FA, month_name_fa

# ---------------------------------------------------------------------------
# رنگ دکمه‌ها (Bot API 9.4 — style: primary/success/danger)
# ---------------------------------------------------------------------------

_RED_WORDS = ("خیر", "بازگشت", "منوی اصلی", "حذف", "لغو", "انصراف", "بستن")


def _infer_style(text: str, callback_data: str = "") -> Optional[str]:
    if any(word in text for word in _RED_WORDS):
        return "danger"
    if any(
        token in callback_data
        for token in ("_del", ":del", "delete", "btn_del")
    ):
        return "danger"
    return "success"


def ib(
    text: str,
    style: Optional[str] = None,
    **kwargs,
) -> InlineKeyboardButton:
    if style is None:
        style = _infer_style(text, str(kwargs.get("callback_data") or ""))
    if style:
        kwargs["style"] = style
    return InlineKeyboardButton(text, **kwargs)


def ib_exact(
    text: str,
    style: Optional[str] = None,
    **kwargs,
) -> InlineKeyboardButton:
    """دکمه اینلاین با رنگ دقیق انتخابی (بدون استنتاج خودکار رنگ)."""
    if style in ("primary", "success", "danger"):
        kwargs["style"] = style
    return InlineKeyboardButton(text, **kwargs)


MAIN_MENU_STYLES = {
    "📝 ساخت پست جدید": "success",
    "📂 پست‌های ذخیره‌شده": "primary",
    OLD_EDIT_BUTTON: "primary",
    "📊 آمار": "primary",
    SWITCH_WORKSPACE_BUTTON: "primary",
    SETTINGS_BUTTON: "danger",
    "💾 بک‌آپ": "danger",
}


def _menu_row(row: list) -> list:
    return [
        KeyboardButton(text, style=MAIN_MENU_STYLES.get(text, "primary"))
        for text in row
    ]


def build_main_menu(
    is_owner: bool = True,
    has_admin_roles: bool = False,
    is_env_owner: bool = False,
) -> ReplyKeyboardMarkup:
    rows: List[List[str]] = [
        ["📝 ساخت پست جدید", "📂 پست‌های ذخیره‌شده"],
        [OLD_EDIT_BUTTON, "📊 آمار"],
    ]

    if is_owner:
        last_row = [SETTINGS_BUTTON]
        if is_env_owner:
            last_row.append("💾 بک‌آپ")
        if has_admin_roles:
            last_row.append(SWITCH_WORKSPACE_BUTTON)
        rows.append(last_row)
    else:
        last_row = []
        if is_env_owner:
            last_row.append("💾 بک‌آپ")
        if has_admin_roles:
            last_row.append(SWITCH_WORKSPACE_BUTTON)
        if last_row:
            rows.append(last_row)

    return ReplyKeyboardMarkup(
        [_menu_row(r) for r in rows],
        resize_keyboard=True,
    )


def main_menu_for(user_id: Optional[int]) -> ReplyKeyboardMarkup:
    """منوی اصلی بر اساس نقش فعال کاربر (و نمایش بک‌آپ فقط برای OWNER_ID در .env)."""
    if user_id is None:
        return build_main_menu(
            is_owner=True,
            has_admin_roles=False,
            is_env_owner=False,
        )

    _, status, _ = resolve_user_workspace(user_id)
    is_owner = status != "admin"
    has_admin_roles = bool(registry.get_admin_workspaces(user_id))
    is_env_owner = config.OWNER_ID is not None and int(user_id) == int(config.OWNER_ID)
    return build_main_menu(
        is_owner=is_owner,
        has_admin_roles=has_admin_roles,
        is_env_owner=is_env_owner,
    )


def workspace_choice_keyboard(
    user_id: int,
    admin_workspaces: List[Dict[str, Any]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            ib(
                "👑 ورود به عنوان مالک (دیتابیس شخصی خودم)",
                callback_data=f"ws_pick:{int(user_id)}",
                style="success",
            )
        ]
    ]
    for ws in admin_workspaces:
        owner_id = ws["workspace_owner_id"]
        owner_name = ws.get("owner_display") or str(owner_id)
        rows.append(
            [
                ib(
                    f"👨‍💼 ادمینِ «{owner_name}»",
                    callback_data=f"ws_pick:{owner_id}",
                    style="primary",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# دکمه‌های لغو اینلاین + تأیید لغو
# ---------------------------------------------------------------------------

POST_TITLE_INLINE = InlineKeyboardMarkup(
    [
        [ib("⏭ رد شدن (بدون نام)", callback_data="pb:skip_title", style="primary")],
        [ib("❌ لغو ساخت پست", callback_data="cancel_prompt", style="danger")],
    ]
)

POST_CANCEL_INLINE = InlineKeyboardMarkup(
    [[ib("❌ لغو ساخت پست", callback_data="cancel_prompt", style="danger")]]
)

CANCEL_CONFIRM_INLINE = InlineKeyboardMarkup(
    [
        [
            ib("✅ بله، لغو کن", style="danger", callback_data="cancel_yes"),
            ib("❌ نه، ادامه بده", style="primary", callback_data="cancel_no"),
        ]
    ]
)


def input_back_keyboard(back_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[ib("◀️ بازگشت", callback_data=back_callback, style="danger")]]
    )


def build_pagination_row(
    page: int,
    total_pages_count: int,
    previous_callback: str,
    refresh_callback: str,
    next_callback: str,
) -> List[InlineKeyboardButton]:
    row: List[InlineKeyboardButton] = []

    if page > 0:
        row.append(
            ib("◀️ قبلی", callback_data=previous_callback, style="success")
        )

    row.append(
        ib("🔄", callback_data=refresh_callback, style="primary")
    )

    if page < total_pages_count - 1:
        row.append(
            ib("▶️ بعدی", callback_data=next_callback, style="success")
        )

    return row


# ---------------------------------------------------------------------------
# کیبوردهای کنترل پیش‌نمایش و ویرایشگر پیشرفته دکمه‌های شیشه‌ای
# ---------------------------------------------------------------------------

def post_media_choice_keyboard(
    prefix: str = "pb",
    back_callback: Optional[str] = None,
    include_cancel: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [ib("🚫 بدون عکس", callback_data=f"{prefix}:media:none", style="primary")],
        [ib("📷 عکس عادی (روی پیام)", callback_data=f"{prefix}:media:photo", style="primary")],
        [ib("🔗 عکس به صورت لینک", callback_data=f"{prefix}:media:link", style="success")],
    ]
    if back_callback:
        rows.append([ib("◀️ بازگشت", callback_data=back_callback, style="danger")])
    elif include_cancel:
        rows.append([ib("❌ لغو ساخت پست", callback_data="cancel_prompt", style="danger")])
    return InlineKeyboardMarkup(rows)


def post_caption_too_long_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [ib("🔗 تغییر به عکس با لینک (تا ۴۰۹۶ کاراکتر)", callback_data="pb:media:link", style="success")],
            [ib("✏️ ویرایش و کوتاه‌کردن متن", callback_data="pb:edit_text_pre", style="primary")],
            [ib("🚫 ادامه بدون عکس", callback_data="pb:media:none", style="primary")],
            [ib("❌ لغو ساخت پست", callback_data="cancel_prompt", style="danger")],
        ]
    )


def post_preview_controls_keyboard(
    show_above_text: bool,
    prefer_large_media: bool,
    extra_buttons_count: int = 0,
    media_type: str = "link",
) -> InlineKeyboardMarkup:
    pos_label = (
        "⬆️ پیش‌نمایش: بالای متن" if show_above_text else "⬇️ پیش‌نمایش: پایین متن"
    )
    size_label = (
        "🖼 اندازه: بزرگ" if prefer_large_media else "🖼 اندازه: کوچک"
    )
    btns_label = (
        f"🔘 دکمه‌های شیشه‌ای ({extra_buttons_count})"
        if extra_buttons_count
        else "🔘 دکمه شیشه‌ای (اختیاری)"
    )

    rows = [
        [
            ib("📤 ارسال به کانال", callback_data="pb:send_ch", style="success"),
            ib("🎯 انتخاب مقصد", callback_data="pb:targets", style="success"),
        ],
    ]
    if media_type == "link":
        rows.append(
            [
                ib(pos_label, callback_data="pb:toggle_pos", style="primary"),
                ib(size_label, callback_data="pb:toggle_size", style="primary"),
            ]
        )
    rows.extend(
        [
            [
                ib(btns_label, callback_data="pb:btns", style="primary"),
                ib("⏰ زمان‌بندی پست", callback_data="pb:sched", style="primary"),
            ],
            [
                ib("✏️ ویرایش متن", callback_data="pb:edit_text", style="primary"),
                ib("🖼 تغییر عکس / حالت", callback_data="pb:change_media", style="primary"),
            ],
            [
                ib("✅ ذخیره و منوی اصلی", callback_data="pb:done", style="success"),
                ib("❌ لغو", callback_data="cancel_prompt", style="danger"),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _chat_icon(chat_type: str) -> str:
    return "👥" if chat_type in ("group", "supergroup") else "📢"


def post_channels_keyboard(
    channels: List[Dict[str, Any]],
    callback_prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        title = ch.get("title") or (
            f"@{ch['username']}" if ch.get("username") else str(ch["chat_id"])
        )
        icon = _chat_icon(ch.get("chat_type") or "channel")
        rows.append(
            [
                ib(
                    f"{icon} {title}",
                    callback_data=f"{callback_prefix}{ch['id']}",
                    style="primary",
                )
            ]
        )
    rows.append([ib("◀️ بازگشت", callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(rows)


def post_target_picker_keyboard(
    channels: List[Dict[str, Any]],
    selected_chat_ids: List[int],
    prefix: str = "pb",
    back_callback: str = "pb:back_preview",
) -> InlineKeyboardMarkup:
    selected_set = {int(x) for x in selected_chat_ids}
    rows: List[List[InlineKeyboardButton]] = []
    for ch in channels:
        cid = int(ch["chat_id"])
        title = ch.get("title") or (
            f"@{ch['username']}" if ch.get("username") else str(cid)
        )
        icon = _chat_icon(ch.get("chat_type") or "channel")
        mark = "✅" if cid in selected_set else "⬜"
        rows.append(
            [
                ib(
                    f"{mark} {icon} {title}",
                    callback_data=f"{prefix}:tg_toggle:{cid}",
                    style="success" if cid in selected_set else "primary",
                )
            ]
        )
    rows.append(
        [ib("✅ تأیید مقصد", callback_data=f"{prefix}:tg_done", style="success")]
    )
    rows.append([ib("◀️ بازگشت", callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# کیبوردهای زمان‌بندی پست (تاریخ میلادی + ساعت تهران)
# ---------------------------------------------------------------------------

def schedule_panel_keyboard(
    draft: Dict[str, int],
    prefix: str = "pb",
    has_existing: bool = False,
    back_callback: str = "pb:back_preview",
) -> InlineKeyboardMarkup:
    day = int(draft["day"])
    month = int(draft["month"])
    year = int(draft["year"])
    hour = int(draft["hour"])
    minute = int(draft["minute"])

    rows = [
        [
            ib(f"📅 روز: {day:02d}", callback_data=f"{prefix}:sched_day", style="primary"),
            ib(f"📆 ماه: {month_name_fa(month)}", callback_data=f"{prefix}:sched_month", style="primary"),
        ],
        [
            ib(f"🗓 سال: {year}", callback_data=f"{prefix}:sched_year", style="primary"),
            ib(f"🕒 ساعت (تهران): {hour:02d}:{minute:02d}", callback_data=f"{prefix}:sched_time", style="primary"),
        ],
        [
            ib("✅ تأیید زمان و انتخاب مقصد", callback_data=f"{prefix}:sched_confirm", style="success"),
        ],
    ]
    if has_existing:
        rows.append(
            [ib("🗑 لغو زمان‌بندی فعلی", callback_data=f"{prefix}:sched_clear", style="danger")]
        )
    rows.append([ib("◀️ بازگشت", callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(rows)


def schedule_months_keyboard(
    selected_month: int,
    prefix: str = "pb",
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    cur_row: List[InlineKeyboardButton] = []
    for num, name in GREGORIAN_MONTHS_FA:
        mark = "✓ " if num == int(selected_month) else ""
        cur_row.append(
            ib(
                f"{mark}{name}",
                callback_data=f"{prefix}:sched_m:{num}",
                style="success" if num == int(selected_month) else "primary",
            )
        )
        if len(cur_row) == 3:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        rows.append(cur_row)
    rows.append([ib("◀️ بازگشت", callback_data=f"{prefix}:sched_panel", style="danger")])
    return InlineKeyboardMarkup(rows)


def schedule_targets_keyboard(
    channels: List[Dict[str, Any]],
    selected_chat_ids: List[int],
    prefix: str = "pb",
) -> InlineKeyboardMarkup:
    selected_set = {int(x) for x in selected_chat_ids}
    rows: List[List[InlineKeyboardButton]] = []
    for ch in channels:
        cid = int(ch["chat_id"])
        title = ch.get("title") or (
            f"@{ch['username']}" if ch.get("username") else str(cid)
        )
        icon = _chat_icon(ch.get("chat_type") or "channel")
        mark = "✅" if cid in selected_set else "⬜"
        rows.append(
            [
                ib(
                    f"{mark} {icon} {title}",
                    callback_data=f"{prefix}:sched_tg:{cid}",
                    style="success" if cid in selected_set else "primary",
                )
            ]
        )
    rows.append(
        [ib("🚀 ثبت نهایی زمان‌بندی", callback_data=f"{prefix}:sched_save", style="success")]
    )
    rows.append([ib("◀️ بازگشت", callback_data=f"{prefix}:sched_panel", style="danger")])
    return InlineKeyboardMarkup(rows)


def post_buttons_manage_keyboard(
    buttons: List[Dict[str, Any]],
    add_callback: str = "pb:btn_add",
    inspect_prefix: str = "pb:btn_ins:",
    back_callback: str = "pb:back_preview",
) -> InlineKeyboardMarkup:
    normalized = normalize_buttons(buttons)
    grouped = group_buttons_by_row(normalized)
    rows: List[List[InlineKeyboardButton]] = []

    for row_items in grouped:
        kb_row: List[InlineKeyboardButton] = []
        for idx, b in row_items:
            s_icon = STYLE_ICONS.get(b.get("style"), "⚪")
            t_icon = TYPE_ICONS.get(b.get("type", "url"), "🔗")
            short_lbl = b["text"] if len(b["text"]) <= 18 else b["text"][:17] + "…"
            kb_row.append(
                ib_exact(
                    f"{s_icon}{t_icon} {short_lbl}",
                    style=b.get("style"),
                    callback_data=f"{inspect_prefix}{idx}",
                )
            )
        rows.append(kb_row)

    rows.append(
        [ib("➕ افزودن دکمه جدید", callback_data=add_callback, style="success")]
    )
    rows.append(
        [ib("◀️ بازگشت", callback_data=back_callback, style="danger")]
    )
    return InlineKeyboardMarkup(rows)


def post_button_type_keyboard(
    select_prefix: str = "pb:btn_type:",
    back_callback: str = "pb:btns",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [ib("🔗 دکمه لینکدار (URL)", callback_data=f"{select_prefix}url", style="primary")],
            [ib("💬 دکمه نمایشی (پاپ‌آپ بدون لینک)", callback_data=f"{select_prefix}popup", style="primary")],
            [ib("📋 دکمه کپی متن", callback_data=f"{select_prefix}copy", style="primary")],
            [ib("❤️ دکمه لایک / شمارنده واکنش", callback_data=f"{select_prefix}like", style="success")],
            [ib("📊 دکمه نظرسنجی (تک‌انتخابی)", callback_data=f"{select_prefix}vote", style="primary")],
            [ib("◀️ بازگشت", callback_data=back_callback, style="danger")],
        ]
    )


def post_button_inspector_keyboard(
    buttons: List[Dict[str, Any]],
    idx: int,
    prefix: str = "pb",
    back_callback: str = "pb:btns",
) -> InlineKeyboardMarkup:
    normalized = normalize_buttons(buttons)
    cur = normalized[idx]
    cur_style = cur.get("style")
    cur_row = cur["row"]
    same_row_count = sum(1 for b in normalized if b["row"] == cur_row)

    color_choices = [
        ("⚪ شفاف", "none", None),
        ("🔵 آبی", "primary", "primary"),
        ("🟢 سبز", "success", "success"),
        ("🔴 قرمز", "danger", "danger"),
    ]
    color_row = []
    for label, token, style_val in color_choices:
        mark = "✓ " if cur_style == style_val else ""
        color_row.append(
            ib_exact(
                f"{mark}{label}",
                style=style_val,
                callback_data=f"{prefix}:btn_col:{idx}:{token}",
            )
        )

    pos_row_1 = [
        ib("⬆️ ردیف بالا", callback_data=f"{prefix}:btn_row_up:{idx}", style="primary"),
        ib("⬇️ ردیف پایین", callback_data=f"{prefix}:btn_row_dn:{idx}", style="primary"),
    ]
    pos_row_2 = [
        ib("⬅️ چپ", callback_data=f"{prefix}:btn_left:{idx}", style="primary"),
        ib("➡️ راست", callback_data=f"{prefix}:btn_right:{idx}", style="primary"),
    ]
    if same_row_count > 1:
        pos_row_2.append(
            ib("➖ ردیف جدا", callback_data=f"{prefix}:btn_split:{idx}", style="primary")
        )

    return InlineKeyboardMarkup(
        [
            color_row,
            pos_row_1,
            pos_row_2,
            [
                ib("✏️ تغییر متن", callback_data=f"{prefix}:btn_etxt:{idx}", style="primary"),
                ib("🔀 تغییر نوع / مقدار", callback_data=f"{prefix}:btn_eval:{idx}", style="primary"),
            ],
            [
                ib("🗑 حذف دکمه", callback_data=f"{prefix}:btn_del:{idx}", style="danger"),
                ib("◀️ بازگشت", callback_data=back_callback, style="danger"),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# کیبوردهای مدیریت پست‌های ذخیره‌شده
# ---------------------------------------------------------------------------

def post_manage_list_keyboard(
    posts: List[Dict[str, Any]],
    page: int,
    total_pages_count: int,
    offset: int,
    *,
    tab: str = "all",
    all_count: Optional[int] = None,
    sched_count: Optional[int] = None,
    search_query: str = "",
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    for idx, post in enumerate(posts):
        num = offset + idx + 1
        title = (post.get("title") or "").strip()
        if title:
            label_body = title if len(title) <= 26 else title[:26] + "…"
        else:
            snippet = " ".join((post.get("post_text") or "").split())
            label_body = snippet if len(snippet) <= 26 else snippet[:26] + "…"

        if post.get("schedule_status") == "pending":
            badge = "⏰"
        elif post.get("status") == "published":
            badge = "✅"
        else:
            badge = "📝"

        time_suffix = ""
        if tab == "sched" and post.get("scheduled_at"):
            from .services.scheduler import fmt_schedule_tehran

            hhmm = fmt_schedule_tehran(post["scheduled_at"]).split("—")[-1].strip()
            time_suffix = f" ({hhmm})"

        rows.append(
            [
                ib(
                    f"{num}. {badge} {label_body}{time_suffix}",
                    callback_data=f"pm:detail:{post['id']}",
                    style="primary",
                )
            ]
        )

    if total_pages_count > 1 or posts:
        rows.append(
            build_pagination_row(
                page=page,
                total_pages_count=total_pages_count,
                previous_callback=f"pm:page:{page - 1}",
                refresh_callback=f"pm:page:{page}",
                next_callback=f"pm:page:{page + 1}",
            )
        )

    all_lbl = f"📋 همه ({all_count})" if all_count is not None else "📋 همه پست‌ها"
    sched_lbl = (
        f"⏰ زمان‌بندی‌شده ({sched_count})"
        if sched_count is not None
        else "⏰ زمان‌بندی‌شده"
    )
    rows.append(
        [
            ib(
                f"{'✓ ' if tab == 'all' else ''}{all_lbl}",
                callback_data="pm:tab:all",
                style="success" if tab == "all" else "primary",
            ),
            ib(
                f"{'✓ ' if tab == 'sched' else ''}{sched_lbl}",
                callback_data="pm:tab:sched",
                style="success" if tab == "sched" else "primary",
            ),
        ]
    )

    search_row = [ib("🔍 جست‌وجو", callback_data="pm:search", style="primary")]
    if search_query:
        search_row.append(
            ib("❌ پاک کردن جست‌وجو", callback_data="pm:search_clear", style="danger")
        )
    rows.append(search_row)

    rows.append([ib("🏠 منوی اصلی", callback_data="pm:close", style="danger")])
    return InlineKeyboardMarkup(rows)


def post_manage_detail_keyboard(
    post: Dict[str, Any],
    buttons_count: int = 0,
    published_count: Optional[int] = None,
) -> InlineKeyboardMarkup:
    post_id = post["id"]
    btns_label = (
        f"🔘 دکمه‌های شیشه‌ای ({buttons_count})"
        if buttons_count
        else "🔘 دکمه شیشه‌ای (اختیاری)"
    )

    has_published = (
        (published_count is not None and published_count > 0)
        or bool(post.get("target_channel_id") and post.get("channel_message_id"))
    )

    send_row = [
        ib("📤 ارسال به کانال/گروه", callback_data=f"pm:send_ch:{post_id}", style="success"),
        ib("🎯 انتخاب مقصد", callback_data=f"pm:targets:{post_id}", style="success"),
    ]

    rows: List[List[InlineKeyboardButton]] = [send_row]
    if has_published:
        rows.append(
            [
                ib(
                    "♻️ آپدیت در کانال/گروه",
                    callback_data=f"pm:update_ch:{post_id}",
                    style="primary",
                )
            ]
        )

    media_type = post.get("media_type") or "link"
    rows.extend(
        [
            [
                ib(btns_label, callback_data=f"pm:btns:{post_id}", style="primary"),
                ib("⏰ زمان‌بندی پست", callback_data=f"pm:sched:{post_id}", style="primary"),
            ],
            [
                ib("✏️ ویرایش متن", callback_data=f"pm:edit_text:{post_id}", style="primary"),
                ib("🖼 تغییر عکس / حالت", callback_data=f"pm:media_menu:{post_id}", style="primary"),
            ],
        ]
    )
    if media_type == "link":
        rows.append(
            [
                ib(
                    "⬆️/⬇️ جای پیش‌نمایش",
                    callback_data=f"pm:toggle_pos:{post_id}",
                    style="primary",
                ),
                ib(
                    "🖼 اندازه پیش‌نمایش",
                    callback_data=f"pm:toggle_size:{post_id}",
                    style="primary",
                ),
            ]
        )
    rows.append(
        [
            ib("✅ ذخیره و بازگشت", callback_data=f"pm:save_back:{post_id}", style="success"),
        ]
    )
    rows.append(
        [
            ib("🗑 حذف پست", callback_data=f"pm:del:{post_id}", style="danger"),
            ib("◀️ بازگشت به لیست", callback_data="pm:list", style="danger"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def post_manage_update_targets_keyboard(
    post_id: int,
    targets: List[Dict[str, Any]],
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for t in targets:
        icon = _chat_icon(t.get("chat_type") or "channel")
        title = t.get("title") or (
            f"@{t['username']}" if t.get("username") else str(t["chat_id"])
        )
        rows.append(
            [
                ib(
                    f"{icon} {title}",
                    callback_data=f"pm:upd_pick:{post_id}:{t['chat_id']}",
                    style="primary",
                )
            ]
        )
    if len(targets) > 1:
        rows.append(
            [
                ib(
                    f"♻️ آپدیت در همه ({len(targets)})",
                    callback_data=f"pm:upd_all:{post_id}",
                    style="success",
                )
            ]
        )
    rows.append([ib("◀️ بازگشت", callback_data=f"pm:detail:{post_id}", style="danger")])
    return InlineKeyboardMarkup(rows)


def confirm_action_keyboard(
    yes_text: str,
    yes_callback: str,
    no_text: str = "❌ انصراف",
    no_callback: str = "cancel",
    yes_style: str = "danger",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                ib(yes_text, callback_data=yes_callback, style=yes_style),
                ib(no_text, callback_data=no_callback, style="primary"),
            ]
        ]
    )


def old_post_edit_panel_keyboard(
    media_type: str = "none",
    buttons_count: int = 0,
) -> InlineKeyboardMarkup:
    btns_lbl = (
        f"🔘 دکمه‌های شیشه‌ای ({buttons_count})"
        if buttons_count
        else "🔘 دکمه شیشه‌ای (اختیاری)"
    )
    rows = [
        [
            ib("✏️ ویرایش متن / کپشن", callback_data="old:edit_text", style="primary"),
            ib("🖼 تغییر / حذف / لینک عکس", callback_data="old:media_menu", style="primary"),
        ],
        [
            ib(btns_lbl, callback_data="old:btns", style="primary"),
        ],
    ]
    if media_type == "link":
        rows.append(
            [
                ib("⬆️/⬇️ جای پیش‌نمایش", callback_data="old:toggle_pos", style="primary"),
                ib("🖼 اندازه پیش‌نمایش", callback_data="old:toggle_size", style="primary"),
            ]
        )
    rows.extend(
        [
            [
                ib("♻️ اعمال تغییرات روی پست کانال/گروه", callback_data="old:apply", style="success"),
            ],
            [
                ib("🔄 انتخاب پست دیگر", callback_data="old:restart", style="primary"),
                ib("🏠 منوی اصلی", callback_data="old:close", style="danger"),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# کیبوردهای پنل تنظیمات (فقط مالک فضای کاری)
# ---------------------------------------------------------------------------

def settings_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                ib("👨‍💼 ادمین‌ها", callback_data="st:admins"),
                ib("📢 کانال‌ها و گروه‌ها", callback_data="st:channels"),
            ],
            [
                ib("🖼 تنظیمات پیش‌نمایش و دکمه", callback_data="st:preview"),
            ],
            [
                ib("🔄 به‌روزرسانی", style="primary", callback_data="st:panel"),
                ib("🔙 بازگشت", callback_data="st:back"),
            ],
        ]
    )


def settings_admins_keyboard(
    admin_ids: List[int],
    owner_id: Optional[int],
    names: Optional[dict] = None,
) -> InlineKeyboardMarkup:
    names = names or {}
    rows = [
        [
            ib(
                str(admin_id),
                style="primary",
                callback_data=f"st:adm_del:{admin_id}",
            ),
            ib(
                names.get(admin_id) or names.get(str(admin_id)) or "—",
                style="primary",
                callback_data=f"st:adm_del:{admin_id}",
            ),
        ]
        for admin_id in admin_ids
        if owner_id is None or admin_id != owner_id
    ]
    rows.append([ib("➕ افزودن ادمین", callback_data="st:adm_add")])
    rows.append([ib("🔙 بازگشت", callback_data="st:panel")])
    return InlineKeyboardMarkup(rows)


def settings_channels_keyboard(
    channels: List[Dict[str, Any]],
) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        id_text = str(ch["chat_id"])
        icon = _chat_icon(ch.get("chat_type") or "channel")
        name_text = ch.get("title") or (
            f"@{ch['username']}" if ch.get("username") else "—"
        )
        rows.append(
            [
                ib(
                    id_text,
                    style="primary",
                    callback_data=f"st:ch_del:{ch['id']}",
                ),
                ib(
                    f"{icon} {name_text}",
                    style="primary",
                    callback_data=f"st:ch_del:{ch['id']}",
                ),
            ]
        )
    rows.append([ib("➕ افزودن کانال یا گروه", callback_data="st:ch_add")])
    rows.append([ib("🔙 بازگشت", callback_data="st:panel")])
    return InlineKeyboardMarkup(rows)


def settings_preview_keyboard(
    show_above_text: bool,
    prefer_large_media: bool,
) -> InlineKeyboardMarkup:
    pos_text = (
        "⬆️ محل پیش‌نمایش: بالای متن" if show_above_text else "⬇️ محل پیش‌نمایش: پایین متن"
    )
    size_text = (
        "🖼 اندازه پیش‌نمایش: بزرگ" if prefer_large_media else "🖼 اندازه پیش‌نمایش: کوچک"
    )
    return InlineKeyboardMarkup(
        [
            [ib(pos_text, style="primary", callback_data="st:pv_pos")],
            [ib(size_text, style="primary", callback_data="st:pv_size")],
            [ib("🏷 تغییر متن پیش‌فرض دکمه", callback_data="st:btn_lbl")],
            [ib("🔙 بازگشت", callback_data="st:panel")],
        ]
    )


def settings_confirm_keyboard(
    yes_callback: str,
    no_callback: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                ib("✅ بله، حذف کن", callback_data=yes_callback, style="danger"),
                ib("❌ خیر", style="primary", callback_data=no_callback),
            ]
        ]
    )


def settings_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[ib("❌ انصراف", callback_data="st:cancel", style="danger")]]
    )


def stats_keyboard(is_env_owner: bool = False) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if is_env_owner:
        rows.append(
            [ib("👥 مدیریت کاربران ربات", style="primary", callback_data="stats:users:0")]
        )
    rows.append(
        [
            ib("🔄 به‌روزرسانی", style="primary", callback_data="stats:refresh"),
            ib("🔙 بازگشت", callback_data="stats:close"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def stats_users_keyboard(
    users: List[Dict[str, Any]],
    page: int,
    total_pages_count: int,
    env_owner_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for u in users:
        uid = int(u["user_id"])
        full = " ".join(
            p for p in (u.get("first_name") or "", u.get("last_name") or "") if p
        ).strip()
        label = full or (f"@{u['username']}" if u.get("username") else str(uid))
        if env_owner_id is not None and uid == int(env_owner_id):
            icon = "👑"
        elif u.get("is_banned"):
            icon = "🚫"
        else:
            icon = "👤"
        rows.append(
            [
                ib(
                    f"{icon} {label} ({uid})",
                    callback_data=f"stats:user:{uid}",
                    style="danger" if u.get("is_banned") else "primary",
                )
            ]
        )
    if total_pages_count > 1 or users:
        rows.append(
            build_pagination_row(
                page=page,
                total_pages_count=total_pages_count,
                previous_callback=f"stats:users:{page - 1}",
                refresh_callback=f"stats:users:{page}",
                next_callback=f"stats:users:{page + 1}",
            )
        )
    rows.append([ib("◀️ بازگشت به آمار", callback_data="stats:refresh", style="danger")])
    return InlineKeyboardMarkup(rows)


def stats_user_detail_keyboard(
    target_uid: int,
    is_banned: bool,
    is_env_owner: bool = False,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [
            ib(
                "✉️ ارسال پیام به کاربر",
                callback_data=f"stats:msg_ask:{target_uid}",
                style="success",
            )
        ],
    ]
    if not is_env_owner:
        rows.append(
            [
                ib(
                    "✅ رفع بن کاربر" if is_banned else "🚫 بن کردن کاربر",
                    callback_data=f"stats:ban_ask:{target_uid}",
                    style="success" if is_banned else "danger",
                )
            ]
        )
    rows.append([ib("◀️ بازگشت به فهرست کاربران", callback_data="stats:users:0", style="danger")])
    return InlineKeyboardMarkup(rows)
