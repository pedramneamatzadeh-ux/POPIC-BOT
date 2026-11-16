import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram import MessageEntity

import main as app_main
from bot.application import build_application
from bot.config import Config, config
from bot.constants import BOT_VERSION, BOT_VERSION_DATE
from bot.database import Database, db
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
        self._db_path = os.path.join(self._tmp.name, "test.db")
        self._orig_token = config.BOT_TOKEN
        self._orig_owner = config.OWNER_ID
        self._orig_admins = list(config.ADMIN_IDS)
        self._orig_db_path = db.path
        config.BOT_TOKEN = "123456:TEST_TOKEN"
        config.OWNER_ID = 999999
        config.ADMIN_IDS = []
        db.path = self._db_path

    def tearDown(self) -> None:
        config.BOT_TOKEN = self._orig_token
        config.OWNER_ID = self._orig_owner
        config.ADMIN_IDS = self._orig_admins
        db.path = self._orig_db_path
        self._tmp.cleanup()

    def test_build_application_handlers(self) -> None:
        app = build_application()
        self.assertIn(-1, app.handlers)
        self.assertIn(0, app.handlers)
        # group 0: /start + 4 conversations + backup + stale_callback_answer = 7
        self.assertEqual(len(app.handlers[0]), 7)
        self.assertTrue(len(app.error_handlers) >= 1)

    def test_preflight_owner_only(self) -> None:
        self.assertTrue(app_main.preflight())
        test_db = Database(self._db_path)
        with test_db.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_preflight_missing_owner_fails(self) -> None:
        config.OWNER_ID = None
        self.assertFalse(app_main.preflight())

    def test_config_invalid_admin_tokens(self) -> None:
        tokens = Config._invalid_admin_tokens("123, abc, 456, -5")
        self.assertEqual(tokens, ["abc", "-5"])
        valid = Config._parse_admin_ids("123, abc, 456")
        self.assertEqual(valid, [123, 456])

    def test_database_settings_per_item_filter(self) -> None:
        test_db = Database(self._db_path)
        test_db.init()
        test_db.set_setting("admin_ids", [111, "bad", -2, "222"])
        self.assertEqual(config.ADMIN_IDS, [111, 222])

    def test_database_channels_and_posts_crud(self) -> None:
        test_db = Database(self._db_path)
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
        # 2024-03-19 20:30:00 UTC == 1403/01/01 00:00 Tehran
        dt = datetime(2024, 3, 19, 20, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(fmt_tehran(dt), "1403/01/01 — 00:00")
        self.assertEqual(fmt_tehran(None), "نامشخص")
        self.assertRegex(BOT_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(BOT_VERSION_DATE, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
