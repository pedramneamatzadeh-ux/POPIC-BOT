"""سرویس ساخت و ارسال پست متنی با پیش‌نمایش لینک عکس (LinkPreviewOptions)."""

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageEntity,
)

logger = logging.getLogger(__name__)


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


def serialize_buttons(buttons: Optional[List[Dict[str, str]]]) -> str:
    if not buttons:
        return "[]"
    clean = [
        {"text": str(b["text"]), "url": str(b["url"])}
        for b in buttons
        if isinstance(b, dict) and b.get("text") and b.get("url")
    ]
    return json.dumps(clean, ensure_ascii=False)


def deserialize_buttons(raw_json: Optional[str]) -> List[Dict[str, str]]:
    if not raw_json:
        return []
    try:
        items = json.loads(raw_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [
        {"text": str(b["text"]), "url": str(b["url"])}
        for b in items
        if isinstance(b, dict) and b.get("text") and b.get("url")
    ]


def build_buttons_markup(
    buttons: Optional[List[Dict[str, str]]],
) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    rows = [
        [InlineKeyboardButton(text=b["text"], url=b["url"])]
        for b in buttons
        if b.get("text") and b.get("url")
    ]
    return InlineKeyboardMarkup(rows) if rows else None


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
    photo_url: str,
    show_above_text: bool = True,
    prefer_large_media: bool = True,
    buttons: Optional[List[Dict[str, str]]] = None,
) -> Message:
    entities = deserialize_entities(entities_json)
    preview = build_link_preview_options(
        photo_url=photo_url,
        show_above_text=show_above_text,
        prefer_large_media=prefer_large_media,
    )
    markup = build_buttons_markup(buttons)

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
    photo_url: str,
    show_above_text: bool = True,
    prefer_large_media: bool = True,
    buttons: Optional[List[Dict[str, str]]] = None,
) -> Any:
    entities = deserialize_entities(entities_json)
    preview = build_link_preview_options(
        photo_url=photo_url,
        show_above_text=show_above_text,
        prefer_large_media=prefer_large_media,
    )
    markup = build_buttons_markup(buttons)

    return await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        entities=entities,
        link_preview_options=preview,
        reply_markup=markup,
    )
