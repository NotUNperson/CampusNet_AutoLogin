"""环境隔离与延迟退出。

config.ini 是极易与其它程序重名的文件名。若运行目录里已有别人的
config.ini，或目录中杂项过多，程序会把自身复制到一个干净的隔离
子文件夹并退出，避免读写别人的配置、也避免弄脏用户目录。
"""
import configparser
import os
import shutil
import sys
import time
from datetime import datetime

from .config import APP_SIGNATURE
from .paths import CONFIG_FILE, IS_FROZEN, ISOLATED_ENV_PREFIX, PACKAGE_DIR, WORK_DIR

CLUTTER_THRESHOLD = 2  # 首次运行时，目录中无关文件数量超过该值即触发隔离


def delayed_exit(seconds=15, exit_code=1):
    """延迟退出，防止控制台瞬间关闭导致提示无法阅读。"""
    print(f"\n为了让你看清以上提示，程序将在 {seconds} 秒后自动退出...")
    time.sleep(seconds)
    sys.exit(exit_code)


def _own_component_names():
    """程序自身相关文件/目录名，不计入杂乱统计。"""
    if IS_FROZEN:
        return {os.path.basename(sys.executable), 'config.ini', 'logs'}
    return {
        'config.ini', 'logs', 'run.py', 'campus_net', 'README.md',
        'requirements.txt', 'build.bat', 'tests', '__pycache__', 'build', 'dist',
        # 版本管理与配套文档（git 仓库工作副本的固有内容，不算杂项）
        '.git', '.gitignore', '.zcode',
        '状态.md', '记录.md', '跨平台规划.md',
    }


def _unrelated_items():
    """列出工作目录中与本程序无关的文件/目录（隔离子文件夹视为己方产物）。"""
    try:
        current_items = set(os.listdir(WORK_DIR))
    except OSError:
        return set()
    return {
        name for name in current_items - _own_component_names()
        if not name.startswith(ISOLATED_ENV_PREFIX) and not name.endswith('.spec')
    }


def _copy_program_files(target_dir):
    """把程序自身复制到 target_dir，返回复制内容的描述（用于提示语）。"""
    if IS_FROZEN:
        exe_name = os.path.basename(sys.executable)
        shutil.copy2(sys.executable, os.path.join(target_dir, exe_name))
        return exe_name
    # 源码运行：复制整个包目录（不含 __pycache__）与启动器
    shutil.copytree(PACKAGE_DIR, os.path.join(target_dir, 'campus_net'),
                    ignore=shutil.ignore_patterns('__pycache__'))
    launcher = os.path.join(WORK_DIR, 'run.py')
    if os.path.exists(launcher):
        shutil.copy2(launcher, os.path.join(target_dir, 'run.py'))
    return 'campus_net 程序目录与 run.py'


def migrate_and_exit(reason):
    """把自身复制到干净的隔离子文件夹后退出。"""
    print(f"\n[X] 环境异常拦截: {reason}")

    target_dir = os.path.join(WORK_DIR, ISOLATED_ENV_PREFIX)
    if os.path.exists(target_dir):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        target_dir = os.path.join(WORK_DIR, f"{ISOLATED_ENV_PREFIX}_{timestamp}")

    try:
        os.makedirs(target_dir, exist_ok=True)
        copied = _copy_program_files(target_dir)
        print(f"[OK] 已自动创建纯净的隔离文件夹：{target_dir}")
        print(f"程序已将自身（{copied}）复制过去，请进入该文件夹重新运行，当前程序即将退出。")
        print("提示：确认新位置可正常运行后，可以安全删除当前目录下的旧程序文件。")
    except Exception as exc:
        print(f"[!] 自动隔离失败 ({exc})，请手动将本程序移动到一个新建的空白文件夹中运行！")

    delayed_exit(15)


def _ownership_or_clutter_reason():
    """检查 config.ini 归属与目录杂乱度，返回需要隔离的原因，无需隔离返回 None。"""
    if os.path.exists(CONFIG_FILE):
        probe = configparser.ConfigParser()
        try:
            probe.read(CONFIG_FILE, encoding='utf-8')
            if not probe.has_section('System') or \
                    probe.get('System', 'app_signature', fallback='') != APP_SIGNATURE:
                return "检测到当前目录下的 config.ini 并非本程序的配置，为防止污染其他程序，必须隔离运行。"
        except Exception:
            return "检测到当前目录下的 config.ini 格式损坏或属于其他程序，必须隔离运行。"
        return None

    if len(_unrelated_items()) > CLUTTER_THRESHOLD:
        return "检测到当前文件夹内存在较多杂乱文件，为防止生成的配置文件弄脏你的文件夹，必须隔离运行。"
    return None


def ensure_runnable_environment():
    """main() 启动时最先调用的环境检查：无写权限直接退出，归属异常触发隔离。"""
    test_file = os.path.join(WORK_DIR, '.permission_test')
    try:
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write('test')
        os.remove(test_file)
    except OSError:
        print(f"[X] 致命错误：程序在当前目录 ({WORK_DIR}) 没有读写权限，无法生成配置文件。请更换目录后运行。")
        delayed_exit(15)

    reason = _ownership_or_clutter_reason()
    if reason:
        migrate_and_exit(reason)
