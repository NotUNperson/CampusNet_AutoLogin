"""daemon 模块的单元测试（ping/HTTP 探测与登录执行器用替身替换）。
运行：python -m unittest discover tests"""
import queue
import threading
import time
import unittest
from unittest import mock
from types import SimpleNamespace

from campus_net import daemon as daemon_mod
from campus_net.config import AppConfig
from campus_net.status import AppStatus, TimerState


def _config():
    return AppConfig(
        username='u', password='p',
        login_url='http://10.1.1.55/', internal_test_url='10.1.1.55',
        external_test_url='www.baidu.com', check_interval=45,
        quick_retry_times=2, max_retries=3, retry_delay=180,
        show_notifications=True, logout_fallback='browser', ui_theme='system',
        username_input_id='username', password_input_id='password',
        login_btn_id='login-account', logout_btn_id='logout')


def _ok(detail='ok'):
    return (True, detail)


def _down(detail='不通'):
    return (False, detail)


class _FakeLogin:
    """登录执行器替身：可控制 execute_login 的返回值。"""

    def __init__(self, result=True):
        self.result = result
        self.calls = 0

    def bind_events(self, window):
        pass

    def execute_login(self, window):
        self.calls += 1
        return self.result


class _FakeWindow:
    def evaluate_js(self, script):
        return None


class DaemonIconTests(unittest.TestCase):
    """图标推送状态机与外网探测级联的行为验证。"""

    def setUp(self):
        self.icon_log = []
        self.login = _FakeLogin()
        self.timer = TimerState()
        self.worker = daemon_mod.DaemonWorker(
            _config(), self.login, AppStatus(),
            threading.Event(), threading.Event(),
            timer=self.timer,
            icon_cb=lambda color, breathing: self.icon_log.append((color, breathing)))

    @mock.patch.object(daemon_mod, 'probe_host', return_value=_ok())
    def test_connected_green_without_timer(self, _):
        self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('green', False))

    @mock.patch.object(daemon_mod, 'probe_host', return_value=_ok())
    def test_connected_blue_with_active_timer(self, _):
        self.timer.start(10)
        self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('blue', False))

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('超时'))
    @mock.patch.object(daemon_mod, 'CONFIRM_GAP', 0)
    @mock.patch.object(daemon_mod, 'LOGIN_RETRY_GAP', 0)
    @mock.patch.object(daemon_mod, 'probe_host',
                       side_effect=[_down(), _down(), _down(), _ok()])
    def test_login_success_stops_breathing_immediately(self, _, __):
        # 外网 3 连确认全失败 + HTTP 也不通 → 登录；内网可达
        self.worker._run_once()
        self.assertTrue(self.login.calls >= 1)
        # 最后一帧必须是非呼吸的连接态颜色（旧 bug：呼吸挂到下一轮）
        color, breathing = self.icon_log[-1]
        self.assertEqual((color, breathing), ('green', False))

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('超时'))
    @mock.patch.object(daemon_mod, 'CONFIRM_GAP', 0)
    @mock.patch.object(daemon_mod, 'LOGIN_RETRY_GAP', 0)
    @mock.patch.object(daemon_mod, 'probe_host',
                       side_effect=[_down(), _down(), _down(), _ok()])
    def test_login_success_blue_with_timer(self, _, __):
        self.timer.start(10)
        self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('blue', False))

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('超时'))
    @mock.patch.object(daemon_mod, 'CONFIRM_GAP', 0)
    @mock.patch.object(daemon_mod, 'LOGIN_RETRY_GAP', 0)
    @mock.patch.object(daemon_mod, 'probe_host',
                       side_effect=[_down(), _down(), _down(), _ok()])
    def test_login_failure_pushes_red(self, _, __):
        self.login.result = False
        self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('red', False))
        self.assertTrue(any(b for _, b in self.icon_log))  # 登录期间有呼吸帧

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('超时'))
    @mock.patch.object(daemon_mod, 'CONFIRM_GAP', 0)
    @mock.patch.object(daemon_mod, 'probe_host',
                       side_effect=[_down(), _down(), _down(), _down()])
    def test_offline_pushes_red(self, _, __):
        self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('red', False))

    def test_exception_recovery_stops_breathing(self):
        # 基色 red（门户拦截）→ 登录呼吸（呼吸不改变基色）→ 异常恢复回到 red 非呼吸
        self.worker._push_icon('red', False)
        self.worker._push_icon('red', True)
        self.worker._recover_from_exception()
        self.assertEqual(self.icon_log[-1], ('red', False))

    def test_backoff_short_circuits_before_full_probe(self):
        # 退避窗口内：单次 ping 失败 → 直接显示退避中，不做完整级联
        self.worker.error_count = self.worker._config.max_retries
        self.worker.last_error_time = __import__('time').time()
        with mock.patch.object(daemon_mod, 'probe_host', return_value=_down()) as m:
            self.worker._run_once()
        self.assertEqual(m.call_count, 1)  # 只有退避快查，没有级联
        self.assertEqual(self.login.calls, 0)
        self.assertEqual(self.icon_log[-1], ('red', False))

    def test_backoff_exits_when_single_ping_recovers(self):
        self.worker.error_count = self.worker._config.max_retries
        self.worker.last_error_time = __import__('time').time()
        with mock.patch.object(daemon_mod, 'probe_host', return_value=_ok()):
            self.worker._run_once()
        self.assertEqual(self.icon_log[-1], ('green', False))


