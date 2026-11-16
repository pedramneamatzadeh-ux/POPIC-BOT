import unittest

from bot.config import config
from bot.constants import SETTINGS_BUTTON
from bot.keyboards import (
    CANCEL_CONFIRM_INLINE,
    POST_CANCEL_INLINE,
    ib,
    main_menu_for,
    post_preview_controls_keyboard,
    settings_confirm_keyboard,
    settings_panel_keyboard,
    stats_keyboard,
)


class ButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_owner = config.OWNER_ID
        self._orig_admins = list(config.ADMIN_IDS)
        config.OWNER_ID = 100
        config.ADMIN_IDS = [200]

    def tearDown(self) -> None:
        config.OWNER_ID = self._orig_owner
        config.ADMIN_IDS = self._orig_admins

    def test_main_menu_roles_and_styles(self) -> None:
        owner_menu = main_menu_for(100)
        owner_texts = [b.text for row in owner_menu.keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, owner_texts)
        owner_styles = {b.text: b.style for row in owner_menu.keyboard for b in row}
        self.assertEqual(owner_styles["📝 ساخت پست جدید"], "success")
        self.assertEqual(owner_styles["📂 پست‌های ذخیره‌شده"], "primary")
        self.assertEqual(owner_styles["💾 بک‌آپ"], "danger")
        self.assertEqual(owner_styles[SETTINGS_BUTTON], "danger")

        admin_menu = main_menu_for(200)
        admin_texts = [b.text for row in admin_menu.keyboard for b in row]
        self.assertIn(SETTINGS_BUTTON, admin_texts)

        stranger_menu = main_menu_for(999)
        stranger_texts = [b.text for row in stranger_menu.keyboard for b in row]
        self.assertNotIn(SETTINGS_BUTTON, stranger_texts)

        none_menu = main_menu_for(None)
        none_texts = [b.text for row in none_menu.keyboard for b in row]
        self.assertNotIn(SETTINGS_BUTTON, none_texts)

    def test_ib_inference_and_inline_keyboards(self) -> None:
        self.assertEqual(ib("تأیید", callback_data="ok").style, "success")
        self.assertEqual(ib("بازگشت", callback_data="back").style, "danger")
        self.assertEqual(ib("حذف", callback_data="x").style, "danger")
        self.assertEqual(ib("آیتم", callback_data="st:adm_del:1").style, "danger")
        self.assertEqual(
            ib("انتخاب", style="primary", callback_data="x").style, "primary"
        )

        self.assertEqual(
            POST_CANCEL_INLINE.inline_keyboard[0][0].style, "danger"
        )
        self.assertEqual(
            CANCEL_CONFIRM_INLINE.inline_keyboard[0][0].style, "danger"
        )
        self.assertEqual(
            CANCEL_CONFIRM_INLINE.inline_keyboard[0][1].style, "primary"
        )

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


if __name__ == "__main__":
    unittest.main()
