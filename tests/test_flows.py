import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram import MessageEntity, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from bot.application import build_application
from bot.config import config
from bot.constants import (
    BOT_VERSION,
    PM_DETAIL,
    PM_LIST,
    POST_PHOTO_LINK,
    POST_PREVIEW,
    POST_TEXT,
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    STATS_VIEW,
    Msg,
)
from bot.database import db
from bot.handlers.backup import handle_backup
from bot.handlers.post_builder import (
    cancel_from_inline,
    confirm_cancel_no,
    confirm_cancel_yes,
    handle_preview_callback,
    receive_photo_link,
    receive_post_text,
    start_post_builder,
)
from bot.handlers.post_manage import (
    handle_post_manage_callback,
    receive_pm_edit_link,
    receive_pm_edit_text,
    start_post_manage,
)
from bot.handlers.settings import (
    open_settings,
    receive_admin_input,
    receive_button_label_input,
    receive_channel_input,
    settings_callback,
)
from bot.handlers.stats import handle_stats_callback, show_stats
from bot.services.panels import panel_gate


class _FakeBot:
    def __init__(self, bot_id: int = 777) -> None:
        self.id = bot_id
        self.username = "popic_test_bot"
        self.sent_messages = []
        self.edited_messages = []
        self.deleted_messages = []
        self.sent_documents = []
        self.chats = {}
        self.chat_members = {}
        self._next_mid = 100

    def _alloc_mid(self) -> int:
        self._next_mid += 1
        return self._next_mid

    async def send_message(self, chat_id, text, **kwargs):
        mid = self._alloc_mid()
        record = {"chat_id": chat_id, "message_id": mid, "text": text, **kwargs}
        self.sent_messages.append(record)
        return SimpleNamespace(chat_id=chat_id, message_id=mid, text=text)

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        record = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            **kwargs,
        }
        self.edited_messages.append(record)
        return SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text)

    async def delete_message(self, chat_id, message_id):
        self.deleted_messages.append((chat_id, message_id))
        return True

    async def send_document(self, chat_id, document, **kwargs):
        mid = self._alloc_mid()
        data = document.read() if hasattr(document, "read") else b""
        self.sent_documents.append(
            {"chat_id": chat_id, "data": data, **kwargs}
        )
        return SimpleNamespace(chat_id=chat_id, message_id=mid)

    async def get_chat(self, target):
        if target in self.chats:
            return self.chats[target]
        raise RuntimeError("not found")

    async def get_chat_member(self, chat_id, user_id):
        return self.chat_members[(chat_id, user_id)]


def _make_message(bot: _FakeBot, chat_id: int, user_id: int, text: str = "", **extra):
    mid = bot._alloc_mid()

    async def reply_text(reply_txt, **kwargs):
        return await bot.send_message(chat_id, reply_txt, **kwargs)

    return SimpleNamespace(
        chat_id=chat_id,
        message_id=mid,
        text=text,
        entities=extra.get("entities", ()),
        forward_origin=extra.get("forward_origin"),
        forward_from=extra.get("forward_from"),
        forward_from_chat=extra.get("forward_from_chat"),
        forward_from_message_id=extra.get("forward_from_message_id"),
        reply_text=reply_text,
    )


def _make_query(bot: _FakeBot, chat_id: int, user_id: int, message_id: int, data: str):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(text, **kwargs):
        return await bot.edit_message_text(chat_id, message_id, text, **kwargs)

    msg = SimpleNamespace(chat_id=chat_id, message_id=message_id)
    query = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username="owner", first_name="Owner", last_name=""),
        message=msg,
        answer=answer,
        edit_message_text=edit_message_text,
        answers=answers,
    )
    return query


def _make_update(user_id: int, chat_id: int, message=None, callback_query=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(
            id=user_id,
            username=f"u{user_id}",
            first_name="User",
            last_name=str(user_id),
        ),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
        callback_query=callback_query,
    )


def _make_context(bot: _FakeBot, application=None):
    return SimpleNamespace(
        bot=bot,
        user_data={},
        chat_data={},
        bot_data=application.bot_data if application else {},
        application=application,
    )


class FlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._tmp.name, "flow.db")
        self._orig_token = config.BOT_TOKEN
        self._orig_owner = config.OWNER_ID
        self._orig_admins = list(config.ADMIN_IDS)
        self._orig_db_path = db.path
        self._orig_cfg_db_path = config.DB_PATH
        self._orig_above = config.DEFAULT_SHOW_ABOVE_TEXT
        self._orig_large = config.DEFAULT_PREFER_LARGE_MEDIA
        self._orig_btn = config.DEFAULT_BUTTON_TEXT

        config.BOT_TOKEN = "123456:TEST_TOKEN"
        config.OWNER_ID = 1000
        config.ADMIN_IDS = [2000]
        config.DEFAULT_SHOW_ABOVE_TEXT = True
        config.DEFAULT_PREFER_LARGE_MEDIA = True
        config.DEFAULT_BUTTON_TEXT = "🖼 مشاهده تصویر"
        config.DB_PATH = self._db_path
        db.path = self._db_path
        db.init()

    def tearDown(self) -> None:
        config.BOT_TOKEN = self._orig_token
        config.OWNER_ID = self._orig_owner
        config.ADMIN_IDS = self._orig_admins
        config.DEFAULT_SHOW_ABOVE_TEXT = self._orig_above
        config.DEFAULT_PREFER_LARGE_MEDIA = self._orig_large
        config.DEFAULT_BUTTON_TEXT = self._orig_btn
        config.DB_PATH = self._orig_cfg_db_path
        db.path = self._orig_db_path
        self._tmp.cleanup()

    def test_post_builder_full_flow_pv_and_channel(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            db.add_target_channel(-100555, "Main Channel", "main_ch", 1000)

            # 1) Start post builder -> ReplyKeyboardRemove + text prompt
            msg1 = _make_message(bot, 2000, 2000, "📝 ساخت پست جدید")
            state = await start_post_builder(_make_update(2000, 2000, message=msg1), ctx)
            self.assertEqual(state, POST_TEXT)
            self.assertIsInstance(bot.sent_messages[0]["reply_markup"], ReplyKeyboardRemove)

            # 2a) Over 4096 chars rejected
            too_long = _make_message(bot, 2000, 2000, "ا" * 4100)
            state = await receive_post_text(_make_update(2000, 2000, message=too_long), ctx)
            self.assertEqual(state, POST_TEXT)
            self.assertIn("4100", bot.sent_messages[-1]["text"])

            # 2b) Send 4000-char formatted text -> prompt closed, photo link asked
            long_text = "ا" * 4000
            entities = [MessageEntity(type="bold", offset=0, length=10)]
            msg2 = _make_message(bot, 2000, 2000, long_text, entities=entities)
            state = await receive_post_text(_make_update(2000, 2000, message=msg2), ctx)
            self.assertEqual(state, POST_PHOTO_LINK)
            self.assertIn("4000 کاراکتر", bot.edited_messages[-1]["text"])

            # 3) Private channel forward rejected
            priv_msg = _make_message(
                bot,
                2000,
                2000,
                "",
                forward_origin=SimpleNamespace(
                    chat=SimpleNamespace(username=None),
                    message_id=7,
                ),
            )
            state = await receive_photo_link(_make_update(2000, 2000, message=priv_msg), ctx)
            self.assertEqual(state, POST_PHOTO_LINK)
            self.assertIn("کانال خصوصی", bot.sent_messages[-1]["text"])

            # 4) Public channel forward accepted -> live preview + control panel
            pub_msg = _make_message(
                bot,
                2000,
                2000,
                "",
                forward_origin=SimpleNamespace(
                    chat=SimpleNamespace(username="my_pics"),
                    message_id=42,
                ),
            )
            state = await receive_photo_link(_make_update(2000, 2000, message=pub_msg), ctx)
            self.assertEqual(state, POST_PREVIEW)
            preview_sent = bot.sent_messages[-2]
            self.assertEqual(len(preview_sent["text"]), 4000)
            self.assertEqual(preview_sent["link_preview_options"].url, "https://t.me/my_pics/42")
            self.assertTrue(preview_sent["link_preview_options"].show_above_text)
            self.assertTrue(preview_sent["link_preview_options"].prefer_large_media)
            self.assertEqual(preview_sent["entities"][0].type, "bold")
            self.assertIn((2000, pub_msg.message_id), bot.deleted_messages)

            # 5) Toggle preview position -> refreshes preview with show_above_text=False
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            q_toggle = _make_query(bot, 2000, 2000, ctrl_mid, "pb:toggle_pos")
            state = await handle_preview_callback(_make_update(2000, 2000, callback_query=q_toggle), ctx)
            self.assertEqual(state, POST_PREVIEW)
            self.assertFalse(bot.sent_messages[-2]["link_preview_options"].show_above_text)

            # 6) Deliver clean post in PV
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            q_pv = _make_query(bot, 2000, 2000, ctrl_mid, "pb:send_pv")
            state = await handle_preview_callback(_make_update(2000, 2000, callback_query=q_pv), ctx)
            self.assertEqual(state, POST_PREVIEW)
            self.assertIsNone(bot.sent_messages[-1].get("reply_markup"))
            self.assertEqual(db.get_stats_summary()["pv_deliveries"], 1)

            # 7) Publish to channel -> sent to -100555, marked published, main menu restored
            q_ch = _make_query(bot, 2000, 2000, ctrl_mid, "pb:send_ch")
            state = await handle_preview_callback(_make_update(2000, 2000, callback_query=q_ch), ctx)
            self.assertEqual(state, ConversationHandler.END)
            ch_posts = [m for m in bot.sent_messages if m["chat_id"] == -100555]
            self.assertEqual(len(ch_posts), 1)
            self.assertEqual(ch_posts[0]["link_preview_options"].url, "https://t.me/my_pics/42")
            self.assertEqual(db.get_stats_summary()["published_posts"], 1)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.MAIN_MENU_PROMPT)

        asyncio.run(run())

    def test_cancel_confirmation_yes_and_no(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ctx.user_data["pb"] = {"text": "keep"}

            q_ask = _make_query(bot, 2000, 2000, 10, "cancel_prompt")
            res = await cancel_from_inline(_make_update(2000, 2000, callback_query=q_ask), ctx)
            self.assertIsNone(res)
            confirm_mid = ctx.user_data["cancel_confirm_mid"]

            q_no = _make_query(bot, 2000, 2000, confirm_mid, "cancel_no")
            res = await confirm_cancel_no(_make_update(2000, 2000, callback_query=q_no), ctx)
            self.assertIsNone(res)
            self.assertEqual(ctx.user_data["pb"]["text"], "keep")

            q_yes = _make_query(bot, 2000, 2000, confirm_mid, "cancel_yes")
            res = await confirm_cancel_yes(_make_update(2000, 2000, callback_query=q_yes), ctx)
            self.assertEqual(res, ConversationHandler.END)
            self.assertEqual(ctx.user_data, {})

        asyncio.run(run())

    def test_post_manage_and_channel_update(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ch_id = db.add_target_channel(-100777, "Target Ch", "target_ch", 1000)
            post_id = db.create_post("پست اول", "[]", "https://t.me/pics/9", 1000)

            msg = _make_message(bot, 1000, 1000, "📂 پست‌های ذخیره‌شده")
            state = await start_post_manage(_make_update(1000, 1000, message=msg), ctx)
            self.assertEqual(state, PM_LIST)
            mid = ctx.user_data["pm"]["msg_id"]

            q_det = _make_query(bot, 1000, 1000, mid, f"pm:detail:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_det), ctx)
            self.assertEqual(state, PM_DETAIL)

            q_pub = _make_query(bot, 1000, 1000, mid, f"pm:send_ch:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_pub), ctx)
            self.assertEqual(state, PM_DETAIL)
            self.assertEqual(db.get_post(post_id)["status"], "published")

            # Edit text then update published message in channel
            q_et = _make_query(bot, 1000, 1000, mid, f"pm:edit_text:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_et), ctx)
            new_msg = _make_message(bot, 1000, 1000, "متن ویرایش‌شده")
            state = await receive_pm_edit_text(_make_update(1000, 1000, message=new_msg), ctx)
            self.assertEqual(state, PM_DETAIL)

            q_upd = _make_query(bot, 1000, 1000, mid, f"pm:update_ch:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_upd), ctx)
            ch_edits = [m for m in bot.edited_messages if m["chat_id"] == -100777]
            self.assertEqual(len(ch_edits), 1)
            self.assertEqual(ch_edits[0]["text"], "متن ویرایش‌شده")

            # Edit link
            q_el = _make_query(bot, 1000, 1000, mid, f"pm:edit_link:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_el), ctx)
            link_msg = _make_message(bot, 1000, 1000, "t.me/pics/99")
            await receive_pm_edit_link(_make_update(1000, 1000, message=link_msg), ctx)
            self.assertEqual(db.get_post(post_id)["photo_url"], "https://t.me/pics/99")

            # Delete with confirm
            q_del = _make_query(bot, 1000, 1000, mid, f"pm:del_yes:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_del), ctx)
            self.assertEqual(state, PM_LIST)
            self.assertIsNone(db.get_post(post_id))
            self.assertTrue(ch_id > 0)

        asyncio.run(run())

    def test_settings_owner_guard_and_actions(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)

            # Admin (non-owner) is rejected
            admin_msg = _make_message(bot, 2000, 2000, "⚙️ تنظیمات")
            res = await open_settings(_make_update(2000, 2000, message=admin_msg), ctx)
            self.assertEqual(res, ConversationHandler.END)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.ACCESS_DENIED)

            # Owner opens panel -> version shown
            owner_msg = _make_message(bot, 1000, 1000, "⚙️ تنظیمات")
            res = await open_settings(_make_update(1000, 1000, message=owner_msg), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertIn(BOT_VERSION, bot.sent_messages[-1]["text"])
            panel_mid = bot.sent_messages[-1]["message_id"]

            # Add admin
            q_add_adm = _make_query(bot, 1000, 1000, panel_mid, "st:adm_add")
            res = await settings_callback(_make_update(1000, 1000, callback_query=q_add_adm), ctx)
            self.assertEqual(res, SETTINGS_ADMIN)
            adm_in = _make_message(bot, 1000, 1000, "3000")
            res = await receive_admin_input(_make_update(1000, 1000, message=adm_in), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertIn(3000, config.ADMIN_IDS)

            # Add channel with precheck
            bot.chats["@my_chan"] = SimpleNamespace(
                id=-100999, type="channel", title="My Chan", username="my_chan"
            )
            bot.chat_members[(-100999, bot.id)] = SimpleNamespace(status="administrator")
            q_add_ch = _make_query(bot, 1000, 1000, panel_mid, "st:ch_add")
            res = await settings_callback(_make_update(1000, 1000, callback_query=q_add_ch), ctx)
            self.assertEqual(res, SETTINGS_CHANNEL)
            ch_in = _make_message(bot, 1000, 1000, "@my_chan")
            res = await receive_channel_input(_make_update(1000, 1000, message=ch_in), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertEqual(db.count_target_channels(), 1)

            # Toggle preview position & update button label
            q_pos = _make_query(bot, 1000, 1000, panel_mid, "st:pv_pos")
            await settings_callback(_make_update(1000, 1000, callback_query=q_pos), ctx)
            self.assertFalse(config.DEFAULT_SHOW_ABOVE_TEXT)

            q_lbl = _make_query(bot, 1000, 1000, panel_mid, "st:btn_lbl")
            res = await settings_callback(_make_update(1000, 1000, callback_query=q_lbl), ctx)
            self.assertEqual(res, SETTINGS_BTN_LABEL)
            lbl_in = _make_message(bot, 1000, 1000, "📸 باز کردن عکس")
            res = await receive_button_label_input(_make_update(1000, 1000, message=lbl_in), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertEqual(config.DEFAULT_BUTTON_TEXT, "📸 باز کردن عکس")

        asyncio.run(run())

    def test_stats_and_panel_gate_and_backup_guard(self) -> None:
        async def run():
            app = build_application()
            bot = _FakeBot()
            ctx = _make_context(bot, application=app)

            # Stranger rejected by stats
            bad_msg = _make_message(bot, 9999, 9999, "📊 آمار")
            res = await show_stats(_make_update(9999, 9999, message=bad_msg), ctx)
            self.assertEqual(res, ConversationHandler.END)

            # Owner opens stats -> registered in _panel_msgs
            ok_msg = _make_message(bot, 1000, 1000, "📊 آمار")
            res = await show_stats(_make_update(1000, 1000, message=ok_msg), ctx)
            self.assertEqual(res, STATS_VIEW)
            stats_mid = bot.sent_messages[-1]["message_id"]
            self.assertIn(stats_mid, ctx.chat_data["_panel_msgs"])

            # Simulate active conversation in stats, then trigger panel_gate
            app.bot_data["panel_convs"]["stats"]._conversations[(1000, 1000)] = STATS_VIEW
            gate_msg = _make_message(bot, 1000, 1000, "📂 پست‌های ذخیره‌شده")
            await panel_gate(_make_update(1000, 1000, message=gate_msg), ctx)
            self.assertNotIn((1000, 1000), app.bot_data["panel_convs"]["stats"]._conversations)
            self.assertEqual(bot.edited_messages[-1]["text"], Msg.PANEL_CLOSED)
            self.assertIsNone(bot.edited_messages[-1]["reply_markup"])

            # Close stats via callback
            q_close = _make_query(bot, 1000, 1000, stats_mid, "stats:close")
            res = await handle_stats_callback(_make_update(1000, 1000, callback_query=q_close), ctx)
            self.assertEqual(res, ConversationHandler.END)

            # Backup rejects admin, allows owner
            adm_bk = _make_message(bot, 2000, 2000, "💾 بک‌آپ")
            await handle_backup(_make_update(2000, 2000, message=adm_bk), ctx)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.ACCESS_DENIED)

            own_bk = _make_message(bot, 1000, 1000, "💾 بک‌آپ")
            await handle_backup(_make_update(1000, 1000, message=own_bk), ctx)
            self.assertTrue(len(bot.sent_documents) >= 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
