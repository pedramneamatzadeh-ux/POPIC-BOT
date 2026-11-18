"""سرویس ساخت و ارسال پست متنی با پیش‌نمایش لینک عکس و دکمه‌های شیشه‌ای پیشرفته."""

import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from telegram import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
    MessageEntity,
)

from ..constants import (
    DEFAULT_POPUP_TEXT,
    MAX_BUTTONS_PER_ROW,
    MAX_COPY_TEXT_LENGTH,
    MAX_POPUP_TEXT_LENGTH,
)

logger = logging.getLogger(__name__)

VALID_BUTTON_TYPES = ("url", "popup", "copy")
VALID_BUTTON_STYLES = (None, "primary", "success", "danger")

STYLE_LABELS = {
    None: "⚪ معمولی (شفاف)",
    "primary": "🔵 آبی",
    "success": "🟢 سبز",
    "danger": "🔴 قرمز",
}

STYLE_ICONS = {
    None: "⚪",
    "primary": "🔵",
    "success": "🟢",
    "danger": "🔴",
}

TYPE_LABELS = {
    "url": "🔗 لینکدار",
    "popup": "💬 نمایشی (پاپ‌آپ)",
    "copy": "📋 کپی متن",
}

TYPE_ICONS = {
    "url": "🔗",
    "popup": "💬",
    "copy": "📋",
}


def serialize_entities(entities: Optional[Sequence[MessageEntity]]) -> str:
    if not entities:
        return "[]"
    data = []
    for entity in entities:
        try:
            data.append(entity.to_dict())
        except Exception:
            logger.debug("نادیده‌گرفتن entity نامعتبر", exc_info=True)
    return json.dumps(data, ensure_ascii=False)


def deserialize_entities(
    raw_json: Optional[str],
) -> Optional[List[MessageEntity]]:
    if not raw_json:
        return None
    try:
        items = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(items, list) or not items:
        return None

    result: List[MessageEntity] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            entity = MessageEntity.de_json(item, None)
            if entity is not None:
                result.append(entity)
        except Exception:
            logger.debug("خطا در بازسازی entity", exc_info=True)
    return result or None


