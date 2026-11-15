# -*- coding: utf-8 -*-
"""
大麦抢票 - 配置向导（图形界面）

启动方式：双击同目录下的 wizard.bat

核心心智模型（一句话）：
    一个账号 = 一个分页 = 一份完整的抢票配置。
    账号一抢 A 演出、账号二抢 B 演出，各填各的，互不影响。

界面上每个页签就是一个登录账号，页里同时装着两件事：
  * 「用谁的身份抢」，手机号 / 密码（密码存本机）
  * 「抢什么」，演出链接 / 城市 / 场次 / 日历日期（选填）/ 票档 / 观演人 / 数量

数据落地：
  accounts.json，唯一的数据文件。每个账号自带全套抢票参数；
                      顶层另存一份所有账号共用的「观演人名单」（roster）。
  credentials.json，账号密码。明文存放，只应该留在自己的程序目录里。
                      密码永远不会写进 accounts.json。

两个启动按钮：
  「保存并开始抢票」        = 只跑当前分页这个账号
  「全部账号一起抢（多开）」 = 每个启用的账号各开一个浏览器，同时抢各自要抢的票

说明：本文件是额外生成的外壳，不修改原仓库任何代码。删除本文件与 wizard.bat 即可完全复原。
"""

import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import font as tkfont
from tkinter import messagebox, ttk

# 高 DPI 屏幕下避免界面发虚
try:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 路径口径见 accounts.py：源码形态两个目录是同一个（跟改造前一字不差），
# exe 形态则分开，数据落在 exe 同级，代码/资源留在解压目录里。
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()          # 用户数据：账号 / 登录态
    PKG_DIR = app_paths.resource_dir()       # 随包分发的只读资源（.bat 等）
    LOGS_DIR = app_paths.logs_dir()          # 日志：data/logs/，不跟源码摊一起
    # 抢票 / 登录子进程的日志。这里和「任务面板」用的是同一个 runs/（见 task_manager.RUNS_DIR），两条路起的
    # 任务文件名规则一样，「查看日志」一个入口就都能读到（见 latest_task_log）。改这里要连那边一起想。
    RUNS_DIR = os.path.join(LOGS_DIR, "runs")
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PKG_DIR = BASE_DIR
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
    RUNS_DIR = os.path.join(LOGS_DIR, "runs")

# damai/ 有两个身份，必须拆开看：包目录（damai/*.py）随包分发，打包后躺在临时解压目录里；
# 配置目录（damai/config.json）是用户数据，必须跟 BASE_DIR 走。打包后两者不在同一个地方，混用会让配置写进临时目录、退出就没了。
DAMAI_PKG_DIR = os.path.join(PKG_DIR, "damai")
DAMAI_DIR = os.path.join(BASE_DIR, "damai")
CONFIG_PATH = os.path.join(DAMAI_DIR, "config.json")
GRAB_SCRIPT = "damai.py"
# 这个 .bat 是运行时生成的（见 Wizard.write_launcher），所以算数据不算资源。
# 打包版不会生成它（`damai/damai.py` 那条老路径在 exe 里不存在，见那个方法）。
LAUNCHER_BAT = os.path.join(BASE_DIR, "run_grab.bat")
LOG_PATH = os.path.join(LOGS_DIR, "damai_wizard.log")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")
ACCOUNTS_BAT = os.path.join(PKG_DIR, "accounts.bat")
# 老版本把「观演人名单」和「演出方案」放在这里。名单会自动迁移进 accounts.json，方案维度已取消（配置现在
# 跟着账号走）。这个文件不再读写，留着只是不当删用户东西。
LEGACY_PROFILES_PATH = os.path.join(BASE_DIR, "profiles.json")

# 多账号清单的数据层（accounts.py）是纯标准库，向导跑在没有 selenium 的解释器上也能直接 import。
# 这里只用它读写账号清单，不碰浏览器；密码不在 accounts.py 里，而在 cred_store 里。
try:
    import accounts as acc_store
except Exception:                                          # pragma: no cover
    acc_store = None

# 账号密码的本地存储（明文 JSON）。
# 密码只在这里，绝不会写进 accounts.json。
try:
    import cred_store as creds
except Exception:                                          # pragma: no cover
    creds = None

# 「任务面板」的数据层：起停任务、聚合状态，纯标准库。它和界面上原有的启动按钮并存、不替换，
# 两条路都把子进程的控制台窗口藏掉（见 spawn_runner），区别只在面板这条路把进程句柄留在手里，所以能在一个窗口看全部、也能单独停某一个。
try:
    import task_manager as tasks
except Exception:                                          # pragma: no cover
    tasks = None

# 实时状态出口（logs/live/<账号>.json）：子进程每处理一个账号就写一次。面板「不管任务从哪条路起、
# 都算到账号自己那一行」靠的就是它：老路的进程句柄拿不到，只有这个文件是两条路共同的、按账号说话的信源。
try:
    import live_status
except Exception:                                          # pragma: no cover
    live_status = None

# 「任务画面」的后端：把每个开着的浏览器窗口截成缩略图（Windows 原生 API，纯 ctypes，不碰 selenium，
# 抢票在子进程里跑、driver 主进程根本拿不到）。没有它也不影响别的功能，只是「画面墙」那个窗口打不开。
try:
    import window_wall
except Exception:                                          # pragma: no cover
    window_wall = None

# 多开设置（一次开几个 / 错开多久 / 浏览器参数）。纯标准库。
try:
    import run_settings
except Exception:                                          # pragma: no cover
    run_settings = None

# 环境检测（有没有 Chrome / 驱动 / 网络 / 抢票解释器）。纯标准库、只看不动手，它正是要在「环境有问题」
# 的机器上跑出结果，所以不能依赖 selenium / tkinter。
try:
    import env_check
except Exception:                                          # pragma: no cover
    env_check = None

# 程序名与版本号。零依赖的小模块，界面和报告都用它，别在各处写死版本字符串。
try:
    import version as app_version
except Exception:                                          # pragma: no cover
    app_version = None

# 更新日志（版本号旁边那个圆圈问号点开的就是它）。零依赖，只读一个 txt。
# 读不到文件不影响任何功能，只影响那个窗口里显示什么（见 changelog.hint）。
try:
    import changelog
except Exception:                                          # pragma: no cover
    changelog = None

# 内存体检 + Chrome 参数。多开减负用：resource_guard 回答「这台机器现在还能开几个」，
# chrome_flags 回答「给 Chrome 加哪些参数」。
try:
    import resource_guard
    import chrome_flags
except Exception:                                          # pragma: no cover
    resource_guard = None
    chrome_flags = None

_TASK_MANAGER = None


def _fmt_elapsed(seconds):
    """把秒数写成人话。格式和 task_manager.Task.elapsed_text 保持一致。"""
    s = int(max(0, seconds))
    if s < 60:
        return "{} 秒".format(s)
    if s < 3600:
        return "{} 分 {:02d} 秒".format(s // 60, s % 60)
    return "{} 时 {:02d} 分".format(s // 3600, (s % 3600) // 60)


def get_task_manager():
    """全局只留一个 TaskManager 实例。

    进程句柄只在本进程内有效：每新建一个实例，就会把「这些任务是我起的、
    我还握着句柄」这件事丢掉，只能退化成查 pid，状态仍然对，但实时性差一截。
    面板开关几次都该复用同一个。
    """
    global _TASK_MANAGER
    if _TASK_MANAGER is None and tasks is not None:
        _TASK_MANAGER = tasks.TaskManager(on_log=log_line)
    return _TASK_MANAGER


def log_line(text):
    """往日志文件追加一行。pythonw 启动时没有控制台，异常会凭空消失，全靠这个文件。"""
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            for line in str(text).rstrip().splitlines():
                f.write("[{}] {}\n".format(stamp, line))
    except Exception:
        pass

# 装了 selenium 的隔离环境（向导自身只需标准库，可以跑在别的解释器上）。
# 换机器不用改代码：设环境变量 DAMAI_PYTHON，或把虚拟环境放在仓库根目录的 .venv；
# 两样都没有就用向导自己的解释器。
def _default_venv_python():
    env = os.environ.get("DAMAI_PYTHON")
    if env:
        return env
    cand = os.path.join(os.path.dirname(PKG_DIR), ".venv", "Scripts", "python.exe")
    if os.path.isfile(cand):
        return cand
    # 向导自己是 pythonw.exe 时换成同目录的 python.exe：子进程要有控制台才看得到输出
    exe = sys.executable
    alt = os.path.join(os.path.dirname(exe), "python.exe")
    if os.path.basename(exe).lower() == "pythonw.exe" and os.path.isfile(alt):
        return alt
    return exe


VENV_PYTHON = _default_venv_python()

DEFAULT_INDEX_URL = "https://www.damai.cn/"
DEFAULT_LOGIN_URL = "https://passport.damai.cn/login?ru=https%3A%2F%2Fwww.damai.cn%2F"

# 仓库作者自带的示例观演人，命中这些就不预填，免得用户以为要照抄
PLACEHOLDER_USERS = {
    "观演人1", "观演人2", "张三", "李四", "观众1姓名", "观众2姓名",
}

# 写进 accounts.json 顶层的说明（给人看的，程序不读）
ACCOUNTS_NOTE = [
    "一个账号 = 一份完整的抢票配置。账号之间互不影响，可以抢不同的演出。",
    "本文件不保存任何密码。密码存在同目录的 credentials.json 里。",
    "roster 是所有账号共用的「观演人名单」；每个账号在自己的分页里勾选用哪几个人。",
    "prices 的数组顺序就是票档优先级；写票价数字（188）或直接粘贴票价页原文（全价票188元（站席））都能认。",
    "quantity 必须 <= users 的人数（大麦要为每张票指定观演人）。",
    "profile_dir 留空则自动用 profiles/<账号号>，那是该账号独立的浏览器数据目录（登录状态存在里面）。",
    "if_commit_order = true = 替你勾购票协议 + 提交订单；观演人不论开不开都会替你勾好；绝不会自动付款。",
    "改这份文件请先关掉配置向导，否则向导退出时会把界面上的内容写回来覆盖你的修改。",
]

# 外观：颜色 / 字体。整套界面只认这里的常量，想换配色改这一个地方就够了。
_FONT_FAMILY = "Microsoft YaHei UI"

UI_FONT = (_FONT_FAMILY, 10)
UI_FONT_BOLD = (_FONT_FAMILY, 10, "bold")
UI_FONT_TITLE = (_FONT_FAMILY, 17, "bold")
UI_FONT_SMALL = (_FONT_FAMILY, 9)

# 配色：浅灰底 + 白色卡片 + 单一主色的克制方案。比 Tk 默认的「一片系统灰 + 立体边框」干净得多，也不抢内容的注意力。
COLOR_BG = "#f2f3f5"            # 窗口底色
COLOR_CARD = "#ffffff"          # 卡片 / 分组容器
COLOR_INSET = "#fafbfc"         # 卡片里的凹陷区（如观演人名单框）
COLOR_BORDER = "#e4e7ec"        # 描边与分隔线
COLOR_TEXT = "#1f2329"          # 主文字
COLOR_HINT = "#6b7280"          # 说明文字
COLOR_WARN = "#c0392b"          # 危险 / 删除
COLOR_PRIMARY = "#1a5fb4"       # 主色（品牌蓝）
COLOR_PRIMARY_HOVER = "#154a8a"
COLOR_PRIMARY_SOFT = "#e8f0fe"       # 次级按钮底色
COLOR_PRIMARY_SOFT_HOVER = "#d6e5fb"
COLOR_GHOST = "#f1f3f5"         # 中性按钮底色
COLOR_GHOST_HOVER = "#e4e7ec"
COLOR_FIELD = "#ffffff"         # 输入框底色
COLOR_TAB_IDLE = "#e9ebef"      # 未选中的页签
# 环境检测用的三档。绿和签发工具那边用同一个色号，两个界面看起来才像一套。「注意」故意不沿用
# COLOR_WARN（红）：红色在这套界面里是「危险 / 删除」的意思，而「能用、但建议处理一下」没那么重，用琥珀色分得清楚。
COLOR_OK = "#1a7f37"            # 通过
COLOR_ATTENTION = "#b26a00"     # 注意



# 输入框右键菜单（剪切 / 复制 / 粘贴 / 全选）。2026-10-09 用户要求加上：手机号、演出链接
# 这些字段经常要从别处复制过来，只认 Ctrl+V 对很多人是一道坎。挂法是一次挂到 Tk 的类上（Entry / TEntry /
# Text），bind_class 影响同一 root 下所有该类控件（含以后新加的），不用回来补。TEntry 是 ttk.Entry 的类名，和 tk.Entry 不是一个类，两个都要挂。

_CLIP_MENU = None
_CLIP_TARGET = None


def _clip_select_all(widget):
    """全选。Entry 没有内置的 SelectAll 虚拟事件，只能自己来。"""
    if widget is None:
        return
    try:
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end-1c")
        else:
            widget.selection_range(0, tk.END)
            widget.icursor(tk.END)
    except Exception:
        pass


def _clip_send(event_name):
    """把剪贴板动作发给「最后右键点中的那个输入框」。

    为什么记「最后右键的」而不是查当前焦点：菜单一弹出来焦点就会跑，
      等用户点菜单项时再去问「焦点在哪」已经晚了，会贴到别的框里去。
    """
    w = _CLIP_TARGET
    if w is None:
        return
    try:
        w.event_generate(event_name)
    except Exception:
        pass


def install_clipboard_menu(root):
    """给所有输入框挂右键菜单。幂等（重复调用返回同一个菜单）。"""
    global _CLIP_MENU
    if _CLIP_MENU is not None:
        return _CLIP_MENU
    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="剪切", command=lambda: _clip_send("<<Cut>>"))
    menu.add_command(label="复制", command=lambda: _clip_send("<<Copy>>"))
    menu.add_command(label="粘贴", command=lambda: _clip_send("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="全选", command=lambda: _clip_select_all(_CLIP_TARGET))

    def popup(event):
        global _CLIP_TARGET
        _CLIP_TARGET = event.widget
        try:
            event.widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    for cls in ("Entry", "TEntry", "Text"):
        try:
            root.bind_class(cls, "<Button-3>", popup)
        except Exception:
            pass
    _CLIP_MENU = menu
    return menu


def _init_style(root):
    """统一 ttk 控件的外观。

    Windows 默认的 vista 主题几乎不让改颜色，所以切到 clam，它可定制，
    而且在各平台上长相一致，不会「这台机器上圆角、那台方角」。
    """
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, font=UI_FONT)

    # 输入框：白底、细描边；聚焦时描边变主色（Tk 默认没有任何聚焦反馈）
    style.configure(
        "TEntry",
        fieldbackground=COLOR_FIELD, background=COLOR_FIELD,
        foreground=COLOR_TEXT, insertcolor=COLOR_TEXT,
        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
        padding=(7, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", COLOR_PRIMARY)],
        lightcolor=[("focus", COLOR_PRIMARY)],
        darkcolor=[("focus", COLOR_PRIMARY)],
    )

    # 页签样式只在恢复自带标签栏时才会生效（见下面的 layout 清空），留着是为了将来万一要改回去，不用重写一遍。
    style.configure(
        "TNotebook",
        background=COLOR_BG, bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BG, darkcolor=COLOR_BG, borderwidth=0,
        tabmargins=(2, 6, 2, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=COLOR_TAB_IDLE, foreground=COLOR_HINT,
        padding=(16, 8), font=UI_FONT, borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_CARD), ("active", COLOR_PRIMARY_SOFT)],
        foreground=[("selected", COLOR_PRIMARY), ("active", COLOR_PRIMARY)],
        font=[("selected", UI_FONT_BOLD)],
    )

    # 把 Notebook 自带标签栏整条藏掉，只留内容区：ttk 标签栏平分宽度、账号一多标题就挤没了，也不支持横向滚动。
    # 改成 Notebook 只当内容容器，标签栏自己画一条可横向滚动的（_build_tabbar）。要清的是 TNotebook.Tab 的 layout，
    # 不是 TNotebook 的：清 TNotebook 的在 clam 主题下等于没改（reqheight 前后都是 32），会出现两条账号栏；留 Notebook 是因为它的 tab()/select()/index() 等接口自检要用。
    style.layout("TNotebook.Tab", [])

    style.configure(
        "Vertical.TScrollbar",
        background="#d5d8dd", troughcolor=COLOR_BG, bordercolor=COLOR_BG,
        lightcolor=COLOR_BG, darkcolor=COLOR_BG, arrowcolor=COLOR_HINT,
        borderwidth=0, arrowsize=14,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#c2c6cd")])

    style.configure("TSeparator", background=COLOR_BORDER)


def fit_window(win, min_w=0, min_h=0, extra_h=0, max_frac=0.94):
    """按内容实际需要给窗口定尺寸，再夹进屏幕可见区并居中。

    为什么不能写死 `geometry("680x450")`（客户报的「按钮看不到」就是这么来的）：
      窗口尺寸的单位是物理像素，而字体是按点算的，tk scaling
      跟着屏幕 DPI 走。本机 2560x1600 / 200% 缩放时 tk scaling ≈ 2.0，字和控件
      全部翻倍，680x450 的内容实际要 946x595。而 pack 在空间不够时优先牺牲
      后放的控件，这些窗口的按钮恰好都放在最后 → 按钮被切到窗口外面，客户
      看到的就是「按钮没了」。

      所以：布局搭完（所有控件都 pack 好之后）调用它一次，窗口该多大就多大。
      屏幕太小时按比例夹住，宁可让中间的滚动区/文本框变矮，也不让按钮出去。

    必须在_build() 的最后一行调用：reqheight 只统计已经 pack 进去的控件。
    """
    try:
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w = max(int(min_w), int(win.winfo_reqwidth()))
        h = max(int(min_h), int(win.winfo_reqheight()) + int(extra_h))
        w = min(w, int(sw * max_frac))
        h = min(h, int(sh * max_frac))
        x = max(0, int((sw - w) / 2))
        y = max(10, int((sh - h) / 2) - 40)       # 略偏上，别贴着任务栏
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        return w, h
    except Exception:                                        # pragma: no cover
        log_line("窗口自适应尺寸失败：\n" + traceback.format_exc())
        return None


def resolve_python():
    """返回用来跑抢票脚本的解释器：优先用装了 selenium 的虚拟环境"""
    # 打包版没有第二个解释器可用，就是 exe 自己。
    #   子进程由 exe 用 `--runner` 再跑一个自己（见 runner_command）。
    if getattr(sys, "frozen", False):
        return sys.executable

    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON

    # 回退：用向导自己的解释器（若是 pythonw 则换成带控制台的 python）
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


def is_frozen():
    """是不是打包出来的 exe。

    凡是「需要外部文件才能干活」的判断，都得先过这一关：
      源码形态有 `accounts.bat`、有 python.exe；打包形态两样都没有，
      子进程由 exe 自己扮演。不分形态地查外部文件，打包版就会全废。
    """
    return bool(getattr(sys, "frozen", False))


def our_chrome_pids(tm=None):
    """本程序起的浏览器进程号集合（老路 + 面板路都算）。

    为什么要按进程树认，而不是「看到 chrome 就列」：
      面板和画面墙要展示的是抢票开的那些浏览器，用户自己开的浏览器
      不该混进来（2026-10-09 测试里就混进过一个 Edge 窗口，直接把
      「每个账号一行」那条断言顶红了）。
      两条路子的根分别是：
        · 老路（向导那两个绿色按钮）→ 子进程是 exe 自己再跑一份
          （`--runner`），exe 形态下进程名跟主程序同名，认得出；
        · 面板路 → `task_manager` 手里就攥着 pid。
      从这些根往下走整棵进程树，认出来的一定是自己的
      （中间夹着 chromedriver，chrome 是孙辈）。
    源码形态下子进程也是 `python.exe`，跟别的 python 分不开，
      那种情况只认面板那条路；源码形态本来就是开发者在用，不会有这个困惑。
    """
    pids = set()
    if window_wall is None:
        return pids
    try:
        table = window_wall.process_table()
    except Exception:
        return pids
    roots = set()
    if is_frozen():
        me = os.path.basename(sys.executable).lower()
        roots |= {p for p, (_pp, nm) in table.items()
                  if nm == me and p != os.getpid()}
    if tm is not None:
        for _aid, t in list(getattr(tm, "tasks", {}).items()):
            pid = getattr(t, "pid", None)
            if pid:
                roots.add(pid)
    # 实时状态里的 pid 也是「本程序起的」，启动页那条路在源码形态下
    # 认不出同名进程（子进程也叫 python.exe），只能靠它报的 pid 认。
    if live_status is not None:
        try:
            for _aid, v in live_status.read_all().items():
                pid = v.get("pid")
                if pid:
                    roots.add(int(pid))
        except Exception:
            pass
    for p in roots:
        try:
            pids |= window_wall.descendants(p)
        except Exception:
            pass
    return pids


def runner_command(runner_args):
    """把「跑一次 account_runner 的参数」翻译成一条真正能执行的命令。

    源码形态：交给 `accounts.bat`（跟改造之前一模一样，用户还能双击它复现）。
    打包形态：既没有 .bat、也没有 python.exe，只能让 exe 自己再起一个自己，
              用 `--runner` 把参数原样转交给 account_runner.main()。
    """
    if is_frozen():
        return [sys.executable, "--runner"] + list(runner_args)
    return [ACCOUNTS_BAT] + list(runner_args)


# 起抢票 / 登录子进程（不弹控制台窗口）

def _safe_file_tag(text):
    """账号号转成能当文件名的形式。规则必须和 `task_manager._safe_name` 一致，
    两边用的是同一个 runs/ 目录，文件名对不上「查看日志」就找不到。"""
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(text or "")).strip("_")
    return safe or "account"


def runner_log_path(tag):
    """这次启动写哪个日志文件。命名跟任务面板那边一样：`<账号>.<时间戳>.log`。"""
    return os.path.join(RUNS_DIR, "{}.{}.log".format(
        _safe_file_tag(tag), time.strftime("%Y%m%d-%H%M%S")))


def spawn_runner(runner_args, env_extra=None, tag="run"):
    """起一个抢票 / 登录子进程：不弹控制台窗口，输出写进 runs/ 下的日志文件。

    返回 (proc, log_path)。要回那扇窗口时 log_path 是 None。

    为什么不再弹窗口（2026-10-09 用户要求「把弹出来的 cmd 界面隐藏掉」）：
      以前这里用 CREATE_NEW_CONSOLE，每起一个账号就弹一个黑窗，靠它盯进度。
      现在进度有「任务面板」统一看（实时状态 + 缩略图，需要人工介入的会标红
      并顶到前面），那些窗口就只剩下碍事，开得多的时候能铺满一屏。

    三条不能省的细节，少一条都会变成更难查的毛病：
      1) stdin 必须接 DEVNULL。 控制台一藏，子进程里任何 `input()` 都停在
         一个看不见的窗口上等输入，任务会永久卡住，而且不报错、不超时。
         给了 DEVNULL 之后它们立刻读到 EOF，走各自早已写好的兜底分支
         （`hold_browsers` 就是靠这个变成「原地守着浏览器直到你手动关掉」的，
         行为跟任务面板那条路完全一致）。
      2) stdout / stderr 必须落到文件。 没有窗口之后再不管输出去向，
         等于把唯一的现场证据扔了，用户报「没抢到」时我们手上一个字都没有。
      3) creationflags 必须是 CREATE_NO_WINDOW，不能什么都不传。 不传的话
         子进程会继承向导那个被隐藏的控制台，输出照样看不见，而且不报错，
         属于最难查的一类。（`console=True` 打的包才有这个隐藏控制台可继承。）

    想临时要回那扇窗口（源码形态调试用）：设环境变量 `DAMAI_SHOW_CONSOLE=1`，
    行为跟改造前一模一样。
    """
    env = os.environ.copy()
    # 子进程的日志行带图标符号（对勾 / 沙漏）时要按 UTF-8 编码；不指定的话中文 Windows 默认代码页是 GBK，
    # 第一条带符号的 print 就 UnicodeEncodeError 直接挂掉。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 本机代理会拦掉发往本地 ChromeDriver 的请求（症状：unhandled request）
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["no_proxy"] = "localhost,127.0.0.1,::1"
    env.update(env_extra or {})

    if os.environ.get("DAMAI_SHOW_CONSOLE"):
        return subprocess.Popen(
            list(runner_args), cwd=BASE_DIR, env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)), None

    log_path = runner_log_path(tag)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except Exception:
        pass
    fh = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            list(runner_args), cwd=BASE_DIR, env=env,
            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        fh.close()          # 句柄交给子进程，这边关掉不影响它继续写
    return proc, log_path


def latest_task_log(aid):
    """在 runs/ 里找这个账号最近一次任务的日志文件，找不到返回 None。

    为什么需要它：日志现在有两个来源，任务面板起的（`tm.tasks` 里记着
    路径），和向导上「开始抢票 / 登录」起的（句柄不在面板手里，只有文件落在
    runs/ 下，见 spawn_runner）。两边的文件名规则是同一条
    `<账号>.<时间戳>.log`，所以「查看日志」按钮先问面板、问不到就按文件名找。
    """
    prefix = _safe_file_tag(aid) + "."
    try:
        names = os.listdir(RUNS_DIR)
    except Exception:
        return None
    best, best_at = None, -1.0
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".log"):
            continue
        path = os.path.join(RUNS_DIR, name)
        try:
            at = os.path.getmtime(path)
        except Exception:
            continue
        if at > best_at:
            best, best_at = path, at
    return best


