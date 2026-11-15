# -*- coding: utf-8 -*-
"""路径解析：把「程序资源」和「用户数据」分开。

打包成 exe 之后 `__file__` 指向的不是程序目录，而是 PyInstaller 每次启动解压出来的临时
目录（`%TEMP%\\_MEIxxxxxx`），进程退出就被删。账号清单、密码、浏览器登录态要是
还写在 `__file__` 旁边，每次启动都是从零开始，表现就是「关掉再打开，账号全空」；
而 `.py`、图标这类随包分发的东西只能待在那个临时目录里。

所以分成两个概念：
  资源目录 `resource_dir()`：源码形态 = 本文件所在目录；exe 形态 = `sys._MEIPASS`
  数据目录 `data_dir()`：源码形态 = 本文件所在目录；exe 形态 = exe 同级目录，
    不可写时退到 `%APPDATA%\\大麦抢票`

源码形态下两者都返回本文件所在目录，和引入本文件之前
`os.path.dirname(os.path.abspath(__file__))` 一字不差，对现有程序、数据、测试零影响。

数据目录放在 exe 同级，好处是整个文件夹拷走就能换机器，账号、登录态一起跟着走，
客户自己也看得见这些文件；代价是放进 `C:\\Program Files` 这种没有写权限的地方就写不进去。
所以这里真去试写一次，写不了就自动退到 AppData，并把「最后落在哪、为什么」记在
`data_dir_source()` 里。
"""

import os
import sys

# 退到 AppData 时用的目录名
APPDATA_FOLDER = "大麦抢票"

_data_dir = None
_data_source = None


def is_frozen():
    """是不是被 PyInstaller 之类打包出来的。"""
    return bool(getattr(sys, "frozen", False))


def _source_dir():
    """本文件所在目录。源码形态下所有模块都在这个目录里。"""
    return os.path.dirname(os.path.abspath(__file__))


def app_dir():
    """程序所在目录：exe 形态是 exe 所在目录，源码形态是源码目录。

    注意这跟 `resource_dir()` 不是一回事，单文件打包时 exe 旁边
    可能什么都没有，资源全在 `_MEIPASS` 里。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _source_dir()


def resource_dir():
    """只读资源所在目录：打包后是解压出来的临时目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return _source_dir()


def _probe_writable(directory):
    """真去建一个文件试试，别靠 `os.access` 猜（Windows 上它不可靠）。"""
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception:
        return False
    path = os.path.join(directory, ".write_probe_%d.tmp" % os.getpid())
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("ok")
    except Exception:
        return False
    try:
        os.remove(path)
    except Exception:
        pass                    # 删不掉不算失败（可能被杀毒软件短暂锁住）
    return True


def _fallback_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APPDATA_FOLDER)


def _resolve_data_dir():
    if not is_frozen():
        return _source_dir(), "source"
    exe_dir = app_dir()
    if _probe_writable(exe_dir):
        return exe_dir, "exe"
    fb = _fallback_dir()
    try:
        os.makedirs(fb, exist_ok=True)
    except Exception:
        pass
    return fb, "appdata"


def data_dir():
    """用户数据（账号、密码、浏览器登录态、日志）所在目录。

    第一次调用时解析并缓存，里面有一次试写，不适合反复做。
    """
    global _data_dir, _data_source
    if _data_dir is None:
        _data_dir, _data_source = _resolve_data_dir()
        try:
            os.makedirs(_data_dir, exist_ok=True)
        except Exception:
            pass
    return _data_dir


def data_dir_source():
    """数据目录是怎么定下来的：'source' / 'exe' / 'appdata'。"""
    data_dir()
    return _data_source


def logs_dir():
    """日志目录：数据目录下的 `logs/`。

    为什么不让日志跟 `.py` 摊在一起（以前就是那样）：
      源码形态下数据目录就是源码目录，于是 `accounts.log` / `damai_wizard.log`
      直接混在几十个 `.py` 旁边，一眼分不清哪些是代码、哪些是跑出来的东西，
      交付源码时也容易顺手把日志交出去（里面有账号、演出、价格这些信息）。
      所以统一收到一个子目录里：

          源码形态 → `web/logs/`        打包形态 → `<exe 同级>/logs/`

      两个形态一个口径，`task_manager` 的 `runs/`（每个账号一份运行日志）
      也挂在它下面。只影响日志落在哪，不改任何数据文件的位置，
      也就不会掉账号配置。
    """
    path = os.path.join(data_dir(), "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass                    # 建不出来就用原路径，让调用方自己报错
    return path


def _reset():
    """给测试用：清掉缓存，下次重新解析。"""
    global _data_dir, _data_source
    _data_dir = None
    _data_source = None


def describe():
    """给人看的一段话，出问题时直接贴出来就能看懂。"""
    lines = [
        "程序形态：{}".format("打包版 (exe)" if is_frozen() else "源码版"),
        "程序目录：{}".format(app_dir()),
        "资源目录：{}".format(resource_dir()),
        "数据目录：{}".format(data_dir()),
        "日志目录：{}".format(logs_dir()),
    ]
    src = data_dir_source()
    if src == "source":
        lines.append("（源码形态：数据就在程序目录里，跟以前一样）")
    elif src == "exe":
        lines.append("（数据放在 exe 同一层，整个文件夹拷走即可搬迁）")
    else:
        lines.append("（exe 所在目录写不进去，已退到 AppData；"
                     "想放到 exe 旁边，把它挪到有写权限的目录即可）")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
