# -*- coding: utf-8 -*-
"""多账号登录状态管理，账号清单数据层

本模块只做两件事：
  1. 读写根目录下的 accounts.json（账号清单）
  2. 管理每个账号的独立 Chrome Profile 目录（登录状态就存在这里）

设计原则（重要）：
  * 不保存任何密码。 登录一律由用户在浏览器里手动完成，本模块里没有任何
    存放账号密码的字段，也没有任何自动填表的函数。
  * 登录状态由 Chrome 自带的 Profile 目录承载，Cookie、localStorage、
    缓存都在里面。每个账号一个独立目录，所以账号之间天然隔离，
    启动时带上同一个目录就自动是登录态，不需要额外保存票据。
  * 本模块不导入 selenium，不碰浏览器，方便单独测试。
"""

import json
import math
import os
import re
import shutil
import time

# 打包成 exe 后 __file__ 指向临时解压目录（退出即删），路径统一交给 app_paths：源码形态返回本目录
# （跟改造前一字不差），exe 形态返回 exe 同级目录（账号清单 / 登录态才不会每次启动都丢）。
# 拿不到 app_paths 就退回老写法，绝不因路径模块出问题而起不来。
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
    LOGS_DIR = app_paths.logs_dir()             # 日志收在 data/logs/ 下，别混进源码
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")
PROFILES_ROOT = os.path.join(BASE_DIR, "profiles")

# 日志落到哪。默认数据目录下的 logs/（源码形态 = web/logs/，打包形态 = exe 同级/logs/），收在子目录里，不跟几十个 .py 摊在一起，
# 交付源码时也不会顺手把「什么时候抢了哪场票」交出去。允许用环境变量改，是为一个反复出过 7 次的坑：子进程。probe_env() 那种
# 「把模块变量指到临时目录」只管得住当前进程；Popen 起来的调度器是全新解释器，会重读这个文件照样写真实日志，调用方所有重定向都形同虚设（测试里的「假记录」就是这么混进真实记录的），有它才能给子进程一个隔离的日志路径。
LOG_PATH = os.environ.get("DAMAI_ACCOUNTS_LOG") or os.path.join(LOGS_DIR, "accounts.log")

# 账号级字段全集。读旧文件时用它补缺，也用于新建账号打底。
# 注意：这里故意没有任何密码 / 凭据字段。
BLANK_ACCOUNT = {
    "account_id": "",            # 账号标识，日志里显示为 [Account 01]
    "note": "",                  # 备注，方便自己认人
    "enabled": True,             # 是否启用；false 时 run 会跳过
    "profile_dir": "",           # 登录状态保存位置；留空 = profiles/<账号号>
    "target_url": "",            # 目标演出详情页
    "city": "",
    "dates": [],                 # 场次；填详情页「场次」那一栏的原文
    "calendar_date": "",         # 日历日期（选填）；详情页是月历格子时，要把哪天点掉
    "prices": [],                # 票档；数组顺序 = 优先级（先试排前面的）
    "quantity": 1,               # 购买数量
    "users": [],                 # 观演人；数量要与 quantity 对应
    "if_listen": True,           # 持续轮询余票
    "if_commit_order": False,    # true = 替你勾协议 + 提交订单（观演人一律替你勾；不会自动付款）
    "max_retries": 10000,
    "auto_login": True,          # 启动时用本机保存的密码自动填表登录（向导里的开关）
    "dismiss_mask": True,        # 自动关掉「手机扫码购买更便捷」这类挡按钮的弹窗
    "no_seat": True,             # 优先点「不选座购票」，跳过选座直接进提交订单页
    "random_seat": True,         # 真落到选座页就随机选一个座位并确认
}

