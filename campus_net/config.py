"""配置的读取、写入、表单解析与校验。

本模块不做弹窗、退出等交互动作，只返回数据或抛出 ConfigError，
交互编排统一放在 app.py / ui.py 中，便于单元测试。
AppConfig 实例是全局共享的单一对象：设置窗口保存后就地更新字段，
守护循环每轮读取，从而实现"保存即生效"。
"""
import configparser
import os
from dataclasses import dataclass

APP_SIGNATURE = "campusnet_autologin_v1"  # config.ini 归属校验标识（见 isolate.py）

# 必填文本项：缺失时拒绝启动/保存
_REQUIRED_TEXT_FIELDS = {
    'username': 'Account.username（登录账号）',
    'password': 'Account.password（登录密码）',
    'login_url': 'Settings.login_url（学校认证页地址）',
    'internal_test_url': 'Settings.internal_test_url（内网探测地址，通常是认证服务器 IP）',
}

_INT_FIELDS = {
    'check_interval': 'Settings.check_interval（检测间隔秒数）',
    'quick_retry_times': 'Settings.quick_retry_times（登录额外重试次数）',
    'max_retries': 'Settings.max_retries（连续失败退避阈值）',
    'retry_delay': 'Settings.retry_delay（失败退避等待秒数）',
}

# 设置窗口表单中的文本字段（不含 show_notifications 布尔项）
_TEXT_FORM_FIELDS = (
    'username', 'password', 'login_url', 'internal_test_url', 'external_test_url',
    'username_input_id', 'password_input_id', 'login_btn_id', 'logout_btn_id',
    'logout_fallback',
)

# 退出登录兜底方式的合法取值
LOGOUT_FALLBACK_CHOICES = ('browser', 'window', 'manual', 'none')

# 界面主题的合法取值
UI_THEME_CHOICES = ('system', 'light', 'dark')

# 低占用模式：off=关闭仅隐藏 / now=关闭立即销毁 / '5'|'15'|'30'=隐藏 N 分钟后销毁
LOW_MEMORY_CHOICES = ('off', 'now', '5', '15', '30')


class ConfigError(Exception):
    """配置文件无法解析时抛出。"""


@dataclass
class AppConfig:
    username: str
    password: str
    login_url: str
    internal_test_url: str
    external_test_url: str
    check_interval: int
    quick_retry_times: int
    max_retries: int
    retry_delay: int
    show_notifications: bool
    # 一键退出登录自动确认失败时的兜底方式：
    # browser=系统浏览器 / window=应用内可见窗口 / none=不兜底
    logout_fallback: str
    # 界面主题：system=跟随系统 / light=浅色 / dark=深色
    ui_theme: str
    # 门户页面元素 ID：不同学校门户不同时可在设置窗口/config.ini 中修改
    username_input_id: str
    password_input_id: str
    login_btn_id: str
    logout_btn_id: str
    # 低占用模式（托盘右键切换）：off=关闭仅隐藏 / now=关闭立即销毁 / N 分钟后自动销毁
    # 带默认值须置于字段末尾（dataclass 规则），新配置键缺失时回落禁用
    low_memory_mode: str = 'off'


DEFAULT_CONFIG = {
    'System': {
        'app_signature': APP_SIGNATURE,
    },
    'Account': {
        'username': '',
        'password': '',
    },
    'Settings': {
        'login_url': '',
        'internal_test_url': '',
        'external_test_url': 'www.baidu.com',
        'check_interval': '45',
        'quick_retry_times': '2',
        'max_retries': '3',
        'retry_delay': '180',
        'show_notifications': 'true',
        'logout_fallback': 'browser',
        'ui_theme': 'system',
        'low_memory_mode': 'off',
        'username_input_id': 'username',
        'password_input_id': 'password',
        'login_btn_id': 'login-account',
        'logout_btn_id': 'logout',
    },
}


def create_default_config(path):
    """写入一份带默认值的全新配置文件。"""
    config = configparser.ConfigParser(interpolation=None)
    for section, options in DEFAULT_CONFIG.items():
        config[section] = options
    with open(path, 'w', encoding='utf-8') as f:
        config.write(f)


