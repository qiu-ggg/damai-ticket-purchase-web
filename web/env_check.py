# -*- coding: utf-8 -*-
"""环境检测：这台机器到底能不能跑起来。

客户/用户遇到「点了没反应」时，最可能的原因就那么几个，而且都不在程序里：没装 Chrome
（或者装的是别的浏览器）、Chrome 和 ChromeDriver 版本差了好几个大版本、抢票用的解释器里
没有 selenium（源码形态特有：向导自己跑在一个没有 selenium 的解释器上，真正抢票的是另一个
隔离环境）、网络到不了大麦（代理、防火墙、DNS）、程序所在目录写不进去（放进了
`Program Files`）。这些一条条去问、去截图，来回好几轮，所以做一个按钮一次全查完，缺什么就
直接给下载链接，不让人再去搜。

规矩：只看，不动手 —— 不自动下载驱动、不自动装东西、不改任何配置，检测本身不该产生副作用；
从不抛异常 —— 任何一项查炸了就记成「查不了」，其余照常往下走；纯标准库，不 import
selenium / tkinter —— 这个模块要能在「什么都没有」的机器上跑出结果，它才有意义。
"""

import os
import socket
import subprocess
import sys

# 级别：ok 正常 / warn 能用但建议弄一下 / fail 这样是跑不起来的
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_FAIL = "fail"

# 下载地址。`key` 和检查项的 key 对应。
DOWNLOADS = {
    "chrome": ("https://www.google.cn/chrome/", "下载 Chrome"),
    "chrome_alt": ("https://www.google.com/chrome/", "打不开就用这个"),
    "chromedriver": ("https://googlechromelabs.github.io/chrome-for-testing/",
                     "下载 ChromeDriver"),
    "python": ("https://www.python.org/downloads/windows/", "下载 Python"),
}

# 抢票要连的域名（只连 TCP，不发请求、不登录）
PROBE_HOSTS = ("www.damai.cn", "passport.damai.cn")
PROBE_TIMEOUT = 4


def _item(key, name, level, detail, fix="", url=None, url_text=None):
    return {"key": key, "name": name, "level": level, "detail": detail,
            "fix": fix, "url": url, "url_text": url_text or "打开"}


def _frozen():
    return bool(getattr(sys, "frozen", False))



def check_system():
    if os.name == "nt":
        return _item("system", "操作系统", LEVEL_OK, "Windows")
    return _item("system", "操作系统", LEVEL_WARN,
                 "{}（这套程序主要在 Windows 上用）".format(sys.platform),
                 fix="Windows 以外也能跑，但 ChromeDriver 的自动获取和「任务画面」"
                     "只有 Windows 上验证过，可能需要手动指定驱动。")


def check_python():
    v = sys.version_info
    text = "{}.{}.{}".format(v.major, v.minor, v.micro)
    if _frozen():
        return _item("python", "Python 运行环境", LEVEL_OK,
                     "打包版自带（{}），不用另外装".format(text))
    if (v.major, v.minor) < (3, 8):
        return _item("python", "Python 运行环境", LEVEL_FAIL,
                     "{}（太旧了）".format(text),
                     fix="装 Python 3.8 或更高版本，安装时勾上「Add to PATH」。",
                     url=DOWNLOADS["python"][0], url_text=DOWNLOADS["python"][1])
    return _item("python", "Python 运行环境", LEVEL_OK, text)


def check_runner_python():
    """抢票用的那个解释器，源码形态下它和向导不是同一个。

    这是最容易误判的一项：向导自己跑在一个没装 selenium 的解释器上是正常的
      （它只要 tkinter），真正抢票的是 task_manager 里那个隔离环境。
      所以这里查的是那个解释器，不是当前进程。
    """
    if _frozen():
        return _item("runner", "抢票运行环境", LEVEL_OK,
                     "打包版就是本程序自己，selenium 已随包")

    try:
        import task_manager
        py = task_manager.DEFAULT_PYTHON
    except Exception as exc:
        return _item("runner", "抢票运行环境", LEVEL_WARN,
                     "查不了：{}".format(exc))

    if not py or not os.path.isfile(py):
        return _item("runner", "抢票运行环境", LEVEL_FAIL,
                     "找不到抢票用的解释器：{}".format(py or "（没配）"),
                     fix="它一般是项目里的虚拟环境。装好 selenium 之后，"
                         "用环境变量 DAMAI_PYTHON 指向它的 python.exe 也行。")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([py, "-c", "import selenium;print(selenium.__version__)"],
                           capture_output=True, timeout=20, creationflags=flags)
    except Exception as exc:
        return _item("runner", "抢票运行环境", LEVEL_WARN,
                     "跑不起来 {}：{}".format(py, exc))

    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if r.returncode == 0 and out:
        return _item("runner", "抢票运行环境", LEVEL_OK,
                     "selenium {}（{}）".format(out, py))
    return _item("runner", "抢票运行环境", LEVEL_FAIL,
                 "这个解释器里 import selenium 失败了：{}".format(py),
                 fix="在它里面装上 selenium：\"{}\" -m pip install selenium".format(py))


