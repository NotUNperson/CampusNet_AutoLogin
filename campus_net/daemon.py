"""网络探测与核心守护循环。"""
import logging
import queue
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ping3 import ping

from . import status as status_mod

if sys.platform == "win32":
    from . import winapi
else:
    winapi = None  # 非 Windows：winapi 依赖 wintypes，不可导入

LOGIN_RETRY_GAP = 3   # 同一轮内多次登录尝试之间的间隔（秒）
PING_TIMEOUT = 2      # ping 超时（秒）
WAIT_SLICE = 0.5      # 周期等待的切片粒度（秒），保证暂停/立即检测/计时到期能及时响应
CONFIRM_GAP = 1.0     # 外网连续确认 ping 之间的间隔（秒）
HTTP_PROBE_TIMEOUT = 3  # HTTP 兜底探测超时（秒）

_WINDOWS = sys.platform == "win32"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向：让 30x 以 HTTPError 形式原样抛回，
    以便检查 Location 判断请求是否被门户劫持。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)


def _ping3_probe(host):
    """ping3 单次探测，返回 (延迟秒数或 None, 失败描述)。"""
    try:
        delay = ping(host, timeout=PING_TIMEOUT)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if delay is not None and delay is not False:
        return delay, ""
    return None, "无响应"


def probe_host(host):
    """单次 ping 探测，返回 (是否可达, 结果描述)。

    Windows 走系统 IcmpSendEcho——cmd 的 ping 同款 API，不受
    raw socket 防火墙策略影响（ping3 在部分机器上会被整条拦掉
    而 cmd ping 正常，ping3 因此只用于非 Windows）。失败探测
    最多等一个超时周期，不做二次实现复核，保证级联判定不拖沓。
    """
    if not host:
        return False, "探测地址为空，请检查 config.ini"
    if _WINDOWS and winapi is not None:
        delay = winapi.icmp_ping(host)
        if delay is not None:
            return True, f"系统ICMP {delay * 1000:.0f}ms"
        return False, "系统ICMP不通"
    delay, detail = _ping3_probe(host)
    if delay is not None:
        return True, f"ping3 {delay * 1000:.0f}ms"
    return False, detail


def is_network_available(host):
    """ping 探测主机可达性；地址为空或探测异常时记日志并返回 False。"""
    if not host:
        logging.error("探测地址为空，请检查 config.ini 中的相关配置")
    ok, _ = probe_host(host)
    return ok


def classify_http_probe(status_code, location, request_host):
    """纯函数：把一次 HTTP 探测结果分类为 (是否在线, 结果描述)。

    - 连接层失败（status_code=None）→ 不在线
    - 3xx 跳到其他主机（或 Location 缺主机）→ 被门户劫持，不在线
    - 3xx 同主机跳转 → 在线（拿到了真实服务器的应答）
    - 4xx/5xx → 目标服务器真实应答，在线（TCP/HTTP 已通）
    - 2xx → 在线
    注意：少数门户做透明 NAT 直接用 200 返回认证页，状态码无法
    识别这种情况，只能依赖 ping 主判（3 连失败才会走到 HTTP）。
    """
    if status_code is None:
        return False, "无 HTTP 响应"
    if 300 <= status_code < 400:
        target = urlparse(location).hostname if location else None
        if not target:
            return False, f"HTTP {status_code} 重定向缺少主机，按门户劫持处理"
        if target != request_host:
            return False, f"HTTP {status_code} 被重定向到 {target}（疑似门户劫持）"
        return True, f"HTTP {status_code} 同主机跳转"
    if status_code >= 400:
        return True, f"HTTP {status_code}（目标服务器真实应答）"
    return True, f"HTTP {status_code}"


