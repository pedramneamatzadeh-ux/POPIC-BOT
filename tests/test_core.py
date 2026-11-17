import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram import MessageEntity

import main as app_main
from bot.application import create_application
from bot.config import config
from bot.constants import BOT_VERSION, BOT_VERSION_DATE
from bot.database import (
    Database,
    clear_workspace_cache,
    get_workspace_db,
    registry,
    resolve_user_workspace,
)
from bot.services.publisher import (
    build_buttons_markup,
    build_link_preview_options,
    deserialize_buttons,
    deserialize_entities,
    serialize_buttons,
    serialize_entities,
)
from bot.utils import (
    build_telegram_message_link,
    extract_forward_post_link,
    fmt_tehran,
    normalize_digits,
    normalize_photo_url,
)


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_token = config.BOT_TOKEN
        self._orig_owner = config.OWNER_ID
        self._orig_data = config.DATA_DIR
        self._orig_reg = config.REGISTRY_DB_PATH
        self._orig_ws = config.WORKSPACES_DIR
        self._orig_reg_path = registry.path

        config.BOT_TOKEN = "123456:TEST_TOKEN"
        config.OWNER_ID = None
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

    def test_create_application_handlers(self) -> None:
        app = create_application()
        self.assertIn(-1, app.handlers)
        self.assertIn(0, app.handlers)
        # group 0: /start + ws_pick + switch_btn + 4 convs + backup = 8
        self.assertEqual(len(app.handlers[0]), 8)
        self.assertEqual(len(app.handlers[1]), 1)
        self.assertTrue(len(app.error_handlers) >= 1)

    def test_preflight_works_without_owner_id(self) -> None:
        config.OWNER_ID = None
        app_main.preflight()
        self.assertTrue(os.path.isdir(config.DATA_DIR))
        self.assertTrue(os.path.isdir(config.WORKSPACES_DIR))
        ws_db = get_workspace_db(101)
        with ws_db.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_workspace_isolation_and_revocation(self) -> None:
        registry.save_or_update_user(100, "owner", "Ali", "")
        registry.save_or_update_user(200, "admin", "Sara", "")
        db_100 = get_workspace_db(100)
        db_200 = get_workspace_db(200)

        db_100.set_setting("default_show_above_text", False)
        self.assertFalse(db_100.get_show_above_text())
        self.assertTrue(db_200.get_show_above_text())
        self.assertTrue(config.DEFAULT_SHOW_ABOVE_TEXT)

        db_100.create_post("پست مالک", "[]", "https://t.me/pics/1", 100)
        self.assertEqual(db_100.count_posts(), 1)
        self.assertEqual(db_200.count_posts(), 0)

        registry.add_workspace_admin(100, 200)
        registry.set_active_workspace_id(200, 100)
        ws_id, status, ws_db = resolve_user_workspace(200)
        self.assertEqual(ws_id, 100)
        self.assertEqual(status, "admin")
        self.assertEqual(ws_db.count_posts(), 1)

        # Revoke admin -> automatically falls back to personal owner workspace
        registry.remove_workspace_admin(100, 200)
        ws_id, status, ws_db = resolve_user_workspace(200)
        self.assertEqual(ws_id, 200)
        self.assertEqual(status, "revoked_to_owner")
        self.assertEqual(ws_db.count_posts(), 0)

    def test_database_channels_and_posts_crud(self) -> None:
        test_db = Database(os.path.join(self._tmp.name, "crud.db"))
        test_db.init()

        ch_id = test_db.add_target_channel(-100123456, "Anime News", "animenews", 999999)
        self.assertGreater(ch_id, 0)
        self.assertEqual(test_db.count_target_channels(), 1)
        ch = test_db.get_target_channel(ch_id)
        self.assertEqual(ch["username"], "animenews")

        post_id = test_db.create_post(
            post_text="متن طولانی پست",
            entities_json='[{"type":"bold","offset":0,"length":3}]',
            photo_url="https://t.me/pics_ch/10",
            created_by=999999,
            show_above_text=True,
            prefer_large_media=True,
        )
        post = test_db.get_post(post_id)
        self.assertEqual(post["status"], "draft")
        self.assertEqual(post["photo_url"], "https://t.me/pics_ch/10")

        test_db.mark_post_published(post_id, -100123456, 77, "Anime News", "animenews")
        test_db.record_delivery(post_id, "channel", -100123456, 77, 999999)
        test_db.record_delivery(post_id, "pv", 999999, 88, 999999)

        summary = test_db.get_stats_summary()
        self.assertEqual(summary["total_posts"], 1)
        self.assertEqual(summary["published_posts"], 1)
        self.assertEqual(summary["channel_deliveries"], 1)
        self.assertEqual(summary["pv_deliveries"], 1)
        self.assertEqual(summary["total_channels"], 1)

        self.assertTrue(test_db.delete_post(post_id))
        self.assertIsNone(test_db.get_post(post_id))

    def test_normalize_photo_url(self) -> None:
        self.assertEqual(
            normalize_photo_url("https://t.me/my_pics/42"),
            ("https://t.me/my_pics/42", ""),
        )
        self.assertEqual(
            normalize_photo_url("t.me/my_pics/42"),
            ("https://t.me/my_pics/42", ""),
        )
        self.assertEqual(
            normalize_photo_url("@my_pics/42"),
            ("https://t.me/my_pics/42", ""),
        )
        self.assertEqual(
            normalize_photo_url("https://t.me/c/123456/42"),
            (None, "private"),
        )
        self.assertEqual(normalize_photo_url("hello world"), (None, "invalid"))
        self.assertEqual(normalize_photo_url(""), (None, "empty"))

    def test_extract_forward_post_link(self) -> None:
        pub_msg = SimpleNamespace(
            forward_origin=SimpleNamespace(
                chat=SimpleNamespace(username="pics_chan"),
                message_id=55,
            )
        )
        self.assertEqual(
            extract_forward_post_link(pub_msg),
            ("https://t.me/pics_chan/55", "ok"),
        )

        priv_msg = SimpleNamespace(
            forward_origin=SimpleNamespace(
                chat=SimpleNamespace(username=None),
                message_id=55,
            )
        )
        self.assertEqual(
            extract_forward_post_link(priv_msg),
            (None, "private_channel"),
        )

        normal_msg = SimpleNamespace(forward_origin=None)
        self.assertEqual(
            extract_forward_post_link(normal_msg),
            (None, "not_forward"),
        )

    def test_entities_and_preview_options(self) -> None:
        raw = serialize_entities(
            [
                MessageEntity(type="bold", offset=0, length=4),
                MessageEntity(type="text_link", offset=5, length=4, url="https://example.com"),
            ]
        )
        restored = deserialize_entities(raw)
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].type, "bold")
        self.assertEqual(restored[1].url, "https://example.com")

        lpo = build_link_preview_options(
            "https://t.me/pics/12",
            show_above_text=True,
            prefer_large_media=True,
        )
        self.assertFalse(lpo.is_disabled)
        self.assertEqual(lpo.url, "https://t.me/pics/12")
        self.assertTrue(lpo.show_above_text)
        self.assertTrue(lpo.prefer_large_media)

        btns_json = serialize_buttons([{"text": "عکس", "url": "https://t.me/a/1"}])
        btns = deserialize_buttons(btns_json)
        markup = build_buttons_markup(btns)
        self.assertEqual(markup.inline_keyboard[0][0].url, "https://t.me/a/1")

    def test_utils_helpers(self) -> None:
        self.assertEqual(normalize_digits("۱۲۳۴٥٦"), "123456")
        self.assertEqual(
            build_telegram_message_link(-100123456789, "my_ch", 99),
            "https://t.me/my_ch/99",
        )
        self.assertEqual(
            build_telegram_message_link(-100123456789, "", 99),
            "https://t.me/c/123456789/99",
        )
        dt = datetime(2024, 3, 19, 20, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(fmt_tehran(dt), "1403/01/01 — 00:00")
        self.assertEqual(fmt_tehran(None), "نامشخص")
        self.assertRegex(BOT_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(BOT_VERSION_DATE, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
