import logging
import os
import shutil
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from ..decorators import workspace_owner_only
from ..keyboards import main_menu_for

logger = logging.getLogger(__name__)


@workspace_owner_only
async def handle_backup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    ws_db = context.user_data["_ws_db"]

    await update.message.reply_text("💾 در حال تهیه بک‌آپ از دیتابیس شما...")

    db_path = os.path.abspath(ws_db.path)
    if not os.path.exists(db_path):
        await update.message.reply_text(
            "❌ فایل دیتابیس شما پیدا نشد!",
            reply_markup=main_menu_for(user_id),
        )
        return

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        backup_name = f"backup_{os.path.basename(db_path)}"
        backup_path = os.path.join(tmp_dir, backup_name)
        ws_db.wal_checkpoint()
        shutil.copy2(db_path, backup_path)

        with open(backup_path, "rb") as backup_file:
            await context.bot.send_document(
                chat_id=chat_id,
                document=backup_file,
                filename=backup_name,
                caption="💾 بک‌آپ دیتابیس اختصاصی شما با موفقیت تهیه شد.",
            )
    except Exception as err:
        logger.exception("خطا در تهیه بک‌آپ: %s", err)
        await update.message.reply_text(
            f"❌ خطا در تهیه بک‌آپ: {err}",
            reply_markup=main_menu_for(user_id),
        )
        return
    finally:
        if tmp_dir:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    await update.message.reply_text(
        "✅ بک‌آپ کامل شد.",
        reply_markup=main_menu_for(user_id),
    )