def check_chrome():
    try:
        import chromedriver_locator as loc
        major = loc.chrome_major_version()
    except Exception as exc:
        return _item("chrome", "Chrome 浏览器", LEVEL_WARN,
                     "查不了：{}".format(exc))
    if not major:
        return _item("chrome", "Chrome 浏览器", LEVEL_FAIL,
                     "没找到 Chrome",
                     fix="这套程序靠 Chrome 干活，没它跑不起来。装完重开一次程序。",
                     url=DOWNLOADS["chrome"][0], url_text=DOWNLOADS["chrome"][1])
    return _item("chrome", "Chrome 浏览器", LEVEL_OK, "主版本 {}".format(major))


def check_chromedriver():
    """只看不动手，走查的路径和运行时一致，但不触发下载。"""
    try:
        import chromedriver_locator as loc
    except Exception as exc:
        return _item("chromedriver", "ChromeDriver", LEVEL_WARN,
                     "查不了：{}".format(exc))

    major = None
    try:
        major = loc.chrome_major_version()
    except Exception:
        pass

    found = None
    for finder in ("find_chromedriver_in_env", "find_bundled_chromedriver"):
        try:
            p = getattr(loc, finder)()
        except Exception:
            p = None
        if p:
            found = p
            break
    if not found:
        try:
            found = loc.find_cached_chromedriver(major)
        except Exception:
            found = None

    if not found:
        return _item("chromedriver", "ChromeDriver", LEVEL_WARN,
                     "本机还没有（第一次跑的时候程序会自己下载一份）",
                     fix="网络不通就只能手动下：挑和 Chrome 主版本 {} 一致的 "
                         "win64 版本，放进程序所在文件夹。".format(major or "?"),
                     url=DOWNLOADS["chromedriver"][0],
                     url_text=DOWNLOADS["chromedriver"][1])

    try:
        v = loc.chromedriver_major_version(found)
    except Exception:
        v = None
    if major and v and v != major:
        return _item("chromedriver", "ChromeDriver", LEVEL_FAIL,
                     "版本对不上：Chrome {} / 驱动 {}".format(major, v),
                     fix="把 Chrome 更新到最新（设置 → 关于 Chrome），"
                         "或者换一份和它主版本一致的驱动。",
                     url=DOWNLOADS["chromedriver"][0],
                     url_text=DOWNLOADS["chromedriver"][1])
    return _item("chromedriver", "ChromeDriver", LEVEL_OK,
                 "版本 {}（{}）".format(v or "?", found))


def check_network():
    bad = []
    for host in PROBE_HOSTS:
        try:
            s = socket.create_connection((host, 443), timeout=PROBE_TIMEOUT)
            s.close()
        except Exception as exc:
            bad.append("{}（{}）".format(host, exc))
    if bad:
        return _item("network", "网络", LEVEL_FAIL, "连不上：" + "；".join(bad),
                     fix="抢票全程要连大麦。先确认这台机器能打开 damai.cn；"
                         "如果浏览器能开但这里报连不上，多半是代理设置把程序挡了 —— "
                         "检查系统代理 / 防火墙，或者把代理软件先关掉试一次。")
    return _item("network", "网络", LEVEL_OK, "能连上大麦")


def check_selenium():
    """抢票用的 selenium 到底能不能用。

    为什么单列一条：打包时 selenium 是懒加载的，
      `webdriver.ChromeOptions()` 走 `selenium.webdriver.__getattr__`，
      到运行时才去 import `selenium.webdriver.chrome.options`，
      静态分析看不见这条路。漏收的话源码形态一切正常，客户那边
      点「开始抢票」才 `ModuleNotFoundError`（报错点离病根很远）。
      有这一项，环境检测就能在开抢之前把话说清楚。

    只构造一个 options 对象，不起浏览器、不联网、不改任何东西。
    """
    try:
        from selenium import webdriver
        webdriver.ChromeOptions()
    except Exception as exc:
        return _item("selenium", "抢票组件", LEVEL_FAIL,
                     "加载失败：{}".format(exc),
                     fix="这个包不完整（多半是打包时漏收了 selenium 的子模块）。"
                         "把这段原样发给我们，换一个完整的包。")
    try:
        import selenium
        ver = getattr(selenium, "__version__", "?")
    except Exception:                                 # pragma: no cover
        ver = "?"
    return _item("selenium", "抢票组件", LEVEL_OK, "selenium {} 可用".format(ver))


