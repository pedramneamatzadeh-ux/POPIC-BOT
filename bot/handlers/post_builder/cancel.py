"""لغو فلوی ساخت پست با تأیید اینلاین (بدون ذخیرهٔ پیش‌نویس در دیتابیس)."""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ...constants import Msg
from ...decorators import workspace_access
from ...keyboards import CANCEL_CONFIRM_INLINE, main_menu_for
from ...utils import go_to_main_menu

logger = logging.getLogger(__name__)

CANCEL_CONFIRM_TEXT = "❗️ مطمئنی می‌خواهی ساخت پست را لغو کنی؟"


async def _cleanup_preview_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    pb = context.user_data.get("pb") or {}
    for key in ("preview_msg_id", "ctrl_msg_id"):
        mid = pb.get(key)
        if mid:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except TelegramError:
                pass


@workspace_access
async def cancel_builder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    user_id = update.effective_user.id
    if update.message:
        await _cleanup_preview_messages(context, update.message.chat_id)
        await update.message.reply_text(
            Msg.POST_CANCELLED,
            reply_markup=main_menu_for(user_id),
        )
    context.user_data.clear()
    return ConversationHandler.END


async def ask_cancel_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    chat_id = (
        query.message.chat_id
        if query and query.message
        else getattr(update.effective_user, "id", None)
    )
    if chat_id is None:
        return
    try:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=CANCEL_CONFIRM_TEXT,
            reply_markup=CANCEL_CONFIRM_INLINE,
        )
        context.user_data["cancel_confirm_mid"] = getattr(
            sent, "message_id", None
        )
    except TelegramError:
        logger.debug("ارسال پیام تأیید لغو ناموفق بود", exc_info=True)


@workspace_access
async def confirm_cancel_yes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = query.message.chat_id if query.message else user_id
    await query.answer()
    await _cleanup_preview_messages(context, chat_id)
    try:
        await query.edit_message_text(Msg.POST_CANCELLED_INLINE)
    except TelegramError:
        pass
    context.user_data.pop("cancel_confirm_mid", None)
    await go_to_main_menu(context, chat_id, user_id=user_id)
    context.user_data.clear()
    return ConversationHandler.END


@workspace_access
async def confirm_cancel_no(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer("👍 ساخت پست ادامه دارد.")
    if query.message:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
            )
        except TelegramError:
            pass
    context.user_data.pop("cancel_confirm_mid", None)
    return None


@workspace_access
async def cancel_from_inline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        await ask_cancel_confirm(update, context)
    return None
