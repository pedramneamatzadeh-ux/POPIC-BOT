import logging
import re

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..constants import Msg
from ..database import get_workspace_db, registry
from ..decorators import reject_if_banned
from ..keyboards import main_menu_for, workspace_choice_keyboard
from ..services.panels import close_panels_for
from ..utils import register_panel_message, track_user

logger = logging.getLogger(__name__)


async def _send_workspace_picker(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    admin_workspaces: list,
) -> None:
    sent = await message.reply_text(
        Msg.CHOOSE_ROLE_PROMPT,
        reply_markup=workspace_choice_keyboard(user_id, admin_workspaces),
    )
    register_panel_message(context, sent.message_id)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    if update.effective_chat:
        close_panels_for(context, update.effective_chat.id, user.id)

    track_user(update)
    if await reject_if_banned(update):
        return
    # برای هر کسی که ربات را استارت کند دیتابیس اختصاصی ساخته می‌شود
    get_workspace_db(user.id)

    admin_workspaces = registry.get_admin_workspaces(user.id)
    if admin_workspaces:
        await _send_workspace_picker(message, context, user.id, admin_workspaces)
        return

    registry.set_active_workspace_id(user.id, user.id)
    await message.reply_text(
        Msg.OWNER_WELCOME,
        reply_markup=main_menu_for(user.id),
    )


async def open_workspace_switcher(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    track_user(update)
    if await reject_if_banned(update):
        return
    get_workspace_db(user.id)

    admin_workspaces = registry.get_admin_workspaces(user.id)
    if not admin_workspaces:
        registry.set_active_workspace_id(user.id, user.id)
        await message.reply_text(
            Msg.OWNER_WELCOME,
            reply_markup=main_menu_for(user.id),
        )
        return

    await _send_workspace_picker(message, context, user.id, admin_workspaces)


async def workspace_pick_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    match = re.match(r"^ws_pick:(\d+)$", query.data or "")
    if not match:
        await query.answer(Msg.INVALID_COMMAND, show_alert=True)
        return

    target_ws_id = int(match.group(1))
    track_user(update)
    if await reject_if_banned(update):
        return

    if target_ws_id == user.id:
        get_workspace_db(user.id)
        registry.set_active_workspace_id(user.id, user.id)
        if update.effective_chat:
            close_panels_for(context, update.effective_chat.id, user.id)
        await query.answer("✅ وارد فضای شخصی (مالک) شدید.")
        try:
            await query.edit_message_text(
                "✅ نقش فعال: 👑 مالک (دیتابیس اختصاصی شما — دسترسی کامل به تنظیمات و بک‌آپ)"
            )
        except TelegramError:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id if query.message else user.id,
            text=Msg.OWNER_WELCOME,
            reply_markup=main_menu_for(user.id),
        )
        return

    if not registry.is_workspace_admin(target_ws_id, user.id):
        await query.answer(
            "⛔ دسترسی ادمینی شما به این فضا فعال نیست.",
            show_alert=True,
        )
        return

    get_workspace_db(target_ws_id)
    registry.set_active_workspace_id(user.id, target_ws_id)
    if update.effective_chat:
        close_panels_for(context, update.effective_chat.id, user.id)

    owner_name = registry.get_user_display_name(target_ws_id)
    await query.answer(f"✅ وارد فضای ادمینی «{owner_name}» شدید.")
    try:
        await query.edit_message_text(
            f"✅ نقش فعال: 👨‍💼 ادمینِ «{owner_name}» (بدون دسترسی به تنظیمات و بک‌آپ)"
        )
    except TelegramError:
        pass
    await context.bot.send_message(
        chat_id=query.message.chat_id if query.message else user.id,
        text=Msg.ADMIN_WELCOME.format(owner_name=owner_name),
        reply_markup=main_menu_for(user.id),
    )
