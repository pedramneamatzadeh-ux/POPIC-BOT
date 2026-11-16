import html
import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from ..config import config
from ..constants import STATS_VIEW
from ..database import db
from ..decorators import admin_only
from ..keyboards import stats_keyboard
from ..utils import fmt_tehran, go_to_main_menu, register_panel_message, safe_edit_or_send, shorten_text

logger = logging.getLogger(__name__)


async def _safe_answer(query, text=None, show_alert: bool = False) -> None:
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except BadRequest as exc:
        if "query is too old" in str(exc).lower():
            logger.info("query آمار منقضی بود: %s", exc)
            return
        raise


def _build_stats_text() -> str:
    summary = db.get_stats_summary()
    recent = db.list_posts(limit=5, offset=0)

    admins_count = len(
        [x for x in (config.ADMIN_IDS or []) if x != config.OWNER_ID]
    )

    lines = [
        "📊 <b>آمار کلی ربات</b>",
        "",
        f"📝 کل پست‌های ثبت‌شده: <b>{summary['total_posts']}</b>",
        f"📢 منتشرشده در کانال: <b>{summary['published_posts']}</b>",
        f"📄 پیش‌نویس / فقط پی‌وی: <b>{summary['draft_posts']}</b>",
        f"📤 دفعات ارسال به کانال: <b>{summary['channel_deliveries']}</b>",
        f"📥 دفعات تحویل در پی‌وی: <b>{summary['pv_deliveries']}</b>",
        f"📢 کانال‌های مقصد ثبت‌شده: <b>{summary['total_channels']}</b>",
        f"👨‍💼 ادمین‌ها: <b>{admins_count}</b> (+ مالک)",
        f"👥 کاربران ثبت‌شده: <b>{summary['total_users']}</b>",
    ]

    if recent:
        lines.append("")
        lines.append("🕒 <b>آخرین پست‌ها:</b>")
        for idx, post in enumerate(recent, 1):
            snippet = html.escape(shorten_text(post.get("post_text") or "", 42))
            badge = "✅" if post.get("status") == "published" else "📝"
            date_str = html.escape(fmt_tehran(post.get("created_at")))
            lines.append(f"{idx}. {badge} {snippet} — <i>{date_str}</i>")

    return "\n".join(lines)


@admin_only
async def show_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    sent = await update.message.reply_text(
        _build_stats_text(),
        reply_markup=stats_keyboard(),
        parse_mode="HTML",
    )
    register_panel_message(context, sent.message_id)
    return STATS_VIEW


@admin_only
async def handle_stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    data = (query.data or "").strip()

    if data == "stats:refresh":
        await _safe_answer(query)
        chat_id = query.message.chat_id if query.message else update.effective_user.id
        msg_id = query.message.message_id if query.message else None
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=_build_stats_text(),
            reply_markup=stats_keyboard(),
            message_id=msg_id,
            parse_mode="HTML",
        )
        return STATS_VIEW

    if data == "stats:close":
        await _safe_answer(query)
        if query.message:
            try:
                await query.edit_message_text("✅ پنل آمار بسته شد.")
            except Exception:
                pass
            await go_to_main_menu(context, query.message.chat_id)
        return ConversationHandler.END

    await _safe_answer(query, "⚠️ گزینه نامعتبر", show_alert=True)
    return STATS_VIEW
