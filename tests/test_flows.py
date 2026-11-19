import asyncio
import io
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

from telegram import MessageEntity, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from bot.application import create_application
from bot.config import config
from bot.constants import (
    BOT_VERSION,
    DEFAULT_POPUP_TEXT,
    PM_ADD_BTN_TEXT,
    PM_ADD_BTN_URL,
    PM_BTN_EDIT_TEXT,
    PM_BTN_EDIT_VALUE,
    PM_DETAIL,
    PM_EDIT_PHOTO,
    PM_LIST,
    POST_ADD_BTN_TEXT,
    POST_ADD_BTN_URL,
    POST_BTN_EDIT_TEXT,
    POST_BTN_EDIT_VALUE,
    POST_EDIT_TEXT,
    POST_MEDIA_CHOICE,
    POST_PHOTO_LINK,
    POST_PHOTO_UPLOAD,
    POST_PREVIEW,
    POST_TEXT,
    POST_TITLE,
    SETTINGS_ADMIN,
    SETTINGS_BTN_LABEL,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    STATS_VIEW,
    Msg,
)
from bot.database import (
    clear_workspace_cache,
    get_workspace_db,
    registry,
)
from bot.handlers.backup import handle_backup
from bot.handlers.base import (
    open_workspace_switcher,
    start,
    workspace_pick_callback,
)
from bot.handlers.display_button import handle_display_button_callback
from bot.handlers.post_builder import (
    cancel_from_inline,
    confirm_cancel_no,
    confirm_cancel_yes,
    handle_media_choice_callback,
    handle_preview_callback,
    handle_skip_title_callback,
    receive_btn_edit_text,
    receive_btn_edit_value,
    receive_btn_text,
    receive_btn_url,
    receive_edit_text,
    receive_photo_link,
    receive_photo_upload,
    receive_post_text,
    receive_post_title,
    start_post_builder,
)
from bot.handlers.post_manage import (
    handle_post_manage_callback,
    receive_pm_btn_edit_text,
    receive_pm_btn_edit_value,
    receive_pm_btn_text,
    receive_pm_btn_url,
    receive_pm_edit_link,
    receive_pm_edit_photo,
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
from bot.services.publisher import deserialize_buttons


class _FakeBot:
    def __init__(self, bot_id: int = 777) -> None:
        self.id = bot_id
        self.username = "popic_test_bot"
        self.sent_messages = []
        self.sent_photos = []
        self.edited_messages = []
        self.edited_media = []
        self.edited_markups = []
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

    async def send_photo(self, chat_id, photo, **kwargs):
        mid = self._alloc_mid()
        record = {"chat_id": chat_id, "message_id": mid, "photo": photo, **kwargs}
        self.sent_photos.append(record)
        return SimpleNamespace(chat_id=chat_id, message_id=mid, caption=kwargs.get("caption", ""))

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        record = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            **kwargs,
        }
        self.edited_messages.append(record)
        return SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text)

    async def edit_message_media(self, chat_id, message_id, media, **kwargs):
        record = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": media,
            **kwargs,
        }
        self.edited_media.append(record)
        return SimpleNamespace(chat_id=chat_id, message_id=message_id)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            }
        )
        return SimpleNamespace(chat_id=chat_id, message_id=message_id)

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

    async def delete():
        bot.deleted_messages.append((chat_id, mid))
        return True

    return SimpleNamespace(
        chat_id=chat_id,
        message_id=mid,
        text=text,
        entities=extra.get("entities", ()),
        photo=extra.get("photo", ()),
        forward_origin=extra.get("forward_origin"),
        forward_from=extra.get("forward_from"),
        forward_from_chat=extra.get("forward_from_chat"),
        forward_from_message_id=extra.get("forward_from_message_id"),
        reply_text=reply_text,
        delete=delete,
    )


