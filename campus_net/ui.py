"""设置窗口与迷你看板：内嵌 HTML 页面 + pywebview JS 桥。

两个窗口共用一个 UiApi 桥对象：
- 设置窗口：状态栏 + 配置表单 + 计时卡片 + 新手引导；保存经 Python 侧
  二次校验后就地更新共享 AppConfig（热生效）并写回 config.ini。
  关闭行为（低占用模式 low_memory_mode）：off=仅隐藏（重建时重见）/
  now=销毁（下次打开懒重建）/ N=隐藏 N 分钟后销毁；配置无效时关闭退出程序。
- 迷你看板：托盘左键弹出/置前（只开不收，收起走看板 X/关闭按钮）；状态 +
  计时器 + 快捷操作；关闭行为同设置窗口（按低占用模式），重建时恢复上次位置。
两窗口页面隐藏时轮询全部跳过（页内 __cnVisible 开关，显隐时同步）。
"""
import logging
import re
import threading
from dataclasses import asdict

import webview

from .config import (LOW_MEMORY_CHOICES, UI_THEME_CHOICES, apply_form, parse_form,
                     validate)
from .config import save_config as write_config_file
from .ui_html import dashboard_html, settings_html

_WINDOW_TITLE = '校园网守护 - 设置'
_DASHBOARD_TITLE = '校园网守护 - 看板'

_TIMER_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