# 账号清单模板。首次运行时会生成这个文件，里面的值需要你自己改。
TEMPLATE = {
    "version": 1,
    "_说明": [
        "本文件不保存任何密码。登录请在浏览器里手动完成：python account_runner.py login",
        "模板里的字段刻意留空，是为了避免你忘改就拿去跑；填好才能 run。",
        "profile_dir 是该账号的独立浏览器数据目录，登录状态就保存在里面（留空则自动用 profiles/<账号号>）。",
        "prices 的数组顺序就是票档优先级；写票价数字（188）或直接粘贴票价页原文（全价票188元（站席））都能认。",
        "quantity 必须 <= users 的人数（大麦要为每张票指定观演人）。",
        "dates 留空表示不按日期筛选，由页面默认场次决定。",
        "calendar_date（选填）只在详情页的日期是月历格子时才填（例 2026-12-26）："
        "这种页面场次列表只显示选中那天的场次，不先点掉那一天就永远选不中；"
        "不是这种页面就留空。它和 dates（场次）是两回事，别填同一个值。",
        "if_commit_order = true = 替你勾购票协议 + 提交订单；观演人不论开不开都会替你勾好；绝不会自动付款。",
        "dismiss_mask / no_seat / random_seat 三项默认 true：自动关扫码弹窗、优先不选座购票、落选座页就随机选一个座位。",
        "auto_login 默认 true：启动时用本机保存的密码自动填表登录（密码不在本文件里）。",
    ],
    "accounts": [
        {
            "account_id": "Account 01",
            "note": "改成这个账号是谁（例如：本人）",
            "enabled": True,
            "profile_dir": "",
            "target_url": "",
            "city": "",
            "dates": [],
            "calendar_date": "",
            "prices": [],
            "quantity": 1,
            "users": [],
            "if_listen": True,
            "if_commit_order": False,
            "max_retries": 10000,
            "auto_login": True,
            "dismiss_mask": True,
            "no_seat": True,
            "random_seat": True,
        },
        {
            "account_id": "Account 02",
            "note": "第二个账号（不需要就把它 enabled 改成 false）",
            "enabled": False,
            "profile_dir": "",
            "target_url": "",
            "city": "",
            "dates": [],
            "calendar_date": "",
            "prices": [],
            "quantity": 1,
            "users": [],
            "if_listen": True,
            "if_commit_order": False,
            "max_retries": 10000,
            "auto_login": True,
            "dismiss_mask": True,
            "no_seat": True,
            "random_seat": True,
        },
    ],
}



def _write_log(line):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def tag(account):
    """日志前缀，形如 [Account 01]。"""
    return "[{}]".format((account or {}).get("account_id") or "?")


def say(msg, account=None):
    """控制台 + 日志文件双写。控制台保持干净格式：[Account 01] 登录状态有效"""
    line = msg if account is None else "{} {}".format(tag(account), msg)
    print(line, flush=True)
    _write_log(line)
    return line



def _safe_name(account_id):
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(account_id or "")).strip("_")
    return safe or "account"


def default_profile_dir(account_id):
    """默认的登录状态保存位置（相对项目根目录，便于整个文件夹搬走）。"""
    return os.path.join("profiles", _safe_name(account_id))


def profile_path(account):
    """返回该账号 Profile 目录的绝对路径。"""
    raw = str(account.get("profile_dir") or "").strip() or default_profile_dir(account.get("account_id"))
    if not os.path.isabs(raw):
        raw = os.path.join(BASE_DIR, raw)
    return os.path.abspath(raw)


def ensure_profile_dir(account):
    p = profile_path(account)
    os.makedirs(p, exist_ok=True)
    return p


def login_state_exists(account):
    """判断这个账号的登录状态文件在不在（Profile 目录是否已经建起来）。"""
    p = profile_path(account)
    if not os.path.isdir(p):
        return False
    for marker in ("Default", "Local State", "First Run"):
        if os.path.exists(os.path.join(p, marker)):
            return True
    try:
        return bool(os.listdir(p))
    except OSError:
        return False


