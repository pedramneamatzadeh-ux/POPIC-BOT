from functools import wraps
from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .config import config
from .constants import Msg
from .database import registry, resolve_user_workspace
from .keyboards import main_menu_for


def _is_blocked_user(user_id: int) -> bool:
    if config.OWNER_ID is not None and int(user_id) == int(config.OWNER_ID):
        return False
    return registry.is_user_banned(user_id)


async def reject_if_banned(update: Update) -> bool:
    user = update.effective_user
    if not user or not _is_blocked_user(user.id):
        return False
    if update.message:
        await update.message.reply_text(Msg.USER_BANNED)
    elif update.callback_query:
        await update.callback_query.answer(Msg.USER_BANNED, show_alert=True)
    return True


def env_owner_only(func: Callable[..., Any]) -> Callable[..., Any]:
    """فقط مالک اصلی ربات (تنظیم‌شده در OWNER_ID فایل .env) اجازه دسترسی دارد."""
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        if not user:
            return ConversationHandler.END

        registry.save_or_update_user(
            user.id, user.username, user.first_name, user.last_name
        )
        if config.OWNER_ID is None or user.id != config.OWNER_ID:
            if update.message:
                await update.message.reply_text(Msg.ENV_OWNER_ONLY_BACKUP)
            elif update.callback_query:
                await update.callback_query.answer(
                    Msg.ENV_OWNER_ONLY_BACKUP,
                    show_alert=True,
                )
            return ConversationHandler.END

        return await func(update, context, *args, **kwargs)

    return wrapper


def workspace_owner_only(func: Callable[..., Any]) -> Callable[..., Any]:
    """فقط زمانی اجازه می‌دهد که کاربر در فضای شخصی خودش (حالت مالک) باشد."""
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        if not user:
            return ConversationHandler.END

        registry.save_or_update_user(
            user.id, user.username, user.first_name, user.last_name
        )
        if await reject_if_banned(update):
            return ConversationHandler.END

        ws_id, status, ws_db = resolve_user_workspace(user.id)

        if status == "revoked_to_owner":
            if update.message:
                await update.message.reply_text(
                    Msg.ROLE_REVOKED_NOTICE,
                    reply_markup=main_menu_for(user.id),
                )

        if status == "admin" or ws_id != user.id:
            if update.message:
                await update.message.reply_text(Msg.OWNER_ONLY_FEATURE)
            elif update.callback_query:
                await update.callback_query.answer(
                    Msg.OWNER_ONLY_FEATURE,
                    show_alert=True,
                )
            return ConversationHandler.END

        context.user_data["_ws_id"] = ws_id
        context.user_data["_ws_role"] = "owner"
        context.user_data["_ws_db"] = ws_db
        return await func(update, context, *args, **kwargs)

    return wrapper


def workspace_access(func: Callable[..., Any]) -> Callable[..., Any]:
    """دسترسی عمومی ربات — فضای کاری فعال کاربر (مالک یا ادمین مجاز) را آماده می‌کند."""
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        if not user:
            return ConversationHandler.END

        registry.save_or_update_user(
            user.id, user.username, user.first_name, user.last_name
        )
        if await reject_if_banned(update):
            return ConversationHandler.END

        ws_id, status, ws_db = resolve_user_workspace(user.id)

        if status == "revoked_to_owner":
            if update.message:
                await update.message.reply_text(
                    Msg.ROLE_REVOKED_NOTICE,
                    reply_markup=main_menu_for(user.id),
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    Msg.ROLE_REVOKED_NOTICE,
                    show_alert=True,
                )
                return ConversationHandler.END

        context.user_data["_ws_id"] = ws_id
        context.user_data["_ws_role"] = "admin" if status == "admin" else "owner"
        context.user_data["_ws_db"] = ws_db
        return await func(update, context, *args, **kwargs)

    return wrapper