def _make_query(bot: _FakeBot, chat_id: int, user_id: int, message_id: int, data: str):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(text, **kwargs):
        return await bot.edit_message_text(chat_id, message_id, text, **kwargs)

    msg = SimpleNamespace(chat_id=chat_id, message_id=message_id)
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username=f"u{user_id}", first_name="User", last_name=""),
        message=msg,
        answer=answer,
        edit_message_text=edit_message_text,
        answers=answers,
    )


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
        self._orig_token = config.BOT_TOKEN
        self._orig_owner = config.OWNER_ID
        self._orig_data = config.DATA_DIR
        self._orig_reg = config.REGISTRY_DB_PATH
        self._orig_ws = config.WORKSPACES_DIR
        self._orig_reg_path = registry.path

        config.BOT_TOKEN = "123456:TEST_TOKEN"
        config.OWNER_ID = 1000
        config.DATA_DIR = os.path.join(self._tmp.name, "data")
        config.REGISTRY_DB_PATH = os.path.join(config.DATA_DIR, "registry.db")
        config.WORKSPACES_DIR = os.path.join(config.DATA_DIR, "workspaces")
        registry.path = config.REGISTRY_DB_PATH
        clear_workspace_cache()
        registry.init()

    def tearDown(self) -> None:
        config.BOT_TOKEN = self._orig_token
        config.OWNER_ID = self._orig_owner
        config.DATA_DIR = self._orig_data
        config.REGISTRY_DB_PATH = self._orig_reg
        config.WORKSPACES_DIR = self._orig_ws
        registry.path = self._orig_reg_path
        clear_workspace_cache()
        self._tmp.cleanup()

    def test_post_builder_full_flow_pv_and_channel(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(2000)
            ws_db.add_target_channel(-100555, "Main Channel", "main_ch", 2000)

            msg1 = _make_message(bot, 2000, 2000, "📝 ساخت پست جدید")
            state = await start_post_builder(_make_update(2000, 2000, message=msg1), ctx)
            self.assertEqual(state, POST_TITLE)
            self.assertIsInstance(bot.sent_messages[0]["reply_markup"], ReplyKeyboardRemove)

            title_msg = _make_message(bot, 2000, 2000, "عنوان تست پست")
            state = await receive_post_title(_make_update(2000, 2000, message=title_msg), ctx)
            self.assertEqual(state, POST_TEXT)

            too_long = _make_message(bot, 2000, 2000, "ا" * 4100)
            state = await receive_post_text(_make_update(2000, 2000, message=too_long), ctx)
            self.assertEqual(state, POST_TEXT)
            self.assertIn("4100", bot.sent_messages[-1]["text"])

            long_text = "ا" * 4000
            entities = [MessageEntity(type="bold", offset=0, length=10)]
            msg2 = _make_message(bot, 2000, 2000, long_text, entities=entities)
            state = await receive_post_text(_make_update(2000, 2000, message=msg2), ctx)
            self.assertEqual(state, POST_MEDIA_CHOICE)
            self.assertIn("4000 کاراکتر", bot.edited_messages[-1]["text"])

            choice_mid = ctx.user_data["_pb_question_mid"]
            state = await handle_media_choice_callback(
                _make_update(2000, 2000, callback_query=_make_query(bot, 2000, 2000, choice_mid, "pb:media:link")),
                ctx,
            )
            self.assertEqual(state, POST_PHOTO_LINK)

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
            # R2: Before send/publish/done, post is NOT saved in DB
            self.assertEqual(ws_db.count_posts(), 0)
            preview_sent = bot.sent_messages[-2]
            self.assertEqual(len(preview_sent["text"]), 4000)
            self.assertEqual(preview_sent["link_preview_options"].url, "https://t.me/my_pics/42")
            self.assertTrue(preview_sent["link_preview_options"].show_above_text)
            self.assertTrue(preview_sent["link_preview_options"].prefer_large_media)
            self.assertEqual(preview_sent["entities"][0].type, "bold")
            self.assertIn((2000, pub_msg.message_id), bot.deleted_messages)

            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            q_toggle = _make_query(bot, 2000, 2000, ctrl_mid, "pb:toggle_pos")
            state = await handle_preview_callback(_make_update(2000, 2000, callback_query=q_toggle), ctx)
            self.assertEqual(state, POST_PREVIEW)
            self.assertFalse(bot.sent_messages[-2]["link_preview_options"].show_above_text)

            # 6) Publish to channel -> sent to -100555, marked published, main menu restored
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            q_ch = _make_query(bot, 2000, 2000, ctrl_mid, "pb:send_ch")
            state = await handle_preview_callback(_make_update(2000, 2000, callback_query=q_ch), ctx)
            self.assertEqual(state, ConversationHandler.END)
            ch_posts = [m for m in bot.sent_messages if m["chat_id"] == -100555]
            self.assertEqual(len(ch_posts), 1)
            self.assertEqual(ws_db.get_stats_summary()["published_posts"], 1)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.MAIN_MENU_PROMPT)

        asyncio.run(run())

    def test_r8_pv_delivery_deletes_only_control_message_and_restores_menu(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(4000)

            await start_post_builder(
                _make_update(4000, 4000, message=_make_message(bot, 4000, 4000, "📝 ساخت پست جدید")),
                ctx,
            )
            await receive_post_text(
                _make_update(4000, 4000, message=_make_message(bot, 4000, 4000, "متن خالص پی‌وی")),
                ctx,
            )
            await handle_media_choice_callback(
                _make_update(
                    4000,
                    4000,
                    callback_query=_make_query(
                        bot, 4000, 4000, ctx.user_data["_pb_question_mid"], "pb:media:link"
                    ),
                ),
                ctx,
            )
            await receive_photo_link(
                _make_update(4000, 4000, message=_make_message(bot, 4000, 4000, "https://t.me/pics/77")),
                ctx,
            )
            preview_mid = ctx.user_data["pb"]["preview_msg_id"]
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            sent_count_before = len(bot.sent_messages)

            q_pv = _make_query(bot, 4000, 4000, ctrl_mid, "pb:send_pv")
            state = await handle_preview_callback(_make_update(4000, 4000, callback_query=q_pv), ctx)
            self.assertEqual(state, ConversationHandler.END)

            # R8: Control message deleted, preview message NOT deleted, no duplicate post sent
            self.assertIn((4000, ctrl_mid), bot.deleted_messages)
            self.assertNotIn((4000, preview_mid), bot.deleted_messages)
            new_msgs = bot.sent_messages[sent_count_before:]
            self.assertEqual(len(new_msgs), 1)
            self.assertEqual(new_msgs[0]["text"], Msg.POST_PV_DELIVERED)
            self.assertEqual(ws_db.get_stats_summary()["pv_deliveries"], 1)
            self.assertEqual(ws_db.count_posts(), 1)

        asyncio.run(run())

    def test_r7_post_builder_advanced_buttons_colors_rows_and_popup(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(4500)

            await start_post_builder(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "📝 ساخت پست جدید")),
                ctx,
            )
            await receive_post_text(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "پست با دکمه‌های پیشرفته")),
                ctx,
            )
            await handle_media_choice_callback(
                _make_update(
                    4500,
                    4500,
                    callback_query=_make_query(
                        bot, 4500, 4500, ctx.user_data["_pb_question_mid"], "pb:media:link"
                    ),
                ),
                ctx,
            )
            await receive_photo_link(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "https://t.me/pics/88")),
                ctx,
            )
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]

            # Open button manager -> add URL button
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btns")),
                ctx,
            )
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_add")),
                ctx,
            )
            state = await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_type:url")),
                ctx,
            )
            self.assertEqual(state, POST_ADD_BTN_TEXT)
            state = await receive_btn_text(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "لینک ۱")),
                ctx,
            )
            self.assertEqual(state, POST_ADD_BTN_URL)
            state = await receive_btn_url(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "https://t.me/pics/1")),
                ctx,
            )
            self.assertEqual(state, POST_PREVIEW)

            # Add popup display button with default '-' popup text
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_add")),
                ctx,
            )
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_type:popup")),
                ctx,
            )
            await receive_btn_text(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "نمایشی")),
                ctx,
            )
            await receive_btn_url(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "-")),
                ctx,
            )

            # Inspect button 1 -> set color green, move up into row 0, edit text
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_ins:1")),
                ctx,
            )
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_col:1:success")),
                ctx,
            )
            await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_row_up:1")),
                ctx,
            )
            state = await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_etxt:1")),
                ctx,
            )
            self.assertEqual(state, POST_BTN_EDIT_TEXT)
            await receive_btn_edit_text(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "وضعیت ویژه")),
                ctx,
            )
            state = await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_eval:1")),
                ctx,
            )
            self.assertEqual(state, POST_PREVIEW)
            state = await handle_preview_callback(
                _make_update(4500, 4500, callback_query=_make_query(bot, 4500, 4500, ctrl_mid, "pb:btn_ctype:1:popup")),
                ctx,
            )
            self.assertEqual(state, POST_BTN_EDIT_VALUE)
            await receive_btn_edit_value(
                _make_update(4500, 4500, message=_make_message(bot, 4500, 4500, "این دکمه نمایشی اختصاصی است")),
                ctx,
            )

            # Deliver via pb:send_pv -> updates preview reply_markup to permanent pb_disp:<ws_id>:<post_id>:1
            q_pv = _make_query(bot, 4500, 4500, ctrl_mid, "pb:send_pv")
            state = await handle_preview_callback(_make_update(4500, 4500, callback_query=q_pv), ctx)
            self.assertEqual(state, ConversationHandler.END)

            last_markup = bot.edited_markups[-1]["reply_markup"]
            self.assertEqual(len(last_markup.inline_keyboard), 1)
            self.assertEqual(len(last_markup.inline_keyboard[0]), 2)
            disp_cb = last_markup.inline_keyboard[0][1].callback_data
            self.assertTrue(disp_cb.startswith("pb_disp:4500:"))
            self.assertEqual(last_markup.inline_keyboard[0][1].style, "success")

            # Any user clicking that display button sees custom alert
            q_disp = _make_query(bot, -100111, 9999, 50, disp_cb)
            await handle_display_button_callback(_make_update(9999, -100111, callback_query=q_disp), ctx)
            self.assertEqual(q_disp.answers[-1]["text"], "این دکمه نمایشی اختصاصی است")
            self.assertTrue(q_disp.answers[-1]["show_alert"])
            self.assertEqual(ws_db.count_posts(), 1)

        asyncio.run(run())

    def test_r2_cancel_does_not_save_post(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(3000)

            msg1 = _make_message(bot, 3000, 3000, "📝 ساخت پست جدید")
            await start_post_builder(_make_update(3000, 3000, message=msg1), ctx)
            msg2 = _make_message(bot, 3000, 3000, "متن تست برای لغو")
            await receive_post_text(_make_update(3000, 3000, message=msg2), ctx)
            await handle_media_choice_callback(
                _make_update(
                    3000,
                    3000,
                    callback_query=_make_query(
                        bot, 3000, 3000, ctx.user_data["_pb_question_mid"], "pb:media:link"
                    ),
                ),
                ctx,
            )
            msg3 = _make_message(bot, 3000, 3000, "https://t.me/pics/5")
            state = await receive_photo_link(_make_update(3000, 3000, message=msg3), ctx)
            self.assertEqual(state, POST_PREVIEW)
            self.assertEqual(ws_db.count_posts(), 0)

            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            preview_mid = ctx.user_data["pb"]["preview_msg_id"]

            q_ask = _make_query(bot, 3000, 3000, ctrl_mid, "cancel_prompt")
            await cancel_from_inline(_make_update(3000, 3000, callback_query=q_ask), ctx)
            confirm_mid = ctx.user_data["cancel_confirm_mid"]

            q_no = _make_query(bot, 3000, 3000, confirm_mid, "cancel_no")
            await confirm_cancel_no(_make_update(3000, 3000, callback_query=q_no), ctx)
            self.assertIn("pb", ctx.user_data)

            q_yes = _make_query(bot, 3000, 3000, confirm_mid, "cancel_yes")
            res = await confirm_cancel_yes(_make_update(3000, 3000, callback_query=q_yes), ctx)
            self.assertEqual(res, ConversationHandler.END)
            self.assertEqual(ctx.user_data, {})
            self.assertEqual(ws_db.count_posts(), 0)
            self.assertIn((3000, preview_mid), bot.deleted_messages)
            self.assertIn((3000, ctrl_mid), bot.deleted_messages)

        asyncio.run(run())

    def test_r6_post_manage_buttons_and_channel_update(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(1000)
            ws_db.add_target_channel(-100777, "Target Ch", "target_ch", 1000)
            post_id = ws_db.create_post("پست اول", "[]", "https://t.me/pics/9", 1000)

            msg = _make_message(bot, 1000, 1000, "📂 پست‌های ذخیره‌شده")
            state = await start_post_manage(_make_update(1000, 1000, message=msg), ctx)
            self.assertEqual(state, PM_LIST)
            mid = ctx.user_data["pm"]["msg_id"]

            q_det = _make_query(bot, 1000, 1000, mid, f"pm:detail:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_det), ctx)
            self.assertEqual(state, PM_DETAIL)

            # R6 + R7: Manage inline buttons inside saved post (type, color, row merge, edit, delete)
            q_btns = _make_query(bot, 1000, 1000, mid, f"pm:btns:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_btns), ctx)
            self.assertEqual(state, PM_DETAIL)

            q_badd = _make_query(bot, 1000, 1000, mid, f"pm:btn_add:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_badd), ctx)
            self.assertEqual(state, PM_DETAIL)

            q_btype = _make_query(bot, 1000, 1000, mid, f"pm:btn_type:{post_id}:url")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_btype), ctx)
            self.assertEqual(state, PM_ADD_BTN_TEXT)

            btxt = _make_message(bot, 1000, 1000, "-")
            state = await receive_pm_btn_text(_make_update(1000, 1000, message=btxt), ctx)
            self.assertEqual(state, PM_ADD_BTN_URL)

            burl = _make_message(bot, 1000, 1000, "https://t.me/target_ch/15")
            state = await receive_pm_btn_url(_make_update(1000, 1000, message=burl), ctx)
            self.assertEqual(state, PM_DETAIL)

            # Add second button (copy) + inspect color + merge into row 0 + edit text
            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:btn_add:{post_id}")),
                ctx,
            )
            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:btn_type:{post_id}:copy")),
                ctx,
            )
            await receive_pm_btn_text(
                _make_update(1000, 1000, message=_make_message(bot, 1000, 1000, "کد")),
                ctx,
            )
            await receive_pm_btn_url(
                _make_update(1000, 1000, message=_make_message(bot, 1000, 1000, "POPIC100")),
                ctx,
            )
            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_col:1:danger")),
                ctx,
            )
            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_row_up:1")),
                ctx,
            )
            state = await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_etxt:1")),
                ctx,
            )
            self.assertEqual(state, PM_BTN_EDIT_TEXT)
            await receive_pm_btn_edit_text(
                _make_update(1000, 1000, message=_make_message(bot, 1000, 1000, "کپی کد")),
                ctx,
            )
            state = await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_ctype:1:popup")),
                ctx,
            )
            self.assertEqual(state, PM_BTN_EDIT_VALUE)
            await receive_pm_btn_edit_value(
                _make_update(1000, 1000, message=_make_message(bot, 1000, 1000, "توضیح")),
                ctx,
            )

            saved_btns = deserialize_buttons(ws_db.get_post(post_id)["buttons_json"])
            self.assertEqual(len(saved_btns), 2)
            self.assertEqual(saved_btns[0]["url"], "https://t.me/target_ch/15")
            self.assertEqual(saved_btns[1]["row"], 0)
            self.assertEqual(saved_btns[1]["style"], "danger")
            self.assertEqual(saved_btns[1]["text"], "کپی کد")
            self.assertEqual(saved_btns[1]["type"], "popup")

            # Publish with 2 buttons on row 0 -> update channel -> delete both buttons -> delete post
            q_pub = _make_query(bot, 1000, 1000, mid, f"pm:send_ch:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_pub), ctx)
            self.assertEqual(ws_db.get_post(post_id)["status"], "published")
            ch_sent = [m for m in bot.sent_messages if m["chat_id"] == -100777]
            self.assertEqual(len(ch_sent[0]["reply_markup"].inline_keyboard[0]), 2)

            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_del:0")),
                ctx,
            )
            await handle_post_manage_callback(
                _make_update(1000, 1000, callback_query=_make_query(bot, 1000, 1000, mid, f"pm:{post_id}:btn_del:0")),
                ctx,
            )
            self.assertEqual(deserialize_buttons(ws_db.get_post(post_id)["buttons_json"]), [])

            q_et = _make_query(bot, 1000, 1000, mid, f"pm:edit_text:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_et), ctx)
            new_msg = _make_message(bot, 1000, 1000, "متن ویرایش‌شده")
            await receive_pm_edit_text(_make_update(1000, 1000, message=new_msg), ctx)

            q_upd = _make_query(bot, 1000, 1000, mid, f"pm:update_ch:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_upd), ctx)
            ch_edits = [m for m in bot.edited_messages if m["chat_id"] == -100777]
            self.assertEqual(ch_edits[0]["text"], "متن ویرایش‌شده")

            q_el = _make_query(bot, 1000, 1000, mid, f"pm:edit_link:{post_id}")
            await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_el), ctx)
            link_msg = _make_message(bot, 1000, 1000, "t.me/pics/99")
            await receive_pm_edit_link(_make_update(1000, 1000, message=link_msg), ctx)
            self.assertEqual(ws_db.get_post(post_id)["photo_url"], "https://t.me/pics/99")

            q_del = _make_query(bot, 1000, 1000, mid, f"pm:del_yes:{post_id}")
            state = await handle_post_manage_callback(_make_update(1000, 1000, callback_query=q_del), ctx)
            self.assertEqual(state, PM_LIST)
            self.assertIsNone(ws_db.get_post(post_id))

        asyncio.run(run())

    def test_r4_r5_public_start_role_selection_and_admin_guard(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)

            # Public user 5555 starts bot -> gets personal DB & owner menu
            s_msg = _make_message(bot, 5555, 5555, "/start")
            await start(_make_update(5555, 5555, message=s_msg), ctx)
            self.assertTrue(os.path.exists(os.path.join(config.WORKSPACES_DIR, "user_5555.db")))
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.OWNER_WELCOME)

            # Owner 1000 opens Settings and adds 2000 as admin
            owner_msg = _make_message(bot, 1000, 1000, "⚙️ تنظیمات")
            res = await open_settings(_make_update(1000, 1000, message=owner_msg), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertIn(BOT_VERSION, bot.sent_messages[-1]["text"])
            panel_mid = bot.sent_messages[-1]["message_id"]

            q_add_adm = _make_query(bot, 1000, 1000, panel_mid, "st:adm_add")
            res = await settings_callback(_make_update(1000, 1000, callback_query=q_add_adm), ctx)
            self.assertEqual(res, SETTINGS_ADMIN)
            adm_in = _make_message(bot, 1000, 1000, "2000")
            res = await receive_admin_input(_make_update(1000, 1000, message=adm_in), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertTrue(registry.is_workspace_admin(1000, 2000))

            # User 2000 starts bot -> prompted to choose Owner vs Admin of 1000
            u2_start = _make_message(bot, 2000, 2000, "/start")
            await start(_make_update(2000, 2000, message=u2_start), ctx)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.CHOOSE_ROLE_PROMPT)
            pick_mid = bot.sent_messages[-1]["message_id"]

            # User 2000 selects Admin of 1000
            q_pick = _make_query(bot, 2000, 2000, pick_mid, "ws_pick:1000")
            await workspace_pick_callback(_make_update(2000, 2000, callback_query=q_pick), ctx)
            self.assertEqual(registry.get_active_workspace_id(2000), 1000)

            # In admin mode, 2000 is blocked from Settings and Backup
            adm_st = _make_message(bot, 2000, 2000, "⚙️ تنظیمات")
            res = await open_settings(_make_update(2000, 2000, message=adm_st), ctx)
            self.assertEqual(res, ConversationHandler.END)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.OWNER_ONLY_FEATURE)

            adm_bk = _make_message(bot, 2000, 2000, "💾 بک‌آپ")
            await handle_backup(_make_update(2000, 2000, message=adm_bk), ctx)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.ENV_OWNER_ONLY_BACKUP)

            # In admin mode, 2000 creates a post -> saved in 1000's DB, NOT 2000's DB
            pb_s = _make_message(bot, 2000, 2000, "📝 ساخت پست جدید")
            await start_post_builder(_make_update(2000, 2000, message=pb_s), ctx)
            await receive_post_text(
                _make_update(2000, 2000, message=_make_message(bot, 2000, 2000, "پست ادمین")),
                ctx,
            )
            await handle_media_choice_callback(
                _make_update(
                    2000,
                    2000,
                    callback_query=_make_query(
                        bot, 2000, 2000, ctx.user_data["_pb_question_mid"], "pb:media:link"
                    ),
                ),
                ctx,
            )
            await receive_photo_link(
                _make_update(2000, 2000, message=_make_message(bot, 2000, 2000, "https://t.me/pics/7")),
                ctx,
            )
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            await handle_preview_callback(
                _make_update(2000, 2000, callback_query=_make_query(bot, 2000, 2000, ctrl_mid, "pb:done")),
                ctx,
            )
            self.assertEqual(get_workspace_db(1000).count_posts(), 1)
            self.assertEqual(get_workspace_db(2000).count_posts(), 0)

            # Switcher button works
            sw_msg = _make_message(bot, 2000, 2000, "🔄 تغییر فضا / نقش")
            await open_workspace_switcher(_make_update(2000, 2000, message=sw_msg), ctx)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.CHOOSE_ROLE_PROMPT)

            # Owner revokes 2000 -> next action falls back to owner mode with notice
            registry.remove_workspace_admin(1000, 2000)
            st_msg = _make_message(bot, 2000, 2000, "⚙️ تنظیمات")
            res = await open_settings(_make_update(2000, 2000, message=st_msg), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertTrue(
                any(m["text"] == Msg.ROLE_REVOKED_NOTICE for m in bot.sent_messages)
            )

        asyncio.run(run())

    def test_settings_channels_preview_and_r3_backup(self) -> None:
        async def run():
            app = create_application()
            bot = _FakeBot()
            ctx = _make_context(bot, application=app)
            ws_db = get_workspace_db(1000)

            owner_msg = _make_message(bot, 1000, 1000, "⚙️ تنظیمات")
            await open_settings(_make_update(1000, 1000, message=owner_msg), ctx)
            panel_mid = bot.sent_messages[-1]["message_id"]

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
            self.assertEqual(ws_db.count_target_channels(), 1)

            q_pos = _make_query(bot, 1000, 1000, panel_mid, "st:pv_pos")
            await settings_callback(_make_update(1000, 1000, callback_query=q_pos), ctx)
            self.assertFalse(ws_db.get_show_above_text())

            q_lbl = _make_query(bot, 1000, 1000, panel_mid, "st:btn_lbl")
            res = await settings_callback(_make_update(1000, 1000, callback_query=q_lbl), ctx)
            self.assertEqual(res, SETTINGS_BTN_LABEL)
            lbl_in = _make_message(bot, 1000, 1000, "📸 باز کردن عکس")
            res = await receive_button_label_input(_make_update(1000, 1000, message=lbl_in), ctx)
            self.assertEqual(res, SETTINGS_PANEL)
            self.assertEqual(ws_db.get_default_button_text(), "📸 باز کردن عکس")

            # Stats + panel_gate
            ok_msg = _make_message(bot, 1000, 1000, "📊 آمار")
            res = await show_stats(_make_update(1000, 1000, message=ok_msg), ctx)
            self.assertEqual(res, STATS_VIEW)
            stats_mid = bot.sent_messages[-1]["message_id"]
            self.assertIn("آمار کل ربات", bot.sent_messages[-1]["text"])

            app.bot_data["panel_convs"]["stats"]._conversations[(1000, 1000)] = STATS_VIEW
            gate_msg = _make_message(bot, 1000, 1000, "📂 پست‌های ذخیره‌شده")
            await panel_gate(_make_update(1000, 1000, message=gate_msg), ctx)
            self.assertNotIn((1000, 1000), app.bot_data["panel_convs"]["stats"]._conversations)

            q_close = _make_query(bot, 1000, 1000, stats_mid, "stats:close")
            res = await handle_stats_callback(_make_update(1000, 1000, callback_query=q_close), ctx)
            self.assertEqual(res, ConversationHandler.END)

            # R10: Non-env-owner (even owner of own workspace 5555) cannot use backup
            pub_bk = _make_message(bot, 5555, 5555, "💾 بک‌آپ")
            get_workspace_db(5555)
            await handle_backup(_make_update(5555, 5555, message=pub_bk), ctx)
            self.assertEqual(len(bot.sent_documents), 0)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.ENV_OWNER_ONLY_BACKUP)

            # R10: Env owner (1000) receives .zip with registry.db + workspaces/user_*.db (never .env)
            own_bk = _make_message(bot, 1000, 1000, "💾 بک‌آپ")
            await handle_backup(_make_update(1000, 1000, message=own_bk), ctx)
            self.assertEqual(len(bot.sent_documents), 1)
            self.assertTrue(bot.sent_documents[0]["filename"].endswith(".zip"))
            with zipfile.ZipFile(io.BytesIO(bot.sent_documents[0]["data"]), "r") as zf:
                names = set(zf.namelist())
            self.assertIn("registry.db", names)
            self.assertIn("workspaces/user_1000.db", names)
            self.assertIn("workspaces/user_5555.db", names)
            self.assertFalse(any(".env" in n for n in names))

        asyncio.run(run())

    def test_r9_media_modes_none_photo_and_1024_guard(self) -> None:
        async def run():
            bot = _FakeBot()
            ctx = _make_context(bot)
            ws_db = get_workspace_db(6000)
            ws_db.add_target_channel(-100888, "R9 Channel", "r9_ch", 6000)

            # 1) Plain text post (media_type='none') -> LinkPreviewOptions(is_disabled=True)
            await start_post_builder(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "📝 ساخت پست جدید")),
                ctx,
            )
            state = await receive_post_text(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "پست متنی بدون عکس https://t.me/a/1")),
                ctx,
            )
            self.assertEqual(state, POST_MEDIA_CHOICE)
            prompt_mid = ctx.user_data["_pb_question_mid"]
            state = await handle_media_choice_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, prompt_mid, "pb:media:none")),
                ctx,
            )
            self.assertEqual(state, POST_PREVIEW)
            preview_none = bot.sent_messages[-2]
            self.assertTrue(preview_none["link_preview_options"].is_disabled)
            self.assertEqual(len(bot.sent_photos), 0)

            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            await handle_preview_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, ctrl_mid, "pb:done")),
                ctx,
            )
            p_none = ws_db.get_post(1)
            self.assertEqual(p_none["media_type"], "none")

            # 2) Regular photo with text > 1024 -> warns and offers switch or text edit
            await start_post_builder(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "📝 ساخت پست جدید")),
                ctx,
            )
            state = await receive_post_text(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "ب" * 1200)),
                ctx,
            )
            self.assertEqual(state, POST_MEDIA_CHOICE)
            prompt_mid = ctx.user_data["_pb_question_mid"]

            state = await handle_media_choice_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, prompt_mid, "pb:media:photo")),
                ctx,
            )
            self.assertEqual(state, POST_MEDIA_CHOICE)
            self.assertIn("1200", bot.edited_messages[-1]["text"])

            # User clicks pb:edit_text_pre -> POST_EDIT_TEXT -> sends 500-char text -> returns to POST_MEDIA_CHOICE
            state = await handle_media_choice_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, prompt_mid, "pb:edit_text_pre")),
                ctx,
            )
            self.assertEqual(state, POST_EDIT_TEXT)
            state = await receive_edit_text(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "کپشن کوتاه و مناسب")),
                ctx,
            )
            self.assertEqual(state, POST_MEDIA_CHOICE)

            # Now choose regular photo -> POST_PHOTO_UPLOAD
            prompt_mid = ctx.user_data["_pb_question_mid"]
            state = await handle_media_choice_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, prompt_mid, "pb:media:photo")),
                ctx,
            )
            self.assertEqual(state, POST_PHOTO_UPLOAD)

            # Non-photo input warns and stays in POST_PHOTO_UPLOAD
            state = await receive_photo_upload(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "این عکس نیست")),
                ctx,
            )
            self.assertEqual(state, POST_PHOTO_UPLOAD)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.POST_PHOTO_REQUIRED)

            # Real photo -> POST_PREVIEW via send_photo
            photo_msg = _make_message(
                bot,
                6000,
                6000,
                "",
                photo=[SimpleNamespace(file_id="SMALL"), SimpleNamespace(file_id="BIG_FILE_ID")],
            )
            state = await receive_photo_upload(_make_update(6000, 6000, message=photo_msg), ctx)
            self.assertEqual(state, POST_PREVIEW)
            self.assertEqual(len(bot.sent_photos), 1)
            self.assertEqual(bot.sent_photos[0]["photo"], "BIG_FILE_ID")
            self.assertEqual(bot.sent_photos[0]["caption"], "کپشن کوتاه و مناسب")

            # Publish to channel -> saved with media_type='photo' and sent via send_photo
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            state = await handle_preview_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, ctrl_mid, "pb:send_ch")),
                ctx,
            )
            self.assertEqual(state, ConversationHandler.END)
            p_photo = ws_db.get_post(2)
            self.assertEqual(p_photo["media_type"], "photo")
            self.assertEqual(p_photo["photo_file_id"], "BIG_FILE_ID")
            self.assertEqual(len([p for p in bot.sent_photos if p["chat_id"] == -100888]), 1)

            # 3) Manage saved post: switch media photo -> new photo -> update channel via edit_message_media
            await start_post_manage(
                _make_update(6000, 6000, message=_make_message(bot, 6000, 6000, "📂 پست‌های ذخیره‌شده")),
                ctx,
            )
            pm_mid = ctx.user_data["pm"]["msg_id"]
            await handle_post_manage_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, pm_mid, f"pm:detail:{p_photo['id']}")),
                ctx,
            )
            state = await handle_post_manage_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, pm_mid, f"pm:{p_photo['id']}:media:photo")),
                ctx,
            )
            self.assertEqual(state, PM_EDIT_PHOTO)
            new_photo_msg = _make_message(
                bot,
                6000,
                6000,
                "",
                photo=[SimpleNamespace(file_id="NEW_PHOTO_ID")],
            )
            state = await receive_pm_edit_photo(_make_update(6000, 6000, message=new_photo_msg), ctx)
            self.assertEqual(state, PM_DETAIL)
            self.assertEqual(ws_db.get_post(p_photo["id"])["photo_file_id"], "NEW_PHOTO_ID")

            await handle_post_manage_callback(
                _make_update(6000, 6000, callback_query=_make_query(bot, 6000, 6000, pm_mid, f"pm:update_ch:{p_photo['id']}")),
                ctx,
            )
            self.assertEqual(len(bot.edited_media), 1)
            self.assertEqual(bot.edited_media[0]["media"].media, "NEW_PHOTO_ID")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
