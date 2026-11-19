import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram import InlineKeyboardMarkup

from bot.config import config
from bot.constants import (
    BOT_VERSION,
    DEFAULT_LIKE_TEXT,
    PM_ADD_BTN_TEXT,
    PM_DETAIL,
    PM_LIST,
    POST_ADD_BTN_TEXT,
    POST_PREVIEW,
)
from bot.database import clear_workspace_cache, get_workspace_db, registry
from bot.handlers.display_button import handle_react_button_callback
from bot.handlers.old_post_edit import handle_old_edit_callback, receive_old_btn_text
from bot.handlers.post_builder.flow import handle_preview_callback, receive_btn_text
from bot.handlers.post_manage import (
    _list_text,
    handle_post_manage_callback,
    receive_pm_btn_text,
)
from bot.keyboards import (
    post_button_type_keyboard,
    post_manage_detail_keyboard,
    post_preview_controls_keyboard,
)
from bot.services.publisher import build_buttons_markup, normalize_buttons


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages = []
        self.edited_messages = []
        self.edited_markups = []
        self._next_mid = 900

    async def send_message(self, chat_id, text, **kwargs):
        self._next_mid += 1
        rec = SimpleNamespace(
            chat_id=chat_id,
            message_id=self._next_mid,
            text=text,
            caption=None,
            entities=kwargs.get("entities") or [],
            caption_entities=[],
            reply_markup=kwargs.get("reply_markup"),
            link_preview_options=kwargs.get("link_preview_options"),
            photo=None,
            chat=SimpleNamespace(id=chat_id, type="channel", title="Test"),
        )
        self.sent_messages.append(rec)
        return rec

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edited_messages.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs}
        )
        return SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text)

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append(
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        )
        return True

    async def delete_message(self, chat_id, message_id):
        return True


class _FakeQuery:
    def __init__(self, data: str, chat_id: int = 1000, message_id: int = 500) -> None:
        self.data = data
        self.message = SimpleNamespace(
            chat_id=chat_id,
            message_id=message_id,
            chat=SimpleNamespace(id=chat_id, type="private"),
        )
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kwargs):
        self.message.text = text
        self.message.reply_markup = kwargs.get("reply_markup")
        return self.message


def _user(uid: int = 1000):
    return SimpleNamespace(
        id=uid,
        username=f"u{uid}",
        first_name="Test",
        last_name="User",
        is_bot=False,
    )


def _make_ctx(bot, user_data=None):
    return SimpleNamespace(
        bot=bot,
        user_data={} if user_data is None else user_data,
        chat_data={},
        application=SimpleNamespace(bot_data={}),
    )


