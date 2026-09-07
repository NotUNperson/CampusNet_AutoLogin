"""跨线程共享的守护状态（托盘菜单与设置窗口共同读取）。

守护循环写入、其他线程只读；所有字段经 Lock 保护，
snapshot() 返回 JSON 友好的 dict，可直接作为 pywebview JS 桥的返回值。
"""
import threading
import time
from datetime import datetime, timedelta

# 状态常量（state 与 STATE_TEXT 一一对应）
WAITING_CONFIG = "waiting_config"   # 配置未完成，等待用户在设置窗口填写
PAUSED = "paused"                   # 用户通过托盘暂停了守护
OFFLINE = "offline"                 # 外网、内网均不可达
PORTAL_BLOCKED = "portal_blocked"   # 内网可达但未通过认证（含登录失败退避）
LOGGING_IN = "logging_in"           # 正在自动登录
LOGGING_OUT = "logging_out"         # 正在退出登录
CONNECTED = "connected"             # 外网可达

STATE_TEXT = {
    WAITING_CONFIG: "等待配置",
    PAUSED: "已暂停",
    OFFLINE: "网络离线",
    PORTAL_BLOCKED: "被门户拦截",
    LOGGING_IN: "正在登录",
    LOGGING_OUT: "正在退出登录",
    CONNECTED: "已连接",
}


class AppStatus:
    """守护状态的唯一事实来源。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = WAITING_CONFIG
        self._message = ""
        self._last_check_time = None
        self._success_count = 0
        self._fail_count = 0
        self._on_campus = None  # None=未知 True=在校 False=非校园网
        self._manual_check = None    # 最近一次手动检测反馈（dict 或 None）
        self._manual_check_seq = 0   # 递增序号，供前端识别新消息

    def set_state(self, state, message=""):
        with self._lock:
            self._state = state
            self._message = message

    def record_manual_check(self, text, source):
        """记录一次手动检测反馈（看板/设置页轮询读取并展示）。

        整体替换 dict、从不原地修改，跨线程共享引用安全；
        seq 单调递增，前端据此判断"是新消息"而不是历史残留。
        """
        with self._lock:
            self._manual_check_seq += 1
            self._manual_check = {
                "seq": self._manual_check_seq,
                "text": text,
                "source": source,
                "time": time.strftime('%H:%M:%S'),
            }

    def record_check(self):
        with self._lock:
            self._last_check_time = time.time()

    def record_campus(self, on_campus):
        with self._lock:
            self._on_campus = bool(on_campus)

    def count_success(self):
        with self._lock:
            self._success_count += 1

    def count_fail(self):
        with self._lock:
            self._fail_count += 1

    def snapshot(self):
        """返回状态快照；last_check_time 已格式化为 HH:MM:SS。"""
        with self._lock:
            last_check = self._last_check_time
            return {
                "state": self._state,
                "state_text": STATE_TEXT.get(self._state, self._state),
                "message": self._message,
                "last_check_time": (
                    time.strftime('%H:%M:%S', time.localtime(last_check))
                    if last_check else "-"),
                "success_count": self._success_count,
                "fail_count": self._fail_count,
                "on_campus": self._on_campus,
                "manual_check": self._manual_check,
            }


class TimerState:
    """运行期倒计时（不写入 config.ini，面向学校/借用电脑临时用机场景）。

    支持按时长（start）与到点（start_until，已过自动次日）两种设定；
    守护循环轮询 is_expired() 决定自动退出；alert_pending() 在剩余
    时间进入自适应提醒窗口时一次性返回 True 用于提前气泡提醒。
    """

    ALERT_BEFORE_SECONDS = 5 * 60

    def __init__(self):
        self._lock = threading.Lock()
        self._deadline = None
        self._duration = 0.0
        self._alerted = False

    def start(self, minutes):
        """按分钟数启动倒计时；minutes 至少 1。"""
        with self._lock:
            self._duration = max(1, int(minutes)) * 60
            self._deadline = time.time() + self._duration
            self._alerted = False

    def start_until(self, hour, minute):
        """倒计时到指定时刻（24 小时制）；时刻已过则自动次日。"""
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        with self._lock:
            self._duration = (target - now).total_seconds()
            self._deadline = target.timestamp()
            self._alerted = False

    def extend(self, minutes):
        """延长倒计时；未激活时返回 False。延长后离开提醒窗口则重置提醒。"""
        with self._lock:
            if self._deadline is None:
                return False
            extra = max(1, int(minutes)) * 60
            self._deadline += extra
            self._duration += extra
            if self._deadline > time.time() + self._alert_threshold():
                self._alerted = False
            return True

    def cancel(self):
        with self._lock:
            self._deadline = None
            self._duration = 0.0
            self._alerted = False

    def is_active(self):
        with self._lock:
            return self._deadline is not None

    def remaining_seconds(self):
        with self._lock:
            if self._deadline is None:
                return 0
            return max(0, int(self._deadline - time.time()))

    def is_expired(self):
        with self._lock:
            return self._deadline is not None and self._deadline <= time.time()

    def _alert_threshold(self):
        """提前提醒阈值（需持锁调用）：短计时取总时长一半（至少 30 秒），
        长计时 5 分钟——保证 1 分钟这种短倒计时也能及时收到提醒。"""
        if self._duration <= 0:
            return self.ALERT_BEFORE_SECONDS
        return min(self.ALERT_BEFORE_SECONDS, max(30.0, self._duration / 2))

    def alert_pending(self):
        """进入提醒窗口时一次性返回 True（用于提前提醒）。"""
        with self._lock:
            if self._deadline is None or self._alerted:
                return False
            remaining = self._deadline - time.time()
            if 0 < remaining <= self._alert_threshold():
                self._alerted = True
                return True
            return False

    def remaining_text(self):
        seconds = self.remaining_seconds()
        if seconds <= 0:
            return "0 秒"
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours} 小时 {minutes} 分"
        if minutes:
            return f"{minutes} 分 {secs} 秒"
        return f"{secs} 秒"
