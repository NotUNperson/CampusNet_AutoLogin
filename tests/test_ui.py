"""ui.UiApi 的单元测试（计时器唤醒守护循环 / 主题设置 / 手动检测入队 / 窗口低占用生命周期）。
运行：python -m unittest discover tests"""
import os
import queue
import tempfile
import threading
import unittest
from unittest import mock

from campus_net import config
from campus_net.status import AppStatus, TimerState
from campus_net.ui import UiApi
from tests.test_config import _filled_config


class _EventSlot:
    """模拟 pywebview window.events：记录绑定的回调，可手动触发。"""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class _FakeWindow:
    """模拟 pywebview Window：记录 show/hide/destroy/evaluate_js 调用。"""

    def __init__(self, x=0, y=0):
        self.events = type('Events', (), {})()
        self.events.loaded = _EventSlot()
        self.events.closing = _EventSlot()
        self.events.shown = _FakeShownEvent()
        self._x, self._y = x, y
        self.calls = []

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    def show(self):
        self.calls.append('show')
        self.events.shown.set()

    def hide(self):
        self.calls.append('hide')

    def destroy(self):
        self.calls.append('destroy')
        # destroy 触发 FormClosing → closing 事件（与 pywebview 行为一致）
        self.events.closing.fire()

    def evaluate_js(self, script):
        self.calls.append('js:' + script)
        return None


class _FakeShownEvent:
    """模拟 pywebview events.shown（is_set/set 接口）。"""

    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True


class UiApiTimerWakeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, 'config.ini')
        self.login_now_event = threading.Event()
        self.timer = TimerState()
        self.api = UiApi(
            _filled_config(), AppStatus(), threading.Event(),
            self.login_now_event, self.timer, self.config_path)

    def test_timer_start_wakes_daemon(self):
        self.assertTrue(self.api.timer_start(5)['ok'])
        self.assertTrue(self.login_now_event.is_set())

    def test_timer_until_wakes_daemon(self):
        self.assertTrue(self.api.timer_until('23:59')['ok'])
        self.assertTrue(self.login_now_event.is_set())

    def test_timer_cancel_wakes_daemon(self):
        self.timer.start(10)
        self.login_now_event.clear()
        self.assertTrue(self.api.timer_cancel()['ok'])
        self.assertTrue(self.login_now_event.is_set())

    def test_timer_extend_does_not_wake(self):
        self.timer.start(10)
        self.login_now_event.clear()
        self.assertTrue(self.api.timer_extend(5)['ok'])
        self.assertFalse(self.login_now_event.is_set())  # 延长无视觉变化

    def test_toggle_pause_does_not_wake(self):
        self.api.toggle_pause()
        self.assertFalse(self.login_now_event.is_set())


class UiApiThemeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, 'config.ini')
        self.api = UiApi(
            _filled_config(), AppStatus(), threading.Event(),
            threading.Event(), TimerState(), self.config_path)

    def test_get_theme_default_is_system(self):
        self.assertEqual(self.api.get_theme(), {'theme': 'system'})

    def test_set_theme_persists_and_updates_object(self):
        self.assertTrue(self.api.set_theme('dark')['ok'])
        self.assertEqual(self.api.get_theme(), {'theme': 'dark'})
        loaded, _ = config.load_or_create(self.config_path)
        self.assertEqual(loaded.ui_theme, 'dark')

    def test_set_theme_rejects_invalid(self):
        self.assertFalse(self.api.set_theme('magic')['ok'])
        self.assertEqual(self.api.get_theme(), {'theme': 'system'})
        loaded, _ = config.load_or_create(self.config_path)
        self.assertEqual(loaded.ui_theme, 'system')  # 未落盘

    def test_background_color_follows_theme(self):
        self.assertEqual(self.api.background_color(), '#FFFFFF')
        self.api.set_theme('dark')
        self.assertEqual(self.api.background_color(), '#0f172a')


class UiApiManualCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, 'config.ini')
        self.login_now_event = threading.Event()
        self.requests = queue.Queue()
        self.api = UiApi(
            _filled_config(), AppStatus(), threading.Event(),
            self.login_now_event, TimerState(), self.config_path,
            check_requests=self.requests)

    def test_trigger_login_enqueues_dashboard_source(self):
        self.assertTrue(self.api.trigger_login()['ok'])
        self.assertEqual(self.requests.get_nowait(), 'dashboard')
        self.assertTrue(self.login_now_event.is_set())

    def test_trigger_login_without_queue_still_wakes(self):
        # check_requests 缺省 None（如旧调用方）：退回旧行为，只置事件
        api = UiApi(_filled_config(), AppStatus(), threading.Event(),
                    self.login_now_event, TimerState(), self.config_path)
        self.assertTrue(api.trigger_login()['ok'])
        self.assertTrue(self.login_now_event.is_set())


class UiApiWindowLifecycleTests(unittest.TestCase):
    """低占用窗口生命周期：三种模式收起、延迟销毁、位置恢复、未显示防阻塞。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = os.path.join(self._tmp.name, 'config.ini')
        self.exit_reasons = []
        self.api = UiApi(
            _filled_config(), AppStatus(), threading.Event(),
            threading.Event(), TimerState(), self.config_path,
            exit_cb=self.exit_reasons.append)

    def _bind_dashboard(self, shown=True):
        window = _FakeWindow(x=136, y=72)
        window.events.closing += self.api.on_dashboard_closing  # 与 open_dashboard 接线一致
        if shown:
            window.events.shown.set()
        self.api._dashboard_window = window
        self.api._dashboard_visible = True
        return window

    def _set_mode(self, mode):
        self.api._config.low_memory_mode = mode

    # ---- now 模式：关闭即销毁 ----
    def test_close_dashboard_now_destroys_and_resets(self):
        window = self._bind_dashboard()
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]  # 主窗口常驻，非最后窗口
            self.api.close_dashboard()
        self.assertIn('destroy', window.calls)
        self.assertIsNone(self.api._dashboard_window)

    def test_close_dashboard_now_hides_when_last_window(self):
        window = self._bind_dashboard()
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [window]  # 护栏：绝不让销毁落在最后窗口
            self.api.close_dashboard()
        self.assertIn('hide', window.calls)
        self.assertNotIn('destroy', window.calls)

    def test_open_dashboard_visible_shown_is_harmless(self):
        # 托盘左键只开不收：看板已显示时点击仅重复 show（置前），绝不销毁/收起
        window = self._bind_dashboard()
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]
            self.api.open_dashboard()
        self.assertIn('show', window.calls)
        self.assertNotIn('destroy', window.calls)
        self.assertNotIn('hide', window.calls)
        self.assertIs(self.api._dashboard_window, window)
        self.assertTrue(self.api._dashboard_visible)

    def test_close_settings_now_destroys(self):
        window = _FakeWindow()
        window.events.shown.set()  # 用户打开过设置窗口（已显示）
        window.events.closing += self.api._on_settings_closing
        self.api._settings_window = window
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]
            self.api.close_settings()
        self.assertIn('destroy', window.calls)
        self.assertIsNone(self.api._settings_window)

    def test_stop_page_polling_is_async_never_blocks_caller(self):
        # 回归锁：_stop_page_polling 必须在独立线程执行 evaluate_js。
        # 它会被 FormClosing 处理器在 UI 线程上调用，而 evaluate_js 的
        # 完成回调要等 UI 线程泵消息——同步调用 = 自我死锁（点 X 全卡死）
        window = _FakeWindow()
        landed = threading.Event()
        window.evaluate_js = lambda script: landed.set()
        self.api._stop_page_polling(window)
        self.assertNotIn('js:__setUiVisible(false)', window.calls)  # 调用线程未同步执行
        self.assertTrue(landed.wait(2))  # 异步线程里已落地

    def test_closing_now_mode_user_x_allows_natural_close(self):
        # 用户点 X（非 destroy 触发）：立即模式放行关闭，引用复位
        window = self._bind_dashboard()
        self._set_mode('now')
        self.assertTrue(self.api.on_dashboard_closing())
        self.assertIsNone(self.api._dashboard_window)
        self.assertFalse(self.api._dashboard_visible)

    def test_destroy_invoked_closing_resets_refs(self):
        # destroy() 触发的 closing：_destroying 置位，放行并复位引用
        window = self._bind_dashboard()
        self.api._dashboard_destroying = True
        self.assertTrue(self.api.on_dashboard_closing())
        self.assertIsNone(self.api._dashboard_window)

    # ---- off 模式（默认）：关闭仅隐藏 ----
    def test_close_dashboard_off_hides_and_keeps(self):
        window = self._bind_dashboard()
        self.api.close_dashboard()  # 默认 off
        self.assertIn('hide', window.calls)
        self.assertNotIn('destroy', window.calls)
        self.assertIs(self.api._dashboard_window, window)  # 引用保留，重见即用
        self.assertFalse(self.api._dashboard_visible)

    def test_closing_off_mode_user_x_cancels_and_hides(self):
        window = self._bind_dashboard()
        self.assertFalse(self.api.on_dashboard_closing())  # 取消 FormClosing
        self.assertIn('hide', window.calls)
        self.assertIs(self.api._dashboard_window, window)

    def test_settings_closing_off_cancels_and_hides(self):
        window = _FakeWindow()
        self.api._settings_window = window
        self.assertFalse(self.api._on_settings_closing())
        self.assertIn('hide', window.calls)
        self.assertIs(self.api._settings_window, window)

    def test_settings_closing_invalid_exits_program(self):
        self.api._config.username = ''  # 制造配置问题
        window = _FakeWindow()
        self.api._settings_window = window
        self.assertTrue(self.api._on_settings_closing())
        self.assertEqual(self.exit_reasons, ["配置未完成，程序已退出"])

    # ---- 延迟模式：隐藏 N 分钟后销毁 ----
    def test_delayed_mode_hides_and_schedules(self):
        window = self._bind_dashboard()
        self._set_mode('15')
        self.api.close_dashboard()
        self.assertIn('hide', window.calls)
        self.assertNotIn('destroy', window.calls)
        pending = self.api._pending_destroy.get('dashboard')
        self.assertIsNotNone(pending)
        self.assertIs(pending[0], window)
        self.assertEqual(pending[1].interval, 15 * 60)
        pending[1].cancel()
        self.assertIs(self.api._dashboard_window, window)  # 引用保留

    def test_delayed_destroy_fires_after_timeout(self):
        window = self._bind_dashboard()
        self._set_mode('15')
        self.api.close_dashboard()
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]
            self.api._delayed_destroy('dashboard', window)
        self.assertIn('destroy', window.calls)
        self.assertIsNone(self.api._dashboard_window)
        self.assertNotIn('dashboard', self.api._pending_destroy)

    def test_delayed_destroy_cancelled_by_reopen(self):
        window = self._bind_dashboard()
        self._set_mode('15')
        self.api.close_dashboard()
        self.api.open_dashboard()  # 重见：取消待销毁并显示
        self.assertNotIn('dashboard', self.api._pending_destroy)
        self.assertTrue(self.api._dashboard_visible)
        self.api._delayed_destroy('dashboard', window)  # 迟到的定时器：无效
        self.assertNotIn('destroy', window.calls)
        self.assertIs(self.api._dashboard_window, window)
        self.api._pending_destroy.clear()

    def test_set_low_memory_off_cancels_pending(self):
        window = self._bind_dashboard()
        self._set_mode('15')
        self.api.close_dashboard()
        self.assertTrue(self.api.set_low_memory('off')['ok'])
        self.assertNotIn('dashboard', self.api._pending_destroy)

    def test_set_low_memory_persists_and_rejects_invalid(self):
        self.assertTrue(self.api.set_low_memory('5')['ok'])
        self.assertEqual(self.api._config.low_memory_mode, '5')
        loaded, _ = config.load_or_create(self.config_path)
        self.assertEqual(loaded.low_memory_mode, '5')  # 已落盘
        self.assertFalse(self.api.set_low_memory('999')['ok'])
        self.assertEqual(self.api._config.low_memory_mode, '5')  # 未被污染

    # ---- 位置恢复：修复看板位置乱跳 ----
    def test_position_captured_and_restored_on_recreate(self):
        window = self._bind_dashboard()  # x=136, y=72 且已 shown
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]
            self.api.close_dashboard()
        self.assertEqual(self.api._dashboard_pos, (136, 72))
        with mock.patch('campus_net.ui.webview') as fake_create:
            fake_create.windows = [object()]
            self.api.open_dashboard()
        kwargs = fake_create.create_window.call_args.kwargs
        self.assertEqual(kwargs.get('x'), 136)
        self.assertEqual(kwargs.get('y'), 72)
        self.api._pending_destroy.clear()

    # ---- 未显示防阻塞：destroy 会等 shown 事件，必须规避 ----
    def test_close_before_shown_never_calls_destroy(self):
        window = self._bind_dashboard(shown=False)  # 页面未加载完（如打开后秒关）
        self._set_mode('now')
        with mock.patch('campus_net.ui.webview') as fake_webview:
            fake_webview.windows = [object(), window]
            self.api.close_dashboard()
        self.assertIn('hide', window.calls)
        self.assertNotIn('destroy', window.calls)
        pending = self.api._pending_destroy.get('dashboard')  # 30 秒后再销毁
        self.assertIsNotNone(pending)
        pending[1].cancel()
        self.assertIsNone(self.api._dashboard_pos)  # 从未显示过：无位置可记，重建用默认位置

    def test_open_dashboard_during_loading_cancels_pending(self):
        # 加载中重复点击托盘：无副作用，取消待销毁（loaded 后自动弹出）
        window = self._bind_dashboard(shown=False)
        self._set_mode('now')
        timer = threading.Timer(30, lambda: None)
        timer.daemon = True
        self.api._pending_destroy['dashboard'] = (window, timer)
        self.api.open_dashboard()
        self.assertIs(self.api._dashboard_window, window)
        self.assertTrue(self.api._dashboard_visible)
        self.assertNotIn('dashboard', self.api._pending_destroy)  # 待销毁已取消
        self.assertNotIn('destroy', window.calls)
        self.assertNotIn('hide', window.calls)

    # ---- 通用 ----
    def test_open_dashboard_reopens_hidden_window(self):
        window = _FakeWindow()
        window.events.shown.set()  # off 模式隐藏保活过的窗口
        self.api._dashboard_window = window
        self.api._dashboard_visible = False
        self.api.open_dashboard()
        self.assertTrue(self.api._dashboard_visible)
        self.assertIn('show', window.calls)
        self.assertIn('js:__setUiVisible(true)', window.calls)

    def test_loaded_shows_and_syncs_visibility(self):
        window = _FakeWindow()
        window.events.loaded += self.api._on_dashboard_loaded  # 与 open_dashboard 接线一致
        self.api._dashboard_window = window
        self.api._dashboard_visible = True
        window.events.loaded.fire()
        self.assertIn('show', window.calls)
        self.assertIn('js:__setUiVisible(true)', window.calls)

    def test_open_settings_lazy_creates(self):
        self.assertIsNone(self.api._settings_window)
        with mock.patch('campus_net.ui.open_settings_window') as fake_open:
            result = self.api.open_settings()
        self.assertTrue(result['ok'])
        fake_open.assert_called_once_with(self.api, show_now=True, pos=None)

    def test_open_settings_existing_shows(self):
        window = _FakeWindow()
        self.api._settings_window = window
        result = self.api.open_settings()
        self.assertTrue(result['ok'])
        self.assertIn('show', window.calls)
        self.assertIn('js:__setUiVisible(true)', window.calls)


if __name__ == '__main__':
    unittest.main()