def read_log_file(path, max_chars=200000):
    """读一份日志（太长就只留尾巴）。口径和 `TaskManager.read_log` 一致。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception as e:
        return "（读日志失败：{}）".format(e)
    if len(data) > max_chars:
        data = "…（前面省略 {} 字符）\n".format(len(data) - max_chars) + data[-max_chars:]
    return data


def startup_precheck():
    """开抢之前问一句：这台机器上「起抢票子进程」这条路通不通？

    源码形态：要 `accounts.bat`（子进程入口）和一个装了 selenium 的解释器。
    打包形态：两样都不要，没有 .bat，也没有 python.exe，
              子进程就是 exe 自己（`runner_command` 走 `sys.executable --runner`）。

    这里踩过一个把整个程序废掉的坑：以前不分形态地先查这两个文件存在不存在，
      而打包时 `accounts.bat` 并没有随包（也该不该随包，它里面写的是
      开发机的解释器路径，在客户机上必然不存在）。于是客户填好账号、
      按下「开始抢票」，弹出来的是一句

          找不到启动文件
          缺少：D:\\...\\大麦抢票\\_internal\\accounts.bat

      登录、查状态、开抢、任务面板…… 全套功能一个都进不去。
      `runner_command` 早就分好了形态，漏的只是这两个前置检查。

    返回 True 表示可以继续；不通过时自己弹窗说清缺什么。
    """
    if is_frozen():
        return True
    if not os.path.exists(ACCOUNTS_BAT):
        messagebox.showerror("找不到启动文件", "缺少：\n" + ACCOUNTS_BAT)
        return False
    if not os.path.exists(VENV_PYTHON):
        messagebox.showerror(
            "缺少 Python 环境",
            "没找到装了 selenium 的解释器：\n{}\n\n"
            "请先装好 selenium 环境（或设环境变量 DAMAI_PYTHON 指过去）。".format(VENV_PYTHON))
        return False
    return True


def manual_start_hint():
    """启动失败时，告诉用户「还能怎么试」。

    两种形态说法必须分开：源码形态可以让他双击 `accounts.bat` 复现，
    打包形态没有那个文件，让他去找一个不存在的东西，是最糟的提示。
    """
    if is_frozen():
        return "请关掉程序重新打开再试一次；还不行就把日志发给我们。"
    return "也可以手动双击这个文件，选 [4] 开抢：\n{}".format(ACCOUNTS_BAT)


# ChromeDriver 定位。原脚本（damai/check_environment.py）找驱动的顺序是 CHROMEDRIVER_PATH 环境变量再到 PATH、
# 当前工作目录、chromedriver-autoinstaller，它不扫 Selenium Manager 的缓存、兜底还要求从 Google 下载（国内多半不通）。
# 所以这里提前把匹配的驱动找出来、通过 CHROMEDRIVER_PATH 喂给它走第一优先级；查找逻辑在 chromedriver_locator.py，向导与 account_runner 共用。
from chromedriver_locator import (  # noqa: E402
    chrome_major_version,
    download_chromedriver,
    find_cached_chromedriver,
    resolve_chromedriver as _resolve_chromedriver,
)


def resolve_chromedriver():
    """返回本机可用的 chromedriver 绝对路径；找不到返回 None。"""
    return _resolve_chromedriver(venv_python=VENV_PYTHON)


def focus_damai_window(timeout=120, interval=1.5):
    """等大麦的浏览器窗口出现后，把它切到最前。

    脚本打开的 Chrome 经常躲在其它窗口后面，用户会以为「点了没反应」。
    纯 Win32 调用，不依赖 selenium，也不碰原仓库代码。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    deadline = time.time() + timeout
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    while time.time() < deadline:
        hits = []

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if "大麦" in title and ("Chrome" in title or "Edge" in title or "Firefox" in title):
                hits.append(hwnd)
            return True

        try:
            user32.EnumWindows(proto(cb), 0)
        except Exception:
            return None

        if hits:
            hwnd = hits[0]
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                user32.FlashWindow(hwnd, True)
            except Exception:
                pass
            log_line("已把大麦窗口切到最前: hwnd=%s" % hwnd)
            return hwnd

        time.sleep(interval)
    return None


