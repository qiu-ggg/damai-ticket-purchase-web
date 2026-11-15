# -*- coding: utf-8 -*-
"""多开设置：一次开几个、错开多久、浏览器参数怎么给。

为什么必须落盘、不能只在内存里：真正加参数的是子进程 —— 调度器是 `Popen` 起来的一个全新
Python 解释器，它自己 import 自己那份模块，父进程在内存里改的变量它一个都看不见（这个坑
这个项目已经踩过 7 次，见 `tools/_probe_env.py` 开头那张表）。所以设置存在
`web/wizard_settings.json` 里，两边都能读到。新文件，不碰 `accounts.json`（那是账号，
一个人一个样），也不碰 `damai/config.json`（那是老的单账号路径用的）。

读设置永远不能把程序弄崩：文件被手改坏、字段类型不对、值是 `"abc"`，这些都不该让「开始
抢票」失败。所以 `load()` 一律返回能用的值：坏字段退回默认，越界的夹到边界内。`clean()`
负责这件事，它也是测试的入口。
"""

import json
import os
import tempfile

# 路径口径见 accounts.py 里的同款说明（设置文件跟着数据目录走）。
try:
    import app_paths
    HERE = app_paths.data_dir()
except Exception:
    HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "wizard_settings.json")

# 默认值：默认就是「按实测推荐的那套」

DEFAULTS = {
    # 总开关。关掉 = 完全回到改造前（每个账号立刻起一个浏览器，不限数量）
    "enabled": True,

    # 一次最多几个。0 = 按本机可用内存自动算（见 resource_guard.max_windows）
    "max_at_once": 0,

    # 相邻两个账号之间错开多少秒。实测一个浏览器启动约 1 秒，取 2 秒是为了让它起来之后再起下一个，别互相抢 CPU。
    "stagger_seconds": 2.0,

    # 用轻量化参数启动浏览器。如实说：实测内存上几乎没差别（456 vs 458 MB），省的是磁盘缓存和后台流量。
    "light_flags": True,

    # 关掉 Chrome 的后台窗口降速（被遮住的窗口保持全速）。代价是 CPU 高一些；只在出现「只有最前面的账号有反应」时才需要开。
    "keep_awake": False,
}

_LIMITS = {
    "max_at_once": (0, 64),
    "stagger_seconds": (0.0, 60.0),
}


def clean(raw):
    """把任意输入收拾成一份一定可用的设置。

    每个字段都单独兜底，而不是「有一个坏就整个回默认」：用户改了错别字，
      不该把他其它几项设置也一起清掉。
    """
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)

    for key in ("enabled", "light_flags", "keep_awake"):
        if key in raw:
            val = raw[key]
            if isinstance(val, bool):
                out[key] = val
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                out[key] = bool(val)
            elif isinstance(val, str):
                out[key] = val.strip().lower() in ("1", "true", "yes", "on", "开", "是")

    for key, (lo, hi) in _LIMITS.items():
        if key not in raw:
            continue
        val = raw[key]
        try:
            val = float(val) if key == "stagger_seconds" else int(float(val))
        except (TypeError, ValueError):
            continue
        out[key] = min(hi, max(lo, val))

    return out


def load(path=None):
    """读设置。文件不在 / 读不动 / 内容坏了，一律返回默认值。"""
    path = path or SETTINGS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            return clean(json.load(f))
    except Exception:
        return dict(DEFAULTS)


def save(settings, path=None):
    """写设置。先写临时文件再替换，中途断电也不会留下半个坏文件。"""
    path = path or SETTINGS_PATH
    data = clean(settings)
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def describe(settings=None):
    """一句话总结当前设置，界面上和确认框里都显示这个。"""
    s = clean(settings if settings is not None else load())
    if not s["enabled"]:
        return "多开优化：关（每个账号立刻开一个浏览器，不限数量）"
    cap = "按内存自动" if not s["max_at_once"] else "{} 个".format(s["max_at_once"])
    bits = ["一次最多 {}".format(cap),
            "错开 {:.1f} 秒启动".format(s["stagger_seconds"])]
    bits.append("轻量化参数" if s["light_flags"] else "默认浏览器参数")
    if s["keep_awake"]:
        bits.append("保持后台窗口不降速")
    return "多开优化：" + "，".join(bits)


# 给子进程用的浏览器参数

def chrome_args(settings=None):
    """子进程该给 Chrome 加哪些参数。

    这里不 import chrome_flags 的顶层也无所谓，但为了让「关掉多开优化」
      真的回到改造前（一个参数都不加），enabled=False 时直接返回空列表。
    """
    s = clean(settings if settings is not None else load())
    if not s["enabled"] or not s["light_flags"]:
        return []
    try:
        import chrome_flags
        return chrome_flags.build_args(light=True, keep_awake=s["keep_awake"])
    except Exception:
        return []