class UiApi:
    """pywebview js_api：设置窗口与看板页面通过 window.pywebview.api 调用。"""

    def __init__(self, config, app_status, pause_event, login_now_event, timer, config_path,
                 exit_cb=None, logout_now_event=None, check_requests=None):
        self._config = config
        self._status = app_status
        self._pause_event = pause_event
        self._login_now_event = login_now_event
        self._logout_now_event = logout_now_event
        self._check_requests = check_requests
        self._timer = timer
        self._config_path = config_path
        self._exit_cb = exit_cb or (lambda reason: None)
        self._settings_window = None
        self._dashboard_window = None
        self._dashboard_visible = False
        # 低占用模式状态：销毁防重入标志 + 延迟销毁定时器 + 重建时的窗口位置
        self._dashboard_destroying = False
        self._settings_destroying = False
        self._pending_destroy = {}   # kind -> (window, Timer)，重见时取消
        self._dashboard_pos = None   # (x, y) 上次收起前的位置，重建时恢复
        self._settings_pos = None

    def bind_settings(self, window):
        self._settings_window = window

    # ---- 状态 / 配置 ----
    def get_config(self):
        return asdict(self._config)

    def get_status(self):
        return self._status.snapshot()

    def save_config(self, data):
        """校验并保存表单；通过后就地更新共享配置并写回 ini（热生效）。"""
        parsed, problems = parse_form(data)
        if problems:
            return {'ok': False, 'problems': problems}
        apply_form(self._config, parsed)
        write_config_file(self._config, self._config_path)
        logging.info("配置已通过设置窗口保存并即时生效")
        return {'ok': True, 'problems': []}

    def trigger_login(self):
        """手动立即检测：请求带来源入队，守护循环完成后按来源回发反馈。"""
        if self._check_requests is not None:
            self._check_requests.put('dashboard')
        self._login_now_event.set()
        logging.info("已触发立即检测")
        return {'ok': True}

    def toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            logging.info("守护已恢复（看板）")
        else:
            self._pause_event.set()
            logging.info("守护已暂停（看板）")
        return {'ok': True}

    def request_logout(self):
        """一键退出登录：由守护循环执行，成功后自动暂停守护。"""
        if self._logout_now_event is None:
            return {'ok': False, 'problem': '当前运行模式不支持退出登录'}
        self._logout_now_event.set()
        logging.info("已请求退出登录")
        return {'ok': True}

    # ---- 计时器 ----
    def get_timer(self):
        return {
            'active': self._timer.is_active(),
            'remaining_text': self._timer.remaining_text(),
        }

    def _wake_daemon(self):
        """唤醒守护循环立即重评一轮（计时器/图标状态即时生效）。"""
        if self._login_now_event is not None:
            self._login_now_event.set()

    def get_theme(self):
        return {'theme': self._config.ui_theme}

    def set_theme(self, theme):
        """切换界面主题并持久化；两个窗口经 1 秒轮询自动跟随。"""
        theme = (theme or '').strip()
        if theme not in UI_THEME_CHOICES:
            return {'ok': False, 'problem': '主题应为 system / light / dark'}
        if theme != self._config.ui_theme:
            self._config.ui_theme = theme
            write_config_file(self._config, self._config_path)
            logging.info("界面主题已切换为 %s", theme)
        return {'ok': True}

    def background_color(self):
        """窗口创建参数：深色主题下用深色底，避免启动白闪。"""
        return '#0f172a' if self._config.ui_theme == 'dark' else '#FFFFFF'

    def timer_start(self, minutes):
        try:
            minutes_int = int(str(minutes).strip())
        except (TypeError, ValueError):
            return {'ok': False, 'problem': '分钟数无效'}
        if minutes_int < 1:
            return {'ok': False, 'problem': '分钟数至少为 1'}
        self._timer.start(minutes_int)
        # 唤醒守护循环：托盘图标立即变蓝，不必等下一检测周期
        self._wake_daemon()
        logging.info("计时器已启动：%s 分钟后自动退出", minutes_int)
        return {'ok': True}

    def timer_until(self, time_str):
        """到点退出：HH:MM 已过则自动次日生效。"""
        match = _TIMER_TIME_RE.match((time_str or '').strip())
        if not match:
            return {'ok': False, 'problem': '时间格式应为 HH:MM（24 小时制），如 22:30'}
        self._timer.start_until(int(match.group(1)), int(match.group(2)))
        self._wake_daemon()
        logging.info("计时器已设置：将在 %s 自动退出", time_str)
        return {'ok': True}

    def timer_extend(self, minutes=30):
        try:
            minutes_int = int(str(minutes).strip())
        except (TypeError, ValueError):
            return {'ok': False, 'problem': '分钟数无效'}
        if minutes_int < 1:
            return {'ok': False, 'problem': '分钟数至少为 1'}
        if not self._timer.extend(minutes_int):
            return {'ok': False, 'problem': '计时器未启动，请先开始计时'}
        logging.info("计时器已延长 %s 分钟", minutes_int)
        return {'ok': True}

    def timer_cancel(self):
        self._timer.cancel()
        # 唤醒守护循环：图标立即从蓝色恢复常规色
        self._wake_daemon()
        logging.info("计时器已取消")
        return {'ok': True}

    # ---- 低占用模式 ----
    def _low_memory_mode(self):
        mode = getattr(self._config, 'low_memory_mode', 'off')
        return mode if mode in LOW_MEMORY_CHOICES else 'off'

    def get_low_memory(self):
        return self._low_memory_mode()

    def set_low_memory(self, mode):
        """托盘菜单切换低占用模式并持久化（即时生效）。"""
        if mode not in LOW_MEMORY_CHOICES:
            return {'ok': False, 'problem': '低占用模式应为 off / now / 5 / 15 / 30'}
        if mode != self._config.low_memory_mode:
            self._config.low_memory_mode = mode
            write_config_file(self._config, self._config_path)
            logging.info("低占用模式已切换为 %s", mode)
        if mode == 'off':
            self._cancel_pending_destroy('dashboard')
            self._cancel_pending_destroy('settings')
        return {'ok': True}

    def _cancel_pending_destroy(self, kind):
        pending = self._pending_destroy.pop(kind, None)
        if pending is not None:
            pending[1].cancel()

    def _schedule_delayed_destroy(self, kind, window):
        """延迟低占用模式：隐藏 N 分钟后仍未见则销毁；重见时取消。"""
        self._cancel_pending_destroy(kind)
        mode = self._low_memory_mode()
        if mode in ('off', 'now'):
            return
        timer = threading.Timer(int(mode) * 60, self._delayed_destroy, args=(kind, window))
        timer.daemon = True
        self._pending_destroy[kind] = (window, timer)
        timer.start()
        logging.info("低占用：%s 将在隐藏 %s 分钟后自动销毁", kind, mode)

    def _delayed_destroy(self, kind, window):
        """定时器回调：窗口仍处于隐藏待销毁态且未被重建时执行销毁。"""
        pending = self._pending_destroy.get(kind)
        if pending is None or pending[0] is not window:
            return
        del self._pending_destroy[kind]
        current = self._dashboard_window if kind == 'dashboard' else self._settings_window
        if current is not window:
            return
        logging.info("低占用：%s 已隐藏超时，自动销毁", kind)
        self._destroy_ui_window(kind, window)

    def _remember_position(self, kind, window):
        """收起前记录窗口位置，重建时恢复（修复看板位置乱跳）。"""
        try:
            if window.events.shown.is_set():
                setattr(self, f'_{kind}_pos', (window.x, window.y))
        except Exception:
            pass  # 位置读取失败不影响收起

    def _forget_window(self, kind):
        if kind == 'dashboard':
            self._dashboard_visible = False
            self._dashboard_window = None
        else:
            self._settings_window = None

    def _destroy_ui_window(self, kind, window):
        """销毁看板/设置窗口释放浏览器开销（低占用核心路径）。

        销毁前先停页面轮询；窗口从未显示过时 destroy() 会等 shown 事件
        （最长 20 秒），故该情形回退隐藏并在 30 秒后销毁。护栏：绝不让
        销毁落在最后一个窗口上（WebView2 teardown 崩溃只在最后窗口路径，
        主窗口常驻故恒不触发）。
        """
        self._cancel_pending_destroy(kind)
        self._remember_position(kind, window)
        self._stop_page_polling(window)
        if len(webview.windows) <= 1:
            window.hide()
            return
        if not window.events.shown.is_set():
            window.hide()
            self._cancel_pending_destroy(kind)
            timer = threading.Timer(30, self._delayed_destroy, args=(kind, window))
            timer.daemon = True
            self._pending_destroy[kind] = (window, timer)
            timer.start()
            return
        setattr(self, f'_{kind}_destroying', True)
        try:
            window.destroy()   # FormClosing 回调里放行并复位引用
        finally:
            setattr(self, f'_{kind}_destroying', False)

    def _retire_window(self, kind):
        """按低占用模式收起窗口：off → 隐藏；now → 销毁；N → 隐藏 + 定时销毁。"""
        window = self._dashboard_window if kind == 'dashboard' else self._settings_window
        if window is None:
            return
        if self._low_memory_mode() == 'now':
            self._forget_window(kind)
            self._destroy_ui_window(kind, window)
            return
        if kind == 'dashboard':
            self._dashboard_visible = False
        self._stop_page_polling(window)
        self._remember_position(kind, window)
        window.hide()
        self._schedule_delayed_destroy(kind, window)

    # ---- 窗口操作 ----
    def _apply_page_visible(self, window):
        """页面置可见并立即刷一轮（低占用：隐藏期间轮询全部跳过）。

        evaluate_js 失败（页面尚未加载完）只影响首轮刷新时机，
        页面自身的初始刷新会兜底。
        """
        try:
            window.evaluate_js('__setUiVisible(true)')
        except Exception as exc:
            logging.debug("页面可见标志同步失败（页面未就绪）: %s", exc)

    def _stop_page_polling(self, window):
        """收起/销毁前停掉页面轮询，避免在途请求落到已关闭窗口（仅日志噪音）。"""
        try:
            window.evaluate_js('__setUiVisible(false)')
        except Exception:
            pass  # 页面未加载完时本就无轮询在跑

    def open_settings(self):
        """打开设置：懒创建（低占用——按模式销毁，打开时重建/重见并取消待销毁）。"""
        if self._settings_window is None:
            open_settings_window(self, show_now=True, pos=self._settings_pos)
            return {'ok': True}
        self._cancel_pending_destroy('settings')
        try:
            self._apply_page_visible(self._settings_window)
            self._settings_window.show()
            return {'ok': True}
        except Exception:
            return {'ok': False}

    def close_settings(self):
        """设置页「关闭」按钮：按低占用模式收起；配置未完成时退出程序。"""
        window = self._settings_window
        if window is None:
            return
        if validate(self._config):
            logging.info("配置未完成时关闭设置窗口，程序即将退出")
            self._exit_cb("配置未完成，程序已退出")
            return
        self._retire_window('settings')

    def close_dashboard(self):
        """看板「关闭」按钮：按低占用模式收起（off 隐藏 / now 销毁 / N 定时销毁）。"""
        self._retire_window('dashboard')

    # ---- 窗口生命周期 ----
    def _on_settings_closing(self):
        """设置窗口 FormClosing（X 按钮 / destroy 触发）：按低占用模式收尾。

        配置无效 → 统一退出流程。_destroying 置位 = 程序 destroy() 触发的
        内部关闭：放行并复位引用。立即模式：放行自然关闭（等价销毁，
        主窗口常驻、永不为最后窗口，清理安全）。off/延迟：取消 FormClosing
        改隐藏（延迟模式再排定时销毁）。
        """
        if validate(self._config):
            logging.info("配置未完成时关闭设置窗口，程序即将退出")
            self._exit_cb("配置未完成，程序已退出")
            return True
        if self._settings_destroying:
            self._settings_window = None
            return True
        window = self._settings_window
        if self._low_memory_mode() == 'now':
            self._settings_window = None
            if window is not None:
                self._remember_position('settings', window)
            return True
        if window is not None:
            self._stop_page_polling(window)
            self._remember_position('settings', window)
            window.hide()
            self._schedule_delayed_destroy('settings', window)
        return False

    def on_dashboard_closing(self):
        """看板 FormClosing（用户点 X）：按低占用模式处理。

        _destroying 置位 = 程序 destroy() 触发的内部关闭：放行并复位引用。
        立即模式：放行自然关闭（等价销毁）。off/延迟：取消 FormClosing 改
        隐藏（延迟模式再排定时销毁）。
        """
        if self._dashboard_destroying:
            self._forget_window('dashboard')
            return True
        window = self._dashboard_window
        if self._low_memory_mode() == 'now':
            self._forget_window('dashboard')
            if window is not None:
                self._remember_position('dashboard', window)
            return True
        self._dashboard_visible = False
        if window is not None:
            self._stop_page_polling(window)
            self._remember_position('dashboard', window)
            window.hide()
            self._schedule_delayed_destroy('dashboard', window)
        return False

    def _on_dashboard_loaded(self):
        # 用局部变量避免与收起操作的竞态：两次读取 self._dashboard_window
        # 之间引用可能被置 None（打开后秒关），曾致 AttributeError 并干扰
        # 后续 loaded 事件派发
        window = self._dashboard_window
        if window is None or not self._dashboard_visible:
            return
        pending = self._pending_destroy.get('dashboard')
        if pending is not None and pending[0] is window:
            return  # 加载期间已被收起（待销毁）：不再自动弹出
        self._apply_page_visible(window)
        window.show()

    def open_dashboard(self):
        """托盘左键回调：弹出/置前看板——只开不收，收起走看板上的 X/关闭按钮。

        低占用模式销毁后此处懒重建（恢复上次位置）；页面加载中重复点击
        无副作用（取消待销毁，loaded 后自动弹出）。
        """
        window = self._dashboard_window
        if window is None:
            kwargs = {}
            if self._dashboard_pos:
                kwargs = {'x': self._dashboard_pos[0], 'y': self._dashboard_pos[1]}
            window = webview.create_window(
                title=_DASHBOARD_TITLE, html=dashboard_html(), js_api=self,
                width=380, height=700, min_size=(340, 560),
                background_color=self.background_color(), hidden=True, **kwargs)
            self._dashboard_window = window
            window.events.loaded += self._on_dashboard_loaded
            window.events.closing += self.on_dashboard_closing
            self._dashboard_visible = True
            logging.info("迷你看板已创建")
            return
        self._cancel_pending_destroy('dashboard')
        self._dashboard_visible = True
        if window.events.shown.is_set():
            self._apply_page_visible(window)
            window.show()
        # 页面尚未加载完：loaded 回调会自动弹出，此处无需动作


def open_settings_window(api, show_now=False, pos=None):
    """创建设置窗口；show_now=False 仅预建隐藏（懒创建场景传 True 立即显示）。

    pos 为 (x, y) 时按上次收起前的位置创建，避免窗口位置乱跳。
    """
    kwargs = {'x': pos[0], 'y': pos[1]} if pos else {}
    window = webview.create_window(
        title=_WINDOW_TITLE, html=settings_html(), js_api=api,
        width=560, height=880, min_size=(480, 700),
        background_color=api.background_color(), hidden=not show_now, **kwargs)
    api.bind_settings(window)
    if show_now:
        def _on_loaded():
            api._apply_page_visible(window)
            window.show()
        window.events.loaded += _on_loaded
    window.events.closing += api._on_settings_closing
    logging.info("设置窗口已创建")
    return window
