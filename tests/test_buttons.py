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
    post_buttons_manage_keyboard,
    post_manage_detail_keyboard,
    post_preview_controls_keyboard,
    settings_confirm_keyboard,
    settings_panel_keyboard,
    stats_keyboard,
    workspace_choice_keyboard,
)


class ButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data = config.DATA_DIR
        self._orig_reg = config.REGISTRY_DB_PATH
        self._orig_ws = config.WORKSPACES_DIR
        self._orig_reg_path = registry.path

        config.DATA_DIR = os.path.join(self._tmp.name, "data")
        config.REGISTRY_DB_PATH = os.path.join(config.DATA_DIR, "registry.db")
        config.WORKSPACES_DIR = os.path.join(config.DATA_DIR, "workspaces")
        registry.path = config.REGISTRY_DB_PATH
        clear_workspace_cache()
        registry.init()

    def tearDown(self) -> None:
        config.DATA_DIR = self._orig_data
        config.REGISTRY_DB_PATH = self._orig_reg
        config.WORKSPACES_DIR = self._orig_ws
        registry.path = self._orig_reg_path
        clear_workspace_cache()
        self._tmp.cleanup()

    def test_main_menu_roles_and_styles(self) -> None:
        # Regular public user (owner of own workspace, no admin roles)
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

        # User 200 added as admin of 100 -> while in owner mode, sees switch button too
        registry.save_or_update_user(200, "adm", "Admin", "")
        registry.add_workspace_admin(100, 200)
        u200_owner_texts = [b.text for row in main_menu_for(200).keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, u200_owner_texts)
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
        self.assertEqual(pv_kb.inline_keyboard[-1][1].style, "danger")

        pm_kb = post_manage_detail_keyboard({"id": 7}, buttons_count=2)
        all_callbacks = [b.callback_data for row in pm_kb.inline_keyboard for b in row]
        self.assertIn("pm:btns:7", all_callbacks)

        btns_kb = post_buttons_manage_keyboard(
            [{"text": "لینک", "url": "https://t.me/a/1"}],
            add_callback="pm:btn_add:7",
            del_prefix="pm:btn_del:7:",
            back_callback="pm:detail:7",
        )
        self.assertEqual(btns_kb.inline_keyboard[0][0].callback_data, "pm:btn_del:7:0")
        self.assertEqual(btns_kb.inline_keyboard[0][0].style, "danger")


if __name__ == "__main__":
    unittest.main()
