import os
import tempfile
import unittest

from bot.config import config
from bot.constants import SETTINGS_BUTTON, SWITCH_WORKSPACE_BUTTON
from bot.database import clear_workspace_cache, registry
from bot.keyboards import (
    CANCEL_CONFIRM_INLINE,
    POST_CANCEL_INLINE,
    ib,
    main_menu_for,
    post_button_inspector_keyboard,
    post_button_type_keyboard,
    post_buttons_manage_keyboard,
    post_caption_too_long_keyboard,
    post_manage_detail_keyboard,
    post_media_choice_keyboard,
    post_preview_controls_keyboard,
    settings_confirm_keyboard,
    settings_panel_keyboard,
    stats_keyboard,
    workspace_choice_keyboard,
)


class ButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_owner = config.OWNER_ID
        self._orig_data = config.DATA_DIR
        self._orig_reg = config.REGISTRY_DB_PATH
        self._orig_ws = config.WORKSPACES_DIR
        self._orig_reg_path = registry.path

        config.OWNER_ID = 100
        config.DATA_DIR = os.path.join(self._tmp.name, "data")
        config.REGISTRY_DB_PATH = os.path.join(config.DATA_DIR, "registry.db")
        config.WORKSPACES_DIR = os.path.join(config.DATA_DIR, "workspaces")
        registry.path = config.REGISTRY_DB_PATH
        clear_workspace_cache()
        registry.init()

    def tearDown(self) -> None:
        config.OWNER_ID = self._orig_owner
        config.DATA_DIR = self._orig_data
        config.REGISTRY_DB_PATH = self._orig_reg
        config.WORKSPACES_DIR = self._orig_ws
        registry.path = self._orig_reg_path
        clear_workspace_cache()
        self._tmp.cleanup()

    def test_main_menu_roles_and_styles(self) -> None:
        # Main .env owner (100) sees Settings AND Backup
        registry.save_or_update_user(100, "owner", "Owner", "")
        owner_menu = main_menu_for(100)
        owner_texts = [b.text for row in owner_menu.keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, owner_texts)
        self.assertIn("💾 بک‌آپ", owner_texts)
        self.assertNotIn(SWITCH_WORKSPACE_BUTTON, owner_texts)

        owner_styles = {b.text: b.style for row in owner_menu.keyboard for b in row}
        self.assertEqual(owner_styles["📝 ساخت پست جدید"], "success")
        self.assertEqual(owner_styles["📂 پست‌های ذخیره‌شده"], "primary")
        self.assertEqual(owner_styles["💾 بک‌آپ"], "danger")
        self.assertEqual(owner_styles[SETTINGS_BUTTON], "danger")

        # Regular public workspace owner (300 != OWNER_ID) sees Settings but NOT Backup
        registry.save_or_update_user(300, "pub", "Public", "")
        pub_texts = [b.text for row in main_menu_for(300).keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, pub_texts)
        self.assertNotIn("💾 بک‌آپ", pub_texts)

        # User 200 added as admin of 100 -> while in owner mode, sees switch button too (no Backup)
        registry.save_or_update_user(200, "adm", "Admin", "")
        registry.add_workspace_admin(100, 200)
        u200_owner_texts = [b.text for row in main_menu_for(200).keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, u200_owner_texts)
        self.assertNotIn("💾 بک‌آپ", u200_owner_texts)
        self.assertIn(SWITCH_WORKSPACE_BUTTON, u200_owner_texts)

        # User 200 switches to admin mode for 100 -> NO Settings and NO Backup
        registry.set_active_workspace_id(200, 100)
        u200_admin_texts = [b.text for row in main_menu_for(200).keyboard for b in row]
        self.assertNotIn(SETTINGS_BUTTON, u200_admin_texts)
        self.assertNotIn("💾 بک‌آپ", u200_admin_texts)
        self.assertIn(SWITCH_WORKSPACE_BUTTON, u200_admin_texts)
        self.assertIn("📝 ساخت پست جدید", u200_admin_texts)

        # Choice keyboard
        choice_kb = workspace_choice_keyboard(200, registry.get_admin_workspaces(200))
        self.assertEqual(choice_kb.inline_keyboard[0][0].callback_data, "ws_pick:200")
        self.assertEqual(choice_kb.inline_keyboard[1][0].callback_data, "ws_pick:100")

    def test_ib_inference_and_inline_keyboards(self) -> None:
        self.assertEqual(ib("تأیید", callback_data="ok").style, "success")
        self.assertEqual(ib("بازگشت", callback_data="back").style, "danger")
        self.assertEqual(ib("حذف", callback_data="x").style, "danger")
        self.assertEqual(ib("آیتم", callback_data="st:adm_del:1").style, "danger")
        self.assertEqual(
            ib("انتخاب", style="primary", callback_data="x").style, "primary"
        )

        self.assertEqual(POST_CANCEL_INLINE.inline_keyboard[0][0].style, "danger")
        self.assertEqual(CANCEL_CONFIRM_INLINE.inline_keyboard[0][0].style, "danger")
        self.assertEqual(CANCEL_CONFIRM_INLINE.inline_keyboard[0][1].style, "primary")

        confirm = settings_confirm_keyboard("yes", "no")
        self.assertEqual(confirm.inline_keyboard[0][0].style, "danger")
        self.assertEqual(confirm.inline_keyboard[0][1].style, "primary")

        st_kb = settings_panel_keyboard()
        self.assertEqual(st_kb.inline_keyboard[-1][0].style, "primary")
        self.assertEqual(st_kb.inline_keyboard[-1][1].style, "danger")

        stats_kb = stats_keyboard()
        self.assertEqual(stats_kb.inline_keyboard[0][0].style, "primary")
        self.assertEqual(stats_kb.inline_keyboard[0][1].style, "danger")

        pv_kb = post_preview_controls_keyboard(True, True, 0)
        self.assertEqual(pv_kb.inline_keyboard[0][0].style, "success")
        self.assertEqual(pv_kb.inline_keyboard[0][1].style, "success")
        self.assertEqual(pv_kb.inline_keyboard[0][1].callback_data, "pb:targets")
        self.assertEqual(pv_kb.inline_keyboard[0][1].text, "🎯 انتخاب مقصد")
        pv_texts = [b.text for row in pv_kb.inline_keyboard for b in row]
        self.assertIn("🔘 دکمه شیشه‌ای (اختیاری)", pv_texts)
        self.assertEqual(pv_kb.inline_keyboard[-1][1].style, "danger")

        pv_none_kb = post_preview_controls_keyboard(True, True, 0, media_type="none")
        pv_none_cbs = [b.callback_data for row in pv_none_kb.inline_keyboard for b in row]
        self.assertNotIn("pb:toggle_pos", pv_none_cbs)
        self.assertIn("pb:change_media", pv_none_cbs)

        pv_photo_kb = post_preview_controls_keyboard(True, True, 0, media_type="photo")
        pv_photo_cbs = [b.callback_data for row in pv_photo_kb.inline_keyboard for b in row]
        self.assertNotIn("pb:toggle_pos", pv_photo_cbs)
        self.assertIn("pb:change_media", pv_photo_cbs)

        mc_kb = post_media_choice_keyboard()
        mc_cbs = [b.callback_data for row in mc_kb.inline_keyboard for b in row]
        self.assertIn("pb:media:none", mc_cbs)
        self.assertIn("pb:media:photo", mc_cbs)
        self.assertIn("pb:media:link", mc_cbs)

        cap_kb = post_caption_too_long_keyboard()
        cap_cbs = [b.callback_data for row in cap_kb.inline_keyboard for b in row]
        self.assertEqual(cap_cbs[:3], ["pb:media:link", "pb:edit_text_pre", "pb:media:none"])

        pm_kb = post_manage_detail_keyboard({"id": 7}, buttons_count=2)
        all_callbacks = [b.callback_data for row in pm_kb.inline_keyboard for b in row]
        self.assertIn("pm:btns:7", all_callbacks)
        self.assertIn("pm:media_menu:7", all_callbacks)
        self.assertIn("pm:targets:7", all_callbacks)
        self.assertIn("pm:save_back:7", all_callbacks)
        self.assertNotIn("pm:pv:7", all_callbacks)
        self.assertEqual(pm_kb.inline_keyboard[0][1].style, "success")
        self.assertEqual(pm_kb.inline_keyboard[0][1].text, "🎯 انتخاب مقصد")
        self.assertEqual(pm_kb.inline_keyboard[-2][0].style, "success")
        self.assertEqual(pm_kb.inline_keyboard[-2][0].text, "✅ ذخیره و بازگشت")

        pm_none_kb = post_manage_detail_keyboard({"id": 8, "media_type": "none"}, buttons_count=0)
        pm_none_cbs = [b.callback_data for row in pm_none_kb.inline_keyboard for b in row]
        pm_none_texts = [b.text for row in pm_none_kb.inline_keyboard for b in row]
        self.assertNotIn("pm:toggle_pos:8", pm_none_cbs)
        self.assertIn("pm:media_menu:8", pm_none_cbs)
        self.assertIn("🔘 دکمه شیشه‌ای (اختیاری)", pm_none_texts)

        btns_kb = post_buttons_manage_keyboard(
            [
                {"text": "لینک", "type": "url", "url": "https://t.me/a/1", "style": "primary", "row": 0},
                {"text": "نمایشی", "type": "popup", "popup_text": "تست", "style": "success", "row": 0},
            ],
            add_callback="pm:btn_add:7",
            inspect_prefix="pm:btn_ins:7:",
            back_callback="pm:detail:7",
        )
        self.assertEqual(len(btns_kb.inline_keyboard[0]), 2)
        self.assertEqual(btns_kb.inline_keyboard[0][0].callback_data, "pm:btn_ins:7:0")
        self.assertEqual(btns_kb.inline_keyboard[0][0].style, "primary")
        self.assertEqual(btns_kb.inline_keyboard[0][1].style, "success")

        type_kb = post_button_type_keyboard("pb:btn_type:", "pb:btns")
        self.assertEqual(type_kb.inline_keyboard[0][0].callback_data, "pb:btn_type:url")
        self.assertEqual(type_kb.inline_keyboard[1][0].callback_data, "pb:btn_type:popup")
        self.assertEqual(type_kb.inline_keyboard[2][0].callback_data, "pb:btn_type:copy")
        self.assertEqual(type_kb.inline_keyboard[3][0].callback_data, "pb:btn_type:like")
        self.assertEqual(type_kb.inline_keyboard[3][0].style, "success")
        self.assertEqual(type_kb.inline_keyboard[4][0].callback_data, "pb:btn_type:vote")

        insp_kb = post_button_inspector_keyboard(
            [
                {"text": "لینک", "type": "url", "url": "https://t.me/a/1", "style": "primary", "row": 0},
                {"text": "نمایشی", "type": "popup", "popup_text": "تست", "style": None, "row": 0},
            ],
            idx=0,
            prefix="pb",
            back_callback="pb:btns",
        )
        # 4 color buttons on first row: None, primary, success, danger
        self.assertEqual(len(insp_kb.inline_keyboard[0]), 4)
        self.assertIsNone(insp_kb.inline_keyboard[0][0].style)
        self.assertEqual(insp_kb.inline_keyboard[0][1].style, "primary")
        self.assertEqual(insp_kb.inline_keyboard[0][2].style, "success")
        self.assertEqual(insp_kb.inline_keyboard[0][3].style, "danger")
        self.assertIn("✓", insp_kb.inline_keyboard[0][1].text)
        # Row 2 has left, right, AND split because 2 buttons share row 0
        self.assertEqual(len(insp_kb.inline_keyboard[2]), 3)


if __name__ == "__main__":
    unittest.main()
