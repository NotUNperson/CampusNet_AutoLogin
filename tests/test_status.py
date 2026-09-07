"""status 模块（AppStatus / TimerState）的单元测试。运行：python -m unittest discover tests"""
import time
import unittest
from datetime import datetime, timedelta

from campus_net.status import AppStatus, TimerState


class TimerStateTests(unittest.TestCase):
    def test_inactive_by_default(self):
        timer = TimerState()
        self.assertFalse(timer.is_active())
        self.assertEqual(timer.remaining_seconds(), 0)
        self.assertFalse(timer.is_expired())
        self.assertFalse(timer.alert_pending())
        self.assertFalse(timer.extend(30))  # 未激活时延长返回 False

    def test_start_and_remaining_text(self):
        timer = TimerState()
        timer.start(90)
        self.assertTrue(timer.is_active())
        remaining = timer.remaining_seconds()
        self.assertGreater(remaining, 89 * 60)
        self.assertLessEqual(remaining, 90 * 60)
        self.assertIn('小时', timer.remaining_text())

    def test_extend_and_cancel(self):
        timer = TimerState()
        timer.start(10)
        before = timer.remaining_seconds()
        self.assertTrue(timer.extend(30))
        self.assertGreater(timer.remaining_seconds(), before + 29 * 60)
        timer.cancel()
        self.assertFalse(timer.is_active())
        self.assertEqual(timer.remaining_seconds(), 0)

    def test_expired(self):
        timer = TimerState()
        timer.start(1)
        timer._deadline = time.time() - 1  # 测试便利：直接把到期时间拨到过去
        self.assertTrue(timer.is_expired())

    def test_alert_pending_fires_once(self):
        timer = TimerState()
        timer.start(60)  # 长计时：阈值为 5 分钟
        timer._deadline = time.time() + 100
        self.assertTrue(timer.alert_pending())
        self.assertFalse(timer.alert_pending())  # 一次性

    def test_short_timer_alerts_at_half_duration(self):
        timer = TimerState()
        timer.start(1)  # 1 分钟：阈值取总时长一半（30 秒）
        timer._deadline = time.time() + 100
        self.assertFalse(timer.alert_pending())  # 100 秒 > 30 秒阈值
        timer._deadline = time.time() + 20
        self.assertTrue(timer.alert_pending())

    def test_extend_beyond_alert_window_resets_alert(self):
        timer = TimerState()
        timer.start(60)
        timer._deadline = time.time() + 100
        self.assertTrue(timer.alert_pending())
        timer.extend(60)  # 延长后离开提醒窗口，提醒标志应重置
        timer._deadline = time.time() + 100  # 再次临近到期
        self.assertTrue(timer.alert_pending())

    def test_start_until_future_today(self):
        timer = TimerState()
        now = datetime.now()
        target = now.replace(second=0, microsecond=0) + timedelta(minutes=5)
        if target <= now:
            target += timedelta(minutes=1)
        timer.start_until(target.hour, target.minute)
        self.assertTrue(timer.is_active())
        remaining = timer.remaining_seconds()
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 6 * 60)

    def test_start_until_past_goes_next_day(self):
        timer = TimerState()
        now = datetime.now()
        timer.start_until(3, 15)
        target = now.replace(hour=3, minute=15, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        expected = (target - now).total_seconds()
        self.assertAlmostEqual(timer.remaining_seconds(), expected, delta=2)

    def test_remaining_text_formats(self):
        timer = TimerState()
        timer.start(120)
        self.assertIn('小时', timer.remaining_text())
        timer.start(5)
        self.assertIn('分', timer.remaining_text())


class AppStatusTests(unittest.TestCase):
    def test_snapshot_defaults_and_updates(self):
        status = AppStatus()
        snap = status.snapshot()
        self.assertEqual(snap['state'], 'waiting_config')
        self.assertEqual(snap['state_text'], '等待配置')
        self.assertEqual(snap['success_count'], 0)
        self.assertIsNone(snap['on_campus'])

        status.set_state('connected', '网络正常')
        status.count_success()
        status.count_fail()
        status.record_check()
        status.record_campus(True)
        snap = status.snapshot()
        self.assertEqual(snap['state'], 'connected')
        self.assertEqual(snap['state_text'], '已连接')
        self.assertEqual(snap['success_count'], 1)
        self.assertEqual(snap['fail_count'], 1)
        self.assertTrue(snap['on_campus'])
        self.assertNotEqual(snap['last_check_time'], '-')

    def test_manual_check_record_and_snapshot(self):
        status = AppStatus()
        self.assertIsNone(status.snapshot()['manual_check'])
        status.record_manual_check('手动检测完成：已连接（校园网内）', 'dashboard')
        snap = status.snapshot()
        mc = snap['manual_check']
        self.assertEqual(mc['seq'], 1)
        self.assertEqual(mc['source'], 'dashboard')
        self.assertIn('已连接', mc['text'])
        self.assertNotEqual(mc['time'], '')

    def test_manual_check_seq_monotonic_and_replaced(self):
        status = AppStatus()
        status.record_manual_check('第一次', 'dashboard')
        status.record_manual_check('第二次', 'dashboard')
        mc = status.snapshot()['manual_check']
        self.assertEqual(mc['seq'], 2)
        self.assertEqual(mc['text'], '第二次')

    def test_timer_is_isolated_per_instance(self):
        first = TimerState()
        second = TimerState()
        first.start(10)
        self.assertTrue(first.is_active())
        self.assertFalse(second.is_active())


if __name__ == '__main__':
    unittest.main()
