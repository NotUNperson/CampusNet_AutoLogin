"""启动编排：命令行解析 → 环境隔离检查 → 配置 → 窗口 → 托盘 + 守护协调。

窗口分工：
- 主窗口（隐藏）：承载登录页加载与 JS 注入，守护循环的工作窗口
- 设置窗口：状态栏 + 配置表单 + 计时器 + 新手引导；配置无效时启动即弹出
  （首次运行引导），有效时隐藏、可从托盘菜单打开
- 迷你看板：托盘左键弹出/收起（懒创建）

命令行：--once（单次运行，登录成功后退出）、--timer N（守护 N 分钟后退出）
"""
import argparse
import logging
import logging.handlers
import os
import queue
import re
import threading
import time

import webview

from . import __version__, isolate, status as status_mod, ui, winapi
from .config import ConfigError, load_or_create, validate
from .daemon import DaemonWorker
from .login import LoginExecutor
from .paths import CONFIG_FILE, LOG_DIR, LOG_FILE
from .tray import TrayBridge, setup_tray

LOG_FORMAT = '[%(asctime)s] %(levelname)s: %(message)s'
LOG_DATE_FORMAT = '%Y.%m.%d %H:%M:%S'
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='CampusNet', description='校园网自动登录守护')
    parser.add_argument(
        '--once', action='store_true',
        help='单次运行：守护至登录成功（或本就在线）后自动退出')
    parser.add_argument(
        '--timer', type=int, metavar='分钟',
        help='临时模式：守护指定分钟后自动退出，如 --timer 120')
    parser.add_argument(
        '--until', metavar='HH:MM',
        help='临时模式：运行到指定时刻自动退出，如 --until 22:30')
    args = parser.parse_args(argv)
    if args.timer is not None and args.until:
        parser.error('--timer 与 --until 只能二选一')
    if args.until is not None:
        match = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', args.until.strip())
        if not match:
            parser.error('--until 格式应为 HH:MM（24 小时制），如 22:30')
        args.until = (int(match.group(1)), int(match.group(2)))
    return args


def _setup_logging():
    """控制台 + 滚动文件双通道日志；日志目录不可写时仅保留控制台。"""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        logging.getLogger().addHandler(file_handler)
    except OSError as exc:
        logging.warning("无法创建日志文件 (%s)，本次仅输出到控制台", exc)


def _install_console_cleanup(bridge):
    """注册控制台事件清理：Ctrl+C / 关闭终端窗口时先移除托盘图标再退出。

    主线程阻塞在 webview 的 GUI 循环（原生代码）里，Python 级 signal
    处理永远轮不到执行，所以必须在 Win32 层拦截控制台事件。
    """

    def _on_console_event(event_type):
        try:
            logging.info("收到控制台退出事件（type=%s），正在清理", event_type)
            bridge.remove_tray()
        finally:
            os._exit(0)

    if winapi.set_console_ctrl_handler(_on_console_event):
        logging.debug("控制台清理处理器注册成功")
    else:
        logging.debug("控制台清理处理器未注册（无控制台环境，属正常）")


def _make_tray_actions(pause_event, login_now_event, timer, api, check_requests=None):
    """托盘菜单回调集合（在 pystray 线程中执行，需保持轻量）。"""

    def open_dashboard():
        api.open_dashboard()

    def open_settings():
        api.open_settings()

    def trigger_login():
        # 来源 tray：守护循环完成后经气泡播报结果（看板来源走看板内提示）
        if check_requests is not None:
            check_requests.put('tray')
        login_now_event.set()

    def toggle_pause():
        if pause_event.is_set():
            pause_event.clear()
            logging.info("守护已恢复（托盘菜单）")
        else:
            pause_event.set()
            logging.info("守护已暂停（托盘菜单）")

    def timer_extend():
        if not timer.extend(30):
            logging.info("计时器未启动，忽略延长请求")

    def timer_cancel():
        timer.cancel()
        logging.info("计时器已取消（托盘菜单）")

    def set_low_memory(mode):
        api.set_low_memory(mode)

    def get_low_memory():
        return api.get_low_memory()

    def open_logs():
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)

    return {
        'open_dashboard': open_dashboard,
        'open_settings': open_settings,
        'trigger_login': trigger_login,
        'toggle_pause': toggle_pause,
        'timer_extend': timer_extend,
        'timer_cancel': timer_cancel,
        'set_low_memory': set_low_memory,
        'get_low_memory': get_low_memory,
        'open_logs': open_logs,
        'is_paused': pause_event.is_set,
    }


def _coordinator(main_window, worker, config, timer, timer_minutes, timer_until):
    """webview 主线程入口：配置有效前只等待，有效后进入守护循环。"""
    problems = validate(config)
    if not problems:
        logging.info("配置校验通过，直接进入守护循环")
    else:
        logging.info("配置未完成，等待设置窗口保存：%s", "；".join(problems))
        while validate(config):
            time.sleep(1)
        logging.info("配置校验通过，启动守护循环")
    if timer_until:
        # 计时从守护真正开始时起算，等待配置的时间不计入
        timer.start_until(*timer_until)
        logging.info("临时模式：将在 %s 后自动退出", timer.remaining_text())
    elif timer_minutes:
        timer.start(timer_minutes)
        logging.info("临时模式：将在 %s 后自动退出", timer.remaining_text())
    worker.daemon_worker(main_window)


