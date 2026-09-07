"""tray 模块 TrayAnimator 的单元测试。运行：python -m unittest discover tests"""
import unittest

from campus_net.tray import TrayAnimator


class _FakeIcon:
    """模拟 pystray Icon 的 icon 赋值与气泡通知，可注入失败。"""

    def __init__(self):
        self.assignments = 0
        self.balloons = []
        self.fail = False

    @property
    def icon(self):
        return None

    @icon.setter
    def icon(self, value):
        if self.fail:
            raise OSError('boom')
        self.assignments += 1

    def notify(self, message, title=None):
        if self.fail:
            raise OSError('boom')
        self.balloons.append(message)


class TrayAnimatorTests(unittest.TestCase):
    def test_color_change_triggers_redraw(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        animator.set_target('green', False)
        animator._tick_once(icon)
        self.assertEqual(icon.assignments, 1)
        animator.set_target('red', False)
        animator._tick_once(icon)
        self.assertEqual(icon.assignments, 2)

    def test_steady_state_dedup_and_forced_redraw(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        animator.set_target('green', False)
        # 稳态同帧去重生效：仅首帧（换帧）+ 每 FORCE_REDRAW_TICKS tick
        # 一次强制重绘。30 tick = 首帧 1 次 + 强制 3 次 = 4 次赋值。
        total_ticks = TrayAnimator.FORCE_REDRAW_TICKS * 3
        for _ in range(total_ticks):
            animator._tick_once(icon)
        self.assertEqual(icon.assignments, 1 + total_ticks // TrayAnimator.FORCE_REDRAW_TICKS)

    def test_failure_retries_next_tick(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        icon.fail = True
        animator.set_target('green', False)
        animator._tick_once(icon)
        self.assertEqual(icon.assignments, 0)  # 失败不计入成功账本
        icon.fail = False
        animator._tick_once(icon)
        self.assertEqual(icon.assignments, 1)  # 下一轮自动重试

    def test_stop_makes_tick_report_stop(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        animator.stop()
        self.assertTrue(animator._tick_once(icon))
        self.assertEqual(icon.assignments, 0)

    def test_breathing_draws_each_tick(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        animator.set_target('red', True)
        for _ in range(3):
            animator._tick_once(icon)
        self.assertEqual(icon.assignments, 3)

    def test_balloon_sent_serially_and_cleared(self):
        animator = TrayAnimator()
        icon = _FakeIcon()
        animator.set_balloon('测试气泡')
        animator._tick_once(icon)
        self.assertEqual(icon.balloons, ['测试气泡'])
        # 已消费，下一 tick 不重复发送
        animator._tick_once(icon)
        self.assertEqual(len(icon.balloons), 1)


if __name__ == '__main__':
    unittest.main()
