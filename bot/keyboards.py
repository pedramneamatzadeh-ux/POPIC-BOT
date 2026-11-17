from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .constants import SETTINGS_BUTTON, SWITCH_WORKSPACE_BUTTON
from .database import registry, resolve_user_workspace
from .services.publisher import (
    STYLE_ICONS,
    TYPE_ICONS,
    group_buttons_by_row,
    normalize_buttons,
)

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
) -> ReplyKeyboardMarkup:
    rows: List[List[str]] = [
        ["📝 ساخت پست جدید", "📂 پست‌های ذخیره‌شده"],
    ]

    if is_owner:
        rows.append(["📊 آمار", "💾 بک‌آپ"])
        last_row = [SETTINGS_BUTTON]
        if has_admin_roles:
            last_row.append(SWITCH_WORKSPACE_BUTTON)
        rows.append(last_row)
    else:
        row2 = ["📊 آمار"]
        if has_admin_roles:
            row2.append(SWITCH_WORKSPACE_BUTTON)
        rows.append(row2)

    return ReplyKeyboardMarkup(
        [_menu_row(r) for r in rows],
        resize_keyboard=True,
    )


def main_menu_for(user_id: Optional[int]) -> ReplyKeyboardMarkup:
    """منوی اصلی بر اساس نقش فعال کاربر (مالک فضای شخصی یا ادمینِ فضای دیگر)."""
    if user_id is None:
        return build_main_menu(is_owner=True, has_admin_roles=False)

    _, status, _ = resolve_user_workspace(user_id)
    is_owner = status != "admin"
    has_admin_roles = bool(registry.get_admin_workspaces(user_id))
    return build_main_menu(
        is_owner=is_owner,
        has_admin_roles=has_admin_roles,
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

def post_preview_controls_keyboard(
    show_above_text: bool,
    prefer_large_media: bool,
    extra_buttons_count: int = 0,
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
        else "🔘 افزودن دکمه شیشه‌ای (اختیاری)"
    )

    return InlineKeyboardMarkup(
        [
            [
                ib("📤 ارسال به کانال", callback_data="pb:send_ch", style="success"),
                ib("📥 تحویل در پی‌وی", callback_data="pb:send_pv", style="success"),
            ],
            [
                ib(pos_label, callback_data="pb:toggle_pos", style="primary"),
                ib(size_label, callback_data="pb:toggle_size", style="primary"),
            ],
            [
                ib(btns_label, callback_data="pb:btns", style="primary"),
            ],
            [
                ib("✏️ ویرایش متن", callback_data="pb:edit_text", style="primary"),
                ib("🖼 تغییر لینک عکس", callback_data="pb:edit_link", style="primary"),
            ],
            [
                ib("✅ ذخیره و منوی اصلی", callback_data="pb:done", style="success"),
                ib("❌ لغو", callback_data="cancel_prompt", style="danger"),
            ],
        ]
    )


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
        rows.append(
            [
                ib(
                    f"📢 {title}",
                    callback_data=f"{callback_prefix}{ch['id']}",
                    style="primary",
                )
            ]
        )
    rows.append([ib("◀️ بازگشت", callback_data=back_callback, style="danger")])
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
) -> InlineKeyboardMarkup:
    rows = []
    for idx, post in enumerate(posts):
        num = offset + idx + 1
        snippet = " ".join((post.get("post_text") or "").split())
        if len(snippet) > 28:
            snippet = snippet[:28] + "…"
        badge = "✅" if post.get("status") == "published" else "📝"
        rows.append(
            [
                ib(
                    f"{num}. {badge} {snippet}",
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

    rows.append([ib("🏠 منوی اصلی", callback_data="pm:close", style="danger")])
    return InlineKeyboardMarkup(rows)


def post_manage_detail_keyboard(
    post: Dict[str, Any],
    buttons_count: int = 0,
) -> InlineKeyboardMarkup:
    post_id = post["id"]
    btns_label = (
        f"🔘 دکمه‌های شیشه‌ای ({buttons_count})"
        if buttons_count
        else "🔘 افزودن دکمه شیشه‌ای (اختیاری)"
    )

    rows = [
        [
            ib("👁 ارسال پیش‌نمایش به پی‌وی", callback_data=f"pm:pv:{post_id}", style="success"),
        ],
        [
            ib("📤 ارسال به کانال", callback_data=f"pm:send_ch:{post_id}", style="success"),
        ],
    ]

    if post.get("target_channel_id") and post.get("channel_message_id"):
        rows[1].append(
            ib(
                "♻️ آپدیت در کانال",
                callback_data=f"pm:update_ch:{post_id}",
                style="primary",
            )
        )

    rows.extend(
        [
            [
                ib(btns_label, callback_data=f"pm:btns:{post_id}", style="primary"),
            ],
            [
                ib("✏️ ویرایش متن", callback_data=f"pm:edit_text:{post_id}", style="primary"),
                ib("🖼 تغییر لینک عکس", callback_data=f"pm:edit_link:{post_id}", style="primary"),
            ],
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
            ],
            [
                ib("🗑 حذف پست", callback_data=f"pm:del:{post_id}", style="danger"),
                ib("◀️ بازگشت به لیست", callback_data="pm:list", style="danger"),
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
                ib("📢 کانال‌های مقصد", callback_data="st:channels"),
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
                    name_text,
                    style="primary",
                    callback_data=f"st:ch_del:{ch['id']}",
                ),
            ]
        )
    rows.append([ib("➕ افزودن کانال مقصد", callback_data="st:ch_add")])
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


def stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                ib("🔄 به‌روزرسانی", style="primary", callback_data="stats:refresh"),
                ib("🔙 بازگشت", callback_data="stats:close"),
            ]
        ]
    )
