import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..constants import Msg
from ..keyboards import main_menu_for
from ..services.panels import close_panels_for
from ..utils import track_user

logger = logging.getLogger(__name__)


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

    if user.id in config.ADMIN_IDS or user.id == config.OWNER_ID:
        await message.reply_text(
            Msg.ADMIN_WELCOME,
            reply_markup=main_menu_for(user.id),
        )
    else:
        await message.reply_text(Msg.USER_WELCOME)
