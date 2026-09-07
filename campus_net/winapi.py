"""Windows 原生 API 封装（消息弹窗、控制台事件、系统 ICMP 探测）。"""
import ctypes
import socket
import struct
from ctypes import wintypes

_MB_ICONINFO = 0x40
_MB_ICONERROR = 0x10

# 保存回调的函数指针引用，防止被垃圾回收后 Windows 调用野指针
_console_handler_ref = None

# IP_SUCCESS（IcmpSendEcho 的 Status 成功值）
_IP_STATUS_SUCCESS = 0

# 显式声明 64 位安全的参数/返回类型：IcmpCreateFile 返回的 HANDLE 是
# 指针宽度，缺省的 c_int 会在 64 位 Python 上被截断，导致调用崩溃
_icmp_ready = False
try:
    if hasattr(ctypes, "windll"):
        _iphlpapi = ctypes.windll.iphlpapi
        _iphlpapi.IcmpCreateFile.restype = ctypes.c_void_p
        _iphlpapi.IcmpSendEcho.argtypes = [
            ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p,
            ctypes.c_ushort, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.ULONG, wintypes.ULONG]
        _iphlpapi.IcmpSendEcho.restype = wintypes.ULONG
        _iphlpapi.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
        _icmp_ready = True
except Exception:
    _icmp_ready = False


class _IpOptionInformation(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class _IcmpEchoReply(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", _IpOptionInformation),
    ]


def icmp_ping(host, timeout_ms=2000):
    """用系统 IcmpSendEcho 探测主机可达性，返回延迟（秒）或 None。

    cmd 里的 ping 走的就是这个 API；ping3 那类 raw socket 实现
    在部分机器上会被防火墙拦掉而系统级 ICMP 不受影响，两者结果
    可能完全不同，所以 Windows 上探测优先走这里。
    仅支持 IPv4 目标；解析失败/无响应/任何异常都返回 None。
    """
    if not _icmp_ready:
        return None
    try:
        ip = socket.gethostbyname(host)
        dest = struct.unpack("<I", socket.inet_aton(ip))[0]
        payload = b"campusnet-probe"
        reply_size = ctypes.sizeof(_IcmpEchoReply) + len(payload) + 8
        reply = ctypes.create_string_buffer(reply_size)
        handle = _iphlpapi.IcmpCreateFile()
        if not handle:
            return None
        try:
            replies = _iphlpapi.IcmpSendEcho(
                handle, dest, payload, len(payload), None,
                reply, reply_size, timeout_ms)
            if replies == 0:
                return None
            parsed = ctypes.cast(
                reply, ctypes.POINTER(_IcmpEchoReply)).contents
            if parsed.Status != _IP_STATUS_SUCCESS:
                return None
            return parsed.RoundTripTime / 1000.0
        finally:
            _iphlpapi.IcmpCloseHandle(handle)
    except Exception:
        return None


def show_info(title, text):
    ctypes.windll.user32.MessageBoxW(0, text, title, _MB_ICONINFO)


def show_error(title, text):
    ctypes.windll.user32.MessageBoxW(0, text, title, _MB_ICONERROR)


def set_console_ctrl_handler(callback):
    """注册 Win32 控制台事件处理器（Ctrl+C、关闭终端窗口、注销、关机）。

    callback(event_type) 由 Windows 在新建的线程中执行，不依赖 Python
    主线程的字节码循环——主线程阻塞在 webview 原生 GUI 循环里时，
    Python 级 signal 处理永远轮不到执行，必须在这一层拦截。
    事件被处理后应自行结束进程；返回是否注册成功（pythonw / -w 打包
    等无控制台环境会失败，属正常现象）。
    """
    global _console_handler_ref
    try:
        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _wrap(event_type):
            try:
                callback(event_type)
            except Exception:
                pass  # 绝不能把异常抛回 C 层
            return True

        _console_handler_ref = handler_type(_wrap)
        kernel32 = ctypes.windll.kernel32
        return bool(kernel32.SetConsoleCtrlHandler(_console_handler_ref, True))
    except Exception:
        return False