def load_or_create(path):
    """读取配置，返回 (AppConfig, is_first_run)。

    首次运行时生成默认配置文件；已有文件缺失的段落/键自动补默认值并回写。
    数值/布尔项格式错误时抛出 ConfigError。
    """
    is_first_run = not os.path.exists(path)
    config = configparser.ConfigParser(interpolation=None)
    if is_first_run:
        create_default_config(path)
    else:
        config.read(path, encoding='utf-8')

    needs_save = False
    for section, options in DEFAULT_CONFIG.items():
        if not config.has_section(section):
            config.add_section(section)
            needs_save = True
        for key, default in options.items():
            if not config.has_option(section, key):
                config.set(section, key, default)
                needs_save = True
    if needs_save:
        with open(path, 'w', encoding='utf-8') as f:
            config.write(f)

    parsed_ints = {}
    for key, label in _INT_FIELDS.items():
        raw = config.get('Settings', key, fallback='')
        try:
            parsed_ints[key] = int(raw.strip())
        except ValueError:
            raise ConfigError(f"{label} 不是有效整数：{raw!r}")

    raw_notifications = config.get('Settings', 'show_notifications', fallback='true')
    try:
        show_notifications = config.getboolean('Settings', 'show_notifications')
    except ValueError:
        raise ConfigError(
            f"Settings.show_notifications（是否显示气泡通知）不是有效布尔值：{raw_notifications!r}")

    ui_theme = config.get('Settings', 'ui_theme', fallback='system').strip()
    if ui_theme not in UI_THEME_CHOICES:
        ui_theme = 'system'  # 外观偏好不阻止启动，非法值回落跟随系统

    low_memory_mode = config.get('Settings', 'low_memory_mode', fallback='off').strip()
    if low_memory_mode not in LOW_MEMORY_CHOICES:
        low_memory_mode = 'off'  # 低占用偏好不阻止启动，非法值回落禁用

    app_config = AppConfig(
        username=config.get('Account', 'username', fallback='').strip(),
        password=config.get('Account', 'password', fallback='').strip(),
        login_url=config.get('Settings', 'login_url', fallback='').strip(),
        internal_test_url=config.get('Settings', 'internal_test_url', fallback='').strip(),
        external_test_url=config.get('Settings', 'external_test_url', fallback='').strip(),
        username_input_id=config.get('Settings', 'username_input_id', fallback='username').strip(),
        password_input_id=config.get('Settings', 'password_input_id', fallback='password').strip(),
        login_btn_id=config.get('Settings', 'login_btn_id', fallback='login-account').strip(),
        logout_btn_id=config.get('Settings', 'logout_btn_id', fallback='logout').strip(),
        show_notifications=show_notifications,
        logout_fallback=config.get('Settings', 'logout_fallback', fallback='browser').strip(),
        ui_theme=ui_theme,
        low_memory_mode=low_memory_mode,
        **parsed_ints,
    )
    return app_config, is_first_run


def validate(app_config):
    """返回配置问题列表（中文描述）；列表为空表示配置可用。"""
    problems = []
    for key, label in _REQUIRED_TEXT_FIELDS.items():
        if not getattr(app_config, key):
            problems.append(f"{label} 为空，必填")
    if app_config.login_url and not app_config.login_url.lower().startswith(('http://', 'https://')):
        problems.append("Settings.login_url（学校认证页地址）应以 http:// 或 https:// 开头")
    if app_config.check_interval <= 0:
        problems.append("Settings.check_interval（检测间隔秒数）应大于 0")
    if app_config.logout_fallback not in LOGOUT_FALLBACK_CHOICES:
        problems.append(
            "Settings.logout_fallback（退出登录兜底方式）应为 browser / window / manual / none")
    return problems


def parse_form(data):
    """解析设置窗口表单提交的 dict，返回 (parsed, problems)。

    只做清洗与校验，不修改任何对象；problems 非空时 parsed 为 None。
    校验全部通过才允许把 parsed 应用到共享配置（见 apply_form）。
    """
    if not isinstance(data, dict):
        return None, ["表单数据格式错误"]
    parsed = {}
    for key in _TEXT_FORM_FIELDS:
        value = data.get(key, '')
        parsed[key] = (value if isinstance(value, str) else str(value)).strip()
    parsed['show_notifications'] = bool(data.get('show_notifications', True))

    problems = []
    for key, label in _INT_FIELDS.items():
        raw = data.get(key, '')
        try:
            parsed[key] = int(str(raw).strip())
        except (TypeError, ValueError):
            problems.append(f"{label} 不是有效整数：{raw!r}")
    if problems:
        return None, problems

    # ui_theme 不在表单字段内（由独立的 set_theme 即时保存），
    # 校验候选配置时以占位值补齐；返回的 parsed 不含它，
    # apply_form 因此不会覆盖已设置的主题。
    problems = validate(AppConfig(**parsed, ui_theme='system'))
    if problems:
        return None, problems
    return parsed, []


def apply_form(app_config, parsed):
    """把 parse_form 的结果就地写入共享配置对象（守护循环下一轮生效）。"""
    for key, value in parsed.items():
        setattr(app_config, key, value)


_CONFIG_HEADER = (
    "# 校园网自动登录守护配置\n"
    "# 必填项：Account.username / Account.password / Settings.login_url / Settings.internal_test_url\n"
    "# 程序运行中修改配置请使用托盘菜单的「打开设置」，保存后立即生效\n"
)


def save_config(app_config, path):
    """把当前配置对象写回 ini 文件。"""
    config = configparser.ConfigParser(interpolation=None)
    config['System'] = {'app_signature': APP_SIGNATURE}
    config['Account'] = {
        'username': app_config.username,
        'password': app_config.password,
    }
    config['Settings'] = {
        'login_url': app_config.login_url,
        'internal_test_url': app_config.internal_test_url,
        'external_test_url': app_config.external_test_url,
        'check_interval': str(app_config.check_interval),
        'quick_retry_times': str(app_config.quick_retry_times),
        'max_retries': str(app_config.max_retries),
        'retry_delay': str(app_config.retry_delay),
        'show_notifications': 'true' if app_config.show_notifications else 'false',
        'logout_fallback': app_config.logout_fallback,
        'ui_theme': app_config.ui_theme,
        'low_memory_mode': app_config.low_memory_mode,
        'username_input_id': app_config.username_input_id,
        'password_input_id': app_config.password_input_id,
        'login_btn_id': app_config.login_btn_id,
        'logout_btn_id': app_config.logout_btn_id,
    }
    with open(path, 'w', encoding='utf-8') as f:
        f.write(_CONFIG_HEADER)
        config.write(f)
