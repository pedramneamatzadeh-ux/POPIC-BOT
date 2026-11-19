import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from bot.config import config
from bot.constants import (
    OLD_EDIT_BUTTON,
    OLD_EDIT_TEXT,
    OLD_PANEL,
    OLD_TARGET,
    PM_DETAIL,
    PM_LIST,
    PM_SCHED_DAY,
    PM_SCHED_PANEL,
    PM_SCHED_TARGET,
    PM_SCHED_TIME,
    PM_SCHED_YEAR,
    PM_SEARCH,
    POST_MEDIA_CHOICE,
    POST_PREVIEW,
    POST_SCHED_DAY,
    POST_SCHED_PANEL,
    POST_SCHED_TARGET,
    POST_SCHED_TIME,
    POST_SCHED_YEAR,
    POST_TEXT,
    POST_TITLE,
    SETTINGS_CHANNEL,
    SETTINGS_PANEL,
    STATS_MSG_INPUT,
    STATS_VIEW,
    Msg,
)
from bot.database import clear_workspace_cache, get_workspace_db, registry
from bot.handlers.base import start
from bot.handlers.display_button import handle_display_button_callback
from bot.handlers.old_post_edit import (
    handle_old_edit_callback,
    receive_old_edit_text,
    receive_old_post_target,
    start_old_post_edit,
)
from bot.handlers.post_builder import (
    handle_media_choice_callback,
    handle_pb_schedule_callback,
    handle_preview_callback,
    handle_skip_title_callback,
    receive_pb_sched_day,
    receive_pb_sched_time,
    receive_pb_sched_year,
    receive_post_text,
    receive_post_title,
    start_post_builder,
)
from bot.handlers.post_manage import (
    handle_post_manage_callback,
    receive_pm_edit_photo,
    receive_pm_search,
    start_post_manage,
)
from bot.handlers.settings import receive_channel_input, settings_callback
from bot.handlers.stats import (
    handle_stats_callback,
    receive_stats_user_message,
    show_stats,
)
from bot.keyboards import main_menu_for
from bot.services.scheduler import (
    days_in_gregorian_month,
    parse_time_input,
    process_due_scheduled_posts,
    validate_future_tehran_dt,
)
from bot.utils import (
    extract_forwarded_message_target,
    parse_telegram_message_link,
    resolve_chat_target,
)


class _V050FakeBot:
    def __init__(self, bot_id: int = 888) -> None:
        self.id = bot_id
        self.username = "popic_v05_bot"
        self.sent_messages = []
        self.sent_photos = []
        self.edited_messages = []
        self.edited_media = []
        self.edited_markups = []
        self.deleted_messages = []
        self.chats = {}
        self.chat_members = {}
        self.fail_edit_with = None
        self._next_mid = 200

    def _alloc_mid(self) -> int:
        self._next_mid += 1
        return self._next_mid

    async def send_message(self, chat_id, text, **kwargs):
        mid = self._alloc_mid()
        rec = {"chat_id": chat_id, "message_id": mid, "text": text, **kwargs}
        self.sent_messages.append(rec)
        return SimpleNamespace(chat_id=chat_id, message_id=mid, text=text)

    async def send_photo(self, chat_id, photo, **kwargs):
        mid = self._alloc_mid()
        rec = {"chat_id": chat_id, "message_id": mid, "photo": photo, **kwargs}
        self.sent_photos.append(rec)
        return SimpleNamespace(chat_id=chat_id, message_id=mid, caption=kwargs.get("caption", ""))

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        if self.fail_edit_with is not None:
            err = self.fail_edit_with
            self.fail_edit_with = None
            raise err
        rec = {"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs}
        self.edited_messages.append(rec)
        return SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text)

    async def edit_message_media(self, chat_id, message_id, media, **kwargs):
        rec = {"chat_id": chat_id, "message_id": message_id, "media": media, **kwargs}
        self.edited_media.append(rec)
        return SimpleNamespace(chat_id=chat_id, message_id=message_id)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append(
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        )
        return SimpleNamespace(chat_id=chat_id, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted_messages.append((chat_id, message_id))
        return True

    async def get_chat(self, target):
        if target in self.chats:
            return self.chats[target]
        raise BadRequest("Chat not found")

    async def get_chat_member(self, chat_id, user_id):
        return self.chat_members[(chat_id, user_id)]


def _msg(bot: _V050FakeBot, chat_id: int, user_id: int, text: str = "", **extra):
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
        caption=extra.get("caption"),
        entities=extra.get("entities", ()),
        caption_entities=extra.get("caption_entities", ()),
        photo=extra.get("photo", ()),
        link_preview_options=extra.get("link_preview_options"),
        reply_markup=extra.get("reply_markup"),
        forward_origin=extra.get("forward_origin"),
        forward_from=extra.get("forward_from"),
        forward_from_chat=extra.get("forward_from_chat"),
        forward_from_message_id=extra.get("forward_from_message_id"),
        reply_text=reply_text,
        delete=delete,
    )