class ExternalProbeCascadeTests(unittest.TestCase):
    """外网在线判定级联（ping → HTTP 兜底 → 连续 ping 确认）。"""

    def setUp(self):
        self.login = _FakeLogin()
        self.worker = daemon_mod.DaemonWorker(
            _config(), self.login, AppStatus(),
            threading.Event(), threading.Event())

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('超时'))
    @mock.patch.object(daemon_mod, 'probe_host',
                       side_effect=[_down(), _ok()])
    def test_single_ping_flap_recovers(self, _, __):
        # 第 1 次 ping 失败、HTTP 也不通、第 2 次 ping 恢复 → 偶发丢包，判定在线
        self.assertEqual(self.worker._check_external_online()[0], True)
        self.assertEqual(self.login.calls, 0)

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_ok('HTTP 200'))
    @mock.patch.object(daemon_mod, 'probe_host', return_value=_down())
    def test_http_fallback_online(self, mock_probe, mock_http):
        # ping 第 1 次失败后立刻 HTTP 兜底：可达即判在线，不等 3 连
        online, summary = self.worker._check_external_online()
        self.assertTrue(online)
        self.assertIn('HTTP', summary)
        self.assertEqual(mock_probe.call_count, 1)  # 不再继续 ping 2/3
        self.assertEqual(mock_http.call_count, 1)
        self.assertEqual(self.login.calls, 0)

    @mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('无 HTTP 响应'))
    @mock.patch.object(daemon_mod, 'CONFIRM_GAP', 0)
    @mock.patch.object(daemon_mod, 'probe_host', return_value=_down())
    def test_all_channels_down_reports_offline(self, _, __):
        online, summary = self.worker._check_external_online()
        self.assertFalse(online)
        self.assertIn('ping 3/3', summary)
        self.assertIn('HTTP', summary)

    def test_cascade_logs_each_attempt(self):
        with mock.patch.object(daemon_mod, 'http_reachable', return_value=_down('x')), \
                mock.patch.object(daemon_mod, 'probe_host', return_value=_down('超时')):
            with self.assertLogs(level='INFO') as captured:
                self.worker._check_external_online()
        text = '\n'.join(captured.output)
        self.assertIn('第 1/3 次不通', text)
        self.assertIn('尝试 HTTP 兜底', text)
        self.assertIn('HTTP 兜底不通', text)
        self.assertIn('继续 ping 确认', text)
        self.assertIn('第 2/3 次不通', text)
        self.assertIn('第 3/3 次不通', text)

    def test_http_online_logs_warning(self):
        with mock.patch.object(daemon_mod, 'http_reachable', return_value=_ok('HTTP 200')), \
                mock.patch.object(daemon_mod, 'probe_host', return_value=_down('超时')):
            with self.assertLogs(level='WARNING') as captured:
                self.worker._check_external_online()
        self.assertIn('ICMP 可能被拦截', '\n'.join(captured.output))


