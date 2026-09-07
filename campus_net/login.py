"""通过隐藏浏览器窗口注入 JS 完成门户登录/退出登录。

本模块不 import webview，只接收 pywebview 的 window 对象，
因此可以脱离 GUI 环境对 JS 生成逻辑做单元测试。
"""
import json
import logging
import threading
import time

PAGE_LOAD_TIMEOUT = 15      # 等待登录页加载完成的最长秒数
INJECT_MAX_TRIES = 15       # 页面元素未就绪时的注入重试次数（校园网慢时表单渲染晚）
INJECT_RETRY_INTERVAL = 1   # 注入重试间隔（秒）
SUCCESS_POLL_TIMES = 12     # 点击后轮询"操作生效"标志的次数
SUCCESS_POLL_INTERVAL = 1   # 成功轮询间隔（秒）


def build_inject_js(username, password, selectors):
    """生成注入脚本：填入账号密码、派发 input/change 事件并点击登录按钮。

    账号、密码与元素 ID 都经 json.dumps 转成合法的 JS 字符串字面量，
    含引号、反斜杠、换行等特殊字符时不会破坏脚本。
    """
    user_id, pass_id, btn_id = (json.dumps(s) for s in selectors)
    user_value = json.dumps(username)
    pass_value = json.dumps(password)
    return (
        "(function() {\n"
        f"    const userInp = document.getElementById({user_id});\n"
        f"    const passInp = document.getElementById({pass_id});\n"
        f"    const loginBtn = document.getElementById({btn_id});\n"
        "    if (userInp && passInp && loginBtn) {\n"
        f"        userInp.value = {user_value};\n"
        f"        passInp.value = {pass_value};\n"
        "        const inputEvent = new Event('input', { bubbles: true });\n"
        "        const changeEvent = new Event('change', { bubbles: true });\n"
        "        userInp.dispatchEvent(inputEvent);\n"
        "        passInp.dispatchEvent(inputEvent);\n"
        "        userInp.dispatchEvent(changeEvent);\n"
        "        passInp.dispatchEvent(changeEvent);\n"
        "        loginBtn.click();\n"
        "        return 'attempted';\n"
        "    }\n"
        "    return 'not_found';\n"
        "})();"
    )


def build_success_js(logout_btn_id):
    """生成检测"已登录"（注销按钮出现）的脚本。"""
    return f"document.getElementById({json.dumps(logout_btn_id)}) !== null;"


def build_logout_click_js(logout_btn_id):
    """生成点击注销按钮的脚本。"""
    return (
        "(function() {\n"
        f"    const logoutBtn = document.getElementById({json.dumps(logout_btn_id)});\n"
        "    if (logoutBtn) {\n"
        "        logoutBtn.click();\n"
        "        return 'clicked';\n"
        "    }\n"
        "    return 'not_found';\n"
        "})();"
    )


def build_confirm_click_js():
    """生成查找并点击"注销确认弹窗"按钮的脚本。

    不少门户点击注销后会弹出确认框（SweetAlert/Layer/Bootbox 等），
    需要再点一次确认按钮才会真正注销；这里按常见样式通用匹配，
    无弹窗时点击操作无副作用。
    """
    return (
        "(function() {\n"
        "    const selectors = ['.swal2-confirm', '.confirm', '.swal-button--confirm',"
        " '.layui-layer-btn0', '.bootbox-accept', '.modal-confirm', '#confirm-logout'];\n"
        "    let clicked = 0;\n"
        "    for (const sel of selectors) {\n"
        "        document.querySelectorAll(sel).forEach(function(el) {\n"
        "            const rect = el.getBoundingClientRect();\n"
        "            if (rect.width > 0 && rect.height > 0) { el.click(); clicked++; }\n"
        "        });\n"
        "    }\n"
        "    return 'clicked:' + clicked;\n"
        "})();"
    )


def build_logged_out_check_js(logout_btn_id):
    """生成确认"已退出登录"的脚本：注销按钮从页面上消失。"""
    return f"!document.getElementById({json.dumps(logout_btn_id)});"