def _query(bot: _V050FakeBot, chat_id: int, user_id: int, message_id: int, data: str):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(text, **kwargs):
        return await bot.edit_message_text(chat_id, message_id, text, **kwargs)

    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username=f"u{user_id}", first_name="User", last_name=""),
        message=SimpleNamespace(chat_id=chat_id, message_id=message_id),
        answer=answer,
        edit_message_text=edit_message_text,
        answers=answers,
    )


def _upd(user_id: int, chat_id: int, message=None, callback_query=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(
            id=user_id, username=f"u{user_id}", first_name="User", last_name=str(user_id)
        ),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
        callback_query=callback_query,
    )


def _ctx(bot: _V050FakeBot):
    return SimpleNamespace(
        bot=bot,
        user_data={},
        chat_data={},
        bot_data={},
        application=None,
    )


class V050Tests(unittest.TestCase):
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

    def test_r11_saved_posts_preview_multi_channel_and_media_type_change(self) -> None:
        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            ws_db = get_workspace_db(1000)
            ch1 = ws_db.add_target_channel(-100111, "Channel One", "ch_one", 1000, chat_type="channel")
            ch2 = ws_db.add_target_channel(-100222, "Group Two", "grp_two", 1000, chat_type="supergroup")

            post_id = ws_db.create_post(
                post_text="متن اولیه پست",
                entities_json="[]",
                photo_url="",
                created_by=1000,
                media_type="none",
                title="پست تستی R11",
            )

            # Open saved posts -> open detail -> renders real preview ABOVE detail panel
            await start_post_manage(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "📂 پست‌های ذخیره‌شده")),
                ctx,
            )
            pm_mid = ctx.user_data["pm"]["msg_id"]
            state = await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:detail:{post_id}")),
                ctx,
            )
            self.assertEqual(state, PM_DETAIL)
            pv_mid = ctx.user_data["pm"]["preview_msg_id"]
            self.assertIsNotNone(pv_mid)
            self.assertEqual(bot.sent_messages[-2]["text"], "متن اولیه پست")

            # Publish to ch1 and ch2 -> both recorded in post_deliveries
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:send_ch:{post_id}")),
                ctx,
            )
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:pub:{post_id}:{ch1}")),
                ctx,
            )
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:send_ch:{post_id}")),
                ctx,
            )
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:pub:{post_id}:{ch2}")),
                ctx,
            )
            deliveries = ws_db.get_published_targets(post_id)
            self.assertEqual(len(deliveries), 2)

            # Switch media from none -> photo -> upload photo
            state = await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:{post_id}:media:photo")),
                ctx,
            )
            await receive_pm_edit_photo(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "", photo=[SimpleNamespace(file_id="PHOTO_V05")])),
                ctx,
            )
            self.assertEqual(ws_db.get_post(post_id)["media_type"], "photo")
            # Live preview recreated as photo
            self.assertTrue(any(p["photo"] == "PHOTO_V05" for p in bot.sent_photos))

            # Update channel when published in >1 destination -> prompts which channel/all
            pm_mid = ctx.user_data["pm"]["msg_id"]
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:update_ch:{post_id}")),
                ctx,
            )
            self.assertIn("در چند مقصد منتشر شده", bot.edited_messages[-1]["text"])

            # Choosing update all -> detects media_type mismatch (none -> photo) -> asks confirm_delete_resend!
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:upd_all:{post_id}")),
                ctx,
            )
            self.assertEqual(bot.edited_messages[-1]["text"], Msg.MEDIA_TYPE_CHANGE_CONFIRM)

            # Confirm delete & resend all -> deletes both old channel messages and sends new photos
            old_mids = {(d["chat_id"], d["message_id"]) for d in deliveries}
            await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, f"pm:upd_replace_all:{post_id}")),
                ctx,
            )
            for pair in old_mids:
                self.assertIn(pair, bot.deleted_messages)
            new_deliveries = ws_db.get_published_targets(post_id)
            self.assertEqual({d["media_type"] for d in new_deliveries}, {"photo"})

            # Close post manage -> cleans up live preview
            pv_after = ctx.user_data["pm"]["preview_msg_id"]
            state = await handle_post_manage_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, pm_mid, "pm:close")),
                ctx,
            )
            self.assertEqual(state, ConversationHandler.END)
            self.assertIn((1000, pv_after), bot.deleted_messages)

        asyncio.run(run())

    def test_r12_groups_and_target_resolution(self) -> None:
        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            ws_db = get_workspace_db(1000)

            self.assertEqual(resolve_chat_target("https://t.me/my_supergroup"), "@my_supergroup")
            self.assertEqual(resolve_chat_target("-100987654321"), -100987654321)
            self.assertEqual(resolve_chat_target("-444555"), -444555)
            fwd_sender = SimpleNamespace(
                forward_origin=SimpleNamespace(sender_chat=SimpleNamespace(id=-100333)),
                forward_from_chat=None,
            )
            self.assertEqual(resolve_chat_target("", fwd_sender), -100333)

            bot.chats[-444555] = SimpleNamespace(
                id=-444555, type="group", title="گروه تست", username=""
            )
            bot.chat_members[(-444555, bot.id)] = SimpleNamespace(status="administrator")

            ctx.user_data["st_msg_id"] = 10
            state = await settings_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, 10, "st:ch_add")),
                ctx,
            )
            self.assertEqual(state, SETTINGS_CHANNEL)
            state = await receive_channel_input(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "-444555")),
                ctx,
            )
            self.assertEqual(state, SETTINGS_PANEL)
            channels = ws_db.get_target_channels()
            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0]["chat_type"], "group")

        asyncio.run(run())

    def test_r13_title_search_scheduler_validation_and_daily_tabs(self) -> None:
        # Gregorian leap year & Tehran past-time validation
        self.assertEqual(days_in_gregorian_month(2024, 2), 29)
        self.assertEqual(days_in_gregorian_month(2025, 2), 28)
        self.assertEqual(parse_time_input("۱۴:۳۰"), (14, 30))
        self.assertIsNone(parse_time_input("25:99"))

        now_utc = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)  # 13:30 Tehran
        utc_past, err_past = validate_future_tehran_dt(2026, 11, 18, 13, 0, now_utc=now_utc)
        self.assertIsNone(utc_past)
        self.assertEqual(err_past, Msg.SCHED_PAST_ERROR)
        utc_ok, err_ok = validate_future_tehran_dt(2026, 11, 18, 14, 30, now_utc=now_utc)
        self.assertIsNotNone(utc_ok)
        self.assertIsNone(err_ok)

        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            ws_db = get_workspace_db(7000)
            ws_db.add_target_channel(-100701, "Sched Ch 1", "sc1", 7000)
            ws_db.add_target_channel(-100702, "Sched Ch 2", "sc2", 7000)

            # 1) Create post with title -> skip media -> schedule in future
            state = await start_post_builder(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "📝 ساخت پست جدید")),
                ctx,
            )
            self.assertEqual(state, POST_TITLE)
            state = await receive_post_title(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "کمپین یلدا")),
                ctx,
            )
            self.assertEqual(state, POST_TEXT)
            state = await receive_post_text(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "تخفیف ویژه شب یلدا")),
                ctx,
            )
            self.assertEqual(state, POST_MEDIA_CHOICE)
            q_mid = ctx.user_data["_pb_question_mid"]
            await handle_media_choice_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, q_mid, "pb:media:none")),
                ctx,
            )
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]
            state = await handle_preview_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_PANEL)

            # Set year 2099, month 12, day 25, time 16:45 -> confirm -> select both channels -> save
            state = await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_year")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_YEAR)
            state = await receive_pb_sched_year(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "۲۰۹۹")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_PANEL)

            await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_m:12")),
                ctx,
            )
            state = await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_day")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_DAY)
            await receive_pb_sched_day(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "25")),
                ctx,
            )
            state = await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_time")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_TIME)
            await receive_pb_sched_time(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "16:45")),
                ctx,
            )
            state = await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_confirm")),
                ctx,
            )
            self.assertEqual(state, POST_SCHED_TARGET)
            await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_tg:-100702")),
                ctx,
            )
            state = await handle_pb_schedule_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, ctrl_mid, "pb:sched_save")),
                ctx,
            )
            self.assertEqual(state, POST_PREVIEW)
            pid1 = ctx.user_data["pb"]["post_id"]
            self.assertEqual(ws_db.get_post(pid1)["schedule_status"], "pending")
            self.assertEqual(ws_db.get_post(pid1)["title"], "کمپین یلدا")

            # Also verify skip-title callback path
            ctx2 = _ctx(bot)
            await start_post_builder(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "📝 ساخت پست جدید")),
                ctx2,
            )
            q_skip_mid = ctx2.user_data["_pb_question_mid"]
            state2 = await handle_skip_title_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, q_skip_mid, "pb:skip_title")),
                ctx2,
            )
            self.assertEqual(state2, POST_TEXT)

            # 2) Search in saved posts + daily scheduled tab
            pid2 = ws_db.create_post("پست دوم زمان‌بندی", "[]", "", 7000, media_type="none", title="نوروز")
            past_due = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
            ws_db.set_post_schedule(pid2, past_due, [-100701])

            await start_post_manage(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "📂 پست‌های ذخیره‌شده")),
                ctx,
            )
            pm_mid = ctx.user_data["pm"]["msg_id"]
            state = await handle_post_manage_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, pm_mid, "pm:search")),
                ctx,
            )
            self.assertEqual(state, PM_SEARCH)
            state = await receive_pm_search(
                _upd(7000, 7000, message=_msg(bot, 7000, 7000, "یلدا")),
                ctx,
            )
            self.assertEqual(state, PM_LIST)
            self.assertIn("یلدا", bot.edited_messages[-1]["text"])

            # Clear search, then scheduled tab shows 1 day per page across 2 distinct days
            await handle_post_manage_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, pm_mid, "pm:search_clear")),
                ctx,
            )
            await handle_post_manage_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, pm_mid, "pm:tab:sched")),
                ctx,
            )
            self.assertIn("صفحه 1 از 2", bot.edited_messages[-1]["text"])
            await handle_post_manage_callback(
                _upd(7000, 7000, callback_query=_query(bot, 7000, 7000, pm_mid, "pm:page:1")),
                ctx,
            )
            self.assertIn("صفحه 2 از 2", bot.edited_messages[-1]["text"])

            # 3) Background scheduler processes due post pid2 ONCE (no duplicate on second tick!)
            before_sent = len([m for m in bot.sent_messages if m["chat_id"] == -100701])
            processed = await process_due_scheduled_posts(bot)
            self.assertEqual(processed, 1)
            processed_again = await process_due_scheduled_posts(bot)
            self.assertEqual(processed_again, 0)
            after_sent = len([m for m in bot.sent_messages if m["chat_id"] == -100701])
            self.assertEqual(after_sent - before_sent, 1)
            self.assertEqual(ws_db.get_post(pid2)["schedule_status"], "sent")
            self.assertEqual(ws_db.get_post(pid2)["status"], "published")

        asyncio.run(run())

    def test_r14_old_post_edit_forward_link_and_replace(self) -> None:
        self.assertEqual(parse_telegram_message_link("https://t.me/my_ch/45"), ("@my_ch", 45))
        self.assertEqual(parse_telegram_message_link("https://t.me/c/123456789/99"), (-100123456789, 99))
        fwd = SimpleNamespace(
            forward_origin=SimpleNamespace(chat=SimpleNamespace(id=-100500), message_id=77)
        )
        self.assertEqual(extract_forwarded_message_target(fwd), (-100500, 77))

        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            bot.chats[-100500] = SimpleNamespace(
                id=-100500, type="channel", title="Old Ch", username="old_ch"
            )
            bot.chat_members[(-100500, bot.id)] = SimpleNamespace(status="administrator")

            # Main menu shows OLD_EDIT_BUTTON
            menu_texts = [b.text for row in main_menu_for(1000).keyboard for b in row]
            self.assertIn(OLD_EDIT_BUTTON, menu_texts)

            state = await start_old_post_edit(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, OLD_EDIT_BUTTON)),
                ctx,
            )
            self.assertEqual(state, OLD_TARGET)

            # Forward a photo post sent by this bot (including a url button)
            fwd_photo = _msg(
                bot,
                1000,
                1000,
                "",
                caption="کپشن قدیمی",
                photo=[SimpleNamespace(file_id="OLD_PHOTO")],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("سایت", url="https://example.com")]]),
                forward_origin=SimpleNamespace(
                    chat=SimpleNamespace(id=-100500),
                    message_id=77,
                ),
            )
            state = await receive_old_post_target(_upd(1000, 1000, message=fwd_photo), ctx)
            self.assertEqual(state, OLD_PANEL)
            panel_mid = ctx.user_data["old_edit"]["panel_msg_id"]

            # Edit text & remove photo (switches photo -> none -> requires delete + resend confirmation)
            state = await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:edit_text")),
                ctx,
            )
            self.assertEqual(state, OLD_EDIT_TEXT)
            state = await receive_old_edit_text(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "متن اصلاح‌شده پست قدیمی")),
                ctx,
            )
            self.assertEqual(state, OLD_PANEL)

            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:media:none")),
                ctx,
            )
            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:apply")),
                ctx,
            )
            self.assertEqual(bot.edited_messages[-1]["text"], Msg.MEDIA_TYPE_CHANGE_CONFIRM)

            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:apply_replace")),
                ctx,
            )
            self.assertIn((-100500, 77), bot.deleted_messages)
            sent_to_ch = [m for m in bot.sent_messages if m["chat_id"] == -100500]
            self.assertEqual(len(sent_to_ch), 1)
            self.assertEqual(sent_to_ch[0]["text"], "متن اصلاح‌شده پست قدیمی")

            # Also verify old-popup callback handler works
            ws_db = get_workspace_db(1000)
            pid = ws_db.save_old_popup("متن پاپ‌آپ پست قدیمی")
            q_pop = _query(bot, -100500, 9999, 80, f"pb_disp:old:1000:{pid}")
            await handle_display_button_callback(_upd(9999, -100500, callback_query=q_pop), ctx)
            self.assertEqual(q_pop.answers[-1]["text"], "متن پاپ‌آپ پست قدیمی")
            self.assertTrue(q_pop.answers[-1]["show_alert"])

            # Verify bot explains nicely if Telegram says message can't be edited (e.g., sent by human admin)
            bot.chats["@old_ch"] = bot.chats[-100500]
            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:restart")),
                ctx,
            )
            await receive_old_post_target(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "https://t.me/old_ch/88")),
                ctx,
            )
            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:edit_text")),
                ctx,
            )
            await receive_old_edit_text(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "تلاش ویرایش")),
                ctx,
            )
            bot.fail_edit_with = BadRequest("Message can't be edited")
            await handle_old_edit_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, panel_mid, "old:apply")),
                ctx,
            )
            self.assertIn("توسط خود همین ربات ارسال شده باشند", bot.edited_messages[-1]["text"])

        asyncio.run(run())

    def test_r15_owner_user_management_ban_and_direct_message(self) -> None:
        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            registry.save_or_update_user(1000, "owner", "Main", "Owner")
            registry.save_or_update_user(2222, "ali_user", "Ali", "Rezaei")

            # Non-env-owner does NOT see user management button or access callbacks
            await show_stats(
                _upd(2222, 2222, message=_msg(bot, 2222, 2222, "📊 آمار")),
                ctx,
            )
            u_stats_mid = bot.sent_messages[-1]["message_id"]
            u_cbs = [
                b.callback_data
                for row in bot.sent_messages[-1]["reply_markup"].inline_keyboard
                for b in row
            ]
            self.assertNotIn("stats:users:0", u_cbs)
            q_denied = _query(bot, 2222, 2222, u_stats_mid, "stats:users:0")
            await handle_stats_callback(_upd(2222, 2222, callback_query=q_denied), ctx)
            self.assertEqual(q_denied.answers[-1]["text"], Msg.ACCESS_DENIED)

            # Env owner (1000) opens stats -> opens users list -> inspects 2222 -> bans with confirm
            await show_stats(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "📊 آمار")),
                ctx,
            )
            o_mid = bot.sent_messages[-1]["message_id"]
            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:users:0")),
                ctx,
            )
            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:user:2222")),
                ctx,
            )
            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:ban_ask:2222")),
                ctx,
            )
            self.assertIn("بن شود", bot.edited_messages[-1]["text"])
            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:ban_yes:2222")),
                ctx,
            )
            self.assertTrue(registry.is_user_banned(2222))

            # Banned user 2222 is rejected everywhere (start + workspace_access)
            await start(_upd(2222, 2222, message=_msg(bot, 2222, 2222, "/start")), ctx)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.USER_BANNED)
            res = await start_post_builder(
                _upd(2222, 2222, message=_msg(bot, 2222, 2222, "📝 ساخت پست جدید")),
                ctx,
            )
            self.assertEqual(res, ConversationHandler.END)
            self.assertEqual(bot.sent_messages[-1]["text"], Msg.USER_BANNED)

            # Env owner sends direct message to 2222 with confirmation
            state = await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:msg_ask:2222")),
                ctx,
            )
            self.assertEqual(state, STATS_MSG_INPUT)
            state = await receive_stats_user_message(
                _upd(1000, 1000, message=_msg(bot, 1000, 1000, "سلام، پیام تست مدیریت")),
                ctx,
            )
            self.assertEqual(state, STATS_VIEW)
            self.assertIn("مطمئنی این پیام", bot.edited_messages[-1]["text"])

            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:msg_send:2222")),
                ctx,
            )
            dm = [m for m in bot.sent_messages if m["chat_id"] == 2222 and "سلام، پیام تست مدیریت" in m["text"]]
            self.assertEqual(len(dm), 1)

            # Unban 2222 -> allowed again
            await handle_stats_callback(
                _upd(1000, 1000, callback_query=_query(bot, 1000, 1000, o_mid, "stats:ban_yes:2222")),
                ctx,
            )
            self.assertFalse(registry.is_user_banned(2222))

        asyncio.run(run())

    def test_r16_target_picker_button_label_and_schedule_warning(self) -> None:
        from bot.handlers.post_builder.common import build_control_summary
        from bot.keyboards import old_post_edit_panel_keyboard

        old_texts = [
            b.text
            for row in old_post_edit_panel_keyboard(buttons_count=0).inline_keyboard
            for b in row
        ]
        self.assertIn("🔘 دکمه شیشه‌ای (اختیاری)", old_texts)

        summary = build_control_summary(
            {
                "text": "پست تست",
                "media_type": "none",
                "show_above_text": True,
                "prefer_large_media": True,
                "buttons": [],
            }
        )
        self.assertIn("همون لحظه", summary)
        self.assertIn("انتخاب مقصد", summary)
        self.assertIn("ذخیره و منوی اصلی", summary)

        async def run():
            bot = _V050FakeBot()
            ctx = _ctx(bot)
            ws_db = get_workspace_db(7100)
            ws_db.add_target_channel(-100801, "Ch 801", "ch801", 7100)
            ws_db.add_target_channel(-100802, "Grp 802", "grp802", 7100, chat_type="supergroup")

            await start_post_builder(
                _upd(7100, 7100, message=_msg(bot, 7100, 7100, "📝 ساخت پست جدید")),
                ctx,
            )
            await receive_post_title(
                _upd(7100, 7100, message=_msg(bot, 7100, 7100, "پست مقصد")),
                ctx,
            )
            await receive_post_text(
                _upd(7100, 7100, message=_msg(bot, 7100, 7100, "متن زمان‌بندی")),
                ctx,
            )
            q_mid = ctx.user_data["_pb_question_mid"]
            await handle_media_choice_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, q_mid, "pb:media:none")),
                ctx,
            )
            ctrl_mid = ctx.user_data["pb"]["ctrl_msg_id"]

            # Pick targets directly from preview control panel without immediate publish
            await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:targets")),
                ctx,
            )
            await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:tg_toggle:-100801")),
                ctx,
            )
            await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:tg_toggle:-100802")),
                ctx,
            )
            await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:tg_done")),
                ctx,
            )
            self.assertEqual(set(ctx.user_data["pb"]["target_chat_ids"]), {-100801, -100802})
            self.assertIn("Ch 801", bot.edited_messages[-1]["text"])
            # Nothing sent immediately to channels!
            self.assertEqual(
                [m for m in bot.sent_messages if m["chat_id"] in (-100801, -100802)],
                [],
            )

            # Schedule for 2099 and then click pb:done -> saved with both targets, not sent yet
            await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:sched")),
                ctx,
            )
            await handle_pb_schedule_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:sched_year")),
                ctx,
            )
            await receive_pb_sched_year(
                _upd(7100, 7100, message=_msg(bot, 7100, 7100, "2099")),
                ctx,
            )
            await handle_pb_schedule_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:sched_confirm")),
                ctx,
            )
            await handle_pb_schedule_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:sched_save")),
                ctx,
            )
            state = await handle_preview_callback(
                _upd(7100, 7100, callback_query=_query(bot, 7100, 7100, ctrl_mid, "pb:done")),
                ctx,
            )
            self.assertEqual(state, ConversationHandler.END)
            saved = ws_db.get_post(1)
            self.assertEqual(saved["schedule_status"], "pending")
            self.assertIn("-100801", saved["scheduled_chat_ids"])
            self.assertIn("-100802", saved["scheduled_chat_ids"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