def forget_login_state(account):
    """删除该账号的登录状态（整个 Profile 目录）。返回是否真的删掉了。

    只允许删除本项目 profiles 目录下的内容，避免误删别的地方。
    """
    p = profile_path(account)
    root = os.path.abspath(PROFILES_ROOT)

    # 先做路径校验，再判断存在性，配错路径要立刻报错，不能悄悄放过
    try:
        inside = os.path.commonpath([p, root]) == root
    except ValueError:
        inside = False  # 不同盘符
    if not inside or p == root:
        raise ValueError("拒绝删除：{} 不在 {} 目录下".format(p, root))

    if not os.path.isdir(p):
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True



def load(auto_create=True):
    """读 accounts.json。文件不存在时按需生成模板。"""
    if not os.path.exists(ACCOUNTS_PATH):
        if not auto_create:
            return {"version": 1, "accounts": []}
        save(dict(TEMPLATE))
        say("未找到 accounts.json，已生成模板：{}".format(ACCOUNTS_PATH))
        say("请先按需修改里面的账号信息（本文件不保存密码）。")

    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data.get("accounts"), list):
        raise ValueError("accounts.json 格式不对：accounts 必须是数组")

    seen = set()
    for acc in data["accounts"]:
        if not isinstance(acc, dict):
            continue
        for k, v in BLANK_ACCOUNT.items():
            acc.setdefault(k, v)
        aid = str(acc.get("account_id") or "").strip()
        if aid and aid in seen:
            say("警告：account_id 「%s」重复，后面的会被当成同一个账号处理".format(aid))
        seen.add(aid)
    return data


def save(data):
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)



def all_accounts(data):
    return [a for a in data.get("accounts", []) if isinstance(a, dict)]


def enabled_accounts(data):
    return [a for a in all_accounts(data) if a.get("enabled", True)]


def find(data, account_id):
    for a in all_accounts(data):
        if str(a.get("account_id")) == str(account_id):
            return a
    return None


def pick(data, want_ids=None, only_enabled=False):
    """按命令行给的 account_id 过滤；不给就取全部（或全部启用的）。"""
    pool = enabled_accounts(data) if only_enabled else all_accounts(data)
    if not want_ids:
        return pool
    out = []
    for aid in want_ids:
        acc = find(data, aid)
        if acc is None:
            say("找不到账号：{}".format(aid))
            continue
        out.append(acc)
    return out



def price_number(raw):
    """从一处票档写法里取出票价数字；取不出返回 None。

    运行时（damai/concert.py 的 `_price_int_list`）就是按第一个数字串去
    和页面票档匹配的，所以校验必须拿同一把尺子量。下面这些都算合法：

        188            "188"          "¥680"         "680元"
        "全价票188元（站席）"          "内场 188 元"

    历史包袱：早期校验写的是 `str(p).strip().isdigit()`，只认纯数字。
    用户从票价页复制原文（"全价票188元（站席）"）会被判「配置不完整」而
    整个账号被跳过，运行时明明能正常匹配，向导也照抄不拦。
    校验比运行时严，等于凭空拦掉本来能跑的配置，所以这里放宽到与运行时一致。
    """
    m = re.search(r"(\d+)", str(raw if raw is not None else ""))
    return int(m.group(1)) if m else None