class HttpProbeTests(unittest.TestCase):
    """HTTP 兜底探测的分类与异常处理。"""

    def test_classify_connection_failure(self):
        self.assertFalse(daemon_mod.classify_http_probe(None, None, 'a')[0])

    def test_classify_portal_hijack_redirect(self):
        ok, detail = daemon_mod.classify_http_probe(
            302, 'http://192.168.57.33/', 'www.baidu.com')
        self.assertFalse(ok)
        self.assertIn('门户', detail)

    def test_classify_same_host_redirect_online(self):
        ok, _ = daemon_mod.classify_http_probe(
            302, 'https://www.baidu.com/', 'www.baidu.com')
        self.assertTrue(ok)

    def test_classify_redirect_without_host_treated_as_portal(self):
        ok, detail = daemon_mod.classify_http_probe(
            302, '/login.html', 'www.baidu.com')
        self.assertFalse(ok)
        self.assertIn('缺少主机', detail)

    def test_classify_http_error_is_online(self):
        # 4xx/5xx 也是目标服务器的真实应答，说明 HTTP 通道已通
        ok, _ = daemon_mod.classify_http_probe(403, None, 'www.baidu.com')
        self.assertTrue(ok)

    def test_classify_ok(self):
        ok, _ = daemon_mod.classify_http_probe(200, None, 'www.baidu.com')
        self.assertTrue(ok)

    def test_empty_url(self):
        self.assertFalse(daemon_mod.http_reachable('')[0])

    def test_connection_refused_is_down(self):
        ok, detail = daemon_mod.http_reachable('http://127.0.0.1:1/', timeout=1)
        self.assertFalse(ok)
        self.assertTrue(detail)


class ProbeHostTests(unittest.TestCase):
    """probe_host 的通道选择：Windows 只用系统 ICMP，非 Windows 用 ping3。"""

    def test_prefers_system_icmp(self):
        fake = SimpleNamespace(icmp_ping=lambda host, timeout_ms=2000: 0.05)
        with mock.patch.object(daemon_mod, 'winapi', fake):
            ok, detail = daemon_mod.probe_host('x')
        self.assertTrue(ok)
        self.assertIn('系统ICMP', detail)

    def test_windows_system_fail_does_not_recheck_with_ping3(self):
        # 系统 ICMP 与 cmd ping 同源，失败即失败；ping3 复核只会拖慢级联
        fake = SimpleNamespace(icmp_ping=lambda host, timeout_ms=2000: None)
        with mock.patch.object(daemon_mod, 'winapi', fake), \
                mock.patch.object(daemon_mod, 'ping') as ping_mock:
            ok, detail = daemon_mod.probe_host('x')
        self.assertFalse(ok)
        self.assertEqual(detail, '系统ICMP不通')
        ping_mock.assert_not_called()

    def test_non_windows_uses_ping3(self):
        with mock.patch.object(daemon_mod, '_WINDOWS', False), \
                mock.patch.object(daemon_mod, 'ping', return_value=0.03):
            ok, detail = daemon_mod.probe_host('x')
        self.assertTrue(ok)
        self.assertIn('ping3', detail)

    def test_non_windows_ping3_exception_is_reported(self):
        with mock.patch.object(daemon_mod, '_WINDOWS', False), \
                mock.patch.object(daemon_mod, 'ping',
                                  side_effect=OSError('raw socket not allowed')):
            ok, detail = daemon_mod.probe_host('x')
        self.assertFalse(ok)
        self.assertIn('OSError', detail)

    def test_empty_host(self):
        ok, detail = daemon_mod.probe_host('')
        self.assertFalse(ok)
        self.assertIn('为空', detail)


