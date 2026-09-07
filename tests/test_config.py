"""config 模块的单元测试。运行：python -m unittest discover tests"""
import os
import tempfile
import unittest

from campus_net import config


def _filled_config():
    """一份填写完整、应当通过校验的配置。"""
    return config.AppConfig(
        username='20210001', password='secret',
        login_url='http://10.1.1.55/', internal_test_url='10.1.1.55',
        external_test_url='www.baidu.com', check_interval=45,
        quick_retry_times=2, max_retries=3, retry_delay=180,
        show_notifications=True, logout_fallback='browser', ui_theme='system',
        username_input_id='username', password_input_id='password',
        login_btn_id='login-account', logout_btn_id='logout')


def _form_data():
    """设置窗口表单提交的一份合法数据。"""
    return {
        'username': '20210001', 'password': 'secret',
        'login_url': 'http://10.1.1.55/', 'internal_test_url': '10.1.1.55',
        'external_test_url': 'www.baidu.com', 'check_interval': '60',
        'quick_retry_times': '2', 'max_retries': '3', 'retry_delay': '180',
        'show_notifications': True, 'logout_fallback': 'browser',
        'username_input_id': 'username', 'password_input_id': 'password',
        'login_btn_id': 'login-account', 'logout_btn_id': 'logout',
    }


class LoadOrCreateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'config.ini')

    def test_first_run_creates_file_and_reports_flag(self):
        app_config, is_first_run = config.load_or_create(self.path)
        self.assertTrue(is_first_run)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(app_config.external_test_url, 'www.baidu.com')
        self.assertEqual(app_config.username_input_id, 'username')
        self.assertEqual(app_config.login_btn_id, 'login-account')

    def test_existing_file_missing_keys_are_backfilled(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write("[System]\napp_signature = campusnet_autologin_v1\n")
        app_config, is_first_run = config.load_or_create(self.path)
        self.assertFalse(is_first_run)
        self.assertEqual(app_config.check_interval, 45)
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('login_url', f.read())

    def test_invalid_int_raises_config_error(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write("[Settings]\ncheck_interval = abc\n")
        with self.assertRaises(config.ConfigError):
            config.load_or_create(self.path)


class SaveAndParseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'config.ini')

    def test_save_and_load_round_trip(self):
        cfg = _filled_config()
        cfg.show_notifications = False
        cfg.check_interval = 60
        config.save_config(cfg, self.path)
        loaded, is_first_run = config.load_or_create(self.path)
        self.assertFalse(is_first_run)
        self.assertEqual(loaded.username, '20210001')
        self.assertEqual(loaded.check_interval, 60)
        self.assertEqual(loaded.login_btn_id, 'login-account')
        self.assertFalse(loaded.show_notifications)
        self.assertEqual(config.validate(loaded), [])

    def test_parse_form_valid(self):
        parsed, problems = config.parse_form(_form_data())
        self.assertEqual(problems, [])
        self.assertEqual(parsed['check_interval'], 60)
        self.assertTrue(parsed['show_notifications'])

    def test_parse_form_invalid_int(self):
        data = _form_data()
        data['check_interval'] = 'abc'
        parsed, problems = config.parse_form(data)
        self.assertIsNone(parsed)
        self.assertEqual(len(problems), 1)
        self.assertIn('check_interval', problems[0])

    def test_parse_form_missing_required(self):
        data = _form_data()
        data['username'] = ''
        parsed, problems = config.parse_form(data)
        self.assertIsNone(parsed)
        joined = '\n'.join(problems)
        self.assertIn('username', joined)

    def test_parse_form_rejects_non_dict(self):
        parsed, problems = config.parse_form("not a dict")
        self.assertIsNone(parsed)
        self.assertEqual(len(problems), 1)

    def test_logout_fallback_default_is_browser(self):
        app_config, _ = config.load_or_create(self.path)
        self.assertEqual(app_config.logout_fallback, 'browser')

    def test_logout_fallback_round_trip(self):
        cfg = _filled_config()
        cfg.logout_fallback = 'window'
        config.save_config(cfg, self.path)
        loaded, _ = config.load_or_create(self.path)
        self.assertEqual(loaded.logout_fallback, 'window')

    def test_logout_fallback_invalid_choice(self):
        cfg = _filled_config()
        cfg.logout_fallback = 'magic'
        problems = config.validate(cfg)
        self.assertEqual(len(problems), 1)
        self.assertIn('logout_fallback', problems[0])

    def test_logout_fallback_manual_is_valid(self):
        cfg = _filled_config()
        cfg.logout_fallback = 'manual'
        self.assertEqual(config.validate(cfg), [])

    def test_ui_theme_default_is_system(self):
        app_config, _ = config.load_or_create(self.path)
        self.assertEqual(app_config.ui_theme, 'system')

    def test_ui_theme_round_trip(self):
        cfg = _filled_config()
        cfg.ui_theme = 'dark'
        config.save_config(cfg, self.path)
        loaded, _ = config.load_or_create(self.path)
        self.assertEqual(loaded.ui_theme, 'dark')

    def test_ui_theme_invalid_clamped_to_system(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write("[System]\napp_signature = campusnet_autologin_v1\n"
                    "[Settings]\nui_theme = magic\n")
        app_config, _ = config.load_or_create(self.path)
        self.assertEqual(app_config.ui_theme, 'system')

    def test_parse_form_rejects_bad_logout_fallback(self):
        data = _form_data()
        data['logout_fallback'] = 'magic'
        parsed, problems = config.parse_form(data)
        self.assertIsNone(parsed)
        self.assertIn('logout_fallback', '\n'.join(problems))


class ValidateTests(unittest.TestCase):
    def test_filled_config_has_no_problems(self):
        self.assertEqual(config.validate(_filled_config()), [])

    def test_empty_required_fields_reported(self):
        cfg = _filled_config()
        cfg.username = ''
        cfg.password = ''
        cfg.login_url = ''
        cfg.internal_test_url = ''
        problems = config.validate(cfg)
        self.assertEqual(len(problems), 4)
        joined = '\n'.join(problems)
        for field in ('username', 'password', 'login_url', 'internal_test_url'):
            self.assertIn(field, joined)

    def test_login_url_scheme_is_checked(self):
        cfg = _filled_config()
        cfg.login_url = '10.1.1.55/portal'
        problems = config.validate(cfg)
        self.assertEqual(len(problems), 1)
        self.assertIn('http', problems[0])

    def test_check_interval_must_be_positive(self):
        cfg = _filled_config()
        cfg.check_interval = 0
        self.assertEqual(len(config.validate(cfg)), 1)


if __name__ == '__main__':
    unittest.main()