def normalize_button_item(
    raw: Dict[str, Any],
    default_row: int = 0,
) -> Optional[Dict[str, Any]]:
    """تبدیل دادهٔ خام (یا قدیمی) به ساختار استاندارد دکمه."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None

    btn_type = raw.get("type")
    if btn_type not in VALID_BUTTON_TYPES:
        if raw.get("url"):
            btn_type = "url"
        elif raw.get("copy_text"):
            btn_type = "copy"
        elif raw.get("popup_text") is not None:
            btn_type = "popup"
        else:
            return None

    style = raw.get("style")
    if style not in ("primary", "success", "danger"):
        style = None

    try:
        row = max(0, int(raw.get("row", default_row)))
    except (TypeError, ValueError):
        row = max(0, int(default_row))

    item: Dict[str, Any] = {
        "text": text[:64],
        "type": btn_type,
        "style": style,
        "row": row,
    }

    if btn_type == "url":
        url = str(raw.get("url") or "").strip()
        if not url:
            return None
        item["url"] = url
    elif btn_type == "popup":
        popup_text = str(raw.get("popup_text") or DEFAULT_POPUP_TEXT).strip()
        item["popup_text"] = (popup_text or DEFAULT_POPUP_TEXT)[:MAX_POPUP_TEXT_LENGTH]
    elif btn_type == "copy":
        copy_text = str(raw.get("copy_text") or "").strip()
        if not copy_text:
            return None
        item["copy_text"] = copy_text[:MAX_COPY_TEXT_LENGTH]

    return item


def normalize_buttons(buttons: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """مرتب‌سازی بر اساس ردیف، فشرده‌سازی شماره ردیف‌ها و کنترل سقف ۴ دکمه در هر ردیف."""
    if not buttons:
        return []

    parsed: List[Tuple[int, int, Dict[str, Any]]] = []
    for idx, raw in enumerate(buttons):
        item = normalize_button_item(raw, default_row=idx)
        if item is not None:
            parsed.append((item["row"], idx, item))

    if not parsed:
        return []

    parsed.sort(key=lambda pair: (pair[0], pair[1]))

    result: List[Dict[str, Any]] = []
    current_src_row: Optional[int] = None
    compact_row = -1
    count_in_row = 0

    for src_row, _, item in parsed:
        if src_row != current_src_row or count_in_row >= MAX_BUTTONS_PER_ROW:
            compact_row += 1
            current_src_row = src_row
            count_in_row = 0
        copied = dict(item)
        copied["row"] = compact_row
        result.append(copied)
        count_in_row += 1

    return result


def group_buttons_by_row(
    buttons: List[Dict[str, Any]],
) -> List[List[Tuple[int, Dict[str, Any]]]]:
    """خروجی: لیست ردیف‌ها؛ هر ردیف شامل تاپل‌های `(global_idx, button_dict)`."""
    if not buttons:
        return []
    rows: List[List[Tuple[int, Dict[str, Any]]]] = []
    current_row: Optional[int] = None
    for idx, btn in enumerate(buttons):
        r = int(btn.get("row", idx))
        if r != current_row or not rows or len(rows[-1]) >= MAX_BUTTONS_PER_ROW:
            rows.append([])
            current_row = r
        rows[-1].append((idx, btn))
    return rows


def move_button_row(
    buttons: List[Dict[str, Any]],
    idx: int,
    delta: int,
) -> List[Dict[str, Any]]:
    """جابه‌جایی دکمه به ردیف بالا (-1) یا ردیف پایین (+1)."""
    normalized = normalize_buttons(buttons)
    if not 0 <= idx < len(normalized):
        return normalized

    rows = group_buttons_by_row(normalized)
    cur_r = next(r_i for r_i, row in enumerate(rows) if any(i == idx for i, _ in row))
    btn = dict(normalized[idx])

    if delta < 0:
        if cur_r == 0:
            return normalized
        target_r = cur_r - 1
        if len(rows[target_r]) < MAX_BUTTONS_PER_ROW:
            btn["row"] = rows[target_r][0][1]["row"]
        else:
            # ردیف بالایی پر است -> با جایگاه بالایی جابه‌جا کن
            other_idx = rows[target_r][0][0]
            btn["row"], normalized[other_idx]["row"] = (
                normalized[other_idx]["row"],
                btn["row"],
            )
            normalized[idx] = btn
            return normalize_buttons(normalized)
        # ترتیب لیست را هم طوری می‌چینیم که در انتهای ردیف هدف قرار بگیرد
        remaining = [dict(b) for i, b in enumerate(normalized) if i != idx]
        insert_pos = 0
        for i, b in enumerate(remaining):
            if b["row"] <= btn["row"]:
                insert_pos = i + 1
        remaining.insert(insert_pos, btn)
        return normalize_buttons(remaining)

    # delta > 0
    if cur_r == len(rows) - 1:
        # اگر در آخرین ردیف تنهاست دیگر پایین‌تر نمی‌رود؛ اگر با دیگری شریک است، به ردیف جدید می‌رود
        if len(rows[cur_r]) <= 1:
            return normalized
        btn["row"] = rows[cur_r][0][1]["row"] + 1
        remaining = [dict(b) for i, b in enumerate(normalized) if i != idx]
        remaining.append(btn)
        return normalize_buttons(remaining)

    target_r = cur_r + 1
    if len(rows[target_r]) < MAX_BUTTONS_PER_ROW:
        btn["row"] = rows[target_r][0][1]["row"]
        remaining = [dict(b) for i, b in enumerate(normalized) if i != idx]
        insert_pos = 0
        for i, b in enumerate(remaining):
            if b["row"] <= btn["row"]:
                insert_pos = i + 1
        remaining.insert(insert_pos, btn)
        return normalize_buttons(remaining)

    other_idx = rows[target_r][0][0]
    btn["row"], normalized[other_idx]["row"] = (
        normalized[other_idx]["row"],
        btn["row"],
    )
    normalized[idx] = btn
    return normalize_buttons(normalized)


def move_button_side(
    buttons: List[Dict[str, Any]],
    idx: int,
    delta: int,
) -> List[Dict[str, Any]]:
    """جابه‌جایی دکمه به چپ (-1) یا راست (+1) در ردیف فعلی."""
    normalized = normalize_buttons(buttons)
    if not 0 <= idx < len(normalized):
        return normalized

    cur_row = normalized[idx]["row"]
    same_row_indices = [i for i, b in enumerate(normalized) if b["row"] == cur_row]
    pos = same_row_indices.index(idx)
    target_pos = pos + (-1 if delta < 0 else 1)
    if not 0 <= target_pos < len(same_row_indices):
        return normalized

    other_idx = same_row_indices[target_pos]
    normalized[idx], normalized[other_idx] = normalized[other_idx], normalized[idx]
    return normalize_buttons(normalized)


def split_button_new_row(
    buttons: List[Dict[str, Any]],
    idx: int,
) -> List[Dict[str, Any]]:
    """دکمه را از ردیف مشترک جدا کرده و به ردیف جدید بلافاصله بعد از آن می‌برد."""
    normalized = normalize_buttons(buttons)
    if not 0 <= idx < len(normalized):
        return normalized

    cur_row = normalized[idx]["row"]
    same_row_count = sum(1 for b in normalized if b["row"] == cur_row)
    if same_row_count <= 1:
        return normalized

    moved = dict(normalized[idx])
    moved["row"] = cur_row + 1
    out: List[Dict[str, Any]] = []
    inserted = False
    for i, b in enumerate(normalized):
        if i == idx:
            continue
        item = dict(b)
        if item["row"] > cur_row:
            if not inserted:
                out.append(moved)
                inserted = True
            item["row"] += 1
        out.append(item)
    if not inserted:
        out.append(moved)
    return normalize_buttons(out)


def serialize_buttons(buttons: Optional[List[Dict[str, Any]]]) -> str:
    normalized = normalize_buttons(buttons)
    if not normalized:
        return "[]"
    return json.dumps(normalized, ensure_ascii=False)


def deserialize_buttons(raw_json: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_json:
        return []
    try:
        items = json.loads(raw_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return normalize_buttons(items)


def build_buttons_markup(
    buttons: Optional[List[Dict[str, Any]]],
    workspace_id: Optional[int] = None,
    post_id: Optional[int] = None,
) -> Optional[InlineKeyboardMarkup]:
    normalized = normalize_buttons(buttons)
    if not normalized:
        return None

    grouped = group_buttons_by_row(normalized)
    kb_rows: List[List[InlineKeyboardButton]] = []

    for row_items in grouped:
        kb_row: List[InlineKeyboardButton] = []
        for idx, b in row_items:
            kwargs: Dict[str, Any] = {}
            if b.get("style") in ("primary", "success", "danger"):
                kwargs["style"] = b["style"]

            btn_type = b.get("type", "url")
            if btn_type == "url" and b.get("url"):
                kwargs["url"] = b["url"]
            elif btn_type == "copy" and b.get("copy_text"):
                kwargs["copy_text"] = CopyTextButton(text=b["copy_text"])
            elif btn_type == "popup":
                if workspace_id is not None and post_id is not None:
                    kwargs["callback_data"] = f"pb_disp:{int(workspace_id)}:{int(post_id)}:{idx}"
                else:
                    kwargs["callback_data"] = f"pb_disp:pv:{idx}"
            else:
                continue

            kb_row.append(InlineKeyboardButton(text=b["text"], **kwargs))
        if kb_row:
            kb_rows.append(kb_row)

    return InlineKeyboardMarkup(kb_rows) if kb_rows else None


def build_link_preview_options(
    photo_url: str,
    show_above_text: bool = True,
    prefer_large_media: bool = True,
) -> LinkPreviewOptions:
    return LinkPreviewOptions(
        is_disabled=False,
        url=photo_url,
        prefer_large_media=bool(prefer_large_media),
        prefer_small_media=not bool(prefer_large_media),
        show_above_text=bool(show_above_text),
    )


async def send_post_to_chat(
    bot: Any,
    chat_id: int,
    text: str,
    entities_json: Optional[str],
    photo_url: str = "",
    show_above_text: bool = True,
    prefer_large_media: bool = True,
    buttons: Optional[List[Dict[str, Any]]] = None,
    workspace_id: Optional[int] = None,
    post_id: Optional[int] = None,
    media_type: str = "link",
    photo_file_id: str = "",
) -> Message:
    entities = deserialize_entities(entities_json)
    markup = build_buttons_markup(
        buttons,
        workspace_id=workspace_id,
        post_id=post_id,
    )

    if media_type == "photo" and photo_file_id:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo_file_id,
            caption=text,
            caption_entities=entities,
            reply_markup=markup,
        )

    if media_type == "none" or not photo_url:
        preview = LinkPreviewOptions(is_disabled=True)
    else:
        preview = build_link_preview_options(
            photo_url=photo_url,
            show_above_text=show_above_text,
            prefer_large_media=prefer_large_media,
        )

    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        entities=entities,
        link_preview_options=preview,
        reply_markup=markup,
    )


async def edit_post_in_chat(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
    entities_json: Optional[str],
    photo_url: str = "",
    show_above_text: bool = True,
    prefer_large_media: bool = True,
    buttons: Optional[List[Dict[str, Any]]] = None,
    workspace_id: Optional[int] = None,
    post_id: Optional[int] = None,
    media_type: str = "link",
    photo_file_id: str = "",
) -> Any:
    entities = deserialize_entities(entities_json)
    markup = build_buttons_markup(
        buttons,
        workspace_id=workspace_id,
        post_id=post_id,
    )

    if media_type == "photo" and photo_file_id:
        media = InputMediaPhoto(
            media=photo_file_id,
            caption=text,
            caption_entities=entities,
        )
        return await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media,
            reply_markup=markup,
        )

    if media_type == "none" or not photo_url:
        preview = LinkPreviewOptions(is_disabled=True)
    else:
        preview = build_link_preview_options(
            photo_url=photo_url,
            show_above_text=show_above_text,
            prefer_large_media=prefer_large_media,
        )

    return await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        entities=entities,
        link_preview_options=preview,
        reply_markup=markup,
    )