def load_existing():
    """读 damai/config.json（老的单账号路径用的那份，写出来只是为了还能手工跑 damai.py）。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clean_names(names):
    """清理姓名列表：去空、去作者的示例名、去重，保持原顺序。"""
    out = []
    for n in names or []:
        s = str(n).strip()
        if s and s not in PLACEHOLDER_USERS and s not in out:
            out.append(s)
    return out


def _legacy_roster():
    """从老版 profiles.json 里捞观演人名单（一次性迁移用）。"""
    try:
        with open(LEGACY_PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    names = list(data.get("roster") or [])
    for prof in (data.get("profiles") or {}).values():
        if isinstance(prof, dict):
            names.extend(prof.get("users") or [])
    return names


class Wizard:
    def __init__(self, root):
        self.root = root
        self.saved = load_existing()

        # 数据：accounts.json 是唯一来源
        self.acc_data = {"version": 1, "accounts": [], "roster": []}
        self.accounts = []
        self.pages = {}          # account_id -> 该分页的控件字典
        self.tab_order = []      # 页签顺序（与 Notebook 里的下标一一对应）
        self.tab_chips = {}      # 页签下标 -> 自绘标签栏里的那一个标签控件
        self.typed_creds = {}    # account_id -> (手机号, 密码)：界面刚敲、还没保存的
        self._reload_accounts()

        # 窗口标题也带上版本：任务栏 / Alt-Tab / 截图里都能认出来。
        root.title("大麦抢票 · 配置{}".format(
            " " + app_version.APP_VERSION if app_version is not None else ""))
        root.configure(bg=COLOR_BG)
        _init_style(root)
        # 所有输入框都有右键菜单（剪切 / 复制 / 粘贴 / 全选），一次挂类上，启动页 + 以后新增的输入框全覆盖。
        install_clipboard_menu(root)
        # 自适应屏幕：窗口高度不超过屏幕可用区域，保证底部按钮永远在视野内
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        win_w = int(min(1020, max(700, sw - 140)))
        win_h = int(min(860, max(480, sh - 160)))
        root.geometry("%dx%d+%d+%d" % (win_w, win_h, max(0, (sw - win_w) // 2), max(10, (sh - win_h) // 2 - 30)))
        # 最小宽度按底部按钮栏的实际需求定（实测需要 818px，加左右各 20 内边距
        # 就是 858，再留点余量），否则窗口一拖窄，最右边的「退出」就被挤出可视区。
        root.minsize(880, 460)

        self.status = tk.StringVar(value="为每个账号分别配置要抢的票")

        self._build()

        # pythonw 没有控制台，直接点右上角关闭时也要把内容落盘
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)


    @property
    def roster(self):
        """全局观演人名单（存在 accounts.json 顶层）。"""
        if not isinstance(self.acc_data.get("roster"), list):
            self.acc_data["roster"] = []
        return self.acc_data["roster"]

    def _reload_accounts(self):
        """从 accounts.json 重新读一遍账号清单 + 名单。"""
        if acc_store is None:
            self.accounts = []
            return
        try:
            self.acc_data = acc_store.load()
        except Exception:
            log_line("读取账号清单失败：\n" + traceback.format_exc())
            self.acc_data = {"version": 1, "accounts": [], "roster": []}

        if not isinstance(self.acc_data.get("roster"), list):
            self.acc_data["roster"] = []

        # 老版本把名单存在 profiles.json 里，第一次打开新版时搬过来
        if not self.acc_data["roster"]:
            legacy = _clean_names(_legacy_roster())
            if legacy:
                self.acc_data["roster"] = legacy
                log_line("已从 profiles.json 迁移观演人名单：%s" % "、".join(legacy))

        self.accounts = acc_store.all_accounts(self.acc_data)

        # 名单补齐：各账号用到的观演人若不在名单里，补进去，否则界面上勾不到
        for acc in self.accounts:
            for u in (acc.get("users") or []):
                s = str(u).strip()
                if s and s not in self.acc_data["roster"]:
                    self.acc_data["roster"].append(s)
        self.acc_data["roster"] = _clean_names(self.acc_data["roster"])

    def _persist_accounts(self):
        """把内存里的账号数据写回 accounts.json。"""
        if acc_store is None:
            return
        self.acc_data["roster"] = _clean_names(self.acc_data.get("roster") or [])
        self.acc_data["_说明"] = list(ACCOUNTS_NOTE)
        try:
            acc_store.save(self.acc_data)
        except Exception:
            log_line("写 accounts.json 失败：\n" + traceback.format_exc())


    def _build(self):
        root = self.root

        # 顶部标题（固定，不参与滚动）
        top = tk.Frame(root, bg=COLOR_CARD)
        top.pack(side="top", fill="x")
        inner_top = tk.Frame(top, bg=COLOR_CARD)
        inner_top.pack(fill="x", padx=20, pady=(16, 14))

        title_row = tk.Frame(inner_top, bg=COLOR_CARD)
        title_row.pack(anchor="w", fill="x")
        self.title_row = title_row        # 留给自检（确认按钮真的在最上面那一行）
        # 标题前一道主色竖条：成本极低，但一眼就有「被设计过」的感觉
        tk.Frame(title_row, bg=COLOR_PRIMARY, width=4, height=26).pack(side="left", padx=(0, 11))
        tk.Label(title_row, text="大麦抢票 · 配置", font=UI_FONT_TITLE,
                 fg=COLOR_TEXT, bg=COLOR_CARD).pack(side="left")
        # 版本号紧挨着名字。放这儿是为了截图：客户 / 同事发来的界面截图一眼就能看出是哪个版本，省一轮「你装的是哪版」的来回。
        if app_version is not None:
            self.version_label = tk.Label(
                title_row, text=app_version.version_label(), font=UI_FONT_SMALL,
                fg=COLOR_HINT, bg=COLOR_CARD)
            # 稍微下压一点，让它和小一号的标题在视觉上对齐基线
            self.version_label.pack(side="left", padx=(10, 0), pady=(8, 0))
            # 版本号右边紧跟一个「圆圈问号」：点开是更新日志（2026-10-10 用户要求）。做成圆圈而不是普通按钮：
            # 它是「看一眼就关」的东西，不跟右边的「环境检测」抢注意力；但位置就在版本号旁边，想找时一眼能看到。
            self.changelog_btn = self._round_help_btn(
                title_row, self.open_changelog)
            self.changelog_btn.pack(side="left", padx=(7, 0), pady=(8, 0))

        # 环境检测（放最上面、标题右侧）。跑不起来的原因十有八九不在程序本身，而在「这台机器缺什么」：
        # 没装 Chrome、驱动版本对不上、网络到不了大麦、抢票的解释器里没有 selenium。一条条问要来回好几轮，点一下全查完，缺什么直接把下载链接给出来。
        self.env_btn = self._ghost_btn(title_row, "环境检测",
                                       self.open_env_check, padx=14)
        self.env_btn.pack(side="right")
        self.env_var = tk.StringVar(value="")
        tk.Label(title_row, textvariable=self.env_var, font=UI_FONT_SMALL,
                 fg=COLOR_HINT, bg=COLOR_CARD).pack(side="right", padx=(0, 10))
        # 副标题只留一句：说明「分页 = 账号」这一个概念，其余交给界面本身表达。
        subtitle = tk.Label(
            inner_top,
            text="每个分页为一个独立账号，配置互不影响。",
            font=UI_FONT_SMALL, fg=COLOR_HINT, bg=COLOR_CARD,
            justify="left", anchor="w",
        )
        subtitle.pack(anchor="w", fill="x", pady=(9, 0))
        self._bind_wrap(subtitle, inner_top)

        tk.Frame(root, bg=COLOR_BORDER, height=1).pack(side="top", fill="x")

        # 底部按钮先 pack 到 bottom，无论窗口多矮都可见。用白底：中性按钮的浅灰底只有在白底上才看得出
        # 「这是个按钮」，放在 #f2f3f5 的窗口底色上会和背景糊成一片。
        bottom = tk.Frame(root, bg=COLOR_CARD)
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, bg=COLOR_BORDER, height=1).pack(side="top", fill="x")
        inner_bottom = tk.Frame(bottom, bg=COLOR_CARD)
        inner_bottom.pack(fill="x", padx=20, pady=(11, 12))

        bar = tk.Frame(inner_bottom, bg=COLOR_CARD)
        bar.pack(side="top", fill="x")
        self._primary_btn(bar, "保存并开始",
                          lambda: self.start(all_accounts=False), padx=18).pack(side="left")
        self._soft_btn(bar, "全部账号启动",
                       lambda: self.start(all_accounts=True), padx=14).pack(side="left", padx=(8, 0))
        self._ghost_btn(bar, "仅保存", self.save_only,
                        padx=12).pack(side="left", padx=(8, 0))
        self._ghost_btn(bar, "＋ 新建", self.add_account,
                        padx=12).pack(side="left", padx=(8, 0))
        # 新增的一条路：所有账号的任务在一个窗口里看、能单独停。上面那几个按钮的行为一点没动（子进程的控制台窗口两条路都不弹了）。
        self._ghost_btn(bar, "任务面板", self.open_task_panel,
                        padx=12).pack(side="left", padx=(8, 0))
        # 画面墙：把所有开着的浏览器窗口缩成图摆一起，哪个在等验证码一眼看得见。
        self._ghost_btn(bar, "任务画面", self.open_task_wall,
                        padx=12).pack(side="left", padx=(8, 0))
        self._ghost_btn(bar, "退出", self._on_close, padx=12).pack(side="right")

        # 状态行：左边是操作提示。
        status_row = tk.Frame(inner_bottom, bg=COLOR_CARD)
        status_row.pack(side="bottom", fill="x", pady=(9, 0))
        tk.Label(status_row, textvariable=self.status, font=UI_FONT_SMALL,
                 fg=COLOR_HINT, bg=COLOR_CARD, anchor="w").pack(side="left")

        # 中间：账号分页（内容超出时整块滚动，不挤占按钮位置）
        mid = tk.Frame(root, bg=COLOR_BG)
        mid.pack(side="top", fill="both", expand=True, padx=16, pady=(12, 4))
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        canvas = tk.Canvas(mid, highlightthickness=0, borderwidth=0, bg=COLOR_BG)
        self.canvas = canvas          # 留给外部（截图 / 自检）滚动用
        sb = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        inner = tk.Frame(canvas, bg=COLOR_BG)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(0, weight=1)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))

        def on_wheel(event):
            # 鼠标落在文本框里时交给文本框自己滚，不抢滚轮
            if isinstance(event.widget, tk.Text):
                return
            # 落在账号栏上时交给账号栏横向滚（它自己有绑定），别再纵向滚一次
            if self._in_tabbar(event.widget):
                return
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind_all("<MouseWheel>", on_wheel)

        # 账号栏（横向可滑动）先于内容区建，它在视觉上属于「内容」的一部分
        self._build_tabbar(inner, row=0)

        self.nb = ttk.Notebook(inner)
        self.nb.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._render_tabs()

    # 外观小工具。原生 Tk 控件默认底色是系统灰，直接摆在白卡片上很刺眼，所以每个控件都得显式带 bg。
    # 这几个 helper 把「配色 + 悬停 + 内边距」收在一处，免得几十个控件各写一份、改配色时漏掉几个。

    def _mk_btn(self, parent, text, command, *, bg, fg, hover, font=None, padx=18):
        """扁平按钮 + Tk 原生缺失的悬停反馈。"""
        btn = tk.Button(
            parent, text=text, command=command, font=font or UI_FONT,
            bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=padx, pady=8, cursor="hand2",
        )
        btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
        return btn

    def _primary_btn(self, parent, text, command, font=None, padx=22):
        """页面里最重要的那一个动作，一屏只该有一个。"""
        return self._mk_btn(parent, text, command, bg=COLOR_PRIMARY, fg="#ffffff",
                            hover=COLOR_PRIMARY_HOVER, font=font or UI_FONT_BOLD, padx=padx)

    def _soft_btn(self, parent, text, command, font=None, padx=18):
        """次要动作，用主色的浅底，视觉上排在主按钮之后。"""
        return self._mk_btn(parent, text, command, bg=COLOR_PRIMARY_SOFT, fg=COLOR_PRIMARY,
                            hover=COLOR_PRIMARY_SOFT_HOVER, font=font, padx=padx)

    def _ghost_btn(self, parent, text, command, font=None, padx=16, fg=None):
        """中性动作（保存 / 新建 / 退出）。"""
        return self._mk_btn(parent, text, command, bg=COLOR_GHOST, fg=fg or COLOR_TEXT,
                            hover=COLOR_GHOST_HOVER, font=font or UI_FONT, padx=padx)

    @staticmethod
    def _round_help_btn(parent, command, size=16):
        """一个圆圈问号，点了执行 `command`。用在版本号旁边（点开更新日志）。

        为什么用 Canvas 画而不是 tk.Button / tk.Label：
          Tk 没有圆角控件，Label 的 highlight 边框永远是方的，Button 是
          系统主题的立体灰块，摆在标题行上既不圆也不像「帮助」。圆圈只能自己画。
        热区就是这 16×16 的画布本身，够点（比 Tk 默认 8×8 的复选框大）。
        """
        cv = tk.Canvas(parent, width=size, height=size, bg=COLOR_CARD,
                       highlightthickness=0, borderwidth=0, cursor="hand2")
        ring = cv.create_oval(1, 1, size - 1, size - 1, outline=COLOR_HINT)
        mark = cv.create_text(size // 2, size // 2, text="?", fill=COLOR_HINT,
                              font=UI_FONT_SMALL)

        def paint(color):
            cv.itemconfigure(ring, outline=color)
            cv.itemconfigure(mark, fill=color)

        cv.bind("<Enter>", lambda _e: paint(COLOR_PRIMARY))
        cv.bind("<Leave>", lambda _e: paint(COLOR_HINT))
        cv.bind("<Button-1>", lambda _e: command())
        return cv

    def _section(self, parent, title, hint=None):
        """一块白底卡片 + 顶部一行标题。返回 (卡片, 放内容的 frame)。

        用它代替 tk.LabelFrame：LabelFrame 的标题压在边框线上，Windows 下
        既不好看、标题对齐也难调。
        """
        card = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1, borderwidth=0)
        head = tk.Frame(card, bg=COLOR_CARD)
        head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Frame(head, bg=COLOR_PRIMARY, width=3, height=14).pack(side="left", padx=(0, 8))
        tk.Label(head, text=title, font=UI_FONT_BOLD, fg=COLOR_TEXT,
                 bg=COLOR_CARD).pack(side="left")
        if hint:
            tk.Label(head, text=hint, font=UI_FONT_SMALL, fg=COLOR_HINT,
                     bg=COLOR_CARD).pack(side="left", padx=(8, 0))
        body = tk.Frame(card, bg=COLOR_CARD)
        body.pack(fill="both", expand=True, padx=16, pady=(10, 14))
        return card, body

    @staticmethod
    def _label(parent, text, font=None, fg=None, bg=None, **kw):
        """说明文字，默认挂在白卡片上。"""
        return tk.Label(parent, text=text, font=font or UI_FONT_SMALL,
                        fg=fg or COLOR_HINT, bg=bg or COLOR_CARD, **kw)

    @staticmethod
    def _check(parent, text, variable, command=None, bg=None, font=None,
               fg=None, anchor="w"):
        """复选框。不设 activebackground / selectcolor 的话，鼠标划过会闪回系统灰。"""
        b = bg or COLOR_CARD
        return tk.Checkbutton(
            parent, text=text, variable=variable, command=command,
            font=font or UI_FONT, fg=fg or COLOR_TEXT, bg=b,
            activebackground=b, activeforeground=fg or COLOR_TEXT,
            selectcolor=b, anchor=anchor, justify="left",
            highlightthickness=0, borderwidth=0, cursor="hand2",
        )

    @staticmethod
    def _textbox(parent, height=3, **kw):
        """多行输入框。用 highlight 描边替掉 Tk 默认的立体凹槽边框。"""
        return tk.Text(
            parent, height=height, font=UI_FONT, wrap="none",
            bg=COLOR_FIELD, fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
            relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_PRIMARY, padx=8, pady=6, **kw,
        )

    @staticmethod
    def _bind_wrap(label, container, margin=0):
        """让 label 的折行宽度跟着容器走。

        wraplength 是个「写死的像素值」，窗口拉窄时文字会超出容器，
        而 Tk 的 Label 此时会把整段文字居中，左右两边一起被裁掉。
        所以长说明文字一律挂上这个：窗口变宽就重新铺满，变窄就自动折行。
        """
        def on_conf(event):
            want = max(160, event.width - margin)
            if label.cget("wraplength") != want:
                label.configure(wraplength=want)

        container.bind("<Configure>", on_conf, add="+")


    # 账号栏：横向可滑动（为什么不用 Notebook 自带标签栏见 _init_style）。形状是 [‹] │ 账号一 │ 账号二 │ … │ [›]，
    # 每个标签宽度由标题自己决定，账号变多只是需要横向滚动、不互相挤压。两端各有一个翻页按钮，滚轮 / 拖拽也能滚。
    TABBAR_PAD = 12          # 两端箭头按钮的宽度
    TABBAR_CHIP_PADX = 16    # 单个标签的左右内边距

    def _build_tabbar(self, parent, row):
        shell = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                         highlightthickness=1, borderwidth=0)
        shell.grid(row=row, column=0, sticky="ew")
        self.tabbar_shell = shell

        # 两端箭头：默认不占位，只有内容溢出时才 pack 出来（见 _sync_tabbar_arrows）
        self.tb_left = self._mk_btn(
            shell, "‹", lambda: self._scroll_tabbar(-1),
            bg=COLOR_CARD, fg=COLOR_HINT, hover=COLOR_GHOST, padx=6, font=UI_FONT_BOLD)
        self.tb_right = self._mk_btn(
            shell, "›", lambda: self._scroll_tabbar(1),
            bg=COLOR_CARD, fg=COLOR_HINT, hover=COLOR_GHOST, padx=6, font=UI_FONT_BOLD)

        self.tb_canvas = tk.Canvas(shell, bg=COLOR_CARD, highlightthickness=0,
                                   borderwidth=0, height=44)
        self.tb_canvas.pack(side="left", fill="x", expand=True, padx=4, pady=5)

        # 标签排在 strip 里，strip 的宽度 = 所有标签宽度之和（不会被拉伸）
        self.tab_strip = tk.Frame(self.tb_canvas, bg=COLOR_CARD)
        self.tb_window = self.tb_canvas.create_window(
            (0, 0), window=self.tab_strip, anchor="nw")

        self.tab_chips = {}          # 账号 → 标签控件（用来切高亮 / 滚进视野）

        self.tab_strip.bind(
            "<Configure>",
            lambda e: self.tb_canvas.configure(scrollregion=self.tb_canvas.bbox("all")))
        self.tb_canvas.bind("<Configure>", lambda e: self._sync_tabbar_arrows())

        # 滚轮横滚。绑在 canvas 和 strip 上：鼠标停在标签（strip 的子控件）上时，事件会冒泡到 strip，同样能滚。
        for w in (self.tb_canvas, self.tab_strip):
            w.bind("<MouseWheel>", self._on_tabbar_wheel)
        self.tb_canvas.bind("<ButtonPress-1>", self._on_tabbar_drag_start)
        self.tb_canvas.bind("<B1-Motion>", self._on_tabbar_drag)

    def _in_tabbar(self, widget):
        """这个控件是不是账号栏这一挂的（决定滚轮该横滚还是纵滚）。"""
        try:
            node = widget
            while node is not None:
                if node is getattr(self, "tabbar_shell", None):
                    return True
                node = node.master
        except Exception:
            pass
        return False

    def _on_tabbar_wheel(self, event):
        self.tb_canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
        self._sync_tabbar_arrows()
        return "break"          # 别再冒泡去触发外层纵向滚动

    def _on_tabbar_drag_start(self, event):
        self._tb_drag_x = event.x
        self.tb_canvas.scan_mark(event.x, event.y)

    def _on_tabbar_drag(self, event):
        self.tb_canvas.scan_dragto(event.x, event.y, gain=1)
        self._sync_tabbar_arrows()

    def _scroll_tabbar(self, direction):
        """左右翻页按钮。一次翻「一屏的 80%」，保留一点上下文。"""
        width = max(120, int(self.tb_canvas.winfo_width() * 0.8))
        self.tb_canvas.xview_scroll(-width if direction < 0 else width, "units")
        self._sync_tabbar_arrows()

    def _tabbar_overflow(self):
        """内容是否超出可视宽度。"""
        try:
            need = self.tab_strip.winfo_reqwidth()
            have = self.tb_canvas.winfo_width()
        except Exception:
            return False
        return need > have + 2 and have > 1

    def _sync_tabbar_arrows(self):
        """只在内容溢出时才显示左右箭头。

        不溢出时把箭头收起来，免得一两个账号的界面出现两颗点了没反应的灰按钮。
        """
        try:
            if self._tabbar_overflow():
                if not self.tb_left.winfo_ismapped():
                    self.tb_left.pack(side="left", fill="y", padx=(6, 0))
                if not self.tb_right.winfo_ismapped():
                    self.tb_right.pack(side="right", fill="y", padx=(0, 6))
            else:
                if self.tb_left.winfo_ismapped():
                    self.tb_left.pack_forget()
                if self.tb_right.winfo_ismapped():
                    self.tb_right.pack_forget()
                self.tb_canvas.xview_moveto(0)
        except Exception:
            pass

    def _scroll_chip_into_view(self, idx):
        """把选中的标签滚进可视区。

        必须做：翻到第 8 个账号之后，重建分页时若不做这一步，
        选中的标签会停在可视区外面，用户看到的是「界面没反应」。
        """
        chip = self.tab_chips.get(idx)
        if chip is None:
            return
        try:
            self.tab_strip.update_idletasks()
            x0 = chip.winfo_x()
            x1 = x0 + chip.winfo_width()
            view_w = self.tb_canvas.winfo_width()
            total = max(1, self.tab_strip.winfo_reqwidth())
            if view_w <= 1 or total <= view_w:
                return
            off = -self.tb_canvas.canvasx(0)      # 当前已经滚过去多少像素
            if x0 < off:
                self.tb_canvas.xview_moveto(max(0.0, x0 / float(total)))
            elif x1 > off + view_w:
                self.tb_canvas.xview_moveto(
                    max(0.0, min(1.0, (x1 - view_w) / float(total))))
            self._sync_tabbar_arrows()
        except Exception:
            pass

    def _render_tabbar(self):
        """按 tab_order 重建标签栏。

        标签文字不再直接用 Notebook 的页签标题（那里面有城市 / 日期 / 票档，
          一个标签就有 400 像素宽）。渲染时一律走 `_chip_label()`：
          平时是「账号1 / 账号2」，选中的那个显示手机号。
          页签标题（`nb.tab(i,"text")`）保持原样不变，它还有别的用处，
          而且真正的「这一页配的是什么票」在页内摘要里写得更清楚。
        """
        strip = getattr(self, "tab_strip", None)
        if strip is None:
            return
        for w in strip.winfo_children():
            w.destroy()
        self.tab_chips = {}

        for i, aid in enumerate(self.tab_order):
            chip = self._make_chip(strip, i, self._chip_label(i, aid))
            chip.pack(side="left", padx=(0, 6))
            self.tab_chips[i] = chip

        strip.update_idletasks()
        self._sync_tabbar_arrows()


    def _refresh_chip_phones(self):
        """把「账号到已存手机号」的对应关系一次性查好备用。

        账号栏每次切换都要重画，每次都去问一遍磁盘没必要。
          这里只在「账号清单变了」或「存/删了密码」时查一遍。
        """
        phones = {}
        if creds is not None:
            for aid in (self.tab_order or []):
                if not aid:
                    continue
                try:
                    if creds.has(aid):
                        phones[aid] = str(creds.get(aid).get("phone") or "").strip()
                except Exception:
                    continue
        self._chip_phones = phones
        return phones

    def _chip_label(self, idx, aid=None, selected=None):
        """账号栏上那个小标签写什么。

        默认只写「账号N」，一行放得下，账号多了也不会把标签撑成 400 像素宽
        （旧标题带城市/日期/票档时，10 个账号要 4000 像素，每次切换都得横向滚，
        既慢又晃眼）。

        选中的那个改成显示手机号：这是「认人」最直接的信息，
        一眼就知道现在这一页是哪个号，不用点进去看。
        没存过手机号（还没填过密码）就仍显示「账号N」。

        「停用」的标记保留，旧标签上有，砍掉等于把信息弄丢了。
        """
        if aid is None:
            aid = self.tab_order[idx] if 0 <= idx < len(self.tab_order) else ""
        if not aid:
            # 一个账号都没有时，tab_order 里放的是一个空串占位，这时候不能算成「账号1」，那会让人以为真有一个账号。
            return "（空）"
        if selected is None:
            selected = (idx == self._current_index())

        text = ""
        if selected:
            text = (getattr(self, "_chip_phones", None) or {}).get(aid, "")
        if not text:
            text = "账号{}".format(idx + 1)

        try:
            acc = acc_store.find(self.acc_data, aid) if acc_store is not None else None
            if acc is not None and not acc.get("enabled", True):
                text = "[停用] " + text
        except Exception:
            pass
        return text

    def _make_chip(self, parent, idx, title):
        """一个标签。选中态 = 主色浅底 + 主色字 + 底部一道主色横条 + 加粗。"""
        selected = (idx == self._current_index())
        bg = COLOR_PRIMARY_SOFT if selected else COLOR_CARD

        chip = tk.Frame(parent, bg=bg, cursor="hand2")
        # 底部横条：所有标签都留这个高度，选中时才上色，免得选中时整块跳一下
        accent = tk.Frame(chip, height=2, bg=COLOR_PRIMARY if selected else bg)
        accent.pack(side="bottom", fill="x")

        label = tk.Label(chip, text=title, font=UI_FONT_BOLD if selected else UI_FONT,
                         fg=COLOR_PRIMARY if selected else COLOR_TEXT,
                         bg=bg, cursor="hand2", padx=self.TABBAR_CHIP_PADX, pady=9)
        label.pack(side="top", fill="both", expand=True)

        def hover_on(_e, c=chip, a=accent, l=label):
            if idx == self._current_index():
                return
            c.configure(bg=COLOR_GHOST)
            l.configure(bg=COLOR_GHOST)
            a.configure(bg=COLOR_GHOST)

        def hover_off(_e, c=chip, a=accent, l=label):
            if idx == self._current_index():
                return
            c.configure(bg=COLOR_CARD)
            l.configure(bg=COLOR_CARD)
            a.configure(bg=COLOR_CARD)

        for w in (chip, label):
            w.bind("<Button-1>", lambda e, i=idx: self._select_tab(i))
            w.bind("<Enter>", hover_on)
            w.bind("<Leave>", hover_off)
            w.bind("<MouseWheel>", self._on_tabbar_wheel)
        return chip

    def _select_tab(self, idx):
        """点标签切换分页。Notebook 自己会发 <<NotebookTabChanged>>，
        高亮由 _on_tab_changed 统一刷新，这里不重复做。"""
        try:
            self.nb.select(idx)
        except Exception:
            pass
        return "break"

    def _current_index(self):
        """当前分页下标；拿不到返回 -1。"""
        try:
            return int(self.nb.index(self.nb.select()))
        except Exception:
            return -1

    def _tab_title(self, acc):
        aid = str(acc.get("account_id"))
        bits = []
        if acc.get("city"):
            bits.append(str(acc["city"]))
        if acc.get("calendar_date"):
            bits.append("日历 " + str(acc["calendar_date"]))
        if acc.get("dates"):
            bits.append(str(acc["dates"][0]))
        if acc.get("prices"):
            bits.append("¥" + "/".join(str(p) for p in acc["prices"][:3]))
        title = aid if not bits else "{}  ·  {}".format(aid, "  ".join(bits))
        if not acc.get("enabled", True):
            title = "[停用] " + title
        return title[:46]

    def _render_tabs(self, recollect=True):
        """按账号清单重建所有分页。

        recollect=True 时会先把界面上还没保存的内容收进内存。调用方如果刚刚
        在内存里改过账号数据（新建/删除账号、改名单），必须传 False，
        否则这次收集会把界面上的旧值又盖回去。
        """
        want = self._current_aid()      # 记住重建之前停在哪个账号，重建后切回去
        if recollect and self.pages:
            self._collect_all()

        for w in self.nb.winfo_children():
            w.destroy()
        self.pages = {}
        self.tab_order = []

        if not self.accounts:
            placeholder = tk.Frame(self.nb, padx=20, pady=20, bg=COLOR_BG)
            self._label(
                placeholder, "尚未添加账号。点下方「＋ 新建」开始。",
                font=UI_FONT, fg=COLOR_HINT, bg=COLOR_BG,
            ).pack(anchor="w")
            self.nb.add(placeholder, text="（空）")
            self.tab_order.append("")
            self._render_tabbar()
            self._fit_notebook_height()
            return

        for i, acc in enumerate(self.accounts):
            aid = str(acc.get("account_id"))
            page = self._build_one_tab(acc)
            self.nb.add(page["tab"], text=self._tab_title(acc))
            self.pages[aid] = page
            self.tab_order.append(aid)

        # 尽量回到重建之前那个账号
        idx = self._tab_index(want)
        if idx is not None:
            try:
                self.nb.select(idx)
            except Exception:
                pass
        # 顺序：先建完所有分页再重建标签栏，标签是照着 tab_order 画的，建早了拿不到完整的顺序。
        self._refresh_chip_phones()
        self._render_tabbar()
        # 重建之后把选中的标签滚进可视区（只在这里滚一次，见 _on_tab_changed）。增删账号后标签集合整个变了，
        # 选中项确实可能在视野外，不滚的话用户看到的是「点了没反应」。
        self._scroll_chip_into_view(self._current_index())
        self._fit_notebook_height()
        self._update_all_summaries()

    def _build_one_tab(self, acc):
        """一个账号一个页签。页里既有「用谁的身份抢」，也有「抢什么」。"""
        aid = str(acc.get("account_id"))
        page = {"account_id": aid}

        tab = tk.Frame(self.nb, padx=4, pady=6, bg=COLOR_BG)
        tab.columnconfigure(0, weight=1)
        page["tab"] = tab
        r = 0

        # 第 1 段：这个账号是谁、要不要用
        head_card, head = self._section(tab, "账号")
        head_card.grid(row=r, column=0, sticky="ew")
        head.columnconfigure(2, weight=1)
        r += 1

        page["enabled"] = tk.BooleanVar(value=bool(acc.get("enabled", True)))
        self._check(
            head, "启用", page["enabled"],
            command=lambda a=aid: self._on_enabled_toggle(a),
        ).grid(row=0, column=0, sticky="w")

        self._label(head, "备注").grid(row=0, column=1, sticky="w", padx=(16, 4))
        page["note"] = ttk.Entry(head, font=UI_FONT, width=28)
        page["note"].grid(row=0, column=2, sticky="w")
        if acc.get("note"):
            page["note"].insert(0, str(acc["note"]))

        page["state"] = self._label(head, "", fg=COLOR_HINT, anchor="w")
        page["state"].grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        row_btn = tk.Frame(head, bg=COLOR_CARD)
        row_btn.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self._ghost_btn(row_btn, "登录", lambda a=aid: self._login_account(a),
                        font=UI_FONT_SMALL, padx=14).pack(side="left")
        self._ghost_btn(row_btn, "检查登录状态", lambda a=aid: self._launch_accounts("status", [a]),
                        font=UI_FONT_SMALL, padx=14).pack(side="left", padx=(6, 0))
        self._ghost_btn(row_btn, "删除账号", lambda a=aid: self.delete_account(a),
                        font=UI_FONT_SMALL, padx=14, fg=COLOR_WARN).pack(side="left", padx=(6, 0))

        # 第 2 段：手机号 / 密码（存本机）
        cred_card, cred_box = self._section(tab, "手机号 / 密码", "本机存储")
        cred_card.grid(row=r, column=0, sticky="ew", pady=(10, 0))
        cred_box.columnconfigure(5, weight=1)
        r += 1

        self._label(cred_box, "手机号 / 账号").grid(row=0, column=0, sticky="w")
        page["phone"] = ttk.Entry(cred_box, font=UI_FONT, width=18)
        page["phone"].grid(row=0, column=1, sticky="w", padx=(6, 0))
        self._label(cred_box, "密码").grid(row=0, column=2, sticky="w", padx=(12, 0))
        page["pwd"] = ttk.Entry(cred_box, font=UI_FONT, width=18, show="*")
        page["pwd"].grid(row=0, column=3, sticky="w", padx=(6, 0))
        self._soft_btn(cred_box, "保存", lambda a=aid: self.save_cred(a),
                       font=UI_FONT_SMALL, padx=14).grid(row=0, column=4, padx=(12, 0))
        self._ghost_btn(cred_box, "清除", lambda a=aid: self.drop_cred(a),
                        font=UI_FONT_SMALL, padx=14).grid(row=0, column=5, sticky="w", padx=(6, 0))
        self._label(
            cred_box,
            "存在本机 credentials.json 里，不写入 accounts.json，不输出到日志。\n"
            "验证码 / 滑块 / 短信由本人完成，程序只等待、不绕过。",
            anchor="w", justify="left",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        # 第 3 段：抢什么
        show_card, show = self._section(tab, "抢票配置")
        show_card.grid(row=r, column=0, sticky="ew", pady=(10, 0))
        show.columnconfigure(0, weight=1)
        r += 1

        sr = 0
        self._label(show, "演出链接", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=sr, column=0, sticky="w")
        sr += 1
        page["url"] = ttk.Entry(show, font=UI_FONT)
        page["url"].grid(row=sr, column=0, sticky="ew", pady=(3, 12))
        sr += 1

        # 城市 / 场次 / 票档（日历日期在下面单独一行，和场次分开）
        row3 = tk.Frame(show, bg=COLOR_CARD)
        row3.grid(row=sr, column=0, sticky="ew")
        row3.columnconfigure(0, weight=0)
        row3.columnconfigure(1, weight=1)
        row3.columnconfigure(2, weight=1)
        sr += 1

        cbox = tk.Frame(row3, bg=COLOR_CARD)
        cbox.grid(row=0, column=0, sticky="nw", padx=(0, 14))
        self._label(cbox, "城市", font=UI_FONT_BOLD, fg=COLOR_TEXT).pack(anchor="w")
        self._label(cbox, "例：北京").pack(anchor="w")
        page["city"] = ttk.Entry(cbox, font=UI_FONT, width=14)
        page["city"].pack(anchor="w", pady=(3, 0))

        dbox = tk.Frame(row3, bg=COLOR_CARD)
        dbox.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        dbox.columnconfigure(0, weight=1)
        self._label(dbox, "场次", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=0, column=0, sticky="w")
        self._label(dbox, "每行一个 · 详情页「场次」那一栏的原文").grid(row=1, column=0, sticky="w")
        page["dates"] = self._textbox(dbox, height=3)
        page["dates"].grid(row=2, column=0, sticky="ew", pady=(3, 0))

        pbox = tk.Frame(row3, bg=COLOR_CARD)
        pbox.grid(row=0, column=2, sticky="ew", padx=(10, 0))
        pbox.columnconfigure(0, weight=1)
        self._label(pbox, "票档价格", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=0, column=0, sticky="w")
        self._label(pbox, "每行一个 · 靠前的优先 · 数字或页面原文都行").grid(row=1, column=0, sticky="w")
        page["prices"] = self._textbox(pbox, height=3)
        page["prices"].grid(row=2, column=0, sticky="ew", pady=(3, 0))

        # 日历日期：和场次分开的独立一项，选填。有的演出详情页日期是「月历格子」，场次列表只显示选中那天的
        # 场次，不先把那一天点掉，页面上根本没有那天的场次文本。不是这种页面就留空。
        calbox = tk.Frame(show, bg=COLOR_CARD)
        calbox.grid(row=sr, column=0, sticky="ew", pady=(12, 0))
        sr += 1
        self._label(calbox, "日历日期（选填）", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).pack(side="left")
        page["calendar_date"] = ttk.Entry(calbox, font=UI_FONT, width=16)
        page["calendar_date"].pack(side="left", padx=(8, 0))
        self._label(calbox, "例 2026-12-26").pack(side="left", padx=(8, 0))
        self._label(show, "只在详情页的日期是「月历格子」时才填（这种页面场次只显示"
                          "选中那天的，不先点掉那天就永远选不中）；其余页面留空即可，"
                          "它和左边的「场次」不是一回事。").grid(row=sr, column=0, sticky="w", pady=(2, 0))
        sr += 1

        tk.Frame(show, height=12, bg=COLOR_CARD).grid(row=sr, column=0)
        sr += 1

        # 观演人：全局名单，每个账号勾自己要用的人
        self._label(show, "观演人", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=sr, column=0, sticky="w")
        sr += 1
        self._label(show, "姓名须与大麦账号中已添加的观演人完全一致；名单为所有账号共用").grid(
            row=sr, column=0, sticky="w")
        sr += 1

        page["users_box"] = tk.Frame(show, bg=COLOR_INSET, highlightbackground=COLOR_BORDER,
                                     highlightthickness=1, borderwidth=0, padx=10, pady=8)
        page["users_box"].grid(row=sr, column=0, sticky="ew", pady=(3, 8))
        page["users_box"].columnconfigure(0, weight=1)
        page["users_box"].columnconfigure(1, weight=1)
        sr += 1

        add = tk.Frame(show, bg=COLOR_CARD)
        add.grid(row=sr, column=0, sticky="ew")
        sr += 1
        page["new_person"] = ttk.Entry(add, font=UI_FONT, width=14)
        page["new_person"].pack(side="left")
        page["new_person"].bind("<Return>", lambda e, a=aid: self.add_person(a))
        self._ghost_btn(add, "＋ 添加", lambda a=aid: self.add_person(a),
                        font=UI_FONT_SMALL, padx=14).pack(side="left", padx=(8, 0))
        self._ghost_btn(add, "删除勾选项",
                        lambda a=aid: self.remove_checked_persons(a),
                        font=UI_FONT_SMALL, padx=14).pack(side="left", padx=(8, 0))

        tk.Frame(show, height=12, bg=COLOR_CARD).grid(row=sr, column=0)
        sr += 1

        nums = tk.Frame(show, bg=COLOR_CARD)
        nums.grid(row=sr, column=0, sticky="ew")
        sr += 1
        self._label(nums, "购票数量", font=UI_FONT_BOLD, fg=COLOR_TEXT).pack(side="left")
        page["qty"] = ttk.Entry(nums, font=UI_FONT, width=6)
        page["qty"].pack(side="left", padx=(8, 0))
        self._label(nums, "不超过已勾选人数").pack(side="left", padx=(6, 22))
        self._label(nums, "轮询上限", font=UI_FONT_BOLD, fg=COLOR_TEXT).pack(side="left")
        page["retry"] = ttk.Entry(nums, font=UI_FONT, width=10)
        page["retry"].pack(side="left", padx=(8, 0))

        tk.Frame(show, height=12, bg=COLOR_CARD).grid(row=sr, column=0)
        sr += 1

        # 刷新频率（可选）。留空 = 不写这个字段，后端按 fast_mode 走默认值。
        self._label(show, "刷新频率", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=sr, column=0, sticky="w")
        sr += 1
        self._label(show, "整页刷新每隔多久一次（发请求的那个）。留空 = 用默认值："
                          "「还没开票」时 0.3 秒、等配置的场次/票档时 3.5 秒。"
                          "填了按你填的走；不到 2.5 秒时，等票那一圈仍按 2.5 秒。").grid(
            row=sr, column=0, sticky="w")
        sr += 1
        rbox = tk.Frame(show, bg=COLOR_CARD)
        rbox.grid(row=sr, column=0, sticky="ew", pady=(4, 0))
        sr += 1
        page["refresh"] = ttk.Entry(rbox, font=UI_FONT, width=8)
        page["refresh"].pack(side="left")
        self._label(rbox, "秒").pack(side="left", padx=(6, 12))
        page["refresh_warn"] = self._label(rbox, "")
        page["refresh_warn"].pack(side="left")
        # 每敲一下只更新提示文字，不落盘，落盘发生在「仅保存 / 开始抢票」时。
        page["refresh"].bind("<KeyRelease>",
                             lambda e, a=aid: self._on_refresh_typed(a))

        tk.Frame(show, height=12, bg=COLOR_CARD).grid(row=sr, column=0)
        sr += 1

        switches = tk.Frame(show, bg=COLOR_CARD)
        switches.grid(row=sr, column=0, sticky="ew")
        sr += 1
        page["auto_login"] = tk.BooleanVar(value=bool(acc.get("auto_login", True)))
        self._check(
            switches, "启动时自动登录", page["auto_login"],
        ).pack(anchor="w", pady=(2, 0))
        page["if_listen"] = tk.BooleanVar(value=bool(acc.get("if_listen", True)))
        self._check(
            switches, "持续轮询余票", page["if_listen"],
        ).pack(anchor="w", pady=(6, 0))
        page["if_commit_order"] = tk.BooleanVar(value=bool(acc.get("if_commit_order", False)))
        self._check(
            switches, "自动提交订单（含协议勾选与下单扣款）",
            page["if_commit_order"], command=lambda a=aid: self._confirm_commit(a),
        ).pack(anchor="w", pady=(6, 0))

        tk.Frame(show, height=10, bg=COLOR_CARD).grid(row=sr, column=0)
        sr += 1

        self._label(show, "PC 端下单", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT).grid(row=sr, column=0, sticky="w")
        sr += 1

        details = tk.Frame(show, bg=COLOR_CARD)
        details.grid(row=sr, column=0, sticky="ew", pady=(5, 0))
        sr += 1
        page["dismiss_mask"] = tk.BooleanVar(value=bool(acc.get("dismiss_mask", True)))
        self._check(
            details, "自动关闭遮挡弹窗", page["dismiss_mask"],
        ).pack(anchor="w", pady=(2, 0))
        page["no_seat"] = tk.BooleanVar(value=bool(acc.get("no_seat", True)))
        self._check(
            details, "优先不选座购票", page["no_seat"],
        ).pack(anchor="w", pady=(6, 0))
        page["random_seat"] = tk.BooleanVar(value=bool(acc.get("random_seat", True)))
        self._check(
            details, "选座场次自动选座", page["random_seat"],
        ).pack(anchor="w", pady=(6, 0))

        # 页内摘要 + 每页自己的提示行
        page["summary"] = self._label(tab, "", fg=COLOR_HINT, bg=COLOR_BG,
                                      anchor="w", justify="left", wraplength=900)
        page["summary"].grid(row=r, column=0, sticky="ew", pady=(12, 0), padx=6)
        r += 1
        page["msg"] = self._label(tab, "", fg=COLOR_HINT, bg=COLOR_BG,
                                  anchor="w", justify="left", wraplength=900)
        page["msg"].grid(row=r, column=0, sticky="ew", pady=(4, 0), padx=6)
        # 摘要是拼出来的、长短随内容变，折行宽度得跟着窗口走（含 tab 内边距 + grid padx）
        self._bind_wrap(page["summary"], tab, margin=20)
        self._bind_wrap(page["msg"], tab, margin=20)

        page["url"].insert(0, str(acc.get("target_url") or ""))
        page["city"].insert(0, str(acc.get("city") or ""))
        if acc.get("dates"):
            page["dates"].insert("1.0", "\n".join(str(x) for x in acc["dates"]))
        page["calendar_date"].insert(0, str(acc.get("calendar_date") or ""))
        if acc.get("prices"):
            page["prices"].insert("1.0", "\n".join(str(x) for x in acc["prices"]))
        page["qty"].insert(0, str(acc.get("quantity") or 1))
        page["retry"].insert(0, str(acc.get("max_retries") or 10000))
        # 刷新频率没设过就留空，空 ≠ 0，空代表「不写这个字段，用默认值」。
        raw_refresh = acc.get("refresh_interval")
        if raw_refresh not in (None, ""):
            page["refresh"].insert(0, str(raw_refresh))
        self._render_user_checks(page, acc)
        self._fill_cred_entries(aid, page)
        self._refresh_state_labels(aid)
        # 提示文字要在控件值灌完之后算（这只是一个静态判断，不触发收集）
        self._on_refresh_typed(aid, touch=False)

        # 开关一动，页内摘要跟着更新
        for key in ("auto_login", "if_listen", "if_commit_order",
                    "dismiss_mask", "no_seat", "random_seat"):
            page[key].trace_add("write", lambda *a, k=key: self._touch(aid))

        return page

    def _render_user_checks(self, page, acc):
        """按全局名单重建这一页的观演人勾选框，保持 acc 里记录的勾选状态。"""
        want = {str(u) for u in (acc.get("users") or [])}
        box = page["users_box"]
        for w in box.winfo_children():
            w.destroy()
        page["user_vars"] = {}

        if not self.roster:
            self._label(box, "（名单为空，先在下面输入姓名加入）",
                        bg=COLOR_INSET).grid(row=0, column=0, sticky="w")
            return
        for i, name in enumerate(self.roster):
            var = tk.BooleanVar(value=name in want)
            page["user_vars"][name] = var
            self._check(
                box, name, var, bg=COLOR_INSET,
                command=lambda a=page["account_id"]: self._touch(a),
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 18), pady=3)

    def _fill_cred_entries(self, aid, page):
        """把凭据填进这一页的输入框（密码仍是掩码显示）。

        优先用界面上刚敲进去、还没保存的内容，免得一次重绘就把没提交的密码冲掉。
        """
        keep = self.typed_creds.get(aid)
        phone = pwd = ""
        if keep and (keep[0] or keep[1]):
            phone, pwd = keep
        elif creds is not None:
            try:
                got = creds.get(aid)
            except Exception:
                got = None
            if got:
                phone, pwd = got["phone"], got["password"]
        if phone:
            page["phone"].delete(0, "end")
            page["phone"].insert(0, phone)
        if pwd:
            page["pwd"].delete(0, "end")
            page["pwd"].insert(0, pwd)

    def _refresh_state_labels(self, aid):
        """更新这一页顶部的「本地有登录数据 / 已存密码」和页内摘要。"""
        page = self.pages.get(aid)
        acc = acc_store.find(self.acc_data, aid) if acc_store is not None else None
        if page is None or acc is None:
            return
        bits = []
        try:
            logged = acc_store.login_state_exists(acc) if acc_store is not None else False
        except Exception:
            logged = False
        # 措辞必须如实：这里只说明「本地有没有浏览器数据」，不代表登录态还有效。
        bits.append("本地有登录数据" if logged else "还没登录过")
        try:
            has_cred = creds is not None and creds.has(aid)
        except Exception:
            has_cred = False
        if has_cred:
            bits.append("已存密码")
        try:
            errs = self._account_problems(acc)
        except Exception:
            errs = []
        if errs:
            bits.append("还差：" + "、".join(errs))
        page["state"].configure(text="  ·  ".join(bits))
        self._update_page_summary(aid)

    def _update_page_summary(self, aid):
        page = self.pages.get(aid)
        acc = acc_store.find(self.acc_data, aid) if acc_store is not None else None
        if page is None or acc is None:
            return
        bits = []
        if not acc.get("enabled", True):
            bits.append("已停用")
        if acc.get("city"):
            bits.append(str(acc["city"]))
        if acc.get("calendar_date"):
            bits.append("日历 " + str(acc["calendar_date"]))
        if acc.get("dates"):
            shown = "/".join(str(d) for d in acc["dates"][:3])
            if len(acc["dates"]) > 3:
                shown += " 等 %d 个场次" % len(acc["dates"])
            bits.append(shown)
        if acc.get("prices"):
            shown = "/".join(str(p) for p in acc["prices"][:4])
            if len(acc["prices"]) > 4:
                shown += "…"
            bits.append("¥" + shown)
        bits.append("%d 位观演人 / %s 张" % (len(acc.get("users") or []), acc.get("quantity", 1)))
        if acc.get("no_seat"):
            bits.append("不选座")
        elif acc.get("random_seat"):
            bits.append("随机选座")
        ri = acc.get("refresh_interval")
        if ri not in (None, ""):
            try:
                bits.append("刷新 %g 秒" % float(ri))
            except (TypeError, ValueError):
                bits.append("刷新频率「%s」不是数字" % ri)
        bits.append("⚠ 自动下单扣款" if acc.get("if_commit_order") else "仅监听")
        page["summary"].configure(text="  ·  ".join(bits))

    def _update_all_summaries(self):
        for aid in list(self.pages.keys()):
            try:
                self._refresh_state_labels(aid)
            except Exception:
                log_line("刷新摘要失败：\n" + traceback.format_exc())

    def _fit_notebook_height(self):
        """Notebook 高度跟着当前分页的实际内容走（内容长了靠外层画布滚动）。

        这是切换账号时最贵的一步（实测约 27ms，占整次切换三分之一的预算）。
          贵在两次「先把挂起的几何计算全部跑完」：`update_idletasks()` 一次，
          `configure(height=…)` 之后整个 Notebook 重新布局又一次。

          所以能省就省：
            · 高度没变就不 configure，切来切去大多落在同高的页上；
            · 只有高度真的变了才 sync scrollregion；
            · 只有拿到的 reqheight 明显还没算出来（<= 1）才 update_idletasks()。
        """
        try:
            cur = self.nb.nametowidget(self.nb.select())
            need = cur.winfo_reqheight()
            if need <= 1:
                # 这一页还没被布局过（第一次显示）。只有这种情况值得强制算一次。
                cur.update_idletasks()
                need = cur.winfo_reqheight()
            need = max(120, int(need))
            if need == getattr(self, "_nb_height", None):
                return
            self._nb_height = need
            self.nb.configure(height=need)
            self._sync_scrollregion()
        except Exception:
            pass

    def _sync_scrollregion(self):
        """让外层画布知道内容变矮了，并在内容比可视区还矮时把视图归位。

        不这么做的话：删掉账号、页面变矮之后，画布仍以为内容有原来那么高，
        又因为之前往下滚过（yview 停在底部），用户看到的是一片空白。
        """
        try:
            self.canvas.update_idletasks()
            box = self.canvas.bbox("all")
            if not box:
                return
            self.canvas.configure(scrollregion=box)
            if (box[3] - box[1]) <= self.canvas.winfo_height():
                self.canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _on_tab_changed(self, event=None):
        """切分页时的收尾。

        这里故意不滚动账号栏（用户明确要求：「不要默认把选中的账号栏
          移到最右边，维持原状就行」）。

          以前这里会调 `_scroll_chip_into_view()`，效果是：只要点到的标签
          有一丁点超出可视区，整条栏就横移一下，把那个标签顶到最右边，
          连点几个账号，整条栏一直在跳。
          而且那个函数内部要 `update_idletasks()`（强制跑完挂起的几何计算），
          实测一次多花 ~12ms，正是「卡顿」的一大来源。

          它原本是为了防一个真问题：增删账号之后重建标签栏，选中的标签
          可能落在视野外，用户会以为「点了没反应」。那个场景还在，
          所以 `_render_tabs()` 里仍然会调它一次，只是不再挂在每次切换上。
        """
        self._paint_tabbar()
        self._fit_notebook_height()
        aid = self._current_aid()
        if aid:
            self.status.set("当前账号：%s" % aid)

    def _tab_index(self, aid):
        try:
            return self.tab_order.index(str(aid))
        except (ValueError, AttributeError):
            return None

    def _current_aid(self):
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:
            return self.tab_order[0] if self.tab_order else ""
        if 0 <= idx < len(self.tab_order):
            return self.tab_order[idx]
        return self.tab_order[0] if self.tab_order else ""

    def _paint_tabbar(self):
        """按当前分页刷新所有标签的外观（底色 + 字色 + 文字）。

        整条重建最省事也不会出错（标签只有个位数），但它会丢掉滚动位置，
        所以这里只改属性、不重建。

        「切换卡顿」的原因不在这个方法里。实测过热身后：这个回调
          （含改字号、改文字）只花 ~0.5ms；切换的 ~26ms 全是 Tk 自己
          换 Notebook 页面的开销。真正拖慢切换的是另外两处，都已经改掉了：
            · `_scroll_chip_into_view()` 每次切换都强制 update_idletasks()
              并横移整条账号栏（~12ms + 视觉跳动）；
            · `_fit_notebook_height()` 每次都 configure 高度（~27ms），
              现在高度没变就不动。
        """
        cur = self._current_index()
        for idx, chip in list(getattr(self, "tab_chips", {}).items()):
            try:
                kids = chip.winfo_children()
                accent = kids[0] if kids else None      # 先 pack 的是底部横条
                label = kids[1] if len(kids) > 1 else None
                on = (idx == cur)
                bg = COLOR_PRIMARY_SOFT if on else COLOR_CARD
                chip.configure(bg=bg)
                if accent is not None:
                    accent.configure(bg=COLOR_PRIMARY if on else bg)
                if label is not None:
                    # 文字也要跟着选中态变：选中的显示手机号，其余回「账号N」
                    label.configure(bg=bg,
                                    fg=COLOR_PRIMARY if on else COLOR_TEXT,
                                    font=UI_FONT_BOLD if on else UI_FONT,
                                    text=self._chip_label(idx, selected=on))
            except Exception:
                continue

    def _on_enabled_toggle(self, aid):
        self._touch(aid)
        self._refresh_tab_title(aid)

    def _refresh_tab_title(self, aid):
        """页签标题变了就同步 Notebook 和我们自绘的那条账号栏。

        两边显示的内容不一样，这是有意的：
          · Notebook 的页签标题（`nb.tab(i,"text")`）保留完整信息
            （账号号 + 城市 + 日期 + 票档），它是数据、也是别处要用的标题；
          · 账号栏上的小标签走 `_chip_label()`，只写「账号N」，
            选中的那个显示手机号。
          所以这里要分别更新，不能拿一个去覆盖另一个。
        """
        idx = self._tab_index(aid)
        if idx is None:
            return
        acc = acc_store.find(self.acc_data, aid) if acc_store is not None else None
        if acc is None:
            return
        try:
            self.nb.tab(idx, text=self._tab_title(acc))
        except Exception:
            return
        # 账号栏是我们自己画的，Notebook 改了标题不会自动同步，手动跟一下。它要跟的不是那个标题，而是
        # `_chip_label()` 的结果（比如「停用」标记只在账号栏上出现）。
        chip = getattr(self, "tab_chips", {}).get(idx)
        if chip is not None:
            try:
                chip.winfo_children()[1].configure(text=self._chip_label(idx, aid))
                self.tab_strip.update_idletasks()
                self._sync_tabbar_arrows()
            except Exception:
                pass

    def _touch(self, aid):
        """界面上改了点东西：收进内存 + 刷新这一页的摘要和页签标题。"""
        self._collect_page(aid)
        self._update_page_summary(aid)
        self._refresh_tab_title(aid)

    # 刷新频率（账号配置里的可选字段）

    @staticmethod
    def _refresh_hint(raw):
        """把「刷新频率」输入框里的内容翻译成一句提示。返回 (文字, 颜色)。

        只提示、不拦截：真正的下限在别处兜着（`Concert._refresh_wait()` 里
        那句 `max(0.2, base)`）。这里多说一句，是因为这个数字是唯一一个
        「调大调小都直接影响风控面」的参数，刷得越快越像机器，而刷新快
        并不等于更抢得到（决定成败的是放票之后勾人提交那一段）。
        """
        raw = (raw or "").strip()
        if not raw:
            return "留空 = 用默认值", COLOR_HINT
        try:
            v = float(raw)
        except ValueError:
            return "要填数字（秒）", COLOR_WARN
        if not math.isfinite(v):
            return "要填正常的数字", COLOR_WARN
        if v < 0:
            return "不能是负数", COLOR_WARN
        if v < 0.2:
            return "低于 0.2 秒会被按 0.2 秒执行", COLOR_WARN
        if v < 1:
            return "偏快：越像机器越容易被盾，建议 ≥ 1 秒", COLOR_WARN
        if v > 10:
            return "偏慢：可能错过放票那一下", COLOR_WARN
        return "好", COLOR_HINT

    def _on_refresh_typed(self, aid, touch=True):
        """输入框被敲了一下：更新风险提示（顺带刷新页内摘要）。"""
        page = self.pages.get(aid)
        if page is None:
            return
        text, color = self._refresh_hint(page["refresh"].get())
        try:
            page["refresh_warn"].configure(text=text, fg=color)
        except Exception:
            pass
        if touch:
            self._touch(aid)


    @staticmethod
    def _lines(widget):
        raw = widget.get("1.0", "end")
        return [x.strip() for x in raw.splitlines() if x.strip()]

    def _collect_page(self, aid):
        """把某个分页上填的内容读回 acc_data 里对应的账号 dict。"""
        page = self.pages.get(aid)
        if page is None or acc_store is None:
            return
        acc = acc_store.find(self.acc_data, aid)
        if acc is None:
            return

        acc["note"] = page["note"].get().strip()
        acc["enabled"] = bool(page["enabled"].get())
        acc["auto_login"] = bool(page["auto_login"].get())
        acc["if_listen"] = bool(page["if_listen"].get())
        acc["if_commit_order"] = bool(page["if_commit_order"].get())
        acc["dismiss_mask"] = bool(page["dismiss_mask"].get())
        acc["no_seat"] = bool(page["no_seat"].get())
        acc["random_seat"] = bool(page["random_seat"].get())

        acc["target_url"] = page["url"].get().strip()
        acc["city"] = page["city"].get().strip()
        acc["dates"] = self._lines(page["dates"])
        acc["calendar_date"] = page["calendar_date"].get().strip()
        acc["prices"] = self._lines(page["prices"])
        acc["users"] = [n for n in self.roster
                        if n in page["user_vars"] and page["user_vars"][n].get()]

        raw = page["retry"].get().strip()
        acc["max_retries"] = int(raw) if raw.isdigit() else int(acc.get("max_retries") or 10000)
        raw_q = page["qty"].get().strip()
        try:
            qty = int(raw_q)
        except (TypeError, ValueError):
            qty = int(acc.get("quantity") or 1)
        acc["quantity"] = max(1, qty)

        # 刷新频率：空 = 不写这个字段（后端按 fast_mode 走默认值，和没这功能时一样）；填了就必须是数字。
        # 非法值不写进去：写进去会让 accounts.json 变脏，account_runner 那边的校验会因此跳过这个账号，现象就是「改了一下刷新频率，账号就不跑了」。
        raw_refresh = page["refresh"].get().strip()
        if not raw_refresh:
            acc.pop("refresh_interval", None)
        else:
            try:
                rv = float(raw_refresh)
            except ValueError:
                pass                       # 保持上一次的合法值，界面上已有红色提示
            else:
                if math.isfinite(rv) and rv >= 0:
                    acc["refresh_interval"] = rv

        # 界面上刚敲的凭据先记在内存里，重建分页时能填回去（不落盘）
        self.typed_creds[aid] = (page["phone"].get().strip(), page["pwd"].get())

    def _collect_all(self):
        for aid in list(self.pages.keys()):
            try:
                self._collect_page(aid)
            except Exception:
                log_line("读取分页 %s 失败：\n%s" % (aid, traceback.format_exc()))


    def add_account(self):
        if acc_store is None:
            messagebox.showerror("缺文件", "找不到 accounts.py，无法读写账号清单。")
            return
        self._collect_all()
        default = "账号 %02d" % (len(self.accounts) + 1)
        name = self._ask_name("新建账号", default)
        if not name:
            return
        if acc_store.find(self.acc_data, name) is not None:
            messagebox.showwarning("名称重复", "已经有叫「%s」的账号了" % name)
            return
        try:
            acc_store.add_account(self.acc_data, name, note="")
        except Exception as e:
            messagebox.showerror("新建失败", str(e))
            return
        # 新建的账号默认启用
        acc = acc_store.find(self.acc_data, name)
        if acc is not None:
            acc["enabled"] = True
        self._persist_accounts()
        self._reload_accounts()
        self._render_tabs(recollect=False)
        idx = self._tab_index(name)
        if idx is not None:
            try:
                self.nb.select(idx)
            except Exception:
                pass
        self.status.set("已新建账号「%s」" % name)

    def delete_account(self, aid):
        if acc_store is None:
            return
        if not messagebox.askyesno(
            "删除账号",
            "从 accounts.json 里删掉「{}」？\n\n"
            "只删这条账号配置；不会动它的登录数据（profiles 目录）和已保存的密码。".format(aid),
        ):
            return
        self._collect_all()
        self.acc_data["accounts"] = [
            a for a in acc_store.all_accounts(self.acc_data) if str(a.get("account_id")) != aid
        ]
        self.typed_creds.pop(aid, None)
        self._persist_accounts()
        self._reload_accounts()
        self._render_tabs(recollect=False)
        self.status.set("已删除账号「%s」" % aid)

    def refresh_accounts(self):
        self._reload_accounts()
        self._render_tabs(recollect=False)
        self.status.set("已重新载入 accounts.json（{} 个账号）".format(len(self.accounts)))

    # 观演人名单（全局，所有账号共用；每个账号各勾各的）

    def add_person(self, aid=None):
        aid = aid or self._current_aid()
        page = self.pages.get(aid)
        if page is None:
            return
        name = page["new_person"].get().strip()
        if not name:
            return
        self._collect_all()
        if name in self.roster:
            self.status.set("「%s」已在名单中" % name)
        else:
            self.roster.append(name)
            self.status.set("已加入「%s」" % name)
        # 新加入的人默认在当前分页勾上
        acc = acc_store.find(self.acc_data, aid)
        if acc is not None:
            users = [str(u) for u in (acc.get("users") or [])]
            if name not in users:
                users.append(name)
            acc["users"] = users
        self._persist_accounts()
        self._reload_accounts()
        self._render_tabs(recollect=False)
        idx = self._tab_index(aid)
        if idx is not None:
            try:
                self.nb.select(idx)
            except Exception:
                pass
        page = self.pages.get(aid)
        if page is not None:
            page["new_person"].delete(0, "end")

    def remove_checked_persons(self, aid=None):
        aid = aid or self._current_aid()
        page = self.pages.get(aid)
        if page is None:
            return
        self._collect_all()
        checked = [n for n in self.roster
                   if n in page["user_vars"] and page["user_vars"][n].get()]
        if not checked:
            messagebox.showinfo("没有勾选", "先在这个分页的名单里勾选要删除的姓名。")
            return
        if not messagebox.askyesno(
            "删除确认",
            "把下面这些姓名从名单里删除？\n\n{}\n\n"
            "名单是所有账号共用的，所以每个账号对应的人也会一起移除。\n"
            "（只影响这份名单，不影响你在大麦账号里已添加的观演人）".format("、".join(checked)),
        ):
            return
        self.acc_data["roster"] = [n for n in self.roster if n not in checked]
        for acc in self.accounts:
            acc["users"] = [str(u) for u in (acc.get("users") or []) if str(u) not in checked]
        self._persist_accounts()
        self._reload_accounts()
        self._render_tabs(recollect=False)
        idx = self._tab_index(aid)
        if idx is not None:
            try:
                self.nb.select(idx)
            except Exception:
                pass
        self.status.set("已从名单移除 %d 项" % len(checked))

    # 账号密码（存本机；明文只在输入框和 credentials.json 里）

    def save_cred(self, aid):
        if creds is None:
            messagebox.showerror("缺文件", "找不到 cred_store.py，无法保存密码。")
            return
        page = self.pages.get(aid)
        if page is None:
            return
        phone = page["phone"].get().strip()
        pwd = page["pwd"].get()
        if not phone or not pwd:
            page["msg"].configure(text="「{}」的手机号和密码都要填。".format(aid), fg=COLOR_WARN)
            return
        try:
            creds.put(aid, phone, pwd)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self.typed_creds.pop(aid, None)
        log_line("凭据已保存 | account=%s phone=%s" % (aid, creds.mask_phone(phone)))
        self._refresh_state_labels(aid)
        page["msg"].configure(
            text="已保存「{}」的密码（手机号 {}）。密码不在 accounts.json 里，"
                 "存在同目录的 credentials.json。".format(aid, creds.mask_phone(phone)),
            fg=COLOR_HINT)
        # 账号栏上「选中的那个显示手机号」，刚存完密码缓存得跟着更新，否则要等到下次重建标签栏才会显示出来。
        self._refresh_chip_phones()
        self._paint_tabbar()

    def drop_cred(self, aid):
        if creds is None:
            return
        page = self.pages.get(aid)
        try:
            has = creds.has(aid)
        except Exception:
            has = False
        if not has:
            if page is not None:
                page["msg"].configure(text="「{}」本来就没保存过密码。".format(aid), fg=COLOR_WARN)
            return
        if not messagebox.askyesno(
            "删除已保存的密码",
            "确定删除「{}」保存在本机的密码？\n\n"
            "只删 credentials.json 里的这一条，不影响它的登录状态（profiles 目录）。".format(aid),
        ):
            return
        try:
            creds.drop(aid)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        self.typed_creds.pop(aid, None)
        if page is not None:
            page["pwd"].delete(0, "end")
            page["msg"].configure(text="已删除「{}」保存的密码。".format(aid), fg=COLOR_HINT)
        self._refresh_state_labels(aid)
        # 手机号没了，账号栏上选中的那个要退回「账号N」
        self._refresh_chip_phones()
        self._paint_tabbar()

    # 单独触发登录 / 查状态

    def _login_account(self, aid):
        """分页上的「登录这个账号」。要不要自动填表跟着这一页的开关走。"""
        page = self.pages.get(aid)
        auto = bool(page is not None and page["auto_login"].get())
        self._launch_accounts("login", [aid], auto=auto)

    def _launch_accounts(self, command, ids, auto=False):
        if not startup_precheck():
            return
        ids = [str(i) for i in (ids or [])]
        if command == "login" and not ids:
            self.status.set("请先选择一个账号。")
            return

        self._collect_all()

        # 界面上填了手机号 + 密码就直接用，别再要求先单独点一次「立即保存密码」。
        pending_creds = []
        if command == "login" and auto and ids:
            pending_creds, _half = self._collect_filled_creds(ids)

        who = "、".join(ids[:6]) + ("…" if len(ids) > 6 else "") if ids else "全部账号"
        if command == "login":
            title, body = "确认登录", (
                "将为以下 {} 个账号各打开一个浏览器窗口：\n\n{}\n\n"
                "会先用本机保存的账号密码自动填表并点登录；\n"
                "出现验证码 / 滑块 / 短信验证时停下来交给你自己完成。{}"
                "\n\n现在开始吗？"
            ).format(len(ids), who,
                     "\n\n界面上新填的 {} 的密码会先保存到本机。".format(
                         "、".join(a for a, _p, _w in pending_creds))
                     if pending_creds else "")
        else:
            title, body = "确认检查", (
                "接下来会为 {} 各开一次浏览器，真的访问大麦确认登录态还有没有效。\n\n"
                "这一项只读：不登录、不下单、不付款。\n\n现在开始吗？"
            ).format(who)
        if not messagebox.askyesno(title, body):
            return

        if pending_creds:
            saved, failed = self._save_filled_creds(pending_creds)
            if saved:
                self._update_all_summaries()
            if failed:
                messagebox.showwarning(
                    "密码没能保存",
                    "下面这些账号的密码没保存成功（不影响这次登录，只是要你手动填）：\n\n{}".format(
                        "、".join(failed)))

        # 参数以列表交给 subprocess，由 Windows 负责给带空格的 account_id 加引号。千万不要自己把带引号的
        # 命令拼成一个字符串：cmd.exe 不认反斜杠转义（这个坑踩过）。
        args = runner_command([command] + (["--auto"] if auto else []) + list(ids))
        # 不弹控制台窗口（2026-10-09 用户要求）。这条命令是一个进程管多个账号（登录 / 查状态都是一次跑一批），
        # 所以日志按命令命名、不按账号，路径见下面的 log_line；「查看日志」按账号找的是抢票那条路（每账号一进程）。
        try:
            _proc, log_path = spawn_runner(args, tag=command)
        except Exception as e:
            messagebox.showerror("启动失败", "{}\n\n{}".format(e, manual_start_hint()))
            return
        log_line("已启动 %s %s | ids=%s | 日志=%s" % (
            "exe --runner" if is_frozen() else "accounts.bat", command, ids or "ALL",
            log_path))
        self.status.set("已{}，进度见「任务面板」。".format(
            "开始登录 {} 个账号".format(len(ids)) if command == "login" else "开始检查登录状态"))


    def open_task_panel(self):
        """打开任务面板：单窗口看所有账号的任务，每行能单独停。

        面板是新增的一条路：起任务时把进程句柄留在手里，
        所以能单独停、能在一个窗口里看全部。日志写在 runs/ 下。
        """
        if tasks is None:
            messagebox.showerror("缺少文件",
                                 "找不到 task_manager.py，无法打开任务面板。")
            return
        panel = getattr(self, "_task_panel", None)
        if panel is not None and panel.alive():
            panel.lift()
            return
        try:
            self._task_panel = TaskPanel(self)
        except Exception:
            log_line("打开任务面板失败：\n" + traceback.format_exc())
            messagebox.showerror("打开失败", traceback.format_exc())

    def open_task_wall(self):
        """打开「任务画面」：每个开着的浏览器窗口缩成一张图，摆在一个窗口里。

        为什么要它：多个账号一起抢，某个窗口弹了验证码 / 滑块就得有人去点。
          以前只能挨个把浏览器窗口翻出来看，窗口一多就会漏，漏掉的那个
          可能一直停在验证页上，等于白抢。这里摆成一屏缩略图，一眼看见。

        和「任务面板」的关系：面板管起停 / 状态 / 日志，这条只管看得见。
        没开过面板的话会顺手开一个（画面墙要借它那份「谁在跑」的数据）。
        """
        if window_wall is None or not window_wall.available():
            messagebox.showinfo(
                "任务画面",
                "这个功能只在 Windows 上能用。\n\n"
                "抢票本身不受影响，照常用「保存并开始」。")
            return
        if tasks is None:
            messagebox.showerror("缺少文件",
                                 "找不到 task_manager.py，无法打开任务画面。")
            return
        wall = getattr(self, "_task_wall", None)
        if wall is not None and wall.alive():
            wall.lift()
            return
        panel = getattr(self, "_task_panel", None)
        if panel is None or not panel.alive():
            self.open_task_panel()
            panel = getattr(self, "_task_panel", None)
        if panel is None:
            return
        try:
            self._task_wall = TaskWall(panel)
        except Exception:
            log_line("打开任务画面失败：\n" + traceback.format_exc())
            messagebox.showerror("打开失败", traceback.format_exc())


    def _ask_name(self, title, initial=""):
        """要一个账号名。不用 simpledialog，免得跟浅色主题不搭。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        self._label(dlg, "账号名（只是给你自己认的标签）", font=UI_FONT_BOLD,
                    fg=COLOR_TEXT, bg=COLOR_BG, anchor="w").pack(
            fill="x", padx=18, pady=(16, 5))
        e = ttk.Entry(dlg, font=UI_FONT, width=32)
        e.pack(fill="x", padx=18)
        e.insert(0, initial)
        e.focus_set()
        e.select_range(0, "end")

        out = {"v": None}

        def ok():
            out["v"] = e.get().strip()
            dlg.destroy()

        bar = tk.Frame(dlg, bg=COLOR_BG)
        bar.pack(fill="x", padx=18, pady=16)
        self._primary_btn(bar, "确定", ok, font=UI_FONT, padx=18).pack(side="right")
        self._ghost_btn(bar, "取消", dlg.destroy, font=UI_FONT,
                        padx=18).pack(side="right", padx=(0, 8))
        e.bind("<Return>", lambda ev: ok())
        e.bind("<Escape>", lambda ev: dlg.destroy())

        self.root.wait_window(dlg)
        return out["v"]

    def _on_close(self):
        self._collect_all()
        self._persist_accounts()
        self.root.destroy()

    def _confirm_commit(self, aid):
        page = self.pages.get(aid)
        if page is None or not page["if_commit_order"].get():
            return
        ok = messagebox.askyesno(
            "确认开启自动下单",
            "开启后「{}」抢到票时脚本会自动：\n"
            "  1) 勾选观演人\n"
            "  2) 勾选「我已阅读并同意…」购票协议\n"
            "  3) 点「提交订单」\n\n"
            "也就是说会直接下单并扣款，不再询问你。\n"
            "付款仍然要你自己去付，脚本绝不代付。\n\n确定要开启吗？".format(aid),
            icon="warning",
        )
        if not ok:
            page["if_commit_order"].set(False)


    @staticmethod
    def _account_problems(acc):
        """账号缺关键字段就点不出来。account_runner 那边也会拦，这里提前说清楚。"""
        acc = acc or {}
        errs = []
        if not str(acc.get("target_url") or "").strip():
            errs.append("没填演出链接")
        users = [str(u).strip() for u in (acc.get("users") or []) if str(u).strip()]
        if not users:
            errs.append("没勾观演人")
        if not (acc.get("prices") or []):
            errs.append("没填票档")
        try:
            qty = int(acc.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        if users and qty > len(users):
            errs.append("购票数量({})比勾选的观演人({} 位)多".format(qty, len(users)))
        return errs

    # 给「单账号老路径」写 config.json

    def collect(self, aid):
        """把某个账号的配置转成 damai/config.json 的形状（给手工跑 damai.py 用）。"""
        self._collect_all()
        acc = acc_store.find(self.acc_data, aid) if acc_store is not None else None
        if acc is None:
            raise ValueError("找不到账号「{}」".format(aid))
        errs = self._account_problems(acc)
        if errs:
            raise ValueError("；".join(errs))

        cfg = dict(self.saved)
        cfg["login_url"] = cfg.get("login_url") or DEFAULT_LOGIN_URL
        # 一律直接落到登录页：原脚本打开的是大麦首页，得用户自己再去点「登录」
        cfg["index_url"] = cfg["login_url"]
        cfg["target_url"] = str(acc.get("target_url") or "").strip()
        users = [str(u).strip() for u in (acc.get("users") or []) if str(u).strip()]
        qty = int(acc.get("quantity") or 1)
        cfg["users"] = users[:qty]
        cfg["if_listen"] = bool(acc.get("if_listen", True))
        cfg["if_commit_order"] = bool(acc.get("if_commit_order", False))
        cfg["dismiss_mask"] = bool(acc.get("dismiss_mask", True))
        cfg["no_seat"] = bool(acc.get("no_seat", True))
        cfg["random_seat"] = bool(acc.get("random_seat", True))
        cfg["max_retries"] = int(acc.get("max_retries") or 10000)

        city = str(acc.get("city") or "").strip()
        if city:
            cfg["city"] = city
        else:
            cfg.pop("city", None)

        dates = [str(d) for d in (acc.get("dates") or [])]
        if dates:
            cfg["dates"] = dates
        else:
            cfg.pop("dates", None)

        # 日历日期（选填）：空 = 这一页没有月历格子，不写这个字段。
        cal = str(acc.get("calendar_date") or "").strip()
        if cal:
            cfg["calendar_date"] = cal
        else:
            cfg.pop("calendar_date", None)

        prices = [str(p) for p in (acc.get("prices") or [])]
        if prices:
            cfg["prices"] = prices
        else:
            cfg.pop("prices", None)

        return cfg

    def write_config(self, aid):
        cfg = self.collect(aid)
        os.makedirs(DAMAI_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return cfg


    def save_only(self):
        self._collect_all()
        self._persist_accounts()
        aid = self._current_aid()
        extra = ""
        try:
            self.write_config(aid)
            extra = "，并同步了一份给单账号老路径（damai/config.json）"
        except Exception as e:
            extra = "（当前分页「{}」还没填完，config.json 未更新：{}）".format(aid, e)
        self._update_all_summaries()
        self.status.set("已保存「{}」{}".format(aid, extra))


    def open_changelog(self):
        """打开更新日志窗口（版本号旁边那个圆圈问号点开的就是它）。

        找不到文件不算失败：照样把窗口开出来，里面写清「该把文件放到哪」。
          客户点这个圆圈的时候多半是「这版改了啥」，弹一个「缺少文件」的错误框
          是最差的回答，那等于用报错回答一个正常问题。
        """
        if changelog is None:
            messagebox.showerror("缺少文件", "找不到 changelog.py，没法读更新日志。")
            return
        try:
            ChangelogDialog(self.root, changelog, app_version)
        except Exception:
            log_line("打开更新日志失败：\n" + traceback.format_exc())
            messagebox.showerror("打开失败", traceback.format_exc())

    def open_env_check(self):
        """跑一遍环境检测，把结果摆给用户看。

        放后台线程：检测里有一次子进程调用（问抢票那个解释器有没有
          selenium）和几次网络连接，最坏情况要好几秒。在 Tk 主线程里干这个，
          窗口会「未响应」，一个用来排查问题的按钮自己先卡住，那太糟了。

          线程做完把结果挂在 self._env_result 上，主线程用 after 轮询收。
          不用 queue 是因为只有一个值、而且只有一个消费者；用 after 轮询
          还能避开「从子线程直接碰 Tk 控件」这个经典错误。
        """
        if env_check is None:
            messagebox.showerror("缺少文件", "找不到 env_check.py，没法做环境检测。")
            return
        if getattr(self, "_env_busy", False):
            return

        self._env_busy = True
        self._env_result = None
        try:
            self.env_var.set("检测中…")
            self.env_btn.configure(state="disabled")
        except Exception:
            pass
        log_line("开始环境检测")

        def worker():
            try:
                self._env_result = env_check.run()
            except Exception:
                log_line("环境检测失败：\n" + traceback.format_exc())
                self._env_result = None
            finally:
                self._env_busy = False

        threading.Thread(target=worker, name="env-check", daemon=True).start()
        self.root.after(120, self._poll_env_check)

    def _poll_env_check(self):
        if getattr(self, "_env_busy", True):
            self.root.after(120, self._poll_env_check)
            return
        try:
            self.env_btn.configure(state="normal")
        except Exception:
            pass

        results = getattr(self, "_env_result", None)
        if not results:
            self.env_var.set("检测没做完")
            messagebox.showerror(
                "环境检测失败",
                "检测过程出错了，详情见日志：\n{}".format(LOG_PATH))
            return

        tip = env_check.tip(results)
        self.env_var.set(tip)
        log_line("环境检测：" + tip)
        try:
            EnvCheckDialog(self.root, results)
        except Exception:
            log_line("打开检测结果窗口失败：\n" + traceback.format_exc())
            messagebox.showinfo("环境检测", env_check.describe(results))


    def start(self, all_accounts=False):
        """按钮入口。任何异常都必须弹出来，pythonw 没有控制台，异常吞掉就真找不到原因了。"""
        try:
            self._start_impl(all_accounts)
        except Exception:
            tb = traceback.format_exc()
            log_line("启动流程异常：\n" + tb)
            last = tb.strip().splitlines()[-1]
            messagebox.showerror(
                "启动失败",
                "启动过程出错了。\n\n{}\n\n完整信息已写入：\n{}".format(last, LOG_PATH),
            )

    def _collect_filled_creds(self, ids):
        """看看这次要启动的账号里，有哪个在界面上填了手机号 + 密码。

        「保存并开始抢票」这个名字本身就含「保存」，所以不必再单独点一次
        「立即保存密码」，填了就顺手一起存下来，下次不用重填。

        返回 (pending, incomplete)：
          pending    = [(账号号, 手机号, 密码), ...]  该存的；和已存的一模一样就跳过
          incomplete = [账号号, ...]  只填了一半。存不了，也不能当没看见，
                       否则用户以为能自动登录、结果静默失败。
        """
        pending, incomplete = [], []
        if creds is None or acc_store is None:
            return pending, incomplete
        for aid in ids:
            page = self.pages.get(aid)
            if page is None:
                continue
            phone = page["phone"].get().strip()
            pwd = page["pwd"].get()
            if not phone and not pwd:
                continue                       # 完全空着：不动已经存过的凭据
            if not phone or not pwd:
                incomplete.append(aid)
                continue
            try:
                old = creds.get(aid)
            except Exception:
                old = None
            if old and old.get("phone") == phone and old.get("password") == pwd:
                continue                       # 一个字都没改，别重复写盘
            pending.append((aid, phone, pwd))
        return pending, incomplete

    def _save_filled_creds(self, pending):
        """把 pending 真正落盘。返回 (成功列表, 失败列表)。"""
        saved, failed = [], []
        if not pending:
            return saved, failed
        if creds is None:
            log_line("找不到 cred_store.py，跳过保存密码")
            return saved, [aid for aid, _p, _w in pending]
        for aid, phone, pwd in pending:
            try:
                creds.put(aid, phone, pwd)
                saved.append(aid)
                self.typed_creds.pop(aid, None)
                log_line("凭据已保存 | account=%s phone=%s" % (aid, creds.mask_phone(phone)))
            except Exception:
                failed.append(aid)
                log_line("保存账号 %s 的凭据失败：\n%s" % (aid, traceback.format_exc()))
        return saved, failed

    def _start_impl(self, all_accounts=False):
        if acc_store is None:
            messagebox.showerror("缺少文件", "找不到 accounts.py，无法读写账号清单。")
            return

        self._collect_all()
        self._persist_accounts()

        cur = self._current_aid()
        # config.json 是「单账号老路径」（damai.py）读的那份，顺手写一份不亏。
        try:
            self.write_config(cur)
        except Exception as e:
            if not all_accounts:
                messagebox.showwarning("还差一点", str(e))
                return
            log_line("当前分页还没填完（{}），「全部账号一起抢」继续".format(e))

        if all_accounts:
            pool = [a for a in acc_store.all_accounts(self.acc_data) if a.get("enabled", True)]
        else:
            acc = acc_store.find(self.acc_data, cur)
            pool = [acc] if acc is not None else []

        ready, skipped = [], []
        for acc in pool:
            aid = str(acc.get("account_id"))
            if not acc.get("enabled", True):
                skipped.append("  · {}：已停用".format(aid))
                continue
            probs = self._account_problems(acc)
            if probs:
                skipped.append("  · {}：{}".format(aid, "、".join(probs)))
                continue
            ready.append(acc)

        if not ready:
            if skipped:
                messagebox.showwarning(
                    "这些账号还没填完",
                    "没有可以开抢的账号：\n\n{}\n\n"
                    "把演出链接 / 观演人 / 票档 / 购票数量补齐再试。".format("\n".join(skipped)))
            else:
                messagebox.showwarning(
                    "还没有账号",
                    "先点下面的「＋ 新建账号」建一个，填好它要抢的票和手机号密码，然后再开始。")
            return

        if not startup_precheck():
            return

        # 页面上填好的手机号 + 密码，在这里顺手一起保存。用户的心智是「我都填了，凭什么还要再点一次保存」，顺从它。
        all_ids = [str(a.get("account_id")) for a in pool]
        pending_creds, half_filled = self._collect_filled_creds(all_ids)
        pending_ids = {aid for aid, _p, _w in pending_creds}

        lines = []
        auto_count = 0
        for acc in ready:
            aid = str(acc.get("account_id"))
            auto = bool(acc.get("auto_login", True))
            if auto:
                auto_count += 1
            will_have = aid in pending_ids
            try:
                has_cred = will_have or (creds is not None and creds.has(aid))
            except Exception:
                has_cred = will_have
            if has_cred and auto:
                tail = "有密码，先自动登录再抢"
            elif has_cred:
                tail = "有密码，但「启动时自动登录」没勾，这次会等你手动登录"
            else:
                tail = "没填密码，会开浏览器等你手动登录"
            what = []
            if acc.get("city"):
                what.append(str(acc["city"]))
            if acc.get("calendar_date"):
                what.append("日历 " + str(acc["calendar_date"]))
            if acc.get("dates"):
                what.append("/".join(str(d) for d in acc["dates"][:2]))
            if acc.get("prices"):
                what.append("¥" + "/".join(str(p) for p in acc["prices"][:3]))
            lines.append("  · {}{}　{}".format(
                aid, "（{}）".format(" ".join(what)) if what else "", tail))

        tip = ("有账号开启了自动下单，抢到票会直接扣款。"
               if any(a.get("if_commit_order") for a in ready)
               else "都是「只监听不下单」模式，抢到票不会自动付款。")

        notes = []
        if pending_creds:
            notes.append(
                "· 会把 {} 的密码保存到本机，以后不用再填。".format(
                    "、".join(a for a, _p, _w in pending_creds)))
        if half_filled:
            notes.append(
                "· 注意：{} 的手机号和密码没填全，这次不会自动登录"
                "（如果以前存过密码，仍然会用以前那份）。".format("、".join(half_filled)))
        if skipped:
            notes.append("· 下面这些跳过：\n" + "\n".join(skipped))

        blocks = ["将为 {} 个账号各打开一个浏览器窗口：".format(len(ready)),
                  "\n".join(lines), tip]
        if notes:
            blocks.append("\n".join(notes))
        body = "\n\n".join(blocks) + "\n\n现在开始吗？"

        if not messagebox.askyesno(
            ("准备开始（多开 {} 个窗口）" if len(ready) > 1 else "准备开始").format(len(ready)),
            body,
        ):
            self.status.set("配置已保存，未启动")
            return

        # 用户点了「开始」才真正写凭据，点「否」就什么都不留。
        saved_creds, failed_creds = self._save_filled_creds(pending_creds)
        if failed_creds:
            messagebox.showwarning(
                "密码没能保存",
                "下面这些账号的密码没保存成功（不影响启动，只是会等你手动登录）：\n\n{}\n\n"
                "原因通常是 credentials.json 读不了或磁盘写不进去，详见日志：\n{}".format(
                    "、".join(failed_creds), LOG_PATH))

        driver = resolve_chromedriver()
        env_extra = {"CHROMEDRIVER_PATH": driver} if driver else None

        self._collect_all()
        self._persist_accounts()

        started, failed = [], []
        for acc in ready:
            aid = str(acc.get("account_id"))
            auto = bool(acc.get("auto_login", True))
            args = runner_command(["run", aid] + (["--auto"] if auto else []))
            try:
                # 不弹控制台窗口：输出落到 runs/<账号>.<时间戳>.log，「查看日志」按钮找得到它（见 latest_task_log）。
                spawn_runner(args, env_extra=env_extra, tag=aid)
                started.append(aid)
                time.sleep(0.4)        # 稍微错开，别让几个 Chrome 挤在同一秒启动
            except Exception as e:
                failed.append(aid)
                log_line("启动账号 %s 失败：{!r}".format(aid).format(e))

        log_line("已启动抢票 | accounts=%s | 本次保存密码=%s | driver=%s | 日志目录=%s"
                 % (started, saved_creds, driver, RUNS_DIR))
        if not started:
            messagebox.showerror(
                "启动失败",
                "一个都没启动起来。\n\n{}".format(manual_start_hint()))
            return

        # 脚本打开的浏览器窗口经常躲在其它窗口后面，这里后台把它提到最前
        threading.Thread(target=focus_damai_window, daemon=True).start()
        self._update_all_summaries()

        msg = "已为 {} 个账号启动抢票".format(len(started))
        if saved_creds:
            msg += "（已保存 {} 个账号的密码）".format(len(saved_creds))
        if failed:
            msg += "（{} 个启动失败，详见日志）".format(len(failed))
        # 不再弹控制台窗口了，所以明确指一下「进度去哪看」（2026-10-09）
        self.status.set(msg + "；进度见「任务面板」，正在切换到浏览器窗口")

    # 以下函数是「单账号老路径」留下的：生成 run_grab.bat 直接跑 damai.py。现在两个启动按钮统一走
    # accounts.json + account_runner.py（支持多开），界面上不再调用它们；保留是为了还能手工生成一个可双击的启动器。
    def write_launcher(self, exe, driver=None):
        """生成纯 ASCII 的 .bat 启动器，返回其路径。

        好处：绕开 cmd 的引号转义问题；脚本报错时窗口不会闪退，方便看错误；
        也可以直接双击这个 .bat 手动运行。

        这个启动器跑的是 `damai/` 里那条单账号老路径（`damai/damai.py`）。
          打包版里根本没有这个 .py 文件，生成出来的 .bat 会指向一个不存在的
          东西，双击只会闪一下就没了。所以打包版直接拒绝生成，并说清该走哪条路：
          界面上那两个启动按钮 / 任务面板，它们走的是 `--runner`。
        """
        if getattr(sys, "frozen", False):
            raise ValueError(
                "打包版不能生成这种 .bat 启动器：它跑的是 damai/damai.py 这条\n"
                "单账号老路径，而打包之后没有这个文件（代码都在 exe 里）。\n"
                "请用界面上的「保存并开始 / 全部账号启动」，或者任务面板。")
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "set PYTHONIOENCODING=utf-8",
            "set PYTHONUTF8=1",
            # 本机的 HTTP 代理变量会拦掉发往本地 ChromeDriver 的请求，症状是 "Message: unhandled request"
            # （驱动端口被当成外网走了代理）。这里强制让 localhost / 127.0.0.1 直连，不走代理。
            "set NO_PROXY=localhost,127.0.0.1,::1",
            "set no_proxy=localhost,127.0.0.1,::1",
            "title Damai Ticket Grab",
            'cd /d "%~dp0damai"',
            "",
        ]
        if driver:
            lines.append('set "CHROMEDRIVER_PATH={}"'.format(driver))
            lines.append("")
        lines += [
            'if not exist "{}" ('.format(exe),
            "    echo [ERROR] Python interpreter not found:",
            "    echo   {}".format(exe),
            "    echo.",
            "    echo Reinstall the selenium environment, or edit this file.",
            "    pause",
            "    exit /b 1",
            ")",
            "",
            "echo [INFO] interpreter : {}".format(exe),
            "echo [INFO] working dir : %CD%",
            'echo [INFO] chrome driver: %CHROMEDRIVER_PATH%',
            "echo.",
            '"{}" {}'.format(exe, GRAB_SCRIPT),
            "",
            "echo.",
            "echo ============================================",
            "echo  Script exited. Press any key to close.",
            "echo ============================================",
            "pause >nul",
        ]
        content = "\r\n".join(lines) + "\r\n"
        try:
            content.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("路径里含非 ASCII 字符，无法生成 .bat 启动器")
        with open(LAUNCHER_BAT, "w", encoding="ascii", newline="") as f:
            f.write(content)
        return LAUNCHER_BAT


class TaskPanel(object):
    """任务面板：单窗口聚合展示所有账号的抢票任务，每行可以单独停。

    这是新增的一条路，向导底部那两个启动按钮的业务逻辑一个字没改。
      两条路起的子进程现在都不弹控制台窗口（见 spawn_runner），日志都写进
      runs/<账号>.<时间>.log。区别只在面板这条路把进程句柄留在手里，
      所以能在一个窗口里看全部、也能只停某一个。

    「单独停」为什么非做不可：以前想停一个账号，只能去那一堆控制台窗口里
      找它、点右上角的 ×。而 × 关掉的只是控制台，Chrome 通常还活着，
      占着 profiles/<账号> 目录；下一轮启动就撞 `Chrome instance exited`，
      报错信息还完全指不到真正的原因。这里的停止是整棵进程树一起收走。
    """

    # 状态列的宽度见 _build 里的实测校正（2026-10-09 用户要求「把这个状态栏做大一点，起码能显示全部」），
    # 写死的列宽在高缩放屏上会被截，这里只是兜底初值。「画面」列缩略图 2026-10-09 加，同日第二轮要求从
    # 56px 放大到 110px 高（验证码 / 弹窗 / 页面结构都认得出，还是缩略图）。图按高度缩：行高固定，只卡宽度的话横长的窗口会撑爆行高。
    ROW_HEIGHT = 122
    SHOT_H = 110
    SHOT_W = 190
    SHOT_MS = 2000        # 截图节流：状态 1 秒刷，图 2 秒一截就够
    COLS = (("aid", "账号", 140), ("state", "状态", 110),
            ("elapsed", "已运行", 90), ("last", "最近一行", 470),
            # 2026-10-09 新增「操作」列，每行右边写着自己该做的事（停止 / 取消排队 / 启动 / 重跑），
            # 点一下那一格就执行。以前停一个账号要先选中、再点下面的「停止选中」，两步，而「选中」在账号多时很容易点错行。
            ("op", "操作", 88))
    INTERVAL_MS = 1000

    def __init__(self, wizard):
        self.w = wizard
        self.tm = get_task_manager()
        self.win = tk.Toplevel(wizard.root)
        self.win.title("任务面板")
        self.win.configure(bg=COLOR_BG)
        # 窗口高度配着「画面」列的大缩略图（行高 122），太小一屏就两三行。
        self.win.geometry("1120x780")
        self.win.minsize(840, 460)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = None
        self._last_sig = None
        # 正在被「操作」列停掉的账号（停是异步的，这几个先显示「停止中…」），也挡住连点，同一个账号点两下会起两个停止线程。
        self._stopping = set()
        # 多开排队（见 start_all / _pump_queue）：
        self._queue = []            # 还在等空位的 account_id，按顺序
        self._cap = 0               # 这次允许同时跑几个
        self._last_fill = 0.0       # 上一次从队列里放人的时刻（用来错峰）
        self._live = {}             # 刷新时读到的实时状态：{账号: {...}}（见 _attach_live）
        self._thumbs = {}           # 「画面」列的缩略图：{账号: PhotoImage}（留住引用防 GC）
        self._shooting = False      # 截图线程忙不忙（防重入）
        self._last_shot = 0.0       # 上次截图时刻（节流用）
        self._inbox = []            # 子线程 → 主线程的投递箱（见 post 的说明）
        self._build()
        self.refresh()
        self._schedule()


    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def lift(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass

    def close(self):
        """关面板不停任务，任务在别的进程里跑，关掉窗口它们照跑。"""
        # 但排队会没：排队跟「放人」是一体的，而放人的是面板里的定时器，关掉面板 = 剩下那些没人放。所以
        # 这里有队列时必须先说清楚，不然用户会以为「点了全部启动，8 个都在排队」，其实只有 3 个在跑。
        if self._queue:
            n = len(self._queue)
            if not messagebox.askyesno(
                    "还有 {} 个在排队".format(n),
                    "还有 {} 个账号在排队，关掉这个窗口它们就**不会启动**了"
                    "（已经在跑的那几个不受影响，会继续跑）。\n\n"
                    "确定要关吗？".format(n)):
                return
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.win.destroy()
        except Exception:
            pass

    def _schedule(self):
        if not self.alive():
            return
        try:
            self._after_id = self.win.after(self.INTERVAL_MS, self._tick)
        except Exception:
            self._after_id = None

    def _tick(self):
        """每秒刷一次。

        三步各自包一层 try（2026-10-09 补）。这个面板是「看着玩」的窗口，
          里面任何一步出错都不该让定时器停摆，一旦 `_schedule()` 没被执行到，
          整个面板就永久定格，而用户看到的只是「状态怎么不动了」，
          完全指不到原因。
        """
        try:
            self.refresh()
        except Exception:
            log_line("任务面板刷新失败：\n" + traceback.format_exc())
        try:
            self._pump_queue()
        except Exception:
            log_line("任务面板队列处理失败：\n" + traceback.format_exc())
        self._schedule()


    def _btn(self, parent, text, command, primary=False, ghost=False):
        if primary:
            return self.w._primary_btn(parent, text, command, font=UI_FONT_SMALL, padx=16)
        if ghost:
            return self.w._ghost_btn(parent, text, command, font=UI_FONT_SMALL, padx=14)
        return self.w._soft_btn(parent, text, command, font=UI_FONT_SMALL, padx=16)

    def _build(self):
        outer = tk.Frame(self.win, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)

        head = tk.Frame(outer, bg=COLOR_BG)
        head.pack(fill="x")
        tk.Label(head, text="任务面板", font=UI_FONT_TITLE, fg=COLOR_TEXT,
                 bg=COLOR_BG).pack(side="left")
        tk.Label(head, text="一个窗口中看全部账号；每行可以单独停。",
                 font=UI_FONT_SMALL, fg=COLOR_HINT, bg=COLOR_BG).pack(
            side="left", padx=(10, 0))

        tools = tk.Frame(outer, bg=COLOR_BG)
        tools.pack(fill="x", pady=(12, 8))
        self._btn(tools, "全部启动", self.start_all, primary=True).pack(side="left")
        self._btn(tools, "全部停止", self.stop_all).pack(side="left", padx=(8, 0))
        self._btn(tools, "刷新", self.refresh, ghost=True).pack(side="left", padx=(8, 0))
        self._btn(tools, "日志文件夹", self.open_runs_dir,
                  ghost=True).pack(side="left", padx=(8, 0))
        self._btn(tools, "资源体检", self.open_resource_report, ghost=True).pack(
            side="left", padx=(8, 0))
        self._btn(tools, "多开设置", self.open_multi_settings, ghost=True).pack(
            side="left", padx=(8, 0))
        # 画面墙：把每个开着的浏览器窗口缩成一张图摆在一起，哪个弹了验证码 / 滑块一眼能看见（不用挨个窗口翻）。
        self._btn(tools, "画面墙", self.open_wall, ghost=True).pack(
            side="left", padx=(8, 0))

        wrap = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1, borderwidth=0)
        # 表格最后才 pack（见下面的 pack 顺序），它是唯一「可以变矮」的东西，窗口不够高时该牺牲它而不是牺牲按钮。
        # 「画面」列（2026-10-09 用户要求）：每行放对应浏览器窗口的实时缩略图，面板本身就是窗口列表，哪个窗口弹了
        # 验证码不用再开「画面墙」去翻。Tk 的 Treeview 行图片只能放 #0 列，所以把 #0 显出来当「画面」列；缩略图按高度缩，见 _maybe_shoot。
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.COLS],
                                 show="tree headings", height=10)
        _st = ttk.Style(self.win)
        try:
            _st.configure("TaskPanel.Treeview", rowheight=self.ROW_HEIGHT,
                          indent=0)
        except Exception:
            pass
        try:
            self.tree.configure(style="TaskPanel.Treeview")
        except Exception:
            pass
        self.tree.heading("#0", text="画面")
        self.tree.column("#0", width=self.SHOT_W + 14, anchor="center",
                         stretch=False)
        for key, title, width in self.COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w",
                             stretch=(key == "last"))
        # 状态列宽按本机字体实算（2026-10-09 用户要求「起码能显示全部」）：列宽单位是物理像素、字体是点值，
        # 125% 缩放的屏上「已提交，待支付」要 140px，谁写死 110 都会被截。这里拿 Treeview 自己的字体量一遍
        # 最长的那几条状态，量多少给多少（测试里也有一条同样口径的断言）。
        try:
            _fdesc = (ttk.Style(self.win).lookup("Treeview", "font")
                      or "TkDefaultFont")
            _f = tkfont.Font(root=self.win, font=_fdesc)
            _need = max(_f.measure(_s) for _s in
                        ("已提交，待支付", "需要验证码", "登录输错", "停止中…"))
            self.tree.column("state", width=max(130, _need + 22))
        except Exception:
            self.tree.column("state", width=170)      # 量不出来就保守给足
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # 状态用颜色区分：运行中是主色，抢到是绿，失败是红，其余灰。
        self.tree.tag_configure("running", foreground=COLOR_PRIMARY)
        self.tree.tag_configure("success", foreground="#1a7f37")
        self.tree.tag_configure("failed", foreground=COLOR_WARN)
        self.tree.tag_configure("idle", foreground=COLOR_HINT)
        # 「需要验证码 / 登录输错」，要去人工处理的，标红（2026-10-09 用户要求）
        self.tree.tag_configure("alert", foreground=COLOR_WARN)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_ops())
        # 双击账号那一行，把它对应的浏览器窗口拉到最前（2026-10-09 用户要）。想看日志用「查看日志」按钮，
        # 双击这个手势让给浏览器窗口了：弹验证码时人得赶紧切到那个窗口，这个动作更急。
        self.tree.bind("<Double-1>", self._on_double_click)
        # 点「操作」列 = 一键执行那一格写的动作。用 add="+" 追加绑定，上面两条一个字没动；处理函数不返回
        # "break"，所以「点一下先选中这一行」的默认行为照旧，两种用法可以混着来。
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")

        ops = tk.Frame(outer, bg=COLOR_BG)
        self._btn(ops, "启动选中", self.start_selected).pack(side="left")
        self._btn(ops, "停止选中", self.stop_selected).pack(side="left", padx=(8, 0))
        self._btn(ops, "查看日志", self.view_log, ghost=True).pack(
            side="left", padx=(8, 0))
        self._sel = tk.Label(ops, text="", font=UI_FONT_SMALL, fg=COLOR_HINT,
                             bg=COLOR_BG)
        self._sel.pack(side="left", padx=(12, 0))

        # 多开排队状态 / 当前设置。放在消息行上面，因为它比消息更「常驻」。
        self.queue_lbl = tk.Label(outer, text="", font=UI_FONT_SMALL, fg=COLOR_HINT,
                                  bg=COLOR_BG, anchor="w", justify="left")
        self.w._bind_wrap(self.queue_lbl, outer, margin=28)

        self.msg = tk.Label(outer, text="", font=UI_FONT_SMALL, fg=COLOR_HINT,
                            bg=COLOR_BG, anchor="w", justify="left")
        self.w._bind_wrap(self.msg, outer, margin=28)

        hint = tk.Label(outer,
                        text="关掉这个窗口不会停任务（任务在独立进程里跑）。"
                             "想停哪一个，直接点那一行最右边的「操作」格；"
                             "也可以选中它再点「停止选中」。",
                        font=UI_FONT_SMALL, fg=COLOR_HINT, bg=COLOR_BG,
                        anchor="w", justify="left")

        # 底部这一串先钉住（从下往上 pack），最后才轮到表格吃掉剩下的空间。pack 在空间不够时牺牲后放的控件：
        # 原来的顺序是「表格先放、按钮后放」，于是高 DPI 屏上被切掉的正好是「启动选中 / 停止选中」和排队状态行。
        # 这里把顺序倒过来，无论窗口多矮，按钮都在。
        hint.pack(side="bottom", fill="x", pady=(4, 0))
        self.msg.pack(side="bottom", fill="x", pady=(8, 0))
        self.queue_lbl.pack(side="bottom", fill="x", pady=(8, 0))
        ops.pack(side="bottom", fill="x", pady=(10, 0))
        wrap.pack(fill="both", expand=True)
        self._refresh_queue_label()
        fit_window(self.win, min_w=860, min_h=460)


    def _accounts(self):
        """当前界面上的账号清单（先把分页内容收进内存，保证跑的是最新配置）。"""
        if acc_store is None:
            return []
        try:
            self.w._collect_all()
        except Exception:
            pass
        try:
            return list(acc_store.all_accounts(self.w.acc_data) or [])
        except Exception:
            log_line("任务面板读账号清单失败：\n" + traceback.format_exc())
            return []

    def _find(self, aid):
        for a in self._accounts():
            if str(a.get("account_id")) == str(aid):
                return a
        return None

    def _say(self, text):
        try:
            self.msg.configure(text=text)
        except Exception:
            pass

    # 实时状态里的「要去人工处理」的两种，标红（2026-10-09 用户要求：
    # 「如果是账号密码不对，你就说登录输错；或者说需要验证码」要一眼看见）。
    ALERT_STATES = ("需要验证码", "登录输错")
    # 实时状态里的「活着、正在干活」的细状态，用运行色，别落到灰色。
    ACTIVE_STATES = ("刷新中", "轮询中", "选座中", "待提交订单", "已提交，待支付",
                     "等待登录", "已登录", "已进演出页", "正在选票档")

    @classmethod
    def _tag(cls, state):
        if state in cls.ALERT_STATES:
            return "alert"
        if state in (tasks.S_STARTING, tasks.S_RUNNING) or state in cls.ACTIVE_STATES:
            return "running"
        if state == tasks.S_SUCCESS:
            return "success"
        if state in (tasks.S_FAILED,):
            return "failed"
        return "idle"

    def selected_aid(self):
        try:
            sel = self.tree.selection()
        except Exception:
            return None
        return str(sel[0]) if sel else None

    def post(self, fn):
        """子线程往主线程投递函数：塞一个进来，主线程刷新时执行。

        为什么不用 `self.win.after(0, fn)`（2026-10-09 真踩过）：
          tkinter 在非主线程里调 after 会直接抛
          `RuntimeError: main thread is not in main loop`（本机 Python 3.10 实测，
          两次对照实验都复现）。异常被 except 一吞，表现就是「任务在跑、
          界面永远不更新」，截图贴不上、停止的按钮卡在「停止中…」。
          改成：子线程只往纯 Python 列表里塞（list.append 线程安全），
          主线程的每一轮刷新（每秒一次）取出来执行。
        """
        self._inbox.append(fn)

    def _drain_inbox(self):
        """主线程侧：执行投递箱里攒下的回调。"""
        while self._inbox:
            fn = self._inbox.pop(0)
            try:
                fn()
            except Exception:
                log_line("任务面板回调出错：\n" + traceback.format_exc())

    def refresh(self):
        if not self.alive() or self.tm is None:
            return
        self._drain_inbox()          # 先处理子线程投回来的活（截图贴图、停止收尾）
        sel = self.selected_aid()
        rows = self.tm.snapshot(self._accounts())
        # 把「实时状态」贴到对应账号那一行上（2026-10-09 用户要求）：不管是任务面板起的还是启动页那两个绿色
        # 按钮起的，都算到那个账号自己那一行，不再另起「外部窗口：xxx」这种独立条目。子进程每处理一个账号就
        # 更新 logs/live/<账号>.json，两条路都会更新。
        self._attach_live(rows)
        # 排队中的行要看得出来，「点了全部启动怎么只有 3 个在跑」必须一眼能答，否则用户会以为程序坏了、然后反复点「全部启动」。
        if self._queue and tasks is not None:
            busy = (tasks.S_STARTING, tasks.S_RUNNING, tasks.S_STOPPING)
            for r in rows:
                if r["aid"] in self._queue and r["state"] not in busy:
                    r["state"] = "排队中"
                    r["last_line"] = "等前面有空位（同时最多 {} 个）".format(self._cap)
        # 指纹里带上「操作」格的内容：这样 _stopping 一变（点了停、还没停完）那一格也会跟着重绘，否则它会一直停在旧字样上。
        for r in rows:
            r["op"] = self._op_text(r)
        sig = tuple((r["aid"], r["state"], r["elapsed_text"], r["last_line"],
                     r["op"]) for r in rows)
        if sig != self._last_sig:
            self._last_sig = sig
            try:
                self.tree.delete(*self.tree.get_children())
                for r in rows:
                    self.tree.insert("", "end", iid=r["aid"],
                                     values=(r["aid"], r["state"],
                                             r["elapsed_text"], r["last_line"],
                                             r["op"]),
                                     tags=(self._tag(r["state"]),))
                    # 行是整批重建的（delete + insert），刚贴好的缩略图会跟着没掉，从缓存里补回去，不能让图每隔几秒闪一下。
                    _img = self._thumbs.get(str(r["aid"]))
                    if _img is not None:
                        self.tree.item(r["aid"], image=_img)
                if sel and self.tree.exists(sel):
                    self.tree.selection_set(sel)
            except Exception:
                log_line("任务面板刷新失败：\n" + traceback.format_exc())
        self._maybe_shoot()
        self._sync_ops()

    def _sync_ops(self):
        aid = self.selected_aid()
        if not aid:
            self._sel.configure(text="未选中账号")
            return
        t = self.tm.tasks.get(aid) if self.tm else None
        if t is None:
            self._sel.configure(text="{} · 还没跑过".format(aid))
        else:
            extra = "（{}）".format(t.note) if t.note else ""
            self._sel.configure(
                text="{} · {}{}".format(aid, t.state, extra))

    def _attach_live(self, rows):
        """把「实时状态」（logs/live/<账号>.json）贴到对应账号那一行上。

        2026-10-09 用户要求：「不管是任务面板启动的还是启动页启动的，
          都算到那个账号里；不要另起『外部窗口』这种条目」。
          子进程（run 命令）每处理一个账号就更新这个文件，谁起的都会更新，
          所以它是两条路共同的信源：面板不需要知道任务是谁起的。

        贴的东西：细状态（刷新中 / 轮询中 / 待提交订单 / 需要验证码 / 登录输错）、
        pid（下面「停止」和「双击开浏览器」都要用）、最近一行（状态详情）。
        读的时候已经过滤过死进程，文件在、进程没了的不算（强杀不留遗嘱）。
        """
        self._live = {}
        if live_status is None:
            return
        try:
            live = live_status.read_all()
        except Exception:
            return
        for r in rows:
            aid = str(r["aid"])
            v = live.get(aid)
            if not v:
                continue
            self._live[aid] = v
            try:
                r["live_pid"] = int(v.get("pid"))
            except Exception:
                r["live_pid"] = None
            st = str(v.get("state") or "").strip()
            # 面板自己正在停它的（S_STOPPING）不发消息盖，那两个字更准
            if st and r.get("state") != tasks.S_STOPPING:
                r["state"] = st
            detail = str(v.get("detail") or "").strip()
            if detail:
                r["last_line"] = detail
            if r.get("elapsed_text") in (None, "", "-"):
                try:
                    r["elapsed_text"] = _fmt_elapsed(time.time() - float(v["started"]))
                except Exception:
                    pass

    # 「画面」列：每行一个实时缩略图（2026-10-09）

    def _maybe_shoot(self):
        """按节流给在跑的行截缩略图（每 SHOT_MS 一轮）。

        和「画面墙」同一套分工：截图在后台线程里做（一个窗口几十毫秒，
          8 个账号就是几百毫秒，放主线程会把界面卡一下）；回主线程贴图
          （Tk 控件只能主线程碰）。截图只读窗口像素，不碰 selenium，
          抢票子进程里的事一点都不掺和。
        """
        if window_wall is None or not window_wall.available():
            return
        if self._shooting:
            return
        now = time.time()
        if now - self._last_shot < self.SHOT_MS / 1000.0:
            return
        targets = self._shot_targets()
        self._last_shot = now
        if not targets:
            # 一个可截的都没有：把旧图撤掉（窗口关了不该留旧画面）
            for aid in list(self._thumbs):
                self._thumbs.pop(aid, None)
                if self.tree.exists(aid):
                    try:
                        self.tree.item(aid, image="")
                    except Exception:
                        pass
            return

        self._shooting = True

        def work():
            shots = {}
            slock = threading.Lock()

            def one(aid, hwnd):
                try:
                    b64, _tw, _th = window_wall.thumb_b64(
                        hwnd, self.SHOT_W, max_h=self.SHOT_H)
                except Exception:
                    b64 = None
                if b64:
                    with slock:
                        shots[aid] = b64

            # 每个窗口一个线程：窗口最小化时截图要先「离屏恢复」再放回去（~0.25 秒，见 window_wall._grab_rgb_iconic），
            # 8 个串行要 2 秒、正好把整个刷新周期占满；并行一轮 ~0.3 秒。
            ts = [threading.Thread(target=one, args=(a, h), daemon=True)
                  for a, h in targets.items()]
            for t in ts:
                t.start()
            for t in ts:
                t.join()

            def back():
                self._apply_shots(shots, targets)

            # 投回主线程，不能用 self.win.after(0, back)：子线程调 tkinter 的 after 会抛 RuntimeError（见 post 的说明）。
            self.post(back)

        threading.Thread(target=work, daemon=True).start()

    def _shot_targets(self):
        """{账号: 窗口句柄}，现在找得到浏览器窗口的那几行。

        不另设门槛：找得到活窗口 = 它就在跑（窗口是 chrome 的，
          起点是 tm / live 里的 pid）。没在跑的账号自然找不到。
        """
        out = {}
        if self.tm is None or window_wall is None:
            return out
        try:
            aids = [str(r["aid"]) for r in self.tm.snapshot(self._accounts())]
        except Exception:
            return out
        for aid in aids:
            hwnd = self._browser_hwnd_of(aid)
            if hwnd:
                out[aid] = hwnd
        return out

    def _apply_shots(self, shots, targets):
        """回主线程贴图：新图贴上、这轮不在名单里的行撤掉旧图。"""
        self._shooting = False
        if not self.alive():
            return
        for aid, b64 in shots.items():
            try:
                img = tk.PhotoImage(data=b64)
            except Exception:
                continue
            self._thumbs[aid] = img          # 留住引用，不然会被 GC 掉变空白
            if self.tree.exists(aid):
                try:
                    self.tree.item(aid, image=img)
                except Exception:
                    pass
        for aid in list(self._thumbs):
            if aid in targets:
                continue
            self._thumbs.pop(aid, None)
            if self.tree.exists(aid):
                try:
                    self.tree.item(aid, image="")
                except Exception:
                    pass

    def _op_text(self, r):
        """这一行「操作」格里写什么（照它写的点一下就执行）。

        一格只写当前该做的那件事，不让用户自己判断「现在该按停止还是启动」，
          状态是程序知道的，没道理让人记。
        2026-10-09 起：不管任务是谁起的（面板 / 启动页那两个绿色按钮），
          在跑的都显示「停止」，两条路现在都能停（实时状态里有 pid，
          见 _stop_row）。
        """
        aid = str(r["aid"])
        state = r.get("state") or ""
        if aid in self._stopping or state == tasks.S_STOPPING:
            return "停止中…"
        if aid in self._queue:
            return "取消排队"
        # 实时状态说它活着（不管哪条路起的）就能停
        if r.get("live_pid"):
            return "停止"
        if state in (tasks.S_STARTING, tasks.S_RUNNING):
            return "停止"
        if state == tasks.S_NOT_STARTED:
            return "启动"
        return "重跑"

    def _on_tree_click(self, event):
        """点「操作」列，一键执行那一格写的动作。

        只有点在最后一列才接管；点别处一律不管，交给 Treeview 自己
          去选中，老的「选中 + 停止选中」两步法一个字没改。
        """
        try:
            col = self.tree.identify_column(event.x)
            aid = self.tree.identify_row(event.y)
        except Exception:
            return
        if not aid or col != "#{}".format(len(self.COLS)):
            return
        self._row_action(str(aid))

    def _on_double_click(self, event):
        """双击账号那一行，把它的浏览器窗口拉到最前（2026-10-09 用户要）。

        为什么这个手势值钱：弹验证码 / 滑块的时候，人得赶紧切到那个窗口
          去处理，从这里一键切过去，比在任务栏一列 Chrome 里猜哪个是哪个
          快得多。想看日志用「查看日志」按钮。
        """
        try:
            aid = self.tree.identify_row(event.y)
        except Exception:
            return
        if aid:
            self.focus_browser(str(aid))

    def focus_browser(self, aid):
        """把一个账号的浏览器窗口拉到最前。找不到就说人话。"""
        if window_wall is None or not window_wall.available():
            self._say("拉窗口只在 Windows 上能用。")
            return
        hwnd = self._browser_hwnd_of(aid)
        if not hwnd:
            self._say("「{}」现在没有开着的浏览器窗口（没在跑，或已经关掉了）。"
                      .format(aid))
            return
        if window_wall.focus_window(hwnd):
            self._say("已把「{}」的浏览器窗口拉到最前。".format(aid))
        else:
            self._say("拉窗口失败，手动从任务栏切过去吧。")

    def _browser_hwnd_of(self, aid):
        """一个账号的浏览器窗口句柄。

        从它的任务进程往下走整棵进程树才能找到浏览器：
        中间还夹着一个 chromedriver，chrome 是孙辈，不是儿子。
        面板手里没有它的（启动页起的），退到实时状态里的 pid。
        """
        if window_wall is None:
            return None
        t = self.tm.tasks.get(aid) if self.tm else None
        pid = getattr(t, "pid", None) if t is not None else None
        if not pid:
            v = (self._live or {}).get(aid)
            pid = v.get("pid") if v else None
        if not pid:
            return None
        try:
            wins = window_wall.windows_for_pids(window_wall.descendants(pid))
        except Exception:
            wins = []
        return wins[0][0] if wins else None

    def _row_action(self, aid):
        """执行这一行的动作：排队中取消排队，在跑就停，其余启动 / 重跑。"""
        if aid in self._queue:
            self._cancel_queued(aid)
            return
        t = self.tm.tasks.get(aid) if self.tm else None
        state = t.state if t is not None else tasks.S_NOT_STARTED
        if aid in self._stopping or state == tasks.S_STOPPING:
            self._say("「{}」正在停止中，稍等一下。".format(aid))
            return
        if state in (tasks.S_STARTING, tasks.S_RUNNING):
            self._stop_row(aid)
            return
        # 实时状态说它在跑（典型：走「全部账号启动」那条路的）也能停
        if str(aid) in getattr(self, "_live", {}):
            self._stop_row(aid)
            return
        acc = self._find(aid)
        if acc is None:
            self._say("找不到账号「{}」。".format(aid))
            return
        self._launch(aid, acc)
        self.refresh()

    def _cancel_queued(self, aid):
        """把一个还排着队的账号从队列里拿出来。"""
        try:
            self._queue.remove(aid)
        except ValueError:
            pass
        self._refresh_queue_label()
        self._say("已把「{}」移出排队。".format(aid))
        self.refresh()

    def _stop_row(self, aid):
        """一键停掉这一个账号（后台线程里停，界面不卡）。

        为什么开线程：停止要等整棵进程树收完（最多 6 秒），
          放主线程里点一下界面就僵住 6 秒，「一键停」不该是这个手感。
          停完用 `after(0, …)` 回主线程刷界面（tkinter 的控件只能主线程碰）。

        2026-10-09：两条路都能停，
          · 面板自己起的：tm 手里有任务对象，走 tm.stop（原逻辑不动）；
          · 启动页那两个绿色按钮起的：tm 手里没有它，但实时状态
            （logs/live/<账号>.json）里报了 pid，走 task_manager.kill_tree。
          杀法是同一套：`taskkill /F /T` 整棵树，chromedriver / chrome
          一起收走，不留占着 profile 的孤儿窗口（老坑）。
        """
        if self.tm is None:
            return
        self._stopping.add(aid)
        self._say("正在停止「{}」…".format(aid))
        self.refresh()

        # 面板手里有它吗？没有就退到实时状态里的 pid。
        ext_pid = None
        t = self.tm.tasks.get(aid)
        if not (t is not None and getattr(t, "pid", None)):
            v = (self._live or {}).get(aid)
            if v and v.get("pid"):
                try:
                    ext_pid = int(v["pid"])
                except Exception:
                    ext_pid = None

        def work():
            try:
                if ext_pid:
                    ok = tasks.kill_tree(ext_pid)
                    msg = ("「{}」已停止（连同它开的浏览器）。".format(aid) if ok
                           else "「{}」没能完全停掉".format(aid))
                else:
                    ok, msg = self.tm.stop(aid)
            except Exception:
                ok, msg = False, "停止「{}」时出错".format(aid)
                log_line("任务面板停止失败：\n" + traceback.format_exc())

            def back():
                self._stopping.discard(aid)
                if not self.alive():
                    return
                self._say(msg + ("" if ok else "（可以到任务管理器里结束它）"))
                self.refresh()

            # 投回主线程（同上：子线程不能碰 tkinter 的 after）
            self.post(back)

        threading.Thread(target=work, daemon=True).start()


    def _launch(self, aid, acc):
        """起一个账号。返回是否成功。"""
        errs = self.w._account_problems(acc)
        if errs:
            self._say("「{}」还不能开始：{}".format(aid, "、".join(errs)))
            return False
        if not acc.get("enabled", True):
            self._say("「{}」是停用状态。想让它跑，先在它的分页里勾上「启用」。".format(aid))
            return False

        # 界面上刚填、还没保存的密码先落盘，和「保存并开始」保持一致的心智：用户都填了，凭什么还要再点一次保存。
        try:
            pending, _half = self.w._collect_filled_creds([aid])
            self.w._save_filled_creds(pending)
        except Exception:
            log_line("任务面板保存密码失败：\n" + traceback.format_exc())

        env_extra = {}
        try:
            driver = resolve_chromedriver()
            if driver:
                env_extra["CHROMEDRIVER_PATH"] = driver
        except Exception:
            pass

        ok, msg = self.tm.start(acc, env_extra=env_extra)
        self._say(msg)
        return ok

    def start_selected(self):
        aid = self.selected_aid()
        if not aid:
            self._say("先在上面选一个账号。")
            return
        acc = self._find(aid)
        if acc is None:
            self._say("找不到账号「{}」。".format(aid))
            return
        self._launch(aid, acc)
        self.refresh()

    def start_all(self):
        accounts = self._accounts()
        ready, skipped = [], []
        for acc in accounts:
            aid = str(acc.get("account_id"))
            if not acc.get("enabled", True):
                skipped.append("{}（已停用）".format(aid))
                continue
            errs = self.w._account_problems(acc)
            if errs:
                skipped.append("{}（{}）".format(aid, "、".join(errs)))
                continue
            ready.append(acc)
        if not ready:
            self._say("没有配置完整、可以直接开始的账号。{}".format(
                "；".join(skipped) if skipped else ""))
            return

        who = "、".join(str(a.get("account_id")) for a in ready)
        cap, reason = self._plan(len(ready))
        body = ("将为 {} 个账号各开一个浏览器（不弹控制台窗口，进度都在这个面板里）："
                "\n\n{}\n\n{}").format(len(ready), who, reason)
        if len(ready) > cap:
            body += ("\n\n先开 {} 个，其余 {} 个排队，等前面有空位自动补上。\n"
                     "（同时开太多会把内存吃满，结果是每个都变慢、甚至有的起不来）"
                     .format(cap, len(ready) - cap))
        if skipped:
            body += "\n\n跳过：" + "；".join(skipped)
        if not messagebox.askyesno("准备开始（多开 {} 个）".format(len(ready)), body):
            self._say("已取消。")
            return

        # 全部丢进队列，由 _pump_queue 一个一个放。第一批和后面补位走同一条代码路径，不用维护「首轮错峰」和
        # 「补位」两套启动逻辑（两套迟早会长歪，然后只在其中一条上出 bug）。
        self._queue = [str(a.get("account_id")) for a in ready]
        self._cap = max(1, int(cap))
        self._last_fill = 0.0
        self._say("正在启动…（同时最多 {} 个，其余排队）".format(self._cap))
        self._pump_queue(force=True)
        threading.Thread(target=focus_damai_window, daemon=True).start()

    # 多开：一次开几个、怎么排队

    def _settings(self):
        if run_settings is None:
            return {}
        try:
            return run_settings.load()
        except Exception:
            return {}

    def _running_count(self):
        """现在有几个账号在跑（含正在启动 / 正在停止的）。"""
        if self.tm is None or tasks is None:
            return 0
        try:
            rows = self.tm.snapshot(self._accounts())
        except Exception:
            return 0
        return sum(1 for r in rows
                   if r["state"] in (tasks.S_STARTING, tasks.S_RUNNING, tasks.S_STOPPING))

    def _plan(self, wanted):
        """按设置 + 本机可用内存，算出同时允许跑几个。返回 (cap, 一句人话)。"""
        s = self._settings()
        if not bool(s.get("enabled", True)):
            return max(1, wanted), "多开优化已关：{} 个账号会立刻各开一个浏览器。".format(wanted)
        manual = int(s.get("max_at_once") or 0)
        if manual:
            return manual, "多开设置里限定了同时最多 {} 个。".format(manual)
        if resource_guard is None:
            return max(1, min(wanted, 8)), "（找不到 resource_guard.py，按默认上限 8 个处理）"
        try:
            allow, reason = resource_guard.preflight(wanted)
            return max(1, int(allow)), reason
        except Exception:
            log_line("算多开上限失败：\n" + traceback.format_exc())
            return max(1, min(wanted, 8)), "（算本机内存时出错，按默认上限 8 个处理）"

    def _pump_queue(self, force=False):
        """从队列里放人。

        一次只放一个，放完就返回，「错峰」不是另加一个定时器，
          而是这件事本身：下一轮 tick（1 秒后）才会考虑放第二个，
          再叠加上 stagger_seconds 的间隔要求。这样只有一处逻辑要维护。
        """
        if not self._queue or not self.alive():
            self._refresh_queue_label()
            return
        if self.tm is None:
            return
        settle = float(self._settings().get("stagger_seconds") or 0)
        now = time.time()
        if not force and settle > 0 and (now - self._last_fill) < settle:
            self._refresh_queue_label()
            return
        if self._running_count() >= self._cap:
            self._refresh_queue_label()
            return

        aid = self._queue.pop(0)
        acc = self._find(aid)
        if acc is None:
            self._say("「{}」已经从账号列表里没了，跳过。".format(aid))
            return
        self._last_fill = now
        if self._launch(aid, acc):
            self._say("已启动「{}」：在跑 {} 个 / 上限 {}，排队中 {} 个".format(
                aid, self._running_count(), self._cap, len(self._queue)))
        self.refresh()
        self._refresh_queue_label()

    def _refresh_queue_label(self):
        if not hasattr(self, "queue_lbl"):
            return
        try:
            if self._queue:
                head = "、".join(self._queue[:4]) + ("…" if len(self._queue) > 4 else "")
                txt = "排队中 {} 个：{}（正在跑 {} 个，同时最多 {} 个）".format(
                    len(self._queue), head, self._running_count(), self._cap)
            else:
                # 没有排队时，这里显示当前的多开设置，让「为什么只有 3 个在跑」这类问题不用去翻设置就能看到答案。
                txt = "\n".join(x for x in (
                    run_settings.describe() if run_settings else "",
                    "点「多开设置」可以改；点「资源体检」看本机还能开几个。",
                ) if x)
            self.queue_lbl.configure(text=txt)
        except Exception:
            pass

    def _profile_map(self):
        """{账号: Profile 目录}，体检用（profile_path 不会建目录）。"""
        out = {}
        for acc in self._accounts():
            try:
                out[str(acc.get("account_id"))] = acc_store.profile_path(acc)
            except Exception:
                continue
        return out

    def open_resource_report(self):
        """资源体检：本机内存 + 每个账号的浏览器各占多少。"""
        if resource_guard is None:
            self._say("找不到 resource_guard.py，体检做不了。")
            return
        self._say("正在体检（查进程要一两秒）…")
        try:
            self.win.update_idletasks()
        except Exception:
            pass
        try:
            text = resource_guard.report(self._profile_map())
        except Exception:
            text = "体检失败：\n" + traceback.format_exc()

        s = self._settings()
        per = resource_guard.DEFAULT_PER_WINDOW_MB
        body = text + "\n\n" + "-" * 46 + "\n"
        body += "估算口径：每个浏览器按 {} MB（resource_guard.DEFAULT_PER_WINDOW_MB）。\n".format(per)
        body += "想要本机的真实数，跑：\n"
        body += "  python tools/measure_chrome_cost.py --n 1,3\n"
        body += "\n当前设置：\n  " + (run_settings.describe(s) if run_settings else "（缺少 run_settings.py）")
        body += ("\n  轻量化参数实测省的是磁盘缓存和后台流量，**内存几乎不省** —— "
                 "想多开，靠的是上面那个「同时最多几个」。")
        self._show_text("资源体检", body, width=760, height=560)
        self._say("体检完成。")

    def open_multi_settings(self):
        MultiOpenDialog(self)

    def _show_text(self, title, body, width=760, height=520):
        """只读文本框窗口。体检报告、参数原文这类东西都走它。"""
        win = tk.Toplevel(self.win)
        win.title(title)
        win.configure(bg=COLOR_BG)
        win.geometry("{}x{}".format(width, height))
        # 按钮先占住底部，再让文本框去吃剩下的空间（顺序见 fit_window 的注释）
        row = tk.Frame(win, bg=COLOR_BG)
        row.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        box = tk.Frame(win, bg=COLOR_BG)
        box.pack(fill="both", expand=True, padx=12, pady=10)
        text = tk.Text(box, font=("Consolas", 9), bg="#ffffff", fg=COLOR_TEXT,
                       relief="flat", borderwidth=0, highlightthickness=1,
                       highlightbackground=COLOR_BORDER, wrap="word",
                       padx=10, pady=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        text.insert("1.0", body)
        text.configure(state="disabled")

        def copy():
            try:
                self.win.clipboard_clear()
                self.win.clipboard_append(body)
                self._say("已复制到剪贴板。")
            except Exception:
                pass

        self._btn(row, "复制", copy, ghost=True).pack(side="left")
        self._btn(row, "关闭", win.destroy, primary=True).pack(side="right")
        fit_window(win, min_w=width, min_h=height)
        return win


    def stop_selected(self):
        aid = self.selected_aid()
        if not aid:
            self._say("先在上面选一个账号。")
            return
        t = self.tm.tasks.get(aid) if self.tm else None
        if t is None or not t.pid:
            # 面板手里没有它的进程？实时状态说它在跑的话照样能停（启动页那两个绿色按钮起的就走这条，2026-10-09）。
            v = (self._live or {}).get(aid)
            if v and v.get("pid"):
                self._stop_row(aid)
                return
            self._say("「{}」没有正在跑的任务。".format(aid))
            return
        self._say("正在停止「{}」…".format(aid))
        try:
            self.win.update_idletasks()
        except Exception:
            pass
        ok, msg = self.tm.stop(aid)
        self._say(msg + ("" if ok else "（可以到任务管理器里结束它）"))
        self.refresh()

    def stop_all(self):
        if self.tm is None:
            return
        # 先清空队列：不然「全部停止」之后，_pump_queue 还会把排队那些一个个放出来，用户看到的就是「点了全停，浏览器却还在往外冒」。
        queued = len(self._queue)
        self._queue = []
        rows = self.tm.snapshot(self._accounts())
        busy = [r["aid"] for r in rows if r["busy"]]
        if not busy:
            self._say("现在没有在跑的任务。"
                      + ("（已取消 {} 个排队的账号）".format(queued) if queued else ""))
            self._refresh_queue_label()
            return
        tip = "，并取消 {} 个排队的账号".format(queued) if queued else ""
        if not messagebox.askyesno(
                "全部停止",
                "将停止下面 {} 个账号的任务（连同它们打开的浏览器）{}：\n\n{}\n\n"
                "确定吗？".format(len(busy), tip, "、".join(busy))):
            self._queue = []            # 走到这里说明用户看过了，队列仍然作废
            self._refresh_queue_label()
            self._say("已取消。")
            return
        done, bad = self.tm.stop_all()
        text = "已停止：{}".format("、".join(done) or "（无）")
        if bad:
            text += "；没能停掉：{}".format("、".join(bad))
        if queued:
            text += "；已取消 {} 个排队的账号".format(queued)
        self._say(text)
        self.refresh()
        self._refresh_queue_label()


    def view_log(self):
        """看这个账号最近一次任务的日志。

        日志有两个来源，这里都认（2026-10-09）：任务面板起的任务，面板自己
          记着路径；向导上「开始抢票」起的任务句柄不在面板手里，只有文件落在
          runs/ 下，所以问不到面板就按文件名去找（`latest_task_log`）。
          不这么做的话，把这些任务的控制台窗口一藏，用户就彻底看不到运行情况了。
        """
        aid = self.selected_aid()
        if not aid:
            self._say("先在上面选一个账号。")
            return
        t = self.tm.tasks.get(aid) if self.tm else None
        path = t.log_path if t is not None else None
        if not path or not os.path.exists(path):
            path = latest_task_log(aid)
        if not path or not os.path.exists(path):
            self._say("「{}」还没有日志（这个账号还没跑过）。".format(aid))
            return
        win = tk.Toplevel(self.win)
        win.title("日志 · {}".format(aid))
        win.configure(bg=COLOR_BG)
        win.geometry("860x560")
        # 按钮先占住底部，再让文本框去吃剩下的空间（顺序见 fit_window 的注释）
        bar = tk.Frame(win, bg=COLOR_BG)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        box = tk.Frame(win, bg=COLOR_BG)
        box.pack(fill="both", expand=True, padx=12, pady=10)
        text = tk.Text(box, font=("Consolas", 9), bg="#ffffff", fg=COLOR_TEXT,
                       relief="flat", borderwidth=0, highlightthickness=1,
                       highlightbackground=COLOR_BORDER, wrap="word",
                       padx=10, pady=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def reload():
            try:
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.insert("1.0", read_log_file(path))
                text.see("end")
                text.configure(state="disabled")
            except Exception:
                pass

        reload()
        self._btn(bar, "刷新", reload, ghost=True).pack(side="left")
        self._btn(bar, "打开文件所在目录",
                  lambda: self._reveal(path), ghost=True).pack(side="left", padx=(8, 0))
        tk.Label(bar, text=path, font=UI_FONT_SMALL, fg=COLOR_HINT,
                 bg=COLOR_BG).pack(side="left", padx=(10, 0))
        fit_window(win, min_w=760, min_h=520)

    def open_wall(self):
        """打开「任务画面」：每个开着的浏览器窗口缩成一张图，摆在一个窗口里。

        和面板本身的分工：面板管起停 / 状态 / 日志，画面墙只管「看得见」。
        """
        if window_wall is None or not window_wall.available():
            self._say("画面墙只在 Windows 上能用（抢票本身不受影响）。")
            return
        wall = getattr(self, "_wall", None)
        if wall is not None and wall.alive():
            wall.lift()
            return
        try:
            self._wall = TaskWall(self)
        except Exception:
            log_line("打开任务画面失败：\n" + traceback.format_exc())
            self._say("任务画面打不开，日志里有详情。")

    def open_runs_dir(self):
        self._reveal(self.tm.runs_dir if self.tm else BASE_DIR)

    @staticmethod
    def _reveal(path):
        try:
            if os.path.isdir(path):
                os.startfile(path)                       # noqa: S606 (Windows)
            else:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass


class TaskWall(object):
    """任务画面墙：开着的浏览器窗口，每个缩成一格摆在一个窗口里。

    它解决的是哪件事：多个账号一起抢时，某个窗口弹了验证码 / 滑块，
      得有人过去点一下才能继续。以前只能挨个把浏览器窗口翻出来看，
      窗口一多就会漏，而漏掉的那个可能从头到尾停在验证页上，等于白抢。
      这里把所有窗口缩成图摆在一起，哪个在等人一眼就能看见；
      点一格就把它拉到最前，直接去处理。

    和任务面板的分工：
      · 任务面板管起停 / 状态 / 日志；
      · 画面墙只管「看得见」。两条路互不影响，谁开着谁关着都不碍事。

    截图走 Windows 原生 API（`window_wall`，纯 ctypes）：
      抢票跑在子进程里，它的 driver 主进程根本拿不到；而且两个线程同时用
      一个 driver 会让「命令 / 响应」错位，那是能毁掉主流程的风险。
    """

    INTERVAL_MS = 2000        # 每 2 秒刷一轮
    MAX_W = 320               # 缩略图宽（缩放交给 GDI，别把大图搬进 Python）
    COLS = 3                  # 一行摆几格

    def __init__(self, panel):
        self.p = panel
        self.w = panel.w
        self.tm = panel.tm
        self.win = tk.Toplevel(self.w.root)
        self.win.title("任务画面")
        self.win.configure(bg=COLOR_BG)
        self.win.geometry("1160x780")
        self.win.minsize(780, 500)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = None
        self._busy = False        # 上一轮还没抓完就不开新一轮
        self._imgs = {}           # 图片引用必须留着，不然会被 GC，格子变空白
        self._cells = {}
        self._paused = False
        self._inbox = []          # 子线程 → 主线程的投递箱（见 post 的说明）
        self._build()
        self.refresh()
        self._schedule()


    def post(self, fn):
        """子线程往主线程投递函数：塞一个进来，主线程刷新时执行。

        和任务面板的同名机制一个道理（2026-10-09）：tkinter 在非主线程里
          调 after 会抛 `RuntimeError: main thread is not in main loop`，
          被 except 一吞，表现就是「画面墙永远抓不到图」（白摆一屏空格子）。
          子线程只 append（线程安全），主线程的 _tick（每 2 秒）取出来执行。
        """
        self._inbox.append(fn)

    def _drain_inbox(self):
        while self._inbox:
            fn = self._inbox.pop(0)
            try:
                fn()
            except Exception:
                log_line("任务画面回调出错：\n" + traceback.format_exc())

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def lift(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass

    def close(self):
        """关掉画面墙只是不看了，一个任务都不会受影响。"""
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.win.destroy()
        except Exception:
            pass

    def _schedule(self):
        if not self.alive():
            return
        try:
            self._after_id = self.win.after(self.INTERVAL_MS, self._tick)
        except Exception:
            self._after_id = None

    def _tick(self):
        # 同上：抓图那一步出错不许把定时器带走，不然画面就永久定格了。
        try:
            self._drain_inbox()      # 先处理子线程投回来的截图
            if not self._paused:
                self.refresh()
        except Exception:
            log_line("任务画面刷新失败：\n" + traceback.format_exc())
        self._schedule()

    def toggle_pause(self):
        self._paused = not self._paused
        try:
            self._pause_btn.configure(text="继续刷新" if self._paused else "暂停刷新")
        except Exception:
            pass
        self._say("已暂停刷新（画面还是定格的那张）。"
                  if self._paused else "继续刷新。")


    def _say(self, text):
        try:
            self.msg.configure(text=text)
        except Exception:
            pass

    def _build(self):
        outer = tk.Frame(self.win, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)

        head = tk.Frame(outer, bg=COLOR_BG)
        head.pack(side="top", fill="x")
        tk.Label(head, text="任务画面", font=UI_FONT_TITLE, fg=COLOR_TEXT,
                 bg=COLOR_BG).pack(side="left")
        self._pause_btn = self.w._ghost_btn(head, "暂停刷新", self.toggle_pause,
                                            font=UI_FONT_SMALL, padx=12)
        self._pause_btn.pack(side="right")
        self.w._ghost_btn(head, "前置全部", self.raise_all,
                          font=UI_FONT_SMALL, padx=12).pack(side="right",
                                                             padx=(0, 8))

        self.msg = tk.Label(outer, text="", font=UI_FONT_SMALL, fg=COLOR_HINT,
                            bg=COLOR_BG, anchor="w", justify="left")
        self.w._bind_wrap(self.msg, outer, margin=28)

        wrap = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1, borderwidth=0)
        self.canvas = tk.Canvas(wrap, bg=COLOR_CARD, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.grid = tk.Frame(self.canvas, bg=COLOR_CARD)
        self._grid_id = self.canvas.create_window((0, 0), window=self.grid,
                                                  anchor="nw")
        self.grid.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._grid_id, width=e.width))

        # 底部提示先钉住，表格吃掉剩下的空间（和任务面板同一个套路）。
        self.msg.pack(side="bottom", fill="x", pady=(8, 0))
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        fit_window(self.win, min_w=900, min_h=520)


    # 标题里有这些词的，多半就是抢票那个窗口，排在前面
    BROWSER_MARKS = ("大麦", "damai", "确认购买", "订单", "选座", "支付",
                     "登录", "实名", "观演", "alipay", "taobao")

    _BROWSER_EXES = ("chrome", "msedge", "chromium", "brave")

    def _my_window_pids(self):
        """{窗口进程号: 账号}，不管是哪条路起的任务，能对上账号的都标出来。

        从任务进程往下找整棵进程树才找得到浏览器：中间还夹着一个
          chromedriver，chrome 是孙辈，不是儿子。
        2026-10-09：除了面板自己的任务表，也读实时状态（logs/live），
          启动页那条路起的任务不在面板表里，但在 live 里有账号和 pid，
          所以画面墙的每一格现在都能标出账号名，不再有「（外部启动）」。
        """
        out = {}
        if window_wall is None:
            return out
        roots = {}
        if self.tm is not None:
            for aid, t in list(self.tm.tasks.items()):
                pid = getattr(t, "pid", None)
                if pid:
                    roots[int(pid)] = str(aid)
        if live_status is not None:
            try:
                for aid, v in live_status.read_all().items():
                    pid = v.get("pid")
                    if pid:
                        roots.setdefault(int(pid), aid)
            except Exception:
                pass
        for pid, aid in roots.items():
            try:
                for x in window_wall.descendants(pid):
                    out[x] = aid
            except Exception:
                pass
        return out

    def _targets(self):
        """[(标签, 窗口句柄)]，扫所有浏览器窗口，不是只看面板自己的任务表。

        为什么不能只看任务表（2026-10-09 用户报「状态不更新」查出来的）：
          向导底部那两个绿色按钮走的是另一条路，进程句柄不在面板手里。
          只看任务表的话，用那条路起的窗口在画面墙里一个都看不见，
          面板上也全是「未启动」，用户只会以为程序坏了。
          这里直接扫窗口：谁起的都能看见。能对上账号的标账号，对不上的
          标「外部启动」，如实说，不假装是自己管的。
        """
        out = []
        if window_wall is None:
            return out
        try:
            our = our_chrome_pids(self.tm)         # 只认本程序起的浏览器
            wins = window_wall.list_windows()
        except Exception:
            return out
        mine = self._my_window_pids()
        for hwnd, pid, title in wins:
            if pid not in our:                     # 用户自己的浏览器不列
                continue
            if "MarkZz" in title:                  # 测试用的靶子窗口，不显示
                continue
            short = (title or "").strip() or "(无标题)"
            if len(short) > 46:
                short = short[:46] + "…"
            who = mine.get(pid)
            label = "{}　{}".format(who, short) if who else "（外部启动）" + short
            out.append((label, hwnd))
        # 像大麦的排前面
        out.sort(key=lambda x: (0 if any(m in x[0] for m in self.BROWSER_MARKS)
                                else 1, x[0]))
        return out

    def refresh(self):
        """抓一遍画面。截图在后台线程做，主线程只负责贴图。

        8 个账号一轮要 200-300 毫秒。放主线程里做，界面每 2 秒卡一次，
          抢票的时候卡一下可能就错过一个时机，不值当。
          （Tk 的控件只能主线程碰，所以后台只做「截 + 编码」，回主线程贴。）
        """
        if not self.alive():
            return
        if window_wall is None or not window_wall.available():
            self._say("画面墙只在 Windows 上能用（抢票本身不受影响）。")
            return
        if self._busy:
            return
        targets = self._targets()
        if not targets:
            self._drop_cells()
            self._say("现在没有开着的浏览器窗口。启动账号（用「全部账号启动」"
                      "或任务面板都行），窗口起来之后这里就会出现画面。")
            return

        self._busy = True

        def work():
            shots = {}
            slock = threading.Lock()

            def one(label, hwnd):
                try:
                    b64, tw, th = window_wall.thumb_b64(hwnd, self.MAX_W)
                except Exception:
                    b64 = None
                if b64:
                    with slock:
                        shots[label] = (b64, tw, th)

            # 每个窗口一个线程（同上：最小化的窗口截图要先离屏恢复，串行太慢）
            ts = [threading.Thread(target=one, args=(lb, h), daemon=True)
                  for lb, h in targets]
            for t in ts:
                t.start()
            for t in ts:
                t.join()

            def back():
                self._busy = False
                if not self.alive():
                    return
                self._apply(shots, targets)

            # 投回主线程（同上：子线程不能碰 tkinter 的 after，不然图永远贴不上，这里原来就是这么坏掉的）。
            self.post(back)

        threading.Thread(target=work, daemon=True).start()

    def _cell(self, label, hwnd):
        """取（或建）某个窗口的格子：标题 + 图 + 「前置」按钮。"""
        cell = self._cells.get(label)
        if cell is not None:
            return cell
        frame = tk.Frame(self.grid, bg=COLOR_CARD,
                         highlightbackground=COLOR_BORDER, highlightthickness=1)
        title = tk.Label(frame, text=label, font=UI_FONT_SMALL, fg=COLOR_TEXT,
                         bg=COLOR_CARD, anchor="w", justify="left")
        title.pack(side="top", fill="x", padx=6, pady=(5, 2))
        shot = tk.Label(frame, bg=COLOR_BG, fg=COLOR_HINT, text="（取画面中…）",
                        font=UI_FONT_SMALL, width=42, height=12)
        shot.pack(side="top", fill="both", expand=True, padx=6)
        self.w._ghost_btn(frame, "前置这个窗口",
                          lambda h=hwnd: self._raise_hwnd(h),
                          font=UI_FONT_SMALL, padx=10).pack(
            side="bottom", anchor="e", padx=6, pady=(2, 6))
        cell = (frame, title, shot, hwnd)
        self._cells[label] = cell
        return cell

    def _apply(self, shots, items):
        """items: [(标签, 窗口句柄)]。"""
        labels = [l for l, _h in items]
        for i, (label, hwnd) in enumerate(items):
            frame, title, shot, _h = self._cell(label, hwnd)
            frame.grid(row=i // self.COLS, column=i % self.COLS,
                       padx=8, pady=8, sticky="nsew")
            got = shots.get(label)
            if not got:
                shot.configure(image="", text="（这个窗口现在抓不到画面）")
                self._imgs.pop(label, None)
                continue
            b64, tw, th = got
            try:
                img = tk.PhotoImage(data=b64)
            except Exception:
                shot.configure(image="", text="（这张图解不开）")
                continue
            self._imgs[label] = img         # 留住引用
            shot.configure(image=img, text="", width=tw, height=th)
        # 关掉的窗口，格子收掉
        for label in list(self._cells):
            if label not in labels:
                frame = self._cells.pop(label)[0]
                try:
                    frame.destroy()
                except Exception:
                    pass
                self._imgs.pop(label, None)
        for c in range(self.COLS):
            self.grid.columnconfigure(c, weight=1)
        self._say("共 {} 个浏览器窗口，每 {} 秒刷新一次。哪个弹了验证码 / 滑块，"
                  "在这里一眼能看见；点「前置这个窗口」就跳过去处理。".format(
                      len(items), max(1, self.INTERVAL_MS // 1000)))

    def _drop_cells(self):
        for label in list(self._cells):
            try:
                self._cells.pop(label)[0].destroy()
            except Exception:
                pass
        self._cells.clear()
        self._imgs.clear()


    def _raise_hwnd(self, hwnd):
        if window_wall is None or not hwnd:
            return
        ok = window_wall.focus_window(hwnd)
        self._say("已把那个窗口拉到最前。" if ok
                  else "拉窗口失败，手动从任务栏切过去吧。")

    def raise_all(self):
        n = 0
        for label in list(self._cells):
            hwnd = self._cells[label][3]
            if window_wall is not None and window_wall.focus_window(hwnd):
                n += 1
        self._say("把 {} 个窗口都拉到了最前（最后拉的那个在最上面）。".format(n)
                  if n else "没有找到可拉的窗口。")



class ChangelogDialog(object):
    """更新日志。内容就是那份 txt，原样贴出来。

    为什么不做成「按版本号解析、每条一个小卡片」：
      那等于给发布方立格式规矩（少一个空格就解析不出来），而这份文件是
      人写给人看的。原样显示最省事，写什么看到什么。
    找不到文件也把窗口开出来（见 Wizard.open_changelog）。
    """

    def __init__(self, parent, mod, ver=None):
        self.mod = mod
        self.text, self.path = mod.read()
        self.win = tk.Toplevel(parent)
        self.win.title("更新日志")
        self.win.configure(bg=COLOR_BG)
        self.win.geometry("720x560")
        self.win.minsize(560, 380)
        try:
            self.win.transient(parent)
        except Exception:                                    # pragma: no cover
            pass
        # 故意不 grab_set：看日志不影响干别的（跟环境检测窗口一个道理）。
        self._build(ver)

    def _build(self, ver):
        now = ""
        if ver is not None:
            now = "当前版本 {}".format(ver.APP_VERSION)
        tk.Label(self.win, text="更新日志", font=UI_FONT_TITLE, bg=COLOR_BG,
                 fg=COLOR_TEXT, anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(self.win, text=now or "这份程序改过什么，都写在这里。",
                 font=UI_FONT_SMALL, bg=COLOR_BG, fg=COLOR_HINT,
                 anchor="w").pack(fill="x", padx=18)

        # 正文：只读文本域（能选中、能 Ctrl+C，但改不了）
        wrap = tk.Frame(self.win, bg=COLOR_BG)
        # 先 pack 底部按钮栏、再让正文吃掉剩下的空间：顺序反了的话，窗口一矮被切掉的正好是「关闭」（EnvCheckDialog 里踩过）。
        bar = tk.Frame(self.win, bg=COLOR_CARD)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=COLOR_BORDER, height=1).pack(side="top", fill="x")
        row = tk.Frame(bar, bg=COLOR_CARD)
        row.pack(fill="x", padx=16, pady=(11, 12))
        tk.Button(row, text="关闭", command=self.win.destroy, font=UI_FONT,
                  bg=COLOR_PRIMARY, fg="#ffffff", activebackground=COLOR_PRIMARY_HOVER,
                  activeforeground="#ffffff", relief="flat", padx=18, pady=4,
                  cursor="hand2", highlightthickness=0, bd=0).pack(side="right")
        if self.path:
            tk.Label(row, text="文件：{}".format(self.path), font=UI_FONT_SMALL,
                     fg=COLOR_HINT, bg=COLOR_CARD, anchor="w").pack(
                         side="left")

        wrap.pack(fill="both", expand=True, padx=18, pady=(10, 12))
        box = tk.Text(wrap, font=UI_FONT, wrap="word", relief="flat", borderwidth=0,
                      bg=COLOR_CARD, fg=COLOR_TEXT if self.text else COLOR_WARN,
                      padx=12, pady=10, spacing1=1, spacing3=3,
                      highlightthickness=1, highlightbackground=COLOR_BORDER)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        box.insert("1.0", self.text if self.text else self.mod.hint())
        box.configure(state="disabled")          # 只读：能选能复制，不能改
        box.bind("<MouseWheel>",
                 lambda e: box.yview_scroll(int(-e.delta / 120), "units"))


class EnvCheckDialog(object):
    """环境检测结果。

    排序是故意的：有问题的排在最前面。客户点这个按钮是因为「跑不起来」，
      他要的是「我该装什么」，不是一份按固定顺序排列的体检报告。
      全绿的时候才按原顺序平铺。

    「缺什么」那一栏直接给下载按钮，点一下就开浏览器。
      不给链接的话，用户还得自己去搜「Chrome 下载」，搜到一堆假的。
    """

    def __init__(self, parent, results):
        self.results = list(results or [])
        self.win = tk.Toplevel(parent)
        self.win.title("环境检测")
        self.win.configure(bg=COLOR_BG)
        self.win.geometry("720x560")
        self.win.minsize(620, 460)
        try:
            self.win.transient(parent)
        except Exception:                                        # pragma: no cover
            pass
        # 故意不 grab_set：它不是「不处理完不许走」的那种窗口。客户多半要照着这里的提示去装 Chrome，那时候得能切窗口。
        self._build()


    def _btn(self, parent, text, command, primary=False, padx=12):
        return tk.Button(parent, text=text, command=command, font=UI_FONT,
                         bg=COLOR_PRIMARY if primary else COLOR_GHOST,
                         fg="#ffffff" if primary else COLOR_TEXT,
                         activebackground=(COLOR_PRIMARY_HOVER if primary
                                           else COLOR_GHOST_HOVER),
                         activeforeground="#ffffff" if primary else COLOR_TEXT,
                         relief="flat", padx=padx, pady=4, cursor="hand2",
                         highlightthickness=0, bd=0)

    def _open(self, url):
        """开浏览器。打不开就退回「复制链接」，总得有一条路把地址给出去。"""
        try:
            import webbrowser
            if webbrowser.open(url):
                return
        except Exception:
            pass
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(url)
            messagebox.showinfo("链接已复制",
                                "浏览器没能自动打开，链接已经复制到剪贴板：\n\n" + url)
        except Exception:
            messagebox.showinfo("下载地址", url)

    def _copy_report(self):
        text = env_check.describe(self.results)
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
            self.hint_var.set("报告已复制，可以直接发给对方。")
        except Exception:
            messagebox.showinfo("环境检测报告", text)


    def _build(self):
        bad = env_check.failures(self.results)
        warns = [r for r in self.results
                 if r["level"] == env_check.LEVEL_WARN]
        oks = [r for r in self.results if r["level"] == env_check.LEVEL_OK]

        if bad:
            head, head_fg = "有东西没装好，现在跑不起来", COLOR_WARN
        elif warns:
            head, head_fg = "能用，但有几点要注意", COLOR_ATTENTION
        else:
            head, head_fg = "环境没问题，可以开始", COLOR_OK

        tk.Label(self.win, text="环境检测", font=UI_FONT_TITLE, bg=COLOR_BG,
                 fg=COLOR_TEXT, anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(self.win, text=head, font=UI_FONT_BOLD, bg=COLOR_BG, fg=head_fg,
                 anchor="w").pack(fill="x", padx=18)

        # 滚动区：项目多的时候窗口不至于被撑爆。先创建、不 pack，底部那排按钮要先占住位置（见下面的 pack 顺序）。
        outer = tk.Frame(self.win, bg=COLOR_BG)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, bg=COLOR_BG)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=COLOR_BG)
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(wid, width=e.width))

        def on_wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<MouseWheel>", on_wheel)
        inner.bind("<MouseWheel>", on_wheel)

        # 有问题的排前面
        for r in bad + warns + oks:
            self._row(inner, r, on_wheel)

        bar = tk.Frame(self.win, bg=COLOR_CARD)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=COLOR_BORDER, height=1).pack(side="top", fill="x")
        row = tk.Frame(bar, bg=COLOR_CARD)
        row.pack(fill="x", padx=16, pady=(11, 12))
        self._btn(row, "复制检测报告", self._copy_report).pack(side="left")
        self._btn(row, "关闭", self.win.destroy, primary=True).pack(side="right")
        self.hint_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.hint_var, font=UI_FONT_SMALL,
                 fg=COLOR_HINT, bg=COLOR_CARD, anchor="w").pack(
                     fill="x", padx=16, pady=(0, 8))

        # 按钮钉住了，再让滚动区去吃掉剩下的空间（顺序反了的话，高 DPI 屏上被切掉的正好是「关闭」）。这条窗口尺寸也交给 fit_window 收尾。
        outer.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        # 底部先 pack 到 bottom，再回来把滚动区撑满
        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        fit_window(self.win, min_w=660, min_h=480)

    def _row(self, parent, item, on_wheel=None):
        level = item["level"]
        accent = {env_check.LEVEL_OK: COLOR_OK,
                  env_check.LEVEL_WARN: COLOR_ATTENTION,
                  env_check.LEVEL_FAIL: COLOR_WARN}.get(level, COLOR_HINT)
        card = tk.Frame(parent, bg=COLOR_CARD,
                        highlightbackground=COLOR_BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 8))

        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", padx=13, pady=(10, 0))
        tk.Label(line, text="●", font=UI_FONT, fg=accent, bg=COLOR_CARD).pack(side="left")
        tk.Label(line, text=item["name"], font=UI_FONT_BOLD, fg=COLOR_TEXT,
                 bg=COLOR_CARD).pack(side="left", padx=(7, 0))
        tk.Label(line, text=env_check.LEVEL_CN.get(level, ""), font=UI_FONT_SMALL,
                 fg=accent, bg=COLOR_CARD).pack(side="left", padx=(8, 0))

        tk.Label(card, text=item.get("detail") or "", font=UI_FONT,
                 fg=COLOR_HINT, bg=COLOR_CARD, anchor="w", justify="left",
                 wraplength=620).pack(fill="x", padx=13, pady=(3, 0))

        if item.get("fix"):
            tk.Label(card, text="怎么办：" + item["fix"], font=UI_FONT_SMALL,
                     fg=COLOR_TEXT, bg=COLOR_CARD, anchor="w", justify="left",
                     wraplength=620).pack(fill="x", padx=13, pady=(4, 0))

        if item.get("url"):
            link_row = tk.Frame(card, bg=COLOR_CARD)
            link_row.pack(fill="x", padx=13, pady=(7, 0))
            self._btn(link_row, item.get("url_text") or "打开下载页",
                      lambda u=item["url"]: self._open(u),
                      primary=True).pack(side="left")
            tk.Label(link_row, text=item["url"], font=UI_FONT_SMALL,
                     fg=COLOR_HINT, bg=COLOR_CARD).pack(side="left", padx=(9, 0))

        tk.Frame(card, bg=COLOR_CARD, height=10).pack(fill="x")

        if on_wheel is not None:
            for w in (card, line):
                w.bind("<MouseWheel>", on_wheel)


