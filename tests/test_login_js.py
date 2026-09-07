"""login 模块单元测试：JS 生成器的特殊字符转义 + 执行器的诊断日志。

旧版实现用 f-string 裸拼账号密码，密码含引号/反斜杠/换行时会生成
非法 JS 导致登录静默失败——这些用例就是该 bug 的回归测试。
"""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from campus_net import login as login_mod
from campus_net.config import AppConfig
from campus_net.login import (LoginExecutor, build_confirm_click_js,
                              build_inject_js, build_logged_out_check_js,
                              build_logout_click_js, build_page_diag_js,
                              build_success_js)


class InjectJsTests(unittest.TestCase):
    def test_plain_credentials_produce_attempted_script(self):
        js = build_inject_js('20210001', 'secret123',
                             ('username', 'password', 'login-account'))
        self.assertIn(json.dumps('20210001'), js)
        self.assertIn(json.dumps('secret123'), js)
        self.assertIn('getElementById("username")', js)
        self.assertIn('loginBtn.click()', js)
        self.assertIn("return 'attempted'", js)

    def test_quote_and_backslash_in_password_do_not_break_script(self):
        password = "pa'ss\";\\x"
        js = build_inject_js('user', password, ('u', 'p', 'b'))
        self.assertIn(json.dumps(password), js)

    def test_newline_in_password_is_escaped(self):
        password = 'line1\nline2'
        js = build_inject_js('user', password, ('u', 'p', 'b'))
        # 真实换行不允许出现在脚本里，必须是转义后的字面量
        self.assertNotIn(password, js)
        self.assertIn(json.dumps(password), js)

    def test_chinese_characters_survive(self):
        password = '密码"引号"'
        js = build_inject_js('user', password, ('u', 'p', 'b'))
        self.assertIn(json.dumps(password), js)

    def test_element_ids_are_quoted(self):
        js = build_inject_js('u', 'p', ("my'user", 'my"pass', "btn';x"))
        for element_id in ("my'user", 'my"pass', "btn';x"):
            self.assertIn(json.dumps(element_id), js)


class SuccessJsTests(unittest.TestCase):
    def test_plain_id(self):
        self.assertEqual(build_success_js('logout'),
                         'document.getElementById("logout") !== null;')

    def test_id_with_special_chars_is_quoted(self):
        js = build_success_js("log'; out")
        self.assertIn(json.dumps("log'; out"), js)


class LogoutJsTests(unittest.TestCase):
    def test_logout_click_js(self):
        js = build_logout_click_js("logout")
        self.assertIn('getElementById("logout")', js)
        self.assertIn('logoutBtn.click()', js)
        self.assertIn("return 'clicked'", js)
        self.assertIn("return 'not_found'", js)

    def test_logout_click_js_escapes_special_chars(self):
        js = build_logout_click_js("log'; out")
        self.assertIn(json.dumps("log'; out"), js)

    def test_logged_out_check_js(self):
        js = build_logged_out_check_js("logout")
        self.assertEqual(js, '!document.getElementById("logout");')

    def test_logged_out_check_js_escapes_special_chars(self):
        js = build_logged_out_check_js("log'; out")
        self.assertIn(json.dumps("log'; out"), js)

    def test_confirm_click_js_covers_common_modals(self):
        js = build_confirm_click_js()
        for selector in ('.swal2-confirm', '.confirm', '.swal-button--confirm',
                         '.layui-layer-btn0', '.bootbox-accept'):
            self.assertIn("'{}'".format(selector), js)
        self.assertIn("el.click()", js)
        self.assertIn("return 'clicked:' + clicked", js)

    def test_page_diag_js_reports_elements(self):
        js = build_page_diag_js("logout", "username")
        self.assertIn("url: location.href.slice(0, 120)", js)
        self.assertIn('logout: !!document.getElementById("logout")', js)
        self.assertIn('username: !!document.getElementById("username")', js)


class _EventSlot:
    """模拟 window.events.loaded 的 += 绑定。"""

    def __init__(self, sink):
        self._sink = sink

    def __iadd__(self, callback):
        self._sink.append(callback)
        return self


class _FakeWindow:
    """pywebview window 替身：load_url 时同步触发 loaded 事件。"""

    def __init__(self):
        self._loaded_handlers = []
        self.urls = []
        self.js_calls = []
        self.events = SimpleNamespace(loaded=_EventSlot(self._loaded_handlers))

    def load_url(self, url):
        self.urls.append(url)
        for handler in self._loaded_handlers:
            handler()

    def evaluate_js(self, script):
        self.js_calls.append(script)
        return 'not_found'


def _config():
    return AppConfig(
        username='u', password='p',
        login_url='http://10.1.1.55/', internal_test_url='10.1.1.55',
        external_test_url='www.baidu.com', check_interval=45,
        quick_retry_times=2, max_retries=3, retry_delay=180,
        show_notifications=True, logout_fallback='browser', ui_theme='system',
        username_input_id='username', password_input_id='password',
        login_btn_id='login-account', logout_btn_id='logout')


class LoginExecutorDiagTests(unittest.TestCase):
    """注入失败时的页面诊断日志（仅诊断，不参与判定）。"""

    def _run_login_to_inject_failure(self):
        executor = LoginExecutor(_config())
        window = _FakeWindow()
        executor.bind_events(window)
        with mock.patch.object(login_mod, 'INJECT_RETRY_INTERVAL', 0):
            with self.assertLogs(level='WARNING') as captured:
                result = executor.execute_login(window)
        return result, captured, window

    def test_login_inject_failure_logs_diag(self):
        result, captured, window = self._run_login_to_inject_failure()
        self.assertFalse(result)
        text = '\n'.join(captured.output)
        self.assertIn('未找到账号/密码输入框或登录按钮', text)
        self.assertIn('页面状态', text)
        # 结束时页面回到 about:blank
        self.assertEqual(window.urls[-1], 'about:blank')

    def test_logout_inject_failure_logs_diag(self):
        executor = LoginExecutor(_config())
        window = _FakeWindow()
        executor.bind_events(window)
        with mock.patch.object(login_mod, 'INJECT_RETRY_INTERVAL', 0):
            with self.assertLogs(level='WARNING') as captured:
                result = executor.execute_logout(window)
        self.assertFalse(result)
        text = '\n'.join(captured.output)
        self.assertIn('未找到注销按钮', text)
        self.assertIn('页面状态', text)

    def test_inject_retry_budget_widened(self):
        # 慢网络容忍：15 次 × 1 秒（原 10 次）
        self.assertEqual(login_mod.INJECT_MAX_TRIES, 15)


if __name__ == '__main__':
    unittest.main()