def check_data_dir():
    try:
        import app_paths
        d = app_paths.data_dir()
        src = app_paths.data_dir_source()
    except Exception as exc:
        return _item("datadir", "数据目录", LEVEL_WARN, "查不了：{}".format(exc))

    probe = os.path.join(d, ".envcheck_%d.tmp" % os.getpid())
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as exc:
        return _item("datadir", "数据目录", LEVEL_FAIL,
                     "写不进去：{}（{}）".format(d, exc),
                     fix="把整个程序文件夹挪到有写入权限的地方（比如桌面、D 盘），"
                         "别放在 C:\\Program Files 下面。")
    note = {"source": "源码形态，就在程序目录里",
            "exe": "exe 同级目录，整个文件夹拷走就能换机器",
            "appdata": "exe 同级写不进去，已退到 %APPDATA%"}.get(src, src)
    return _item("datadir", "数据目录", LEVEL_OK, "{} —— {}".format(d, note))


CHECKS = (check_system, check_python, check_runner_python, check_chrome,
          check_chromedriver, check_selenium, check_network, check_data_dir)



def run(only=None):
    """跑一遍全部检查。返回条目列表。从不抛异常。

    `only` 给出 key 列表时只跑那几项（自检脚本用，能跑得快一点）。
    """
    out = []
    for fn in CHECKS:
        key = fn.__name__.replace("check_", "")
        if only and key not in only:
            continue
        try:
            out.append(fn())
        except Exception as exc:                      # pragma: no cover
            out.append(_item(key, key, LEVEL_WARN,
                             "这一项查不了：{!r}".format(exc)))
    return out


def worst(results):
    """整体结论：fail / warn / ok。"""
    levels = [r.get("level") for r in results]
    if LEVEL_FAIL in levels:
        return LEVEL_FAIL
    if LEVEL_WARN in levels:
        return LEVEL_WARN
    return LEVEL_OK


def failures(results):
    """挡住「能用」的那些项，也就是要弹窗提醒的。"""
    return [r for r in results if r.get("level") == LEVEL_FAIL]


def download_items(results):
    """带下载链接的失败项。"""
    return [r for r in failures(results) if r.get("url")]


LEVEL_CN = {LEVEL_OK: "正常", LEVEL_WARN: "注意", LEVEL_FAIL: "缺东西"}
LEVEL_MARK = {LEVEL_OK: "[OK]", LEVEL_WARN: "[!]", LEVEL_FAIL: "[X]"}


def describe(results):
    """纯文本报告（命令行 / 日志用，也方便客户直接复制）。

    抬头带上程序版本：这份报告经常是被整段复制发过来的，
      没有版本号的话第一句就得回问「你装的是哪一版」。
    """
    head = "环境检测结果"
    try:
        import version as app_version
        head = "{} · {}".format(head, app_version.label())
    except Exception:
        pass                    # 版本模块没随包 → 老老实实只印标题，不影响检测
    lines = [head, "=" * 56]
    for r in results:
        lines.append("{} {}".format(LEVEL_MARK.get(r["level"], "[?]"), r["name"]))
        lines.append("     {}".format(r.get("detail") or ""))
        if r.get("fix"):
            lines.append("     怎么办：{}".format(r["fix"]))
        if r.get("url"):
            lines.append("     {}：{}".format(r.get("url_text") or "下载", r["url"]))
    lines.append("=" * 56)
    w = worst(results)
    if w == LEVEL_OK:
        lines.append("结论：环境没问题，可以开始。")
    else:
        bad = [r for r in results if r["level"] != LEVEL_OK]
        lines.append("结论：{} 项需要注意 —— {}".format(
            len(bad), "、".join(r["name"] for r in bad)))
    return "\n".join(lines)


def tip(results):
    """一句话结论，放在状态栏那种地方。"""
    w = worst(results)
    if w == LEVEL_OK:
        return "环境检测：没问题"
    bad = [r["name"] for r in results if r["level"] == LEVEL_FAIL]
    if bad:
        return "环境检测：缺少 {}".format("、".join(bad))
    bad = [r["name"] for r in results if r["level"] == LEVEL_WARN]
    return "环境检测：{} 项需要注意".format(len(bad))


if __name__ == "__main__":                            # pragma: no cover
    print(describe(run()))
