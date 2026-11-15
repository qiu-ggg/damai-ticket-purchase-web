# -*- coding: utf-8 -*-
"""短信通知的配置（读 / 写 / 校验）。

两类东西分开放：`notify.json` 是配置本体（开关、事件、手机号、签名、模板号、
AccessKeyId），`notify_secret.txt` 只有一样东西 —— AccessKeySecret（明文）。为什么要
把 Secret 单独拎出来：`notify.json` 是要备份、要给人看、出问题要贴出来排查的文件，
AccessKeySecret 落在里面就等于随手发出去了；这条规矩和「密码不进 accounts.json」是同一条，
见 `cred_store.py` 顶部。

`default_config()` 出来就是 `enabled: false` + `provider: "console"`。没配好之前程序照样跑，
通知只是不往手机发（console 会把内容写进日志），绝不允许「没配通知」变成「抢不了票」。

阿里云那边用户要在控制台做完三件事：申请签名 → 填 `aliyun.sign_name`（例如「大麦提醒」）；
申请模板 → 填 `aliyun.template_code`（例如 `SMS_123456789`）；拿到 AccessKey（建议用只授予
短信权限的 RAM 子账号）→ `access_key_id` 填在 `notify.json`，`access_key_secret` 用
`python account_runner.py notify --set-secret` 存进来。模板里的变量名各自不同，所以
`aliyun.template_params` 要人工对一次：把「模板里的变量名」映射到「程序能提供的字段名」，
可用字段见 `FIELDS`。
"""

import io
import json
import os
import time

try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "notify.json")
SECRET_PATH = os.path.join(BASE_DIR, "notify_secret.txt")

FORMAT_VERSION = 1

# 事件名，代码里一律用这些常量，不要写字面量
EVENT_GRABBED = "grabbed"            # 订单已提交，停在支付环节
EVENT_FAILED = "failed"              # 跑挂了 / 出错
EVENT_NEED_LOGIN = "need_login"      # 登录态失效，要人来
EVENT_STARTED = "started"            # 任务启动（默认关，容易刷屏）

EVENTS = (EVENT_GRABBED, EVENT_FAILED, EVENT_NEED_LOGIN, EVENT_STARTED)

# 程序能给模板提供的字段名，`aliyun.template_params` 的值只能从这里挑。阿里云模板长什么样由用户
# 在控制台定，变量名五花八门，必须人工映射一次；但「有哪些东西可填」是我们说了算，写死在这里，省得配错了才发现拿不到值。
FIELDS = (
    "account",      # 账号号，例如 Account 01
    "show",         # 演出名（页面标题）
    "city",         # 城市
    "price",        # 票档
    "time",         # 触发时间 YYYY-MM-DD HH:MM:SS
    "status",       # 一句话状态
    "reason",       # 失败原因（截断过）
)

# 每个事件默认的冷却秒数：同一账号同一事件在这段时间内只发一条。必须有，因为抢票是死循环重试、
# `failed` 一晚上能触发几千次，而短信按条计费、刷屏比不通知更糟。
DEFAULT_COOLDOWN = {
    EVENT_GRABBED: 60,
    EVENT_FAILED: 600,
    EVENT_NEED_LOGIN: 900,
    EVENT_STARTED: 300,
}

# 默认只开三条「值得吵醒你」的
DEFAULT_EVENTS = {
    EVENT_GRABBED: True,
    EVENT_FAILED: True,
    EVENT_NEED_LOGIN: True,
    EVENT_STARTED: False,
}

_说明 = [
    "短信通知的配置。默认关闭（enabled=false），关着的时候程序照常跑，只是不发短信。",
    "AccessKeySecret 不在这个文件里，它单独存在 notify_secret.txt。",
    "provider 现在支持 console（写进日志，不发短信）和 aliyun（发真短信）。",
    "events 里每个事件都可以单独开关：grabbed=抢到票了，failed=出错了，"
    "need_login=登录态失效要人工处理，started=任务启动。",
    "cooldown_seconds 是「同一账号同一事件多久内只发一条」。抢票会疯狂重试，"
    "没有它一晚上能发几千条。",
    "aliyun.template_params 要把「短信模板里的变量名」映射到程序提供的字段名，"
    "可用字段：" + "、".join(FIELDS) + "。",
    "改这个文件请先关掉向导和正在跑的抢票进程，否则退出时会把界面上的值写回来。",
]


def default_config():
    """一份开箱可用的默认配置：关闭 + console，不需要任何外部东西。"""
    return {
        "version": FORMAT_VERSION,
        "_说明": list(_说明),
        "enabled": False,
        "provider": "console",
        "phone_numbers": [],
        "events": dict(DEFAULT_EVENTS),
        "cooldown_seconds": dict(DEFAULT_COOLDOWN),
        "timeout_seconds": 10,
        "proxy": "",
        "aliyun": {
            "endpoint": "https://dysmsapi.aliyuncs.com/",
            "region": "cn-hangzhou",
            "access_key_id": "",
            "sign_name": "",
            "template_code": "",
            "template_params": {ev: {} for ev in EVENTS},
        },
    }