class ManualCheckFeedbackTests(unittest.TestCase):
    """手动检测按来源回发反馈（托盘→气泡，看板→状态轮询）。"""

    def setUp(self):
        self.login = _FakeLogin()
        self.status = AppStatus()
        self.requests = queue.Queue()
        self.notified = []
        self.worker = daemon_mod.DaemonWorker(
            _config(), self.login, self.status,
            threading.Event(), threading.Event(),
            notify=self.notified.append, check_requests=self.requests)

    def test_tray_source_notifies_only(self):
        self.worker._pending_feedback.add('tray')
        self.worker._emit_manual_feedback()
        self.assertEqual(len(self.notified), 1)
        self.assertIn('手动检测完成', self.notified[0])
        self.assertIsNone(self.status.snapshot()['manual_check'])  # 不进看板

    def test_dashboard_source_records_status_only(self):
        self.status.set_state('connected', '网络连接正常')
        self.status.record_campus(True)
        self.worker._pending_feedback.add('dashboard')
        self.worker._emit_manual_feedback()
        self.assertEqual(self.notified, [])  # 不弹气泡
        mc = self.status.snapshot()['manual_check']
        self.assertEqual(mc['source'], 'dashboard')
        self.assertIn('已连接（校园网内）', mc['text'])

    def test_timer_wake_emits_nothing(self):
        # pending 为空（计时器等自动唤醒）不做任何反馈
        self.worker._emit_manual_feedback()
        self.assertEqual(self.notified, [])
        self.assertIsNone(self.status.snapshot()['manual_check'])

    def test_duplicate_sources_deduped(self):
        self.requests.put('tray')
        self.requests.put('tray')
        self.worker._collect_check_requests()
        self.worker._emit_manual_feedback()
        self.assertEqual(len(self.notified), 1)
        self.assertEqual(self.worker._pending_feedback, set())

    def test_connected_with_unknown_campus_omits_suffix(self):
        self.status.set_state('connected', '网络连接正常')
        self.worker._pending_feedback.add('dashboard')
        self.worker._emit_manual_feedback()
        self.assertNotIn('（', self.status.snapshot()['manual_check']['text'])

    def test_paused_skip_dashboard(self):
        self.worker._pause_event.set()
        self.requests.put('dashboard')
        self.worker._login_now_event.set()
        self.worker._drain_paused_requests()
        self.assertFalse(self.worker._login_now_event.is_set())  # 事件被消费
        self.assertIn('已跳过', self.status.snapshot()['manual_check']['text'])

    def test_paused_skip_tray_notifies(self):
        self.worker._pause_event.set()
        self.requests.put('tray')
        self.worker._drain_paused_requests()
        self.assertEqual(self.notified, ['守护已暂停，手动检测已跳过'])

    def test_no_queue_never_breaks(self):
        # --once 模式 check_requests=None：收集为空操作；回发不依赖队列本身
        worker = daemon_mod.DaemonWorker(
            _config(), self.login, self.status,
            threading.Event(), threading.Event(),
            notify=self.notified.append)
        worker._collect_check_requests()
        worker._pending_feedback.add('tray')
        worker._emit_manual_feedback()
        self.assertEqual(len(self.notified), 1)


class NetworkDownTests(unittest.TestCase):
    def test_all_fail_confirms_down(self):
        with mock.patch.object(daemon_mod, 'is_network_available', return_value=False):
            self.assertTrue(daemon_mod.is_network_down_confirmed('x', gap=0))

    def test_any_success_means_still_online(self):
        with mock.patch.object(daemon_mod, 'is_network_available', side_effect=[False, True]):
            self.assertFalse(daemon_mod.is_network_down_confirmed('x', gap=0))


class ManualLogoutResetTests(unittest.TestCase):
    """手动退出结果检查完成后调用窗口复位回调（门户页回空白页，低占用）。"""

    def _run_paused_cycle(self, worker):
        """在暂停态注入 manual_done 事件并跑完一轮恢复循环。"""
        pause = worker._pause_event
        worker._manual_done_event.set()
        with mock.patch.object(daemon_mod, 'is_network_down_confirmed', return_value=True), \
                mock.patch.object(daemon_mod, 'WAIT_SLICE', 0.01):
            pause.set()
            holder = []
            t = threading.Thread(
                target=lambda: holder.append(worker._wait_until_resumed()), daemon=True)
            t.start()
            deadline = time.time() + 2
            while not holder and time.time() < deadline:
                time.sleep(0.01)
            pause.clear()
            t.join(timeout=2)
        return holder

    def test_reset_cb_called_after_manual_done(self):
        calls = []
        worker = daemon_mod.DaemonWorker(
            _config(), _FakeLogin(), AppStatus(),
            threading.Event(), threading.Event(),
            notify=lambda m: None,
            manual_done_event=threading.Event(),
            manual_logout_reset_cb=lambda: calls.append(1))
        holder = self._run_paused_cycle(worker)
        self.assertEqual(holder, [False])  # 正常恢复，未退出
        self.assertEqual(len(calls), 1)

    def test_missing_reset_cb_never_breaks(self):
        worker = daemon_mod.DaemonWorker(
            _config(), _FakeLogin(), AppStatus(),
            threading.Event(), threading.Event(),
            notify=lambda m: None,
            manual_done_event=threading.Event())
        holder = self._run_paused_cycle(worker)
        self.assertEqual(holder, [False])


if __name__ == '__main__':
    unittest.main()