def http_reachable(url, timeout=HTTP_PROBE_TIMEOUT):
    """HTTP 兜底探测：仅当拿到目标主机的真实 HTTP 应答才算在线。

    门户会把未认证主机的任意 HTTP 请求重定向到认证页，所以这里
    禁止跟随重定向并对 Location 做跨主机检查。返回 (是否在线, 结果描述)。
    """
    if not url:
        return False, "HTTP 探测地址为空"
    if "://" not in url:
        url = "http://" + url
    request_host = urlparse(url).hostname
    try:
        _no_redirect_opener.open(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.headers else None
        return classify_http_probe(exc.code, location, request_host)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return classify_http_probe(200, None, request_host)

# 状态跃迁时发送的气泡通知文案（其余状态不发）
NOTIFY_MESSAGES = {
    status_mod.CONNECTED: "校园网已连接",
    status_mod.PORTAL_BLOCKED: "登录失败，将自动重试",
    status_mod.OFFLINE: "网络不可达，请检查连接",
}


def is_network_down_confirmed(host, attempts=3, gap=1.5):
    """连续 attempts 次 ping 均失败才判定外网已断开。

    单次 ICMP 探测存在偶发丢包/超时，直接以单次结果判定"已断开"
    会造成误报（如退出登录场景误报成功）；要求连续全部失败，
    任一次成功即判仍在网。
    """
    for attempt in range(attempts):
        if is_network_available(host):
            return False
        if attempt < attempts - 1:
            time.sleep(gap)
    return True


class DaemonWorker:
    """守护循环：外网可达则休眠；仅内网可达（被门户拦截）时自动登录。

    通过共享的 AppStatus 上报状态；pause_event 暂停/恢复；
    login_now_event 使"立即检测登录"无需等满一个检测周期；
    logout_now_event 触发一键退出登录（成功后自动暂停守护）；
    timer 到期时经 shutdown_cb 退出进程（临时用机场景）；
    once=True 时守护至登录成功（或本就在线）即退出；
    icon_cb 推送托盘图标的 (颜色, 是否呼吸)。
    """

    def __init__(self, config, login_executor, app_status,
                 pause_event, login_now_event,
                 notify=None, timer=None, once=False, shutdown_cb=None,
                 logout_now_event=None, icon_cb=None,
                 manual_logout_cb=None, manual_done_event=None,
                 manual_logout_reset_cb=None, check_requests=None):
        self._config = config
        self._login = login_executor
        self._status = app_status
        self._pause_event = pause_event
        self._login_now_event = login_now_event
        self._notify = notify or (lambda message: None)
        self._timer = timer
        self._once = once
        self._shutdown_cb = shutdown_cb or (lambda reason: None)
        self._logout_now_event = logout_now_event
        self._icon_cb = icon_cb or (lambda color, breathing: None)
        self._manual_logout_cb = manual_logout_cb
        self._manual_done_event = manual_done_event
        # 手动退出结果检查完成后调用：窗口回空白页（低占用，门户 JS 不在隐藏窗口空转）
        self._manual_logout_reset_cb = manual_logout_reset_cb
        # 手动检测请求队列：UI/托盘侧 put('dashboard'|'tray') 后置 login_now_event，
        # 守护循环完成该轮检测后按来源回发反馈（托盘→气泡，看板→状态轮询）
        self._check_requests = check_requests
        self._pending_feedback = set()
        self._window = None
        self._last_state = None
        self._icon_color = "gray"  # 最近一次推送的图标基色（呼吸时保持）
        self._on_campus = None     # None=未知 True=在校 False=非校园网
        self.error_count = 0
        self.success_count = 0
        self.last_error_time = 0.0

    def daemon_worker(self, window):
        """pywebview 主线程入口，循环运行直到程序退出。"""
        self._window = window
        self._login.bind_events(window)
        logging.info("校园网守护线程启动（每 %s 秒检测一次）", self._config.check_interval)
        if self._timer is not None and self._timer.is_active():
            logging.info("临时模式：将在 %s 后自动退出", self._timer.remaining_text())
        if self._once:
            logging.info("单次运行模式：登录成功（或本就在线）后自动退出")
        while True:
            try:
                if self._pause_event.is_set():
                    if self._wait_until_resumed():
                        return
                    continue
                if self._check_timer():
                    return
                if self._logout_now_event is not None and self._logout_now_event.is_set():
                    self._logout_now_event.clear()
                    self._handle_logout()
                    continue
                self._run_once()
                self._emit_manual_feedback()
                if self._once and self._status.snapshot()["state"] == status_mod.CONNECTED:
                    logging.info("单次运行：网络已连接，自动退出")
                    self._shutdown_cb("网络已连接，守护已结束")
                    return
                self._wait_interval()
            except Exception as exc:
                logging.error("守护循环异常: %s", exc)
                # 异常期间不能停在呼吸态，回到基色等待下一轮重新判定
                self._recover_from_exception()
                self._wait_interval()

    def _check_timer(self):
        """计时器检查：到期则退出进程并返回 True；临近到期先一次性提醒。"""
        if self._timer is None or not self._timer.is_active():
            return False
        if self._timer.alert_pending():
            self._notify(f"守护将在 {self._timer.remaining_text()} 后自动退出，可在看板/托盘延长")
            logging.info("计时器提醒：剩余 %s", self._timer.remaining_text())
        if self._timer.is_expired():
            logging.info("计时结束，守护自动退出")
            self._shutdown_cb("计时结束，守护已自动退出")
            return True
        return False

    def _pause_for_manual(self, message, notify_text):
        """退出登录相关流程的收尾：暂停守护并通知，防止自动重新登录。"""
        self._pause_event.set()
        self._status.set_state(status_mod.PAUSED, message)
        self._last_state = status_mod.PAUSED
        self._push_icon("gray", False)
        self._notify(notify_text)

    def _handle_logout(self):
        """一键退出登录：成功后自动暂停守护，防止立刻被重新登录。"""
        fallback = self._config.logout_fallback
        if fallback == 'manual':
            # 强制应用内手动退出：跳过自动流程，直接打开可见窗口。
            # 门户注销确认弹窗非标准时，自动点击无法兼容，人工最可靠。
            logging.info("强制应用内手动退出模式：打开认证页窗口")
            self._pause_for_manual(
                "已打开认证页窗口，请手动注销后关闭该窗口",
                "已打开认证页窗口，请手动注销；关闭窗口后会自动检查退出结果")
            try:
                self._manual_logout_cb()
            except Exception as exc:
                logging.error("打开手动退出窗口失败: %s", exc)
                self._notify("打开认证页窗口失败，请检查配置")
            return

        self._push_icon(self._icon_color, True)
        self._set_state(status_mod.LOGGING_OUT, "正在退出登录")
        dom_ok = self._login.execute_logout(self._window)
        if dom_ok:
            ok = True
        else:
            # DOM 未确认时不急着判失败：会话真被注销的话外网会立刻
            # 不可达，用网络状态兜底（门户 UI 千差万别，网络是客观事实）；
            # 连续多次 ping 均失败才算"断开"，避免单次丢包误报
            time.sleep(2)
            ok = is_network_down_confirmed(self._config.external_test_url)
            if ok:
                logging.info("DOM 未确认，但外网持续不可达，判定退出登录成功")

        if ok:
            logging.info("已退出登录，守护自动暂停")
            self._pause_for_manual("已退出登录，守护已暂停", "已退出登录，守护已暂停")
            return

        # 自动退出失败：按配置的兜底方式打开认证页供手动退出
        fallback = self._config.logout_fallback
        logging.warning("自动退出登录失败（兜底方式: %s）", fallback)
        if fallback == 'browser':
            try:
                os.startfile(self._config.login_url)
            except Exception as exc:
                logging.error("打开系统浏览器失败: %s", exc)
                self._set_state(status_mod.PORTAL_BLOCKED, "退出登录失败")
                self._notify("退出登录失败，且打开系统浏览器失败")
                return
            self._pause_for_manual(
                "已打开系统浏览器，请手动注销",
                "自动退出失败，已在系统浏览器打开认证页；请手动注销，"
                "完成后可从托盘「恢复守护」")
        elif fallback == 'window' and self._manual_logout_cb is not None:
            try:
                self._manual_logout_cb()
            except Exception as exc:
                logging.error("打开手动退出窗口失败: %s", exc)
                self._set_state(status_mod.PORTAL_BLOCKED, "退出登录失败")
                self._notify("退出登录失败，且打开手动退出窗口失败")
                return
            self._pause_for_manual(
                "已打开认证页窗口，请手动注销后关闭该窗口",
                "自动退出失败，已打开认证页窗口；请手动注销，"
                "关闭窗口后会自动检查退出结果")
        else:
            self._set_state(status_mod.PORTAL_BLOCKED, "退出登录失败")
            self._notify("退出登录失败")

    def _check_external_online(self):
        """外网在线判定级联：ping → HTTP 兜底 → 连续 ping 确认。

        任一通道判在线立即返回，避免在多通道环境里空等：
        - ping 第 1 次失败后先做 HTTP（ICMP 被拦但实际在线的环境
          用最短时间出结果，手动"立即检测"不被拖住）；
        - HTTP 也不通才做第 2、3 次 ping 确认（排除偶发丢包），
          全部失败才判离线。
        返回 (是否在线, 结果描述)。
        """
        host = self._config.external_test_url
        ok, detail = probe_host(host)
        if ok:
            return True, "ping 正常"
        logging.info("外网 ping %s 第 1/3 次不通（%s），尝试 HTTP 兜底", host, detail)
        http_ok, http_detail = http_reachable(host)
        if http_ok:
            logging.warning("外网 ping 不通但 HTTP 可达（%s），ICMP 可能被拦截，判定在线",
                            http_detail)
            return True, "HTTP 兜底可达"
        logging.info("HTTP 兜底不通（%s），继续 ping 确认", http_detail)
        for attempt in (2, 3):
            time.sleep(CONFIRM_GAP)
            ok, detail = probe_host(host)
            if ok:
                logging.info("外网 ping %s 第 %s/3 次恢复，判定为偶发丢包", host, attempt)
                return True, f"ping 第 {attempt}/3 次恢复"
            logging.info("外网 ping %s 第 %s/3 次不通（%s）", host, attempt, detail)
        return False, f"ping 3/3 与 HTTP 均不通（HTTP: {http_detail}）"

    def _run_once(self):
        """执行一轮检测：外网通则结束；仅内网通则尝试登录。"""
        cfg = self._config
        self._status.record_check()

        # 外网不通：达到失败上限后进入退避等待，避免频繁撞击门户。
        # 退避窗口内不做完整探测级联，只单次 ping 快速看是否已恢复
        now = time.time()
        if self.error_count >= cfg.max_retries:
            if now - self.last_error_time < cfg.retry_delay:
                recovered, _ = probe_host(cfg.external_test_url)
                if not recovered:
                    # 每轮无条件重推（动画去重会吸收重复帧），丢失后可自愈
                    self._push_icon("red", False)
                    if self._set_state(status_mod.PORTAL_BLOCKED, "登录失败，退避等待中"):
                        logging.info("连续登录失败，进入退避等待")
                    return
                logging.info("退避期间外网恢复，重新进入正常检测")
            self.error_count = 0

        online, probe_summary = self._check_external_online()
        if online:
            self.error_count = 0
            # 联网时也探测内网，用于区分"在校"与"非校园网"（图标颜色）
            self._on_campus = is_network_available(cfg.internal_test_url)
            self._status.record_campus(self._on_campus)
            self._push_connected_icon()
            if self._set_state(status_mod.CONNECTED, "网络连接正常"):
                logging.info("网络连接正常，无需登录")
            return

        logging.info("外网判定不在线（%s），尝试登录", probe_summary)

        internal_ok, internal_detail = probe_host(cfg.internal_test_url)
        if not internal_ok:
            self._on_campus = False
            self._status.record_campus(False)
            self._push_icon("red", False)
            logging.info("内网 ping %s 不通（%s），判定不在校园网",
                         cfg.internal_test_url, internal_detail)
            if self._set_state(status_mod.OFFLINE,
                               "外部网络与校园内网均不可达，请检查网线/Wi-Fi 连接"):
                logging.info("外部网络与校园内网均不可达，请检查网线/Wi-Fi 连接")
            return

        self._push_icon(self._icon_color, True)
        self._set_state(status_mod.LOGGING_IN, "检测到门户拦截，正在自动登录")
        if self._attempt_login():
            self.success_count += 1
            self.error_count = 0
            self._status.count_success()
            # 内网可达已被验证；登录成功必须立即停止呼吸并更新颜色，
            # 否则呼吸态会一直挂到下一轮检测（最长一个检测周期）
            self._on_campus = True
            self._status.record_campus(True)
            self._push_connected_icon()
            self._set_state(status_mod.CONNECTED, "登录成功，网络已连接")
            logging.info("登录成功（累计成功 %s 次）", self.success_count)
        else:
            self.error_count += 1
            self.last_error_time = now
            self._status.count_fail()
            self._push_icon("red", False)
            self._set_state(status_mod.PORTAL_BLOCKED, "登录失败，进入重试等待")
            logging.warning("登录失败，进入重试等待")

    def _push_connected_icon(self):
        """按当前计时器与在校状态推送连接态图标颜色。"""
        if self._timer is not None and self._timer.is_active():
            self._push_icon("blue", False)
        else:
            self._push_icon("green" if self._on_campus else "yellow", False)

    def _recover_from_exception(self):
        """守护循环异常后停止呼吸并回到基色，等待下一轮重新判定。"""
        self._push_icon(self._icon_color, False)

    def _attempt_login(self):
        total_attempts = self._config.quick_retry_times + 1
        for attempt in range(1, total_attempts + 1):
            logging.info("登录尝试 %s/%s", attempt, total_attempts)
            if self._login.execute_login(self._window):
                return True
            if attempt < total_attempts:
                time.sleep(LOGIN_RETRY_GAP)
        return False

    def _set_state(self, state, message=""):
        """更新共享状态；状态跃迁时返回 True 并发送气泡通知。"""
        self._status.set_state(state, message)
        if state == self._last_state:
            return False
        self._last_state = state
        text = NOTIFY_MESSAGES.get(state)
        if text:
            self._notify(text)
        return True

    def _push_icon(self, color, breathing):
        """推送托盘图标状态；非呼吸色同时记为基色（呼吸时保持基色）。"""
        if not breathing:
            self._icon_color = color
        self._icon_cb(color, breathing)

    def _collect_check_requests(self):
        """取出已到达的手动检测请求来源（去重；计时器唤醒时队列为空）。"""
        if self._check_requests is None:
            return
        try:
            while True:
                self._pending_feedback.add(self._check_requests.get_nowait())
        except queue.Empty:
            pass

    def _emit_manual_feedback(self, skipped=False):
        """手动检测结果按来源回发：托盘→气泡，看板/设置页→状态轮询。

        skipped=True 表示守护处于暂停，未执行检测，仅回发跳过说明。
        pending 为空（计时器唤醒等自动触发）不做任何反馈；
        自动检测"无状态跃迁不提示"的设计不受影响。
        """
        if not self._pending_feedback:
            return
        sources = sorted(self._pending_feedback)
        self._pending_feedback.clear()
        if skipped:
            text = "守护已暂停，手动检测已跳过"
        else:
            snap = self._status.snapshot()
            text = f"手动检测完成：{snap['state_text']}"
            if (snap["state"] == status_mod.CONNECTED
                    and snap["on_campus"] is not None):
                text += "（校园网内）" if snap["on_campus"] else "（非校园网）"
        for source in sources:
            logging.info("%s（来源: %s）", text, source)
            if source == 'tray':
                self._notify(text)
            else:
                self._status.record_manual_check(text, source)

    def _drain_paused_requests(self):
        """暂停期间收到的手动检测请求：不执行检测，仅回发跳过说明。"""
        if self._check_requests is None:
            return
        self._collect_check_requests()
        if self._pending_feedback:
            if self._login_now_event is not None:
                self._login_now_event.clear()
            self._emit_manual_feedback(skipped=True)

    def _wait_interval(self):
        """周期等待：立即检测、退出登录、暂停请求或计时到期均可提前唤醒。"""
        deadline = time.time() + self._config.check_interval
        while time.time() < deadline:
            if (self._login_now_event.is_set() or self._pause_event.is_set()
                    or (self._logout_now_event is not None and self._logout_now_event.is_set())):
                break
            if self._timer is not None and self._timer.is_active() and self._timer.is_expired():
                break
            time.sleep(WAIT_SLICE)
        if self._login_now_event.is_set():
            self._login_now_event.clear()
            self._collect_check_requests()
            logging.info("守护循环被唤醒（立即检测/计时器操作）")

    def _wait_until_resumed(self):
        """暂停等待；计时器为墙钟时限，暂停期间到期同样触发退出；
        退出登录请求与手动退出结果检查在暂停状态下也照常处理。"""
        self._push_icon("gray", False)
        self._set_state(status_mod.PAUSED, "守护已暂停，可在托盘菜单恢复")
        logging.info("守护已暂停")
        while self._pause_event.is_set():
            if self._check_timer():
                return True
            if self._logout_now_event is not None and self._logout_now_event.is_set():
                self._logout_now_event.clear()
                self._handle_logout()
            if self._manual_done_event is not None and self._manual_done_event.is_set():
                self._manual_done_event.clear()
                down = is_network_down_confirmed(self._config.external_test_url)
                logging.info("手动退出结果检查：外网%s", "已断开" if down else "仍在线")
                if down:
                    self._notify("手动退出已生效，外网已断开")
                else:
                    self._notify("手动退出未生效，外网仍在线；可从看板重试退出登录")
                if self._manual_logout_reset_cb is not None:
                    try:
                        self._manual_logout_reset_cb()
                    except Exception as exc:
                        logging.error("手动退出窗口复位失败: %s", exc)
            self._drain_paused_requests()
            time.sleep(WAIT_SLICE)
        logging.info("守护已恢复")
        return False