def _merge_defaults(cfg):
    """把缺的键补上（老配置文件遇到新增字段时不至于 KeyError）。"""
    base = default_config()
    if not isinstance(cfg, dict):
        return base
    out = dict(base)
    out.update({k: v for k, v in cfg.items() if k != "aliyun"})
    aliyun = dict(base["aliyun"])
    if isinstance(cfg.get("aliyun"), dict):
        aliyun.update(cfg["aliyun"])
        # template_params 是「事件到 {模板变量: 字段}」的两层映射
        tp = dict(base["aliyun"]["template_params"])
        if isinstance(cfg["aliyun"].get("template_params"), dict):
            for ev, mapping in cfg["aliyun"]["template_params"].items():
                if isinstance(mapping, dict):
                    tp[ev] = dict(mapping)
        aliyun["template_params"] = tp
    out["aliyun"] = aliyun
    # 事件和冷却也要按事件逐个补齐
    for key in ("events", "cooldown_seconds"):
        merged = dict(base[key])
        if isinstance(cfg.get(key), dict):
            merged.update({k: v for k, v in cfg[key].items() if k in EVENTS})
        out[key] = merged
    return out


def load(path=None):
    """读配置。文件不在或读坏了就返回默认配置，绝不抛异常。

    理由：通知是旁路。配置坏了不该让抢票进程起不来，
    那种「一个可选功能把主流程拖死」的设计是最坑的。
    """
    path = path or CONFIG_PATH
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return default_config()
    return _merge_defaults(cfg)


def save(cfg, path=None):
    """写配置。先写临时文件再替换（中途出错不留半个坏文件）。"""
    path = path or CONFIG_PATH
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    data = _merge_defaults(cfg)
    data["version"] = FORMAT_VERSION
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


# AccessKeySecret

def set_secret(secret, path=None):
    """存 AccessKeySecret（明文）。空串 = 删掉已存的那份。"""
    path = path or SECRET_PATH
    if not str(secret or "").strip():
        if os.path.exists(path):
            os.remove(path)
        return False
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(str(secret).strip() + "\n")
    os.replace(tmp, path)
    return True


def has_secret(path=None):
    return os.path.exists(path or SECRET_PATH)


def get_secret(path=None):
    """取回 AccessKeySecret；没有或读不出来就返回空串（不抛）。

    静默返回空串 + 由上层报「密钥没填，请重新填一次」比较好，
    因为调用它的是一条旁路，抛异常只会变成噪音。
    """
    path = path or SECRET_PATH
    if not os.path.exists(path):
        return ""
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def mask_secret(secret):
    s = str(secret or "")
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def mask_phone(phone):
    """手机号脱敏。和 cred_store.mask_phone 同一套写法，日志里只出现这个形态。"""
    s = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch == "+")
    if len(s) < 7:
        return s or "?"
    return s[:3] + "****" + s[-4:]


def describe(cfg=None, secret_present=None):
    """给人看的一段话，直接贴出来就能判断「配没配好」。"""
    cfg = cfg or load()
    if secret_present is None:
        secret_present = has_secret()
    lines = [
        "通知总开关：{}".format("开" if cfg.get("enabled") else "关"),
        "发送方式：{}".format(cfg.get("provider") or "console"),
        "收信手机号：{}".format(
            "、".join(mask_phone(p) for p in cfg.get("phone_numbers") or []) or "（没填）"),
    ]
    ev = cfg.get("events") or {}
    lines.append("事件开关：" + "，".join(
        "{}{}".format(name_cn(k), "开" if ev.get(k) else "关") for k in EVENTS))
    aliyun = cfg.get("aliyun") or {}
    if (cfg.get("provider") or "") == "aliyun":
        lines += [
            "  签名（SignName）：{}".format(aliyun.get("sign_name") or "（没填）"),
            "  模板号（TemplateCode）：{}".format(aliyun.get("template_code") or "（没填）"),
            "  AccessKeyId：{}".format(_mask_ak(aliyun.get("access_key_id"))),
            "  AccessKeySecret：{}".format("已存" if secret_present
                                           else "（没填）"),
        ]
        missing = [k for k in ("sign_name", "template_code", "access_key_id")
                   if not aliyun.get(k)]
        if not secret_present:
            missing.append("access_key_secret")
        if not (cfg.get("phone_numbers") or []):
            missing.append("phone_numbers")
        if missing:
            lines.append("  ↑ 还缺：{}".format("、".join(missing)))
        else:
            lines.append("  ↑ 看起来齐了，可以 `notify --test` 发一条试试。")
    return "\n".join(lines)


def _mask_ak(ak):
    s = str(ak or "")
    if not s:
        return "（没填）"
    if len(s) <= 8:
        return s[0] + "*" * (len(s) - 1)
    return s[:4] + "****" + s[-4:]


def name_cn(event):
    return {
        EVENT_GRABBED: "抢到票",
        EVENT_FAILED: "出错",
        EVENT_NEED_LOGIN: "要登录",
        EVENT_STARTED: "启动",
    }.get(event, event)


def touch(cfg=None, path=None):
    """确保配置文件存在（第一次运行时落一份默认的，方便人照着改）。"""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        save(cfg or default_config(), path)
        return True
    return False


def mtime(path=None):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(os.path.getmtime(path or CONFIG_PATH)))
    except OSError:
        return ""
