# -*- coding: utf-8 -*-
"""ChromeDriver 定位（纯标准库，不依赖 tkinter / selenium）

为什么需要这个模块：

原仓库的 damai/check_environment.py 里，get_chromedriver_path() 只在下面这些地方找驱动：
    CHROMEDRIVER_PATH / SELENIUM_DRIVER_PATH 环境变量 → PATH → 当前目录 → chromedriver-autoinstaller
它不会去扫 Selenium Manager 的缓存目录（~/.cache/selenium/...）。所以本机明明
已经有一个版本匹配的驱动，它也会报"未找到"，转而去 Google 下载，而那个源在国内基本不通。

这里把缓存目录扫描补上，并把找到的路径通过 CHROMEDRIVER_PATH 环境变量喂给它，
让它命中第一优先级、根本走不到下载那一步。

本模块被 damai_wizard.py（配置向导）和 account_runner.py（多账号调度）共用。
"""

import glob
import json
import os
import re
import subprocess

# Selenium Manager 的驱动缓存位置
CACHE_ROOTS = [
    os.path.join(os.path.expanduser("~"), ".cache", "selenium"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "selenium"),
]

VERSION_DIR_RE = re.compile(r"\d+(?:\.\d+){1,3}")



def chrome_major_version():
    """读本机 Chrome 主版本号。优先看安装目录下以版本号命名的子目录。"""
    app_dirs = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application"),
        "/Applications/Google Chrome.app/Contents/MacOS",
    ]

    best = None
    for app in app_dirs:
        if not app or not os.path.isdir(app):
            continue
        try:
            names = os.listdir(app)
        except OSError:
            continue
        for name in names:
            if not VERSION_DIR_RE.fullmatch(name):
                continue
            if not os.path.isdir(os.path.join(app, name)):
                continue
            try:
                key = tuple(int(x) for x in name.split("."))
            except ValueError:
                continue
            if best is None or key > best[0]:
                best = (key, name)
    if best:
        return best[1].split(".")[0]

    # 退路：读注册表（Windows）
    try:
        import winreg

        for hive, path in (
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        ):
            try:
                with winreg.OpenKey(hive, path) as k:
                    version, _ = winreg.QueryValueEx(k, "version")
                    if version:
                        return str(version).split(".")[0]
            except OSError:
                continue
    except Exception:
        pass
    return None



def find_cached_chromedriver(major):
    """在 Selenium Manager 缓存里找主版本号匹配的 chromedriver。"""
    if not major:
        return None
    for root in CACHE_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        for p in glob.glob(os.path.join(root, "**", "chromedriver*"), recursive=True):
            if not (os.path.isfile(p) and os.path.basename(p).startswith("chromedriver")):
                continue
            parent = os.path.basename(os.path.dirname(p))
            if parent.split(".")[0] == str(major):
                return p
    return None


def _bundled_dirs():
    """随包分发的 ChromeDriver 可能放在哪几个目录。

    打包版把 chromedriver.exe 放在 exe 同级目录，客户看得见、也好自己替换，
    比藏在解压目录里强。同时也把资源目录列上（万一以后打进去）。
    源码形态下这几个目录是同一个，等于跟改造前一样。
    """
    dirs = []
    try:
        import app_paths
        dirs.append(app_paths.app_dir())
        dirs.append(app_paths.resource_dir())
    except Exception:
        pass
    dirs.append(os.path.dirname(os.path.abspath(__file__)))

    seen, out = set(), []
    for d in dirs:
        try:
            d = os.path.abspath(d)
        except Exception:
            continue
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_chromedriver_in_env():
    """环境变量 / PATH / 当前目录里已经配好的驱动。"""
    import shutil as _sh

    for p in (
        os.environ.get("CHROMEDRIVER_PATH"),
        os.environ.get("SELENIUM_DRIVER_PATH"),
        _sh.which("chromedriver.exe"),
        _sh.which("chromedriver"),
        os.path.join(os.getcwd(), "chromedriver.exe"),
        os.path.join(os.getcwd(), "chromedriver"),
    ):
        if p and os.path.exists(p):
            return p
    return None


DRIVER_VERSION_RE = re.compile(r"ChromeDriver\s+(\d+)")


