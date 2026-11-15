# -*- mode: python ; coding: utf-8 -*-
"""主程序打包配置（Windows / PyInstaller）。

跑法（在项目根目录）：
    .build-venv/Scripts/pyinstaller packaging/damai_app.spec --noconfirm

产物是 `dist/大麦抢票/` **一整个文件夹** —— 那个文件夹才是要发给客户的东西，
单拎 exe 出来是跑不起来的。

## 两个关键选择

**console=True（保留控制台子系统）**
看起来反直觉：GUI 程序为什么要留控制台？因为这个 exe 是**一份代码、两种用法**：
双击出来的向导只是其中一个角色，它同时还是个命令行工具（`--runner` 跑一个
账号、`--env` 打环境检测报告、`--version`）。那些角色靠 `print` 说话，
需要真实存在的 stdout —— 打成窗口子系统（`console=False`）之后 stdout 没有
去处，而 `account_runner` / `concert` 里成片的 `print()` 会当场抛异常。

★ 起抢票任务时**不会**弹窗口：两条路都用 `CREATE_NO_WINDOW`，输出重定向进
runs/ 下的日志文件（见 `damai_wizard.spawn_runner` 与 `task_manager.start`）。
代价只是双击时闪一个黑窗口 —— 向导角色在 `main()` 里会立刻把它藏起来
（见 `damai_wizard._hide_console`）。

**upx=False**
UPX 压缩壳是杀毒误报的重灾区，尤其对打包的 Python 程序。宁可大一点。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
WEB = os.path.join(ROOT, "web")
DAMAI = os.path.join(WEB, "damai")

# ★ 下面这些都是「裸模块名」——代码是靠运行时 `sys.path.insert` 才 import 到它们的，
#   PyInstaller 的静态分析看不见。必须手工列全：漏一个，打包后就是 ImportError，
#   而且只有真跑到那一步才会炸（比如点了「全部启动」才发现起不来）。
HIDDEN = [
    # web/ 下的同级模块
    "app_paths",
    "accounts",
    "cred_store",
    "task_manager",
    "run_settings",
    "resource_guard",
    "chrome_flags",
    "chromedriver_locator",
    "env_check",               # 环境检测（顶部那个按钮）。纯标准库
    "version",                 # 版本号（标题栏 + 检测报告抬头）。零依赖
    "changelog",               # 更新日志（版本号旁边那个「?」）。零依赖，只读 txt
    "account_runner",          # `--runner` 角色分发时才 import，静态分析抓不到
    # 短信通知（旁路）。纯标准库，**不需要**任何阿里云 SDK ——
    # 签名是自己按 POP v1.0 实现的，就为了不让 exe 多一个第三方依赖。
    "notify",
    "notify_settings",
    # 实时状态链路（2026-10-09）：面板显示「刷新中 / 轮询中 / 待提交订单 /
    # 需要验证码 / 登录输错」靠它。两个模块都在 try 里 import ——
    # 静态分析大概率能抓到，但这条线**漏一个就是静默失灵**（不报错、
    # 面板永远显示「未启动」），手工列上上一道保险。verify.py 有哨兵看着。
    "live_status",
    "window_wall",             # 「任务画面」后端（同上，漏了画面墙打不开）
    # damai/ 下的模块（代码里写的是 `from concert import Concert` 这种）
    "check_environment",
    "config",
    "concert",
    "quick_diagnosis",
]

# ★ selenium 必须**整包收全**，不能指望静态分析。
#   代码里写的是 `webdriver.ChromeOptions()`，而 selenium 4 的
#   `selenium/webdriver/__init__.py` 是**懒加载**的：`__getattr__` 到运行时
#   才去 import `selenium.webdriver.chrome.options`。PyInstaller 静态分析
#   看不见这条路，打出来的包里就只有 `chrome/__init__.pyi`（类型存根），
#   真正的模块没有 —— 结果是源码形态一切正常，客户那台机器一点「开始抢票」
#   就 `ModuleNotFoundError: No module named 'selenium.webdriver.chrome.options'`。
#   这一条踩过：只有 exe 形态能碰到，而且报错点离病根很远。
try:
    from PyInstaller.utils.hooks import collect_submodules
    HIDDEN += collect_submodules("selenium")
except Exception:                              # pragma: no cover
    HIDDEN += [
        "selenium.webdriver.chrome.options",
        "selenium.webdriver.chrome.service",
        "selenium.webdriver.common.by",
        "selenium.webdriver.common.keys",
        "selenium.webdriver.support.ui",
        "selenium.webdriver.support.expected_conditions",
        "selenium.webdriver.support.wait",
    ]

a = Analysis(
    [os.path.join(WEB, "damai_wizard.py")],
    pathex=[WEB, DAMAI],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    # 这些跟抢票无关，排掉能小一圈
    excludes=[
        "pytest", "numpy", "pandas", "matplotlib", "PIL",
        "PyInstaller", "setuptools", "pip", "pydoc", "doctest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="大麦抢票",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="大麦抢票",
)
