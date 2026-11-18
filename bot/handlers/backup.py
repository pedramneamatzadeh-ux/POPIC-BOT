import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..database import Database, registry
from ..decorators import env_owner_only
from ..keyboards import main_menu_for

logger = logging.getLogger(__name__)


@env_owner_only
async def handle_backup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_id = update.effective_user.id
    chat_id = update.message.chat_id

    await update.message.reply_text("💾 در حال تهیه بک‌آپ کامل دیتابیس‌های ربات...")

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_name = f"backup_popic_data_{stamp}.zip"
        zip_path = os.path.join(tmp_dir, zip_name)

        added_count = 0
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            reg_path = os.path.abspath(registry.path)
            if os.path.exists(reg_path):
                registry.wal_checkpoint()
                zf.write(reg_path, arcname="registry.db")
                added_count += 1

            if os.path.isdir(config.WORKSPACES_DIR):
                for fname in sorted(os.listdir(config.WORKSPACES_DIR)):
                    if not (fname.startswith("user_") and fname.endswith(".db")):
                        continue
                    ws_path = os.path.join(config.WORKSPACES_DIR, fname)
                    if not os.path.isfile(ws_path):
                        continue
                    Database(ws_path).wal_checkpoint()
                    zf.write(ws_path, arcname=os.path.join("workspaces", fname))
                    added_count += 1

        if added_count == 0:
            await update.message.reply_text(
                "❌ هیچ فایل دیتابیسی برای بک‌آپ پیدا نشد!",
                reply_markup=main_menu_for(user_id),
            )
            return

        with open(zip_path, "rb") as backup_file:
            await context.bot.send_document(
                chat_id=chat_id,
                document=backup_file,
                filename=zip_name,
                caption=(
                    f"💾 بک‌آپ کامل دیتابیس‌های ربات ({added_count} فایل) با موفقیت تهیه شد."
                ),
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
