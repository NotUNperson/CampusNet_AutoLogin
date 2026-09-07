"""程序路径常量。

frozen（PyInstaller 打包）时以 exe 所在目录为工作目录；
源码运行时以项目根目录（campus_net 包的上一级）为工作目录，
config.ini 与 logs/ 始终位于工作目录中。
"""
import os
import sys

IS_FROZEN = getattr(sys, 'frozen', False)

if IS_FROZEN:
    PACKAGE_DIR = None
    WORK_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
    WORK_DIR = os.path.dirname(PACKAGE_DIR)

CONFIG_FILE = os.path.join(WORK_DIR, 'config.ini')
LOG_DIR = os.path.join(WORK_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'campus_net.log')

# 隔离子文件夹名前缀（见 isolate.py）
ISOLATED_ENV_PREFIX = 'CampusNet_Isolated_Env'
