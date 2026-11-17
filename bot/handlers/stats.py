import html
import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from ..config import config
from ..constants import STATS_VIEW
from ..database import count_workspace_dbs, registry, resolve_user_workspace
from ..decorators import workspace_access
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


def _build_stats_text(user_id: int) -> str:
    ws_id, status, ws_db = resolve_user_workspace(user_id)
    summary = ws_db.get_stats_summary()
    recent = ws_db.list_posts(limit=5, offset=0)
    ws_admins = registry.get_workspace_admins(ws_id)

    if status == "admin":
        role_line = f"👨‍💼 فضای فعال: <b>ادمینِ {html.escape(registry.get_user_display_name(ws_id))}</b>"
    else:
        role_line = "👑 فضای فعال: <b>فضای شخصی شما (مالک)</b>"

    lines = [
        "📊 <b>آمار فضای کاری</b>",
        role_line,
        "",
        f"📝 کل پست‌های ثبت‌شده: <b>{summary['total_posts']}</b>",
        f"📢 منتشرشده در کانال: <b>{summary['published_posts']}</b>",
        f"📄 پیش‌نویس / فقط پی‌وی: <b>{summary['draft_posts']}</b>",
        f"📤 دفعات ارسال به کانال: <b>{summary['channel_deliveries']}</b>",
        f"📥 دفعات تحویل در پی‌وی: <b>{summary['pv_deliveries']}</b>",
        f"📢 کانال‌های مقصد ثبت‌شده: <b>{summary['total_channels']}</b>",
        f"👨‍💼 ادمین‌های این فضا: <b>{len(ws_admins)}</b> (+ مالک)",
    ]

    if config.OWNER_ID is not None and user_id == config.OWNER_ID:
        g_stats = registry.get_global_stats()
        lines.extend(
            [
                "",
                "🌐 <b>آمار کل ربات (ابرمالک):</b>",
                f"• کل کاربران: <b>{g_stats['total_users']}</b>",
                f"• دیتابیس‌های ساخته‌شده: <b>{count_workspace_dbs()}</b>",
            ]
        )

    if recent:
        lines.append("")
        lines.append("🕒 <b>آخرین پست‌ها:</b>")
        for idx, post in enumerate(recent, 1):
            snippet = html.escape(shorten_text(post.get("post_text") or "", 42))
            badge = "✅" if post.get("status") == "published" else "📝"
            date_str = html.escape(fmt_tehran(post.get("created_at")))
            lines.append(f"{idx}. {badge} {snippet} — <i>{date_str}</i>")

    return "\n".join(lines)


@workspace_access
async def show_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    user_id = update.effective_user.id
    sent = await update.message.reply_text(
        _build_stats_text(user_id),
        reply_markup=stats_keyboard(),
        parse_mode="HTML",
    )
    register_panel_message(context, sent.message_id)
    return STATS_VIEW


@workspace_access
async def handle_stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    data = (query.data or "").strip()

    if data == "stats:refresh":
        await _safe_answer(query)
        chat_id = query.message.chat_id if query.message else user_id
        msg_id = query.message.message_id if query.message else None
        await safe_edit_or_send(
            context=context,
            chat_id=chat_id,
            text=_build_stats_text(user_id),
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
            await go_to_main_menu(context, query.message.chat_id, user_id=user_id)
        return ConversationHandler.END

    await _safe_answer(query, "⚠️ گزینه نامعتبر", show_alert=True)
    return STATS_VIEW
