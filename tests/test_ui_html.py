"""ui_html 页面内嵌 JS 的关键机制断言（waitApi 竞态修复 + 低占用可见性开关）。
运行：python -m unittest discover tests"""
import unittest

from campus_net.ui_html import dashboard_html, settings_html


class VisibilityFlagTests(unittest.TestCase):
    """低占用：页面隐藏时轮询跳过，显隐由 Python 侧经 __setUiVisible 同步。"""

    def test_both_pages_have_visibility_switch(self):
        for html in (settings_html(), dashboard_html()):
            self.assertIn('window.__cnVisible = false;', html)
            self.assertIn('function __setUiVisible(v)', html)
            self.assertIn('if (window.__cnVisible === false) return;', html)

    def test_dashboard_refreshes_status_and_timer_on_visible(self):
        html = dashboard_html()
        self.assertIn('refreshStatus().catch(() => {});', html)
        self.assertIn('refreshTimer().catch(() => {});', html)

    def test_settings_refreshes_status_and_theme_on_visible(self):
        html = settings_html()
        self.assertIn('refreshStatus().catch(() => {});', html)
        self.assertIn('refreshTheme().catch(() => {});', html)


class WaitApiRaceGuardTests(unittest.TestCase):
    """v0.9.4：waitApi 必须等真实方法挂载（注入分两步，空 api 对象会提前放行）。"""

    def test_waitapi_checks_real_method(self):
        for html in (settings_html(), dashboard_html()):
            self.assertIn('!api().get_status', html)

    def test_polling_survives_single_failure(self):
        for html in (settings_html(), dashboard_html()):
            self.assertIn('refreshStatus().catch(() => {})', html)


if __name__ == '__main__':
    unittest.main()
