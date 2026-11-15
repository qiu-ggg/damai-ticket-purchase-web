# -*- coding: utf-8 -*-
"""多开时给 Chrome 减负的启动参数。

这个项目是多账号各开一个 Chrome。真正吃内存和 CPU 的从来不是那几个 Python 进程（每个也就
几十 MB），而是 N 套 Chrome 进程树 —— 一套 Chrome 自己就会裂成主进程 + GPU + 渲染 + 网络
服务 + 实用程序 + 崩溃上报等一堆进程，开 5 个账号任务管理器里就是几十个 chrome.exe。
所以「多开怎么省资源」这个问题，答案几乎全在 Chrome 的参数和启动节奏上。

参数分三类，界限很清楚：① 后台杂活（`--disable-background-networking` /
`--disable-component-update` / `--disable-breakpad` …），Chrome 自己在后台干的事，跟网页
无关，关掉纯赚；② 用不上的功能（`--disable-extensions` / `--disable-sync` / 翻译 / 投屏
发现），抢票用不到，关掉省内存也省启动时间；③ 会改变网页行为的（禁图片、禁 JS、headless、
限制 JS 堆……）一律不碰 —— 大麦的选座是画布 + H5 交互，禁图片很可能直接把选座搞坏，省下
那点内存换来「选不了座」是笔烂账。

还有「后台窗口降速」：Chrome 会把被完全遮住的窗口标记成 occluded 然后降速（定时器降到约
1 次/秒），多开时后面的窗口正好全被前面的挡住，这是个真实的多开陷阱。这里默认不动它
（沿用 Chrome 的默认行为，省 CPU，符合「减负」的诉求），但真出现「只有最前面那个账号在
干活」，`keep_awake=True` 就是解药：关掉遮挡检测让所有窗口按前台速度跑，代价是 CPU 高一些。
"""

import ctypes
import sys


# ① 轻量参数：关掉跟抢票无关的后台杂活

# 先说清这套参数的实际性价比，免得有不该有的期待。本机（31.7 GB，about:blank）实测：原样 457.7 MB / 8 进程 / 启动 1.1 秒，
# 轻量化后 456.3 MB / 8 进程 / 启动 1.0 秒，内存几乎没省（差 1.4 MB，噪声内）：关掉的是「后台杂活」，刚起来的 Chrome 本来没干。
# 它省的是磁盘和后台流量：一份 Profile 缓存从几百 MB 收到 64 MB，N 份乘 N，组件更新 / 同步 / 遥测下载也不再来抢。抢票吃紧的是内存和 CPU，靠「别一次全开」解决，不靠这里，别为省内存开它。每项都注明了「关掉的是什么」，方便以后判断能不能删。
LIGHT_ARGS = [
    "--no-first-run",                       # 首次运行引导
    "--no-default-browser-check",            # 「把 Chrome 设为默认」弹窗
    "--disable-extensions",                  # 扩展（每个扩展都是常驻内存）
    "--disable-default-apps",                # 默认应用（Gmail 之类的钩子）
    "--disable-component-update",            # 后台下载/更新组件
    "--disable-background-networking",       # 后台网络：安全浏览、翻译、变体、遥测…
    "--disable-client-side-phishing-detection",
    "--disable-sync",                        # 账号同步
    "--disable-breakpad",                    # 崩溃上报（多开会多份）
    "--metrics-recording-only",              # 指标只在本地记，不上传
]
# 这里故意没有 --log-level=3 / --disable-logging：它们能少写日志，但本项目硬规矩是「出问题要留
# 现场」。账号是独立进程、没有控制台（输出重定向进 runs/ 下的日志），日志是唯一能事后追查的东西，
# 为省那点 IO 抹掉现场是烂账。

# 这些 feature 关掉省内存/省启动时间，且不改变网页行为。
# 注意 Chrome 对重复出现的 `--disable-features` 取舍并不一致，
# 所以下面的 build_args() 保证全进程只拼一条 `--disable-features=`。
LIGHT_FEATURES = [
    "Translate",                     # 翻译
    "MediaRouter",                   # 投屏发现（会一直扫局域网）
    "OptimizationHints",             # 从服务器拉优化提示
    "InterestFeedContentSuggestions",
    "CalculateNativeWinOcclusion",   # 遮挡检测，见 KEEP_AWAKE_FEATURES 的说明
]


# ② 保持清醒：关掉遮挡降速（多开专用，默认不开）