def validate(account, other_ids=(), for_login=False):
    """返回问题列表，空列表代表通过。

    for_login=True 时只校验登录所需的最小字段，首次登录通常还没填演出信息。
    """
    errs = []
    aid = str(account.get("account_id") or "").strip()
    if not aid:
        errs.append("account_id 不能为空")
    elif aid in other_ids:
        errs.append("account_id 「{}」和别的账号重复".format(aid))

    if for_login:
        return errs

    url = str(account.get("target_url") or "").strip()
    if not url:
        errs.append("target_url 不能为空")
    elif not url.lower().startswith("http"):
        errs.append("target_url 必须以 http 开头")

    users = [str(u).strip() for u in (account.get("users") or []) if str(u).strip()]
    if not users:
        errs.append("users 至少要写 1 位观演人")

    q = account.get("quantity")
    if not isinstance(q, int) or isinstance(q, bool) or q < 1:
        errs.append("quantity 必须是 >=1 的整数")
    elif users and q > len(users):
        errs.append("quantity({}) 大于观演人数量({})；大麦需要为每张票指定观演人".format(q, len(users)))

    prices = account.get("prices") or []
    if not prices:
        errs.append("prices 至少要写一个票档")
    else:
        for p in prices:
            if price_number(p) is None:
                errs.append(
                    "prices 里的「{}」找不到票价数字；填数字（188）或直接粘贴"
                    "票价页上的原文（全价票188元（站席））都可以".format(p))

    retry = account.get("max_retries")
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 1:
        errs.append("max_retries 必须是 >=1 的整数")

    # refresh_interval 是可选字段（不在 BLANK_ACCOUNT 里）：没有它 = 后端按 fast_mode 走默认值，
    # 行为跟没这功能一样；但一旦写了就必须是能用的秒数，写错会让 build_config 里的 float() 静默失败，
    # 表现成「明明设了 2 秒，跑起来还是默认节奏」，查半天。
    ri = account.get("refresh_interval")
    if ri not in (None, ""):
        try:
            rv = float(ri)
        except (TypeError, ValueError):
            errs.append("refresh_interval 必须是秒数，现在是「{}」".format(ri))
        else:
            if not math.isfinite(rv):
                errs.append("refresh_interval 必须是正常的秒数")
            elif rv < 0:
                errs.append("refresh_interval 不能是负数")

    return errs


def validate_all(data, for_login=False):
    """校验所有账号，返回 {account_id: [错误...]}，只包含有问题的。"""
    problems = {}
    ids = [str(a.get("account_id")) for a in all_accounts(data)]
    for acc in all_accounts(data):
        aid = str(acc.get("account_id"))
        others = [i for i in ids if i != aid]
        errs = validate(acc, other_ids=others, for_login=for_login)
        if errs:
            problems[aid] = errs
    return problems


def summary_line(account):
    """一行摘要，方便 status 里看。"""
    bits = []
    if not account.get("enabled", True):
        bits.append("已禁用")
    if account.get("note"):
        bits.append(str(account["note"]))
    if account.get("city"):
        bits.append(str(account["city"]))
    if account.get("dates"):
        bits.append("/".join(str(d) for d in account["dates"]))
    if account.get("prices"):
        bits.append("票档优先 " + " > ".join(str(p) for p in account["prices"]))
    bits.append("{} 张".format(account.get("quantity", 1)))
    if account.get("no_seat", True):
        bits.append("不选座")
    elif account.get("random_seat", True):
        bits.append("随机选座")
    ri = account.get("refresh_interval")
    if ri not in (None, ""):
        try:
            bits.append("刷新 %g 秒" % float(ri))
        except (TypeError, ValueError):
            bits.append("刷新频率「{}」非法".format(ri))
    if not account.get("dismiss_mask", True):
        bits.append("不自动关弹窗")
    if account.get("users"):
        bits.append("观演人 " + "、".join(str(u) for u in account["users"]))
    return "  |  ".join(bits)


def add_account(data, account_id, note="", copy_from=None):
    """新增一个账号并保存。copy_from 可传另一个账号 dict 做模板。"""
    if find(data, account_id) is not None:
        raise ValueError("已经存在 account_id 为「{}」的账号".format(account_id))
    acc = dict(BLANK_ACCOUNT)
    acc["dates"] = []
    acc["prices"] = []
    acc["users"] = []
    if copy_from:
        for k in BLANK_ACCOUNT:
            v = copy_from.get(k)
            if isinstance(v, list):
                acc[k] = list(v)
            else:
                acc[k] = v
    acc["account_id"] = account_id
    acc["note"] = note
    data["accounts"].append(acc)
    save(data)
    return acc