def chromedriver_major_version(driver_path):
    """读一个 chromedriver 的主版本号；读不出来返回 None。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([driver_path, "--version"], capture_output=True,
                           text=True, errors="replace", timeout=15,
                           creationflags=flags)
    except Exception:
        return None
    m = DRIVER_VERSION_RE.search((r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m else None


def find_bundled_chromedriver():
    """随包分发（exe 同级目录）的驱动。

    只有主版本对得上才采用。带一份版本不对的驱动比不带更糟，
      它会挡住后面「让 Selenium Manager 去下匹配版本」那条路，
      于是本来能自愈的机器反而起不来。所以这里逐个验版本，不匹配就跳过。
    """
    major = chrome_major_version()
    for d in _bundled_dirs():
        for name in ("chromedriver.exe", "chromedriver"):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            v = chromedriver_major_version(p)
            if not v:
                continue                     # 读不出驱动版本，多半文件本身有问题
            if major and v != major:
                continue                     # 跟本机 Chrome 对不上，交给后面的分支
            return p
    return None


# 用 Selenium Manager 下载

def find_selenium_manager(venv_python=None):
    """定位 selenium-manager 可执行文件。

    先看当前解释器能不能 import selenium（account_runner 走这条），
    再按传入的解释器路径去猜 site-packages（配置向导跑在系统 Python 上，走这条）。
    """
    candidates = []

    try:
        import selenium
        base = os.path.dirname(os.path.abspath(selenium.__file__))
        for platform_dir in ("windows", "linux", "macos"):
            for name in ("selenium-manager.exe", "selenium-manager"):
                candidates.append(os.path.join(base, "webdriver", "common", platform_dir, name))
    except Exception:
        pass

    if venv_python:
        venv_root = os.path.dirname(os.path.dirname(venv_python))
        for platform_dir in ("windows", "linux", "macos"):
            for name in ("selenium-manager.exe", "selenium-manager"):
                candidates.append(os.path.join(
                    venv_root, "Lib", "site-packages", "selenium",
                    "webdriver", "common", platform_dir, name))
                candidates.append(os.path.join(
                    venv_root, "lib", "python3", "site-packages", "selenium",
                    "webdriver", "common", platform_dir, name))

    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def download_chromedriver(major, venv_python=None, timeout=180):
    """让 Selenium Manager 下载匹配的驱动，返回路径；失败返回 None。"""
    mgr = find_selenium_manager(venv_python)
    if not mgr:
        return None
    args = [mgr, "--browser", "chrome", "--output", "json"]
    if major:
        args += ["--browser-version", str(major)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)
        data = json.loads(r.stdout or "{}")
        path = (data.get("result") or {}).get("driver_path")
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return None



def resolve_chromedriver(venv_python=None):
    """返回一个本机可用的 chromedriver 绝对路径；找不到返回 None。

    查找顺序：环境变量/PATH → Selenium Manager 缓存 → 随包目录（打包版）
              → 用 Selenium Manager 下载。
    """
    already = find_chromedriver_in_env()
    if already:
        return already

    major = chrome_major_version()
    path = find_cached_chromedriver(major)
    if path:
        return path

    # 打包版：驱动就放在 exe 旁边（离线也能用）。放在缓存之后，因为缓存那份是程序自己下过、确认能用的。
    path = find_bundled_chromedriver()
    if path:
        return path

    path = download_chromedriver(major, venv_python)
    if path:
        return path

    # 再全盘扫一遍缓存（下载后可能落在别处）
    return find_cached_chromedriver(major)


def ensure_chromedriver_env(venv_python=None):
    """确保 CHROMEDRIVER_PATH 已设置，并返回该路径。

    原仓库的 get_chromedriver_path() 会优先读这个环境变量，设好它就不会去
    Google 下载驱动了。
    """
    cur = os.environ.get("CHROMEDRIVER_PATH")
    if cur and os.path.exists(cur):
        return cur

    path = resolve_chromedriver(venv_python)
    if path:
        os.environ["CHROMEDRIVER_PATH"] = path
    return path


# 回环地址的各种写法。`localhost` 和 `127.0.0.1` 都得有，urllib3 的 bypass
# 判断是按 host 字符串匹配的，不会替你等价替换；`[::1]` 是 IPv6 带方括号的写法。
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


def relax_local_proxy():
    """让本地地址绕过代理。

    本机上如果装了代理工具，HTTP_PROXY 会连发往本地 ChromeDriver 的请求一起劫走，
    selenium 的表现是一律报 "Message: unhandled request"（连读当前网址都失败）。

    典型症状：driver 能创建成功，但一调 driver.xxx 就炸，炸的是「去敲它的那一下」，
    ChromeDriver 本身没问题。很容易被误判成「页面加载失败」或「驱动版本不对」。

    大小写两种写法都要设：urllib3 认 `no_proxy`，requests 那一路认 `NO_PROXY`。

    必须在创建 driver 之前调用。返回本次是否真的改动过（便于自检断言）。
    """
    changed = False
    for key in ("NO_PROXY", "no_proxy"):
        parts = [p.strip() for p in os.environ.get(key, "").split(",") if p.strip()]
        miss = [h for h in LOOPBACK_HOSTS if h not in parts]
        if not miss:
            continue
        parts.extend(miss)
        os.environ[key] = ",".join(parts)
        changed = True
    return changed
