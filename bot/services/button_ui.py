"""متون کمکی و نمایشی پنل تنظیمات دکمه‌های شیشه‌ای."""

import html
from typing import Any, Dict, List

from ..constants import MAX_EXTRA_BUTTONS
from .publisher import (
    STYLE_ICONS,
    STYLE_LABELS,
    TYPE_ICONS,
    TYPE_LABELS,
    group_buttons_by_row,
    normalize_buttons,
)


def build_buttons_overview_text(
    buttons: List[Dict[str, Any]],
    header: str = "🔘 مدیریت دکمه‌های شیشه‌ای",
) -> str:
    normalized = normalize_buttons(buttons)
    lines = [
        f"<b>{html.escape(header)}</b>",
        f"• تعداد دکمه‌ها: <b>{len(normalized)} از {MAX_EXTRA_BUTTONS}</b>",
    ]
    if not normalized:
        lines.extend(
            [
                "",
                "هنوز دکمه‌ای اضافه نشده است.",
                "با «➕ افزودن دکمه جدید» می‌توانید دکمهٔ لینکدار، نمایشی (پاپ‌آپ) یا کپی متن بسازید.",
            ]
        )
        return "\n".join(lines)

    rows = group_buttons_by_row(normalized)
    lines.append(f"• تعداد ردیف‌ها: <b>{len(rows)}</b>")
    lines.append("")
    lines.append("📐 <b>چیدمان فعلی ردیف‌ها:</b>")
    for r_idx, row_items in enumerate(rows, start=1):
        chips = []
        for _, b in row_items:
            s_icon = STYLE_ICONS.get(b.get("style"), "⚪")
            t_icon = TYPE_ICONS.get(b.get("type", "url"), "🔗")
            chips.append(f"[{s_icon} {t_icon} {html.escape(b['text'])}]")
        lines.append(f"ردیف {r_idx}: " + "  ".join(chips))

    lines.append("")
    lines.append("💡 روی هر دکمه کلیک کن تا رنگ، ردیف، ترتیب، متن یا نوع آن را تنظیم کنی.")
    return "\n".join(lines)


def build_button_inspector_text(
    buttons: List[Dict[str, Any]],
    idx: int,
) -> str:
    normalized = normalize_buttons(buttons)
    if not 0 <= idx < len(normalized):
        return "❌ دکمه پیدا نشد."

    b = normalized[idx]
    rows = group_buttons_by_row(normalized)
    row_num = 1
    col_num = 1
    row_len = 1
    for r_i, row_items in enumerate(rows, start=1):
        for c_i, (g_i, _) in enumerate(row_items, start=1):
            if g_i == idx:
                row_num = r_i
                col_num = c_i
                row_len = len(row_items)
                break

    btn_type = b.get("type", "url")
    type_label = TYPE_LABELS.get(btn_type, btn_type)
    style_label = STYLE_LABELS.get(b.get("style"), "⚪ معمولی (شفاف)")

    if btn_type == "url":
        val_line = f"• لینک: {html.escape(b.get('url') or '')}"
    elif btn_type == "popup":
        val_line = f"• متن پاپ‌آپ: «{html.escape(b.get('popup_text') or '')}»"
    else:
        val_line = f"• متن کپی: «{html.escape(b.get('copy_text') or '')}»"

    return "\n".join(
        [
            f"🎛 <b>تنظیمات دکمه #{idx + 1}</b>",
            "",
            f"• متن دکمه: <b>{html.escape(b['text'])}</b>",
            f"• نوع: {type_label}",
            val_line,
            f"• رنگ: {style_label}",
            f"• جایگاه: ردیف <b>{row_num}</b>، ستون <b>{col_num}</b> از {row_len}",
            "",
            "از گزینه‌های زیر رنگ، ردیف، ترتیب یا محتوای این دکمه را تغییر بده:",
        ]
    )