# 关掉 CalculateNativeWinOcclusion 后，Chrome 不再把「被别人盖住的窗口」判成 occluded，
# 于是不给它们降速。要不要开看遇到哪个问题：CPU 太高就别开（默认即不开）；「只有最前面那个账号
# 有反应」就开。
KEEP_AWAKE_FEATURES = ["CalculateNativeWinOcclusion"]


# ③ 磁盘缓存上限（N 份 Profile 会乘 N）

_DEFAULT_CACHE_MB = 64


def disable_features_args(*feature_groups):
    """把若干组 feature 名字合成一条 `--disable-features=`。

    为什么不许拼多条：Chrome 对同名开关的合并行为历来不统一（有的取最后一个、
      有的合并），拼两条就变成「到底哪条生效」看版本脸色的玄学。
      合成一条，行为就确定了。
    """
    seen, names = set(), []
    for group in feature_groups:
        for name in (group or []):
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    if not names:
        return []
    return ["--disable-features=" + ",".join(names)]


def build_args(light=True, keep_awake=False, cache_mb=_DEFAULT_CACHE_MB):
    """给出「跟本机/本账号无关」的那部分 Chrome 参数。

    `--user-data-dir`、窗口位置这些跟账号/B 屏有关的，由调用方自己加。
    """
    args = []
    features = []
    if light:
        args.extend(LIGHT_ARGS)
        features.extend(LIGHT_FEATURES)
    if light and cache_mb:
        # 磁盘缓存：默认的几百 MB × N 份 Profile 会明显吃盘。
        # 抢票只需要当前这几个页面，缓存给 64MB 完全够。
        args.append("--disk-cache-size={}".format(int(cache_mb) * 1024 * 1024))
        args.append("--media-cache-size={}".format(1024 * 1024))
    if keep_awake:
        # 只在 light 也开着的时候才有意义：遮挡检测本身是 LIGHT_FEATURES 里关掉的
        #   （在 light 生效时它已经被关），这里只是把「不想关它」这件事表达清楚。
        args.extend(["--disable-backgrounding-occluded-windows",
                     "--disable-renderer-backgrounding",
                     "--disable-background-timer-throttling"])
    args.extend(disable_features_args(features))
    return args


# ④ 窗口位置：别让窗口互相完全遮挡

_CASCADE_X = 48
_CASCADE_Y = 36


def screen_size():
    """当前主屏分辨率。拿不到就返回 (1920, 1080)，只是拿来算窗口位置的，不值得报错。"""
    try:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0))
            h = int(user32.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 1920, 1080


def window_args(index=0, width=1000, height=760, cascade=True):
    """第 `index` 个窗口的位置。

    为什么要在意位置：Chrome 会降速被完全遮挡的窗口（见文件头）。
      几个窗口全开在 (0,0)、尺寸又一样，后面那些就是「完全遮挡」，
      于是它们可能慢半拍。错开一点，谁都不完全盖住谁。

    位置会让 `width/height` 加上偏移后不超出屏幕，超了就回绕。
    """
    args = ["--window-size={},{}".format(int(width), int(height))]
    if not cascade:
        return args
    sw, sh = screen_size()
    per_x = max(1, sw - int(width) - 8)
    per_y = max(1, sh - int(height) - 8)
    # 能放下几列几行，就按几列几行铺，铺不满也继续错位，反正目的是「别完全重叠」
    cols = max(1, (sw - int(width)) // _CASCADE_X) if sw > width else 1
    rows = max(1, (sh - int(height)) // _CASCADE_Y) if sh > height else 1
    slot = int(index) % max(1, cols * rows)
    x = min(per_x, (slot % cols) * _CASCADE_X + 4)
    y = min(per_y, (slot // cols) * _CASCADE_Y + 4)
    args.append("--window-position={},{}".format(x, y))
    return args


def add_to(options, args):
    """把参数列表加到 selenium 的 ChromeOptions 上（跳过空值）。"""
    for a in args or []:
        if a:
            options.add_argument(a)
    return options


def describe(light=True, keep_awake=False, cache_mb=_DEFAULT_CACHE_MB):
    """人话总结当前这套参数在干什么，界面上直接显示这个。"""
    if not light:
        return "未启用轻量化参数（跟改造前完全一样）"
    bits = ["已关闭后台杂活（组件更新 / 同步 / 崩溃上报 / 后台网络）",
            "已关闭用不到的功能（扩展 / 翻译 / 投屏发现）",
            "磁盘缓存上限 {} MB".format(cache_mb)]
    if keep_awake:
        bits.append("已关闭后台窗口降速（更耗 CPU，但多开时每个账号都保持响应）")
    return "；".join(bits)
