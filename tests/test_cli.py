"""app.parse_args 的单元测试。"""
import unittest

from campus_net.app import parse_args


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        args = parse_args([])
        self.assertFalse(args.once)
        self.assertIsNone(args.timer)

    def test_once_and_timer(self):
        args = parse_args(['--once', '--timer', '120'])
        self.assertTrue(args.once)
        self.assertEqual(args.timer, 120)

    def test_timer_zero_is_parsed_here_and_clamped_by_timer_state(self):
        # 参数层只负责解析；取值下限由 TimerState.start 钳制为 1 分钟
        args = parse_args(['--timer', '0'])
        self.assertEqual(args.timer, 0)

    def test_until_valid(self):
        args = parse_args(['--until', '22:30'])
        self.assertEqual(args.until, (22, 30))

    def test_until_single_digit_hour(self):
        args = parse_args(['--until', '7:05'])
        self.assertEqual(args.until, (7, 5))

    def test_until_invalid_format(self):
        with self.assertRaises(SystemExit):
            parse_args(['--until', '25:00'])

    def test_timer_and_until_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            parse_args(['--timer', '30', '--until', '22:30'])


if __name__ == '__main__':
    unittest.main()