def main(argv=None):
    args = parse_args(argv)
    _setup_logging()
    logging.info("校园网自动登录守护 v%s 启动", __version__)

    # 环境检查最先执行：config.ini 重名/目录杂乱时会把程序迁移到隔离文件夹
    isolate.ensure_runnable_environment()

    try:
        config, _ = load_or_create(CONFIG_FILE)
    except ConfigError as exc:
        winapi.show_error("配置错误", str(exc))
        isolate.delayed_exit(5, 0)

    app_status = status_mod.AppStatus()
    pause_event = threading.Event()
    login_now_event = threading.Event()
    logout_now_event = threading.Event()
    manual_done_event = threading.Event()
    check_requests = queue.Queue()
    timer = status_mod.TimerState()
    timer_minutes = args.timer
    timer_until = args.until
    bridge = TrayBridge(config)
    _install_console_cleanup(bridge)

    manual_window = {}

    def open_manual_logout():
        """打开（或置前）应用内手动退出登录窗口；由守护线程调用。

        用户在窗口内手动注销，点击关闭按钮时窗口只是隐藏（规避
        WebView2 销毁竞态），同时置 manual_done_event 让守护循环
        ping 外网并气泡播报退出结果。
        """
        window = manual_window.get('window')
        try:
            if window is None:
                def _on_manual_closing():
                    window.hide()
                    manual_done_event.set()
                    return False

                window = webview.create_window(
                    title='校园网守护 - 手动退出登录', url=config.login_url,
                    width=920, height=720, hidden=True)
                manual_window['window'] = window
                window.events.loaded += lambda: window.show()
                window.events.closing += _on_manual_closing
                logging.info("已创建应用内手动退出登录窗口")
            else:
                window.load_url(config.login_url)
                window.show()
        except Exception as exc:
            logging.error("打开手动退出窗口失败: %s", exc)
            raise

    def reset_manual_logout():
        """手动退出收尾：窗口回空白页，不让门户 JS 在隐藏窗口里持续空转。

        重新打开时 open_manual_logout 会重新加载门户页，互不影响。
        """
        window = manual_window.get('window')
        if window is not None:
            try:
                window.load_url('about:blank')
            except Exception as exc:
                logging.debug("手动退出窗口回空白页失败: %s", exc)

    def shutdown(reason):
        """计时到期/单次完成的统一退出路径（在守护线程中调用）。

        注意：这里刻意不调用 window.destroy()——从非主线程销毁 pywebview
        窗口会触发 WebView2 清理竞态（FormClosed 事件中 BrowserProcessId
        为 None 的异常弹窗并卡死进程）。先移除托盘图标避免幽灵残影，
        再由 os._exit 随主进程回收全部资源。
        """
        logging.info("%s", reason)
        bridge.notify(reason)
        bridge.remove_tray()
        logging.info("正在退出进程")
        # 略等片刻让气泡通知来得及显示，随后结束整个进程
        threading.Timer(1.5, os._exit, args=(0,)).start()

    login_executor = LoginExecutor(config)
    worker = DaemonWorker(config, login_executor, app_status,
                          pause_event, login_now_event,
                          notify=bridge.notify, timer=timer,
                          once=args.once, shutdown_cb=shutdown,
                          logout_now_event=logout_now_event,
                          icon_cb=bridge.update_icon,
                          manual_logout_cb=open_manual_logout,
                          manual_done_event=manual_done_event,
                          manual_logout_reset_cb=reset_manual_logout,
                          check_requests=check_requests)

    problems = validate(config)
    if args.once:
        if problems:
            winapi.show_error(
                "配置错误",
                "单次运行前请先完成配置：\n- " + "\n- ".join(problems))
            isolate.delayed_exit(5, 1)
        logging.info("单次运行模式：登录成功后自动退出")
    elif problems:
        app_status.set_state(status_mod.WAITING_CONFIG, "尚有必填项未填写")
        logging.info("配置未完成，打开设置窗口引导填写")

    # 主窗口专用于隐藏登录
    main_window = webview.create_window(
        title='CampusNet_Daemon', url='about:blank', hidden=True, min_size=(1, 1))

    if args.once:
        # 单次运行：无托盘、无设置窗口，登录成功后经 shutdown 退出
        webview.start(_coordinator,
                      (main_window, worker, config, timer, timer_minutes, timer_until))
        return

    api = ui.UiApi(config, app_status, pause_event, login_now_event, timer, CONFIG_FILE,
                   exit_cb=shutdown, logout_now_event=logout_now_event,
                   check_requests=check_requests)
    if problems:
        # 低占用：仅首次运行引导时预建设置窗口；正常待机不创建，打开时懒建
        ui.open_settings_window(api, show_now=True)

    actions = _make_tray_actions(pause_event, login_now_event, timer, api,
                                 check_requests=check_requests)
    tray_thread = threading.Thread(
        target=setup_tray, args=(app_status, actions, bridge, timer), name="tray", daemon=True)
    tray_thread.start()

    webview.start(_coordinator,
                  (main_window, worker, config, timer, timer_minutes, timer_until))
    # 只有所有窗口被销毁才会走到这里（异常情况），兜底退出
    logging.info("程序退出")


if __name__ == '__main__':
    main()