def build_page_diag_js(logout_btn_id, username_input_id):
    """生成页面状态诊断脚本（退出登录失败时记录，便于定位门户差异）。"""
    logout_id = json.dumps(logout_btn_id)
    user_id = json.dumps(username_input_id)
    return (
        "(function(){ return JSON.stringify({"
        "url: location.href.slice(0, 120), "
        f"logout: !!document.getElementById({logout_id}), "
        f"username: !!document.getElementById({user_id})}});"
        "})();"
    )


class LoginExecutor:
    """持有页面加载事件并在给定窗口上执行登录/退出登录。"""

    def __init__(self, config):
        self._config = config
        self._page_loaded = threading.Event()

    def bind_events(self, window):
        """绑定页面加载事件，每个窗口只需调用一次。"""
        window.events.loaded += self._on_page_loaded

    def _on_page_loaded(self):
        self._page_loaded.set()

    def _load_portal(self, window):
        """打开认证页并等待加载完成；返回是否成功。"""
        self._page_loaded.clear()
        window.load_url(self._config.login_url)
        if not self._page_loaded.wait(timeout=PAGE_LOAD_TIMEOUT):
            logging.error("登录页面加载超时（等待 %s 秒未收到 loaded 事件）",
                          PAGE_LOAD_TIMEOUT)
            return False
        return True

    def execute_login(self, window):
        """执行一次登录，返回是否成功。结束时页面总是回到 about:blank。"""
        cfg = self._config
        logging.info("启动浏览器进行登录操作")
        try:
            if not self._load_portal(window):
                return False

            inject_js = build_inject_js(
                cfg.username, cfg.password,
                (cfg.username_input_id, cfg.password_input_id, cfg.login_btn_id))

            result = 'not_found'
            for _ in range(INJECT_MAX_TRIES):
                result = window.evaluate_js(inject_js)
                if result == 'attempted':
                    break
                time.sleep(INJECT_RETRY_INTERVAL)

            if result != 'attempted':
                diag = window.evaluate_js(
                    build_page_diag_js(cfg.logout_btn_id, cfg.username_input_id))
                logging.warning(
                    "登录页上未找到账号/密码输入框或登录按钮"
                    "（重试 %s 次后放弃，页面状态: %s），"
                    "元素 ID 可在设置窗口的高级选项中调整",
                    INJECT_MAX_TRIES, diag)
                return False

            success_js = build_success_js(cfg.logout_btn_id)
            for _ in range(SUCCESS_POLL_TIMES):
                time.sleep(SUCCESS_POLL_INTERVAL)
                if window.evaluate_js(success_js):
                    return True
            logging.warning("登录动作已执行，但未检测到登录成功标志（注销按钮未出现）")
            return False
        finally:
            window.load_url('about:blank')

    def execute_logout(self, window):
        """执行一次退出登录，返回是否成功。结束时页面回到 about:blank。"""
        cfg = self._config
        logging.info("启动浏览器进行退出登录操作")
        try:
            if not self._load_portal(window):
                return False

            click_js = build_logout_click_js(cfg.logout_btn_id)
            result = 'not_found'
            for _ in range(INJECT_MAX_TRIES):
                result = window.evaluate_js(click_js)
                if result == 'clicked':
                    break
                time.sleep(INJECT_RETRY_INTERVAL)

            if result != 'clicked':
                diag = window.evaluate_js(
                    build_page_diag_js(cfg.logout_btn_id, cfg.username_input_id))
                logging.warning(
                    "登录页上未找到注销按钮（重试 %s 次后放弃，页面状态: %s），"
                    "logout_btn_id 可在设置中调整", INJECT_MAX_TRIES, diag)
                return False

            # 点击注销后门户常弹出确认框，每轮轮询顺带尝试点击确认按钮
            confirm_js = build_confirm_click_js()
            check_js = build_logged_out_check_js(cfg.logout_btn_id)
            for _ in range(SUCCESS_POLL_TIMES):
                time.sleep(SUCCESS_POLL_INTERVAL)
                window.evaluate_js(confirm_js)
                if window.evaluate_js(check_js):
                    return True
            logging.warning(
                "已点击注销按钮，但未确认到退出成功。页面状态: %s",
                window.evaluate_js(
                    build_page_diag_js(cfg.logout_btn_id, cfg.username_input_id)))
            return False
        finally:
            window.load_url('about:blank')