class V060Tests(unittest.IsolatedAsyncioTestCase):
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

        self.ws_id = 1000
        self.ws_db = get_workspace_db(self.ws_id)
        self.ws_db.add_target_channel(-100111, "کانال اول", "ch1", "channel")
        self.ws_db.add_target_channel(-100222, "گروه دوم", None, "supergroup")

    def tearDown(self) -> None:
        config.BOT_TOKEN = self._orig_token
        config.OWNER_ID = self._orig_owner
        config.DATA_DIR = self._orig_data
        config.REGISTRY_DB_PATH = self._orig_reg
        config.WORKSPACES_DIR = self._orig_ws
        registry.path = self._orig_reg_path
        clear_workspace_cache()
        self._tmp.cleanup()

    async def test_saved_post_detail_targets_and_save_back_and_wider_list_text(self) -> None:
        self.assertEqual(BOT_VERSION, "0.6.0")

        # 1) Green target picker in new post preview & saved post detail + save_back
        pv_kb = post_preview_controls_keyboard(True, True, 0)
        self.assertEqual(pv_kb.inline_keyboard[0][1].style, "success")
        self.assertEqual(pv_kb.inline_keyboard[0][1].text, "🎯 انتخاب مقصد")

        pm_kb = post_manage_detail_keyboard({"id": 5}, buttons_count=1)
        self.assertEqual(pm_kb.inline_keyboard[0][1].style, "success")
        self.assertEqual(pm_kb.inline_keyboard[0][1].callback_data, "pm:targets:5")
        self.assertEqual(pm_kb.inline_keyboard[-2][0].style, "success")
        self.assertEqual(pm_kb.inline_keyboard[-2][0].callback_data, "pm:save_back:5")

        # 2) Wider/longer list text for balanced visual width
        txt = _list_text(
            tab="all",
            total=7,
            page=0,
            pages=2,
            all_count=7,
            sched_count=2,
        )
        self.assertIn("مدیریت و آرشیو پست‌های ذخیره‌شده", txt)
        self.assertIn("کل پست‌ها", txt)
        self.assertGreaterEqual(max(len(line) for line in txt.splitlines()), 45)

        # 3) Selecting targets in saved post detail and saving back
        post_id = self.ws_db.create_post(
            post_text="متن تست پست",
            entities_json="[]",
            photo_url="",
            media_type="none",
            title="پست اول",
            created_by=1000,
        )
        bot = _FakeBot()
        ctx = _make_ctx(bot, {"pm": {"page": 0, "tab": "all", "search": "", "post_id": post_id}})

        q_open = _FakeQuery(f"pm:targets:{post_id}")
        st = await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_open, effective_user=_user(1000)),
            ctx,
        )
        self.assertEqual(st, PM_DETAIL)
        self.assertEqual(ctx.user_data["pm"]["pending_targets"], [])

        q_tog = _FakeQuery("pm:tg_toggle:-100222")
        await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_tog, effective_user=_user(1000)),
            ctx,
        )
        self.assertEqual(ctx.user_data["pm"]["pending_targets"], [-100222])

        q_done = _FakeQuery("pm:tg_done")
        await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_done, effective_user=_user(1000)),
            ctx,
        )
        saved = self.ws_db.get_post(post_id)
        self.assertEqual(json.loads(saved["scheduled_chat_ids"]), [-100222])

        # Sending to channel uses pre-selected target immediately
        q_send = _FakeQuery(f"pm:send_ch:{post_id}")
        await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_send, effective_user=_user(1000)),
            ctx,
        )
        self.assertEqual(len(bot.sent_messages), 1)
        self.assertEqual(bot.sent_messages[0].chat_id, -100222)

        # Save & back returns to PM_LIST
        q_sb = _FakeQuery(f"pm:save_back:{post_id}")
        st_sb = await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_sb, effective_user=_user(1000)),
            ctx,
        )
        self.assertEqual(st_sb, PM_LIST)

    async def test_like_and_vote_buttons_creation_and_live_voting(self) -> None:
        # 1) Create a like button + two vote buttons via post builder
        bot = _FakeBot()
        pb = {
            "title": "نظرسنجی",
            "text": "نظرتان چیست؟",
            "entities_json": "[]",
            "media_type": "none",
            "photo_url": "",
            "photo_file_id": "",
            "show_above_text": True,
            "prefer_large_media": True,
            "buttons": [],
            "target_chat_ids": [],
            "target_names": [],
        }
        ctx = _make_ctx(bot, {"pb": pb})

        for btype, label in [("like", "🔥 عالی بود"), ("vote", "👍 موافقم"), ("vote", "👎 مخالفم")]:
            q_type = _FakeQuery(f"pb:btn_type:{btype}")
            st = await handle_preview_callback(
                SimpleNamespace(callback_query=q_type, effective_user=_user(1000)),
                ctx,
            )
            self.assertEqual(st, POST_ADD_BTN_TEXT)

            msg = SimpleNamespace(
                text=label,
                chat_id=1000,
                message_id=777,
                delete=lambda: _noop(),
                reply_text=lambda *a, **kw: _noop(),
            )
            st2 = await receive_btn_text(
                SimpleNamespace(message=msg, effective_user=_user(1000)),
                ctx,
            )
            self.assertEqual(st2, POST_PREVIEW)

        btns = normalize_buttons(pb["buttons"])
        self.assertEqual([b["type"] for b in btns], ["like", "vote", "vote"])
        self.assertTrue(all(b.get("btn_id") for b in btns))
        like_id = btns[0]["btn_id"]
        v1_id = btns[1]["btn_id"]
        v2_id = btns[2]["btn_id"]

        # Save post & verify initial markup shows (0)
        import json as _json

        post_id = self.ws_db.create_post(
            post_text=pb["text"],
            entities_json="[]",
            photo_url="",
            media_type="none",
            buttons_json=_json.dumps(btns, ensure_ascii=False),
            created_by=1000,
        )
        markup = build_buttons_markup(btns, workspace_id=self.ws_id, post_id=post_id)
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertEqual(markup.inline_keyboard[0][0].text, "🔥 عالی بود (0)")
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data,
            f"pb_react:{self.ws_id}:{post_id}:{like_id}",
        )

        # 2) User 2001 taps like -> +1, taps again -> -1 (0); User 2002 taps -> +1
        q_u1_like = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{like_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_u1_like, effective_user=_user(2001)),
            _make_ctx(bot),
        )
        self.assertEqual(self.ws_db.get_post_vote_counts(post_id).get(like_id), 1)
        self.assertEqual(bot.edited_markups[-1]["reply_markup"].inline_keyboard[0][0].text, "🔥 عالی بود (1)")

        q_u1_unlike = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{like_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_u1_unlike, effective_user=_user(2001)),
            _make_ctx(bot),
        )
        self.assertEqual(self.ws_db.get_post_vote_counts(post_id).get(like_id, 0), 0)
        self.assertEqual(bot.edited_markups[-1]["reply_markup"].inline_keyboard[0][0].text, "🔥 عالی بود (0)")

        q_u2_like = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{like_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_u2_like, effective_user=_user(2002)),
            _make_ctx(bot),
        )
        self.assertEqual(self.ws_db.get_post_vote_counts(post_id).get(like_id), 1)

        # 3) Single-choice poll (vote): user 2001 votes v1 -> switches to v2 -> removes v2
        q_v1 = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{v1_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_v1, effective_user=_user(2001)),
            _make_ctx(bot),
        )
        counts = self.ws_db.get_post_vote_counts(post_id)
        self.assertEqual(counts.get(v1_id), 1)
        self.assertEqual(counts.get(v2_id, 0), 0)

        q_v2 = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{v2_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_v2, effective_user=_user(2001)),
            _make_ctx(bot),
        )
        counts = self.ws_db.get_post_vote_counts(post_id)
        self.assertEqual(counts.get(v1_id, 0), 0)
        self.assertEqual(counts.get(v2_id), 1)
        self.assertEqual(counts.get(like_id), 1)  # like remains untouched!

        q_v2_off = _FakeQuery(f"pb_react:{self.ws_id}:{post_id}:{v2_id}", chat_id=-100111, message_id=42)
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_v2_off, effective_user=_user(2001)),
            _make_ctx(bot),
        )
        self.assertEqual(self.ws_db.get_post_vote_counts(post_id).get(v2_id, 0), 0)

    async def test_like_button_in_post_manage_and_old_post_edit(self) -> None:
        bot = _FakeBot()
        post_id = self.ws_db.create_post(
            post_text="پست برای دکمه لایک",
            entities_json="[]",
            photo_url="",
            media_type="none",
            created_by=1000,
        )

        # Saved post 1-step like add with "-" default text
        pm_ctx = _make_ctx(bot, {"pm": {"post_id": post_id, "page": 0, "tab": "all", "search": ""}})
        q_pm = _FakeQuery(f"pm:btn_type:{post_id}:like")
        st = await handle_post_manage_callback(
            SimpleNamespace(callback_query=q_pm, effective_user=_user(1000)),
            pm_ctx,
        )
        self.assertEqual(st, PM_ADD_BTN_TEXT)

        msg = SimpleNamespace(
            text="-",
            chat_id=1000,
            message_id=778,
            delete=lambda: _noop(),
            reply_text=lambda *a, **kw: _noop(),
        )
        await receive_pm_btn_text(
            SimpleNamespace(message=msg, effective_user=_user(1000)),
            pm_ctx,
        )
        updated = self.ws_db.get_post(post_id)
        btns = json.loads(updated["buttons_json"])
        self.assertEqual(len(btns), 1)
        self.assertEqual(btns[0]["type"], "like")
        self.assertEqual(btns[0]["text"], DEFAULT_LIKE_TEXT)

        # Old post edit 1-step like add + apply + live vote
        old_ctx = _make_ctx(
            bot,
            {
                "old_edit": {
                    "chat_id": -100111,
                    "message_id": 55,
                    "text": "پست قدیمی",
                    "entities_json": "[]",
                    "media_type": "none",
                    "buttons": [],
                    "orig_is_photo": False,
                }
            },
        )
        await handle_old_edit_callback(
            SimpleNamespace(
                callback_query=_FakeQuery("old:btn_type:like"),
                effective_user=_user(1000),
            ),
            old_ctx,
        )
        await receive_old_btn_text(
            SimpleNamespace(
                message=SimpleNamespace(
                    text="❤️ پسندیدم",
                    chat_id=1000,
                    message_id=779,
                    delete=lambda: _noop(),
                    reply_text=lambda *a, **kw: _noop(),
                ),
                effective_user=_user(1000),
            ),
            old_ctx,
        )
        await handle_old_edit_callback(
            SimpleNamespace(
                callback_query=_FakeQuery("old:apply"),
                effective_user=_user(1000),
            ),
            old_ctx,
        )
        old_set_id = old_ctx.user_data["old_edit"]["old_set_id"]
        old_bid = old_ctx.user_data["old_edit"]["buttons"][0]["btn_id"]
        self.assertIsNotNone(old_set_id)

        q_old_react = _FakeQuery(
            f"pb_react:old:{self.ws_id}:{old_set_id}:{old_bid}",
            chat_id=-100111,
            message_id=55,
        )
        await handle_react_button_callback(
            SimpleNamespace(callback_query=q_old_react, effective_user=_user(3001)),
            _make_ctx(bot),
        )
        self.assertEqual(self.ws_db.get_vote_counts("old", old_set_id).get(old_bid), 1)


async def _noop(*args, **kwargs):
    return None


if __name__ == "__main__":
    unittest.main()
