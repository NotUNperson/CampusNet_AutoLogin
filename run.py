"""启动入口（也是 PyInstaller 打包入口：pyinstaller -F -w run.py）。"""
from campus_net.app import main

if __name__ == '__main__':
    main()
