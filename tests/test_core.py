import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram import MessageEntity

import main as app_main
from bot.application import create_application
from bot.config import config
from bot.constants import BOT_VERSION, BOT_VERSION_DATE, DEFAULT_POPUP_TEXT
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
    group_buttons_by_row,
    move_button_row,
    move_button_side,
    normalize_buttons,
    serialize_buttons,
    serialize_entities,
    split_button_new_row,
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
        # group 0: /start + pb_disp + ws_pick + switch_btn + 5 convs + backup = 10
        self.assertEqual(len(app.handlers[0]), 10)
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

    def test_r9_media_schema_migration_and_modes(self) -> None:
        import sqlite3

        db_path = os.path.join(self._tmp.name, "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_text TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                photo_url TEXT NOT NULL,
                show_above_text INTEGER NOT NULL DEFAULT 1,
                prefer_large_media INTEGER NOT NULL DEFAULT 1,
                buttons_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                target_channel_id INTEGER,
                channel_message_id INTEGER,
                channel_title TEXT,
                channel_username TEXT,
                created_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO posts (post_text, photo_url, created_by) VALUES (?, ?, ?)",
            ("قدیمی", "https://t.me/pics/1", 100),
        )
        conn.commit()
        conn.close()

        legacy_db = Database(db_path)
        legacy_db.init()
        old_post = legacy_db.get_post(1)
        self.assertEqual(old_post["media_type"], "link")
        self.assertEqual(old_post["photo_file_id"], "")

        none_id = legacy_db.create_post("بدون عکس", "[]", "", 100, media_type="none")
        self.assertEqual(legacy_db.get_post(none_id)["media_type"], "none")

        photo_id = legacy_db.create_post(
            "عکس عادی", "[]", "", 100, media_type="photo", photo_file_id="FILE_123"
        )
        self.assertEqual(legacy_db.get_post(photo_id)["photo_file_id"], "FILE_123")

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
        self.assertEqual(btns[0]["type"], "url")
        self.assertIsNone(btns[0]["style"])
        markup = build_buttons_markup(btns)
        self.assertEqual(markup.inline_keyboard[0][0].url, "https://t.me/a/1")

    def test_advanced_buttons_types_colors_and_row_layout(self) -> None:
        raw = [
            {"text": "لینک", "type": "url", "url": "https://t.me/a/1", "style": "primary", "row": 0},
            {"text": "نمایشی", "type": "popup", "popup_text": "", "style": "success", "row": 1},
            {"text": "کپی کد", "type": "copy", "copy_text": "OFF20", "style": None, "row": 2},
        ]
        norm = normalize_buttons(raw)
        self.assertEqual(norm[1]["popup_text"], DEFAULT_POPUP_TEXT)
        self.assertEqual(len(group_buttons_by_row(norm)), 3)

        # Move button 1 up onto row 0 -> 2 buttons in row 0
        merged = move_button_row(norm, 1, -1)
        rows = group_buttons_by_row(merged)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual([b["text"] for _, b in rows[0]], ["لینک", "نمایشی"])

        # Swap left/right inside row 0
        swapped = move_button_side(merged, 1, -1)
        self.assertEqual([b["text"] for _, b in group_buttons_by_row(swapped)[0]], ["نمایشی", "لینک"])

        # Split button 0 back into its own row
        split = split_button_new_row(swapped, 0)
        self.assertEqual(len(group_buttons_by_row(split)), 3)

        # Build markup for saved post in workspace 500, post 12
        markup = build_buttons_markup(merged, workspace_id=500, post_id=12)
        self.assertEqual(len(markup.inline_keyboard), 2)
        self.assertEqual(len(markup.inline_keyboard[0]), 2)
        self.assertEqual(markup.inline_keyboard[0][0].style, "primary")
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, "pb_disp:500:12:1")
        self.assertEqual(markup.inline_keyboard[0][1].style, "success")
        self.assertEqual(markup.inline_keyboard[1][0].copy_text.text, "OFF20")
        self.assertIsNone(markup.inline_keyboard[1][0].style)

        # Preview mode markup uses pb_disp:pv:<idx>
        pv_markup = build_buttons_markup(merged)
        self.assertEqual(pv_markup.inline_keyboard[0][1].callback_data, "pb_disp:pv:1")

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