class MultiOpenDialog(object):
    """多开设置：一次开几个、错开多久、给 Chrome 什么参数。

    这是新增的窗口（任务面板上的「多开设置」按钮）。关掉多开优化就完全
      回到改造前：每个账号立刻各开一个浏览器，不限数量。

    为什么要有这一页而不是写死一组参数：
      「同时开几个」取决于别人电脑的配置，不是我们能替他决定的。
      客户机可能是 8 GB、也可能是 32 GB；写死一个数对一半人是错的。
      所以这里把「按内存自动算」做成默认，并给一个手动覆盖的口子。
    """

    def __init__(self, panel):
        self.panel = panel
        self.win = tk.Toplevel(panel.win)
        self.win.title("多开设置")
        self.win.configure(bg=COLOR_BG)
        self.win.geometry("660x520")
        self.win.minsize(600, 480)
        try:
            self.win.transient(panel.win)
            self.win.grab_set()
        except Exception:                                        # pragma: no cover
            pass
        self.s = (run_settings.load() if run_settings else {})
        self._build()


    def _btn(self, parent, text, command, primary=False, ghost=False):
        if primary:
            return self.panel.w._primary_btn(parent, text, command,
                                             font=UI_FONT_SMALL, padx=16)
        if ghost:
            return self.panel.w._ghost_btn(parent, text, command,
                                           font=UI_FONT_SMALL, padx=14)
        return self.panel.w._soft_btn(parent, text, command,
                                      font=UI_FONT_SMALL, padx=16)

    def _build(self):
        if run_settings is None:
            tk.Label(self.win, text="找不到 run_settings.py，改不了设置。",
                     font=UI_FONT, bg=COLOR_BG, fg=COLOR_WARN).pack(padx=18, pady=18)
            self._btn(self.win, "关闭", self.win.destroy, primary=True).pack()
            fit_window(self.win, min_w=420, min_h=180)
            return

        tk.Label(self.win, text="多开设置", font=UI_FONT_TITLE, bg=COLOR_BG,
                 fg=COLOR_TEXT, anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(self.win,
                 text="多开时真正吃资源的是 Chrome：实测一个自动化浏览器约 460 MB、"
                      "会裂成 8 个进程。所以省资源的关键是「别一次全开」，"
                      "而不是调参数。",
                 font=UI_FONT_SMALL, bg=COLOR_BG, fg=COLOR_HINT, anchor="w",
                 justify="left", wraplength=610).pack(fill="x", padx=18)

        card = tk.Frame(self.win, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(12, 0))

        self.var_enabled = tk.BooleanVar(value=bool(self.s.get("enabled", True)))
        self.var_light = tk.BooleanVar(value=bool(self.s.get("light_flags", True)))
        self.var_awake = tk.BooleanVar(value=bool(self.s.get("keep_awake", False)))

        self._check(card, self.var_enabled,
                    "启用多开优化（按内存排队，别一次全开）",
                    "关掉 = 完全回到改造前：每个账号立刻各开一个浏览器，不限数量。")
        self._check(card, self.var_light,
                    "用轻量化参数启动浏览器（关掉后台杂活）",
                    "如实说：实测内存几乎不省（456 vs 458 MB），省的是磁盘缓存和后台流量。"
                    "别指望靠它多开两个。")
        self._check(card, self.var_awake,
                    "保持后台窗口不降速（更耗 CPU）",
                    "Chrome 会给「被完全遮住」的窗口降速。多开时后面的窗口正好全被挡住。"
                    "只有在出现「只有最前面那个账号有反应」时才需要开。")

        form = tk.Frame(card, bg=COLOR_CARD)
        form.pack(fill="x", padx=14, pady=(6, 14))

        tk.Label(form, text="同时最多开", font=UI_FONT, bg=COLOR_CARD,
                 fg=COLOR_TEXT).grid(row=0, column=0, sticky="w", pady=4)
        self.entry_cap = tk.Entry(form, font=UI_FONT, width=6,
                                  bg="#ffffff", fg=COLOR_TEXT, relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=COLOR_BORDER)
        cap = int(self.s.get("max_at_once") or 0)
        if cap:
            self.entry_cap.insert(0, str(cap))
        self.entry_cap.grid(row=0, column=1, sticky="w", padx=(8, 4), pady=4)
        tk.Label(form, text="个（留空或 0 = 按本机可用内存自动算）", font=UI_FONT_SMALL,
                 bg=COLOR_CARD, fg=COLOR_HINT).grid(row=0, column=2, sticky="w",
                                                    padx=(4, 0), pady=4)

        tk.Label(form, text="相邻两个错开", font=UI_FONT, bg=COLOR_CARD,
                 fg=COLOR_TEXT).grid(row=1, column=0, sticky="w", pady=4)
        self.entry_stagger = tk.Entry(form, font=UI_FONT, width=6,
                                      bg="#ffffff", fg=COLOR_TEXT, relief="flat",
                                      highlightthickness=1,
                                      highlightbackground=COLOR_BORDER)
        self.entry_stagger.insert(0, str(self.s.get("stagger_seconds", 2.0)))
        self.entry_stagger.grid(row=1, column=1, sticky="w", padx=(8, 4), pady=4)
        tk.Label(form, text="秒启动（实测一个浏览器启动约 1 秒）", font=UI_FONT_SMALL,
                 bg=COLOR_CARD, fg=COLOR_HINT).grid(row=1, column=2, sticky="w",
                                                    padx=(4, 0), pady=4)

        self.preview = tk.Label(self.win, text="", font=UI_FONT_SMALL, bg=COLOR_BG,
                                fg=COLOR_HINT, anchor="w", justify="left",
                                wraplength=610)
        self.preview.pack(fill="x", padx=18, pady=(12, 0))

        row = tk.Frame(self.win, bg=COLOR_BG)
        row.pack(fill="x", padx=16, pady=(14, 14), side="bottom")
        self._btn(row, "保存", self.save, primary=True).pack(side="right")
        self._btn(row, "取消", self.win.destroy, ghost=True).pack(
            side="right", padx=(0, 8))
        self._btn(row, "恢复默认", self.reset_defaults, ghost=True).pack(side="left")
        self._btn(row, "现在能开几个？", self.probe, ghost=True).pack(
            side="left", padx=(8, 0))

        self._update_preview()
        fit_window(self.win, min_w=680, min_h=520)

    def _check(self, parent, var, text, hint):
        box = tk.Frame(parent, bg=COLOR_CARD)
        box.pack(fill="x", padx=14, pady=(12, 0))
        cb = tk.Checkbutton(box, text=text, variable=var, font=UI_FONT_BOLD,
                            bg=COLOR_CARD, fg=COLOR_TEXT, activebackground=COLOR_CARD,
                            activeforeground=COLOR_TEXT, selectcolor="#ffffff",
                            anchor="w", justify="left", cursor="hand2",
                            highlightthickness=0, bd=0)
        cb.pack(anchor="w")
        cb.configure(command=self._update_preview)
        tk.Label(box, text=hint, font=UI_FONT_SMALL, bg=COLOR_CARD, fg=COLOR_HINT,
                 anchor="w", justify="left", wraplength=560).pack(anchor="w",
                                                                  padx=(22, 0))


    def _collect(self):
        """把界面上的值收成一份设置。填错的地方返回 (None, 提示)。"""
        def as_int(raw):
            raw = (raw or "").strip()
            if not raw:
                return 0
            return int(float(raw))

        def as_float(raw):
            raw = (raw or "").strip()
            return 0.0 if not raw else float(raw)

        try:
            cap = as_int(self.entry_cap.get())
        except ValueError:
            return None, "「同时最多开」得是数字（留空 = 自动）。"
        try:
            stagger = as_float(self.entry_stagger.get())
        except ValueError:
            return None, "「错开几秒」得是数字。"
        if cap < 0:
            return None, "「同时最多开」不能是负数。"
        if cap > 64:
            return None, "「同时最多开」最多 64 个 —— 再多就别指望稳定了。"
        if stagger < 0:
            return None, "「错开几秒」不能是负数。"
        if stagger > 60:
            return None, "「错开几秒」最多 60 秒。"
        return {
            "enabled": bool(self.var_enabled.get()),
            "max_at_once": cap,
            "stagger_seconds": stagger,
            "light_flags": bool(self.var_light.get()),
            "keep_awake": bool(self.var_awake.get()),
        }, ""

    def _update_preview(self):
        try:
            data, err = self._collect()
            if data is None:
                self.preview.configure(text=err, fg=COLOR_WARN)
                return
            text = run_settings.describe(data)
            if data["enabled"] and resource_guard is not None and not data["max_at_once"]:
                try:
                    n = len([a for a in self.panel._accounts()
                             if a.get("enabled", True)])
                    allow, reason = resource_guard.preflight(max(1, n))
                    text += "\n" + reason
                except Exception:
                    pass
            if data["enabled"] and data["light_flags"]:
                text += ("\n（轻量化参数：实测省磁盘和后台流量，内存几乎不省）")
            self.preview.configure(text=text, fg=COLOR_HINT)
        except Exception:
            pass

    def probe(self):
        if resource_guard is None:
            self.preview.configure(text="找不到 resource_guard.py，算不了。", fg=COLOR_WARN)
            return
        try:
            n = max(1, len([a for a in self.panel._accounts() if a.get("enabled", True)]))
            allow, reason = resource_guard.preflight(n)
            self.preview.configure(
                text="{} 个启用账号 → 这次会同时开 {} 个。\n{}".format(n, allow, reason),
                fg=COLOR_HINT)
        except Exception:
            self.preview.configure(text="算的时候出错了，见向导日志。", fg=COLOR_WARN)

    def reset_defaults(self):
        d = run_settings.DEFAULTS
        self.var_enabled.set(bool(d["enabled"]))
        self.var_light.set(bool(d["light_flags"]))
        self.var_awake.set(bool(d["keep_awake"]))
        self.entry_cap.delete(0, "end")
        if d["max_at_once"]:
            self.entry_cap.insert(0, str(d["max_at_once"]))
        self.entry_stagger.delete(0, "end")
        self.entry_stagger.insert(0, str(d["stagger_seconds"]))
        self._update_preview()

    def save(self):
        data, err = self._collect()
        if data is None:
            messagebox.showwarning("填得不对", err)
            return
        if not run_settings.save(data):
            messagebox.showerror("存不了", "写 settings 文件失败，见向导日志。")
            return
        log_line("多开设置已保存：" + run_settings.describe(data))
        try:
            self.panel._refresh_queue_label()
            self.panel._say(run_settings.describe(data))
        except Exception:
            pass
        self.win.destroy()


# 打包版最容易漏掉的就是这些「裸模块名」：代码靠运行时改 sys.path 才 import 到它们，PyInstaller 静态分析看不见，
# 得在 spec 里手工列全（见 packaging/damai_app.spec）。漏一个的症状特别难查：程序能开、界面正常，点到「全部启动」
# 才 ImportError，所以这里顺手都 import 一遍，让问题在诊断这一步就暴露。改 spec 的 HIDDEN 时记得两边一起改。
_BUNDLE_MODULES = (
    "app_paths", "accounts", "cred_store", "task_manager",
    "run_settings", "resource_guard", "chrome_flags", "chromedriver_locator",
    "env_check", "version", "account_runner", "notify",
    "notify_settings",
    "check_environment", "config", "concert", "quick_diagnosis",
)


def _bundle_selfcheck():
    """把随包模块逐个 import 一遍，返回没 import 成功的那些。"""
    print()
    print("随包模块自检：")
    bad = []
    for name in _BUNDLE_MODULES:
        try:
            __import__(name)
        except Exception as exc:
            bad.append(name)
            print("  [!!] {} —— {!r}".format(name, exc))
        else:
            print("  [OK] {}".format(name))
    return bad


def _run_as_runner(argv):
    """打包版专用：exe 被当成调度器子进程再跑一次时走这里。

    打包之后没有 python.exe、也没有 account_runner.py 这个文件，
    所以子进程只能由 exe 自己扮演。调用方（runner_command / task_manager）
    用 `--runner` 作为第一个参数，后面的参数原样转交给 account_runner。
    """
    # 顺手留一个排查入口：`大麦抢票.exe --runner --paths`，打印「程序目录 / 资源目录 / 数据目录」到底在哪。
    # 打包版最容易出的问题就是数据落错地方（落进临时解压目录 = 每次打开账号全空），有了
    # 这条命令客户截个图就能定位。另有 `--runner --env` 做环境检测（输出纯文本报告，见下）。
    if argv[:1] == ["--paths"]:
        import app_paths
        print(app_paths.describe())
        try:
            # account_runner 的模块级代码会把 web/ 和 web/damai 放进 sys.path，
            # 得先过它一遍，下面那些裸模块名才 import 得到。
            import account_runner                                  # noqa: F401
        except Exception:
            print()
            print("随包模块自检：连 account_runner 都 import 不了，"
                  "这个包是坏的 ——")
            traceback.print_exc()
            return 1
        return 1 if _bundle_selfcheck() else 0

    # 环境检测的命令行版：`大麦抢票.exe --runner --env`。界面上那个「环境检测」按钮不是万能的，窗口
    # 根本起不来的机器（比如缺 DLL、Chromium 里的 Python 起不来）就点不到它。这条命令和按钮走同一份
    # env_check，输出纯文本报告，客户可以整段复制发过来、不用截图。
    if argv[:1] == ["--env"]:
        if env_check is None:
            print("找不到 env_check.py，这个包不完整。")
            return 1
        results = env_check.run()
        print(env_check.describe(results))
        return 0 if env_check.worst(results) != env_check.LEVEL_FAIL else 2

    import account_runner
    return account_runner.main(argv)


def _hide_console():
    """打包版：把双击 exe 时闪出来的那个控制台窗口藏起来。

    只对「向导角色」做。子进程角色（`--runner`）不藏，正常起抢票任务
      时是不带控制台的（`spawn_runner` 用 CREATE_NO_WINDOW，所以那种情况下
      这里本来也没窗口可藏）；但谁要是在 cmd 里手动跑一次
      `大麦抢票.exe --runner run <账号>`，那扇窗口就是唯一能看输出的地方，
      藏掉它等于把人眼睛蒙上。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)          # SW_HIDE
    except Exception:
        pass


def main():
    # 角色分发必须放在建窗口之前，子进程起来时不该闪一个向导窗口。
    if len(sys.argv) > 1 and sys.argv[1] == "--runner":
        sys.exit(_run_as_runner(sys.argv[2:]))

    _hide_console()
    root = tk.Tk()
    Wizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
