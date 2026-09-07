"""系统托盘图标、状态动画、菜单与气泡通知。"""
import logging
import math
import os
import threading
import time

import pystray
from PIL import Image, ImageDraw

from . import winapi
from .paths import LOG_DIR

ICON_NAME = "CampusNetAutoLogin"
ICON_TITLE = "校园网守护中..."

PROMOTE_GUIDE_TEXT = (
    "Windows 默认会把新图标收进任务栏溢出区（^）。要让本工具常显：\n\n"
    "1. 已为你打开系统设置页（也可手动打开 设置 → 个性化 → 任务栏）\n"
    "2. 找到「选择哪些图标显示在任务栏上」（Win11 为「任务栏角溢出」）\n"
    "3. 打开「校园网守护中...」对应的开关\n\n"
    "也可以直接把溢出区里的绿色圆点图标拖到任务栏上。")


def create_tray_icon_image():
    image = Image.new('RGB', (64, 64), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((16, 16, 48, 48), fill=(46, 204, 113))
    return image


ICON_COLORS = {
    "green": (46, 204, 113),   # 在校且网络正常
    "blue": (52, 152, 219),    # 已连接且计时器运行中
    "yellow": (241, 196, 15),  # 已联网但非校园网（内网不通）
    "red": (231, 76, 60),      # 网络异常（离线/门户拦截）
    "gray": (149, 165, 166),   # 等待配置/已暂停
}


class TrayAnimator:
    """托盘图标动画：按状态换色，登录/退出过程中呼吸脉动。

    以约 3fps 重绘 64×64 白底彩点；颜色与呼吸目标由守护循环推送。
    稳态同帧去重以省开销，但每 FORCE_REDRAW_TICKS tick 强制重绘一次，
    用于自愈 pystray NIM_MODIFY 的静默失败；渲染异常时下一 tick
    立即重试。
    """

    INTERVAL = 0.3
    BREATH_STEPS = 8         # 一个呼吸周期的重绘次数
    FORCE_REDRAW_TICKS = 10  # 稳态下强制重绘的周期（约 3 秒）

    def __init__(self):
        self._lock = threading.Lock()
        self._color = "gray"
        self._breathing = False
        self._phase = 0
        self._tick = 0
        self._stop = False
        self._last_drawn = None
        self._fail_count = 0
        self._pending_balloon = None

    def set_target(self, color, breathing):
        with self._lock:
            self._color = color if color in ICON_COLORS else "gray"
            self._breathing = bool(breathing)

    def set_balloon(self, text):
        """排队一条气泡通知，由动画线程在下一 tick 串行发送。

        气泡（NIM_MODIFY/NIF_INFO）与图标更新操作同一个 NOTIFYICONDATA，
        且 pystray 内部无锁——统一收敛到动画线程执行以消除竞态。
        """
        with self._lock:
            self._pending_balloon = text

    def stop(self):
        with self._lock:
            self._stop = True

    def _render(self, color_name, breathing, phase):
        color = ICON_COLORS.get(color_name, ICON_COLORS["gray"])
        image = Image.new('RGB', (64, 64), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        if breathing:
            radius = 16 + 3 * math.sin(2 * math.pi * phase / self.BREATH_STEPS)
        else:
            radius = 16
        r = max(1, int(round(radius)))
        draw.ellipse((32 - r, 32 - r, 32 + r, 32 + r), fill=color)
        return image

    def _tick_once(self, icon):
        """执行一次重绘判定；返回 True 表示需要停止。

        帧与上次一致时跳过重绘；但每 FORCE_REDRAW_TICKS tick 强制
        重绘一次，自愈 pystray NIM_MODIFY 的静默失败。渲染异常时
        清空去重账本，下一 tick 立即重试。
        """
        with self._lock:
            if self._stop:
                return True
            color, breathing = self._color, self._breathing
            phase = self._phase
            self._phase = (self._phase + 1) % self.BREATH_STEPS
            self._tick += 1
            force = self._tick % self.FORCE_REDRAW_TICKS == 0

        frame = (color, breathing, phase if breathing else 0)
        if force or frame != self._last_drawn:
            try:
                icon.icon = self._render(color, breathing, phase)
                self._last_drawn = frame
                self._fail_count = 0
            except Exception as exc:
                self._last_drawn = None
                self._fail_count += 1
                if self._fail_count == 1 or self._fail_count % 10 == 0:
                    logging.warning("托盘图标重绘失败（将自动重试，第 %s 次）: %s",
                                    self._fail_count, exc)

        with self._lock:
            balloon = self._pending_balloon
            self._pending_balloon = None
        if balloon:
            try:
                icon.notify(balloon, "校园网守护")
                logging.info("气泡通知已发送：%s", balloon)
            except Exception as exc:
                logging.warning("气泡通知发送失败: %s", exc)
        return False

    def run(self, icon):
        """动画主循环（须在图标可见之后启动，见 setup_tray）。"""
        while not self._tick_once(icon):
            time.sleep(self.INTERVAL)


class TrayBridge:
    """持有托盘 icon/animator 引用，供其他线程发送通知与推送图标状态。"""

    def __init__(self, config):
        self._config = config
        self._icon = None
        self._animator = None

    def attach(self, icon, animator):
        self._icon = icon
        self._animator = animator

    def notify(self, message):
        if not self._config.show_notifications or self._animator is None:
            return
        # 入队到动画线程串行发送，避免跨线程并发操作 NOTIFYICONDATA
        self._animator.set_balloon(message)

    def update_icon(self, color, breathing):
        """守护循环推送的图标状态（颜色 + 是否呼吸）。"""
        if self._animator is not None:
            self._animator.set_target(color, breathing)

    def remove_tray(self):
        """移除任务栏图标并停止托盘（退出路径调用，避免残留幽灵图标）。

        Shell_NotifyIcon 可在任意线程调用，icon.visible=False 会同步
        执行 NIM_DELETE 立即移除图标。
        """
        if self._animator is not None:
            self._animator.stop()
        if self._icon is None:
            return
        try:
            self._icon.visible = False
            self._icon.stop()
        except Exception as exc:
            logging.debug("托盘移除时发生异常: %s", exc)


def _exit_action(icon, item):
    logging.info("收到退出指令，正在清理资源")
    try:
        # 先同步移除任务栏图标再停机，否则进程结束后图标残影要等
        # 鼠标划过任务栏才会消失。
        icon.visible = False
        icon.stop()
    except Exception as exc:
        logging.error("托盘停止时发生异常: %s", exc)
    # 刻意不销毁 pywebview 窗口：从非主线程销毁会触发 WebView2 清理
    # 竞态（FormClosed 事件异常弹窗），直接结束进程由系统回收资源。
    logging.info("程序已退出")
    os._exit(0)


def _promote_guide_action(_icon=None, _item=None):
    """打开系统任务栏设置页并说明如何让托盘图标常显。"""
    try:
        os.startfile('ms-settings:taskbar')
    except Exception as exc:
        logging.warning("打开系统设置页失败: %s", exc)
    winapi.show_info("如何常显托盘图标", PROMOTE_GUIDE_TEXT)


LOW_MEMORY_OPTIONS = (
    ('off', u'禁用（关闭仅隐藏）'),
    ('now', u'立即销毁'),
    ('5', u'5 分钟后销毁'),
    ('15', u'15 分钟后销毁'),
    ('30', u'30 分钟后销毁'),
)


def _low_memory_menu(actions):
    """低占用模式子菜单：单选切换，选中态实时反映当前配置。"""
    items = []
    for value, label in LOW_MEMORY_OPTIONS:
        def on_click(_icon=None, _item=None, _value=value):
            actions['set_low_memory'](_value)

        def is_checked(item, _value=value):
            return actions['get_low_memory']() == _value

        items.append(pystray.MenuItem(label, on_click, radio=True, checked=is_checked))
    return pystray.Menu(*items)


def setup_tray(app_status, actions, bridge, timer=None):
    """构建托盘图标与菜单并进入消息循环（阻塞，应在后台线程调用）。

    actions 回调集合由 app.py 提供：open_dashboard / open_settings /
    trigger_login / toggle_pause / open_logs / timer_extend / timer_cancel /
    set_low_memory / get_low_memory。
    左键点击图标触发 default 菜单项（打开看板），右键弹出完整菜单。
    """
    def state_text(_item):
        return "状态：%s" % app_status.snapshot()["state_text"]

    def pause_text(_item):
        return "恢复守护" if actions["is_paused"]() else "暂停守护"

    def timer_text(_item):
        return "自动退出：剩余 %s" % timer.remaining_text()

    def timer_visible(_item):
        return timer is not None and timer.is_active()

    def run_action(name):
        def handler(_icon=None, _item=None):
            actions[name]()
        return handler

    menu = pystray.Menu(
        pystray.MenuItem("打开看板", run_action("open_dashboard"), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(state_text, None, enabled=False),
        pystray.MenuItem("打开设置...", run_action("open_settings")),
        pystray.MenuItem("立即检测登录", run_action("trigger_login")),
        pystray.MenuItem(pause_text, run_action("toggle_pause")),
        pystray.MenuItem(timer_text, None, enabled=False, visible=timer_visible),
        pystray.MenuItem("延长 30 分钟", run_action("timer_extend"), visible=timer_visible),
        pystray.MenuItem("取消计时", run_action("timer_cancel"), visible=timer_visible),
        pystray.MenuItem(u"低占用模式", _low_memory_menu(actions)),
        pystray.MenuItem("打开日志文件夹", run_action("open_logs")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("如何常显托盘图标", _promote_guide_action),
        pystray.MenuItem("退出守护程序", _exit_action),
    )
    icon = pystray.Icon(ICON_NAME, create_tray_icon_image(), ICON_TITLE, menu)
    animator = TrayAnimator()
    bridge.attach(icon, animator)

    def _on_tray_ready(ready_icon):
        # pystray 自定义 setup 回调：必须自行置可见。动画线程在此之后
        # 启动，保证所有图标赋值都发生在可见之后——可见前的赋值只会
        # 静默存图不渲染，曾导致图标永远停留在初始颜色。
        ready_icon.visible = True
        threading.Thread(target=animator.run, args=(ready_icon,),
                         name="tray-anim", daemon=True).start()

    icon.run(setup=_on_tray_ready)
