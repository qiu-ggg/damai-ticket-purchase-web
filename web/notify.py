# -*- coding: utf-8 -*-
"""短信通知，一条旁路，只在「值得你掏手机看一眼」的时候响一下。

每一行代码都服从这几条：① 绝不阻塞主流程、绝不改变退出码、绝不抛异常到调用方 —— 抢票是
主任务，通知是附属品，发不出去就算了，不能因此丢票；② 同一账号同一事件有冷却期（默认
60 秒 ~ 1 天，见 `notify_settings.DEFAULT_COOLDOWN`），抢票是死循环重试，没有冷却一晚能发
几千条，短信是计费的，而且刷屏比不通知更糟；③ 手机号只以脱敏形态出现在日志里
（`138****1234`）；④ 不代付、不催付，文案只如实说「订单已提交，请自行到浏览器完成支付」；
⑤ 任何配置缺失都只报「还没配好」，不报错退出。

发送方式两种：`console`（默认，把短信内容写进日志，不发真短信，什么都不需要）和 `aliyun`
（阿里云短信 `SendSms`，要签名 + 模板号 + AccessKey + 手机号）。`aliyun` 用纯标准库实现
（HMAC-SHA1 的 RPC 签名 + `urllib`），不装阿里云 SDK —— 这东西要进打包的 exe，多一个第三方
依赖就多一次「客户机器上装不上 / 漏打进包」的机会，而签名算法就那几行。

加一个新通道（钉钉、企业微信、邮件）：写一个
`def _send_xxx(cfg, secret, fields, body, timeout) -> (ok, message)`，签名照抄，再到
`PROVIDERS` 里注册一个名字，配置、频控、日志、异步都是共用的，不用碰。

挂点在各个 `notify.` 调用处：`account_runner.notify_payment()` 抢到票、
`account_runner.cmd_run()` 跑挂了或登录态失效、`task_manager.refresh()` 兜底补终态。
这几处都是旁路调用，失败只写日志，不看返回值也行。
"""

import datetime
import hashlib
import hmac
import json
import os
import re
import threading
import time
import traceback
import urllib.parse
import urllib.request

import notify_settings as ns

# 状态文件（频控用）跟着数据目录走，和 accounts.json 放一起
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATE_PATH = os.path.join(BASE_DIR, "notify_state.json")

# 失败原因写进短信前先截断，短信模板通常只有几十个字的位置
_REASON_LIMIT = 60



def _log(msg, account=None):
    """写一行日志。复用 accounts.say（控制台 + accounts.log 双写）。

    不自己再开一套日志格式：本项目已经有一处统一出口，
    多写一份迟早会漂移（见 PITFALLS 里那条「同一套修复不要写第二份」）。

    `accounts.say(msg, account)` 的第二个参数要的是账号 dict
      （它内部走 `tag()` → `account.get("account_id")`），不是账号号字符串。
      一开始就是传了字符串，`AttributeError` 被下面的 except 吞掉，
      结果「通知发没发」只出现在控制台、一个字都没落进 accounts.log，
      而客户报障时发的正是那个文件。`check_notify.py` 的 I 组现在盯着这条。
    """
    try:
        import accounts as acc_store
        acc = {"account_id": account} if account else None
        acc_store.say(msg, acc)
        return
    except Exception:
        pass
    try:
        print("[通知] {}".format(msg))
    except Exception:
        pass



def _short(text, limit=_REASON_LIMIT):
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def build_fields(event, account=None, **kw):
    """收集「这次事件能提供哪些字段」。

    字段名固定在 `notify_settings.FIELDS` 里，阿里云模板的变量名由用户
    在控制台自己定，五花八门，所以必须人工映射一次（`template_params`），
    但「有哪些东西可填」是我们说了算。

    返回的字典里带一个 `_event`。它是内部用的：provider 要按事件去查
      「模板变量名 → 字段名」那张映射表。放在这里而不是让调用方补，
      少写一步就少一个「provider 拿到空的映射、静默发出没有内容的短信」的机会。
    """
    fields = {
        "account": str(account or kw.get("account") or ""),
        "show": _short(kw.get("show") or kw.get("title") or "", 40),
        "city": _short(kw.get("city") or "", 12),
        "price": _short(kw.get("price") or "", 16),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": _short(kw.get("status") or "", 40),
        "reason": _short(kw.get("reason") or ""),
        "_event": event,
    }
    return fields


def build_body(event, fields):
    """人话版本的正文。console 用；也是「万一模板没配好」时的兜底内容。"""
    who = fields.get("account") or "账号"
    show = fields.get("show") or ""
    if event == ns.EVENT_GRABBED:
        head = "【抢到票】{} 已提交订单".format(who)
        tail = "请尽快到浏览器里自行完成支付（程序不会代付）。"
    elif event == ns.EVENT_FAILED:
        head = "【抢票出错】{}".format(who)
        tail = fields.get("reason") or "详见日志。"
    elif event == ns.EVENT_NEED_LOGIN:
        head = "【需要登录】{}".format(who)
        tail = "登录态失效了，要人工在浏览器里重新登录一次。"
    elif event == ns.EVENT_STARTED:
        head = "【已启动】{}".format(who)
        tail = "抢票任务开始运行。"
    else:
        head = "【通知】{}".format(event)
        tail = ""

    parts = [head]
    if show:
        parts.append(show)
    if tail:
        parts.append(tail)
    parts.append(fields.get("time") or "")
    return "，".join(p for p in parts if p)



def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("sent"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "sent": {}}


def _save_state(data):
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass                    # 频控状态存不下不是大事，绝不往上抛


def _state_key(event, account):
    return "{}|{}".format(event, account or "-")


def cooldown_left(event, account, cfg):
    """还要等多少秒才允许再发同一条。0 = 现在就能发。"""
    seconds = (cfg.get("cooldown_seconds") or {}).get(event, 600)
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 600.0
    if seconds <= 0:
        return 0.0
    sent = _load_state()["sent"].get(_state_key(event, account))
    try:
        sent = float(sent)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, seconds - (time.time() - sent))


def _mark_sent(event, account):
    data = _load_state()
    data["sent"][_state_key(event, account)] = time.time()
    # 顺手清掉 30 天前的记录，别让这个文件长到几千行
    cutoff = time.time() - 30 * 86400
    data["sent"] = {k: v for k, v in data["sent"].items()
                    if isinstance(v, (int, float)) and v >= cutoff}
    _save_state(data)


def _unmark_sent(event, account):
    """发送失败时把冷却标记撤掉，否则一次网络抖动会白白堵掉十分钟。"""
    data = _load_state()
    if data["sent"].pop(_state_key(event, account), None) is not None:
        _save_state(data)


# provider: console

def _send_console(cfg, secret, fields, body, timeout):
    _log("[通知·console] {}".format(body),
         fields.get("account") or None)
    return True, "console（只写日志，没发真短信）"


# provider: aliyun

def _percent_encode(text):
    """阿里云 RPC 签名的 percentEncode（POP 规范）。

    和普通的 urlencode 有三处不一样：空格是 `%20` 不是 `+`、`*` 要转义、
    `~` 不能转义。写错任何一个都会得到 `SignatureDoesNotMatch`。
    """
    s = urllib.parse.quote(str(text), safe="")
    return s.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def aliyun_signature(params, access_key_secret):
    """按阿里云 POP v1.0 算签名。抽出来是为了能单测。"""
    canonicalized = "&".join(
        "{}={}".format(_percent_encode(k), _percent_encode(params[k]))
        for k in sorted(params))
    string_to_sign = ("POST" + "&" + _percent_encode("/") + "&"
                      + _percent_encode(canonicalized))
    digest = hmac.new((str(access_key_secret) + "&").encode("utf-8"),
                      string_to_sign.encode("utf-8"),
                      hashlib.sha1).digest()
    import base64
    return base64.b64encode(digest).decode("ascii")


# 阿里云返回这些 Code 时，说人话（都是配置问题，不是网络问题）
_ALIYUN_CODE_HINT = {
    "isv.SMS_SIGNATURE_ILLEGAL": "签名没过：aliyun.sign_name 和你在控制台申请的不一致，或还没审核通过",
    "isv.SMS_TEMPLATE_ILLEGAL": "模板没过：aliyun.template_code 不对，或还没审核通过",
    "isv.TEMPLATE_PARAMS_ILLEGAL": "模板参数对不上：aliyun.template_params 里的变量名/个数和模板不一致",
    "isv.SMS_CONTENT_ILLEGAL": "短信内容没过审（签名+模板的组合被拒）",
    "isv.AMOUNT_NOT_ENOUGH": "账户余额不足",
    "isv.DAY_LIMIT_CONTROL": "触发了当日发送上限",
    "isv.MOBILE_NUMBER_ILLEGAL": "手机号格式不对",
    "InvalidAccessKeyId.NotFound": "AccessKeyId 不对",
    "SignatureDoesNotMatch": "签名算错了 —— 多半是 AccessKeySecret 不对",
    "Forbidden.RAM": "这个 AccessKey 没有发短信的权限（RAM 子账号要授权）",
}


def _send_aliyun(cfg, secret, fields, body, timeout):
    aliyun = cfg.get("aliyun") or {}
    missing = []
    if not (cfg.get("phone_numbers") or []):
        missing.append("phone_numbers（收信手机号）")
    if not aliyun.get("access_key_id"):
        missing.append("aliyun.access_key_id")
    if not secret:
        missing.append("AccessKeySecret")
    if not aliyun.get("sign_name"):
        missing.append("aliyun.sign_name（短信签名）")
    if not aliyun.get("template_code"):
        missing.append("aliyun.template_code（模板号）")
    if missing:
        return False, "还没配好，缺：" + "、".join(missing)

    mapping = (aliyun.get("template_params") or {}).get(fields.get("_event")) or {}
    params_payload = {}
    unknown = []
    empty = []
    for tpl_name, field_name in mapping.items():
        if field_name not in fields:
            unknown.append(str(field_name))
            continue
        value = fields.get(field_name)
        # 空值不塞进模板参数：阿里云对「变量为空」的处理是照发，用户会收到一条「您的账号 已抢到 的票」，
        # 看着像程序坏了。宁可在这里拦下来说清楚。
        if str(value if value is not None else "").strip() == "":
            empty.append(str(field_name))
            continue
        params_payload[tpl_name] = value

    if unknown:
        return False, ("aliyun.template_params 里写了程序不认识的字段名：{}"
                       "（可用字段：{}）".format("、".join(unknown),
                                            "、".join(ns.FIELDS)))
    if mapping and not params_payload:
        return False, ("aliyun.template_params 映射的字段这次都是空的（{}），"
                       "发出去会是一条残缺短信，所以没发。".format("、".join(empty)))

    params = {
        "AccessKeyId": aliyun.get("access_key_id"),
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": ",".join(str(p) for p in cfg["phone_numbers"]),
        "RegionId": aliyun.get("region") or "cn-hangzhou",
        "SignName": aliyun.get("sign_name"),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": hashlib.md5(
            "{}{}".format(time.time(), os.getpid()).encode()).hexdigest(),
        "SignatureVersion": "1.0",
        "TemplateCode": aliyun.get("template_code"),
        "Timestamp": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    if params_payload:
        params["TemplateParam"] = json.dumps(params_payload, ensure_ascii=False)

    params["Signature"] = aliyun_signature(params, secret)

    data = "&".join(
        "{}={}".format(_percent_encode(k), _percent_encode(params[k]))
        for k in sorted(params)).encode("utf-8")

    url = aliyun.get("endpoint") or "https://dysmsapi.aliyuncs.com/"
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})

    opener = None
    proxy = str(cfg.get("proxy") or "").strip()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        if opener is not None:
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, "连不上阿里云：{}".format(_short(exc, 80))

    try:
        result = json.loads(raw)
    except Exception:
        return False, "阿里云返回的不是 JSON：{}".format(_short(raw, 80))

    code = str(result.get("Code") or "")
    if code == "OK":
        return True, "已发送（BizId {}）".format(result.get("BizId") or "?")
    hint = _ALIYUN_CODE_HINT.get(code)
    return False, "阿里云返回 {}：{}{}".format(
        code, result.get("Message") or "", ("（{}）".format(hint) if hint else ""))


PROVIDERS = {
    "console": _send_console,
    "aliyun": _send_aliyun,
}



def _prepare(event, account, cfg, kw):
    cfg = cfg or ns.load()
    fields = build_fields(event, account, **kw)      # 里面已经带了 `_event`
    return cfg, fields


def send_sync(event, account=None, cfg=None, **kw):
    """当场发一条（带超时）。返回 `(是否成功, 说明)`。不抛异常。

    给三类调用方用：
      * `notify --test`（要立刻知道成不成）
      * 进程快要退出的地方（抢到票之后），后台线程会被一起收走
      * 自检脚本
    """
    try:
        cfg, fields = _prepare(event, account, cfg, kw)
    except Exception:
        return False, "准备字段时出错：{}".format(_short(traceback.format_exc(), 120))

    if not cfg.get("enabled"):
        return False, "通知是关的（notify.json 里 enabled=false）"
    if not (cfg.get("events") or {}).get(event, False):
        return False, "事件「{}」是关的".format(ns.name_cn(event))

    left = cooldown_left(event, account, cfg)
    if left > 0:
        return False, "冷却中（还有 {:.0f} 秒），这条不发了".format(left)

    provider = cfg.get("provider") or "console"
    fn = PROVIDERS.get(provider)
    if fn is None:
        return False, "不认识的 provider：{}（可选 {}）".format(
            provider, "、".join(sorted(PROVIDERS)))

    body = build_body(event, fields)
    try:
        timeout = float(cfg.get("timeout_seconds") or 10)
    except (TypeError, ValueError):
        timeout = 10.0

    try:
        import notify_settings as _ns
        secret = _ns.get_secret()
    except Exception:
        secret = ""

    try:
        ok, message = fn(cfg, secret, fields, body, timeout)
    except Exception:
        ok, message = False, "发送时出错：{}".format(_short(traceback.format_exc(), 160))

    # 成败都要留痕。发送失败最常见的原因是配置没填对，不写下来「为什么没收到短信」只能靠猜。
    _log("[通知] {} → {}（{}）".format(
        ns.name_cn(event), "成功" if ok else "未发送", message),
        fields.get("account") or None)

    if ok:
        _mark_sent(event, account)
    else:
        _unmark_sent(event, account)
    return ok, message


def notify(event, account=None, wait=False, cfg=None, **kw):
    """主流程用的入口。永远不抛异常、默认不阻塞。

    `wait=False`（默认）→ 扔给后台线程。适合「启动」「出错」这类
    后面还有事要干的位置。
    `wait=True` → 当场发。进程马上要退了（抢到票之后）必须用这个，
    否则 daemon 线程会跟着进程一起消失，短信就没了。
    """
    try:
        if wait:
            return send_sync(event, account=account, cfg=cfg, **kw)

        def _worker():
            try:
                send_sync(event, account=account, cfg=cfg, **kw)
            except Exception:
                pass                      # 兜底：线程里绝不允许漏异常出去

        t = threading.Thread(target=_worker, name="notify-{}".format(event))
        t.daemon = True
        t.start()
        return None
    except Exception:
        return None                        # 连起线程都失败 → 静默，绝不打扰主流程


def self_check():
    """给自检用：把签名算法拿已知输入钉一下，不联网。

    阿里云官方文档给过一组示例，这里不抄它的字符串（怕记串），
    改成钉「不变量」，排序、编码、确定性这三条错了必然出问题：
      * 同样的参数必须得到同样的签名（确定性）
      * 参数顺序不影响结果（内部排序）
      * `~` 不转义、空格转 %20、`*` 转 %2A
    """
    problems = []
    if _percent_encode("a b") != "a%20b":
        problems.append("空格应该编码成 %20")
    if _percent_encode("a*b") != "a%2Ab":
        problems.append("* 应该编码成 %2A")
    if _percent_encode("a~b") != "a~b":
        problems.append("~ 不该被转义")
    if _percent_encode("/") != "%2F":
        problems.append("/ 应该编码成 %2F")

    p1 = {"B": "2", "A": "1", "C": "中文"}
    p2 = {"C": "中文", "A": "1", "B": "2"}
    s1 = aliyun_signature(p1, "secret")
    s2 = aliyun_signature(p2, "secret")
    if s1 != s2:
        problems.append("参数顺序不该影响签名")
    if s1 != aliyun_signature(p1, "secret"):
        problems.append("同样的输入应该得到同样的签名（不确定）")
    if s1 == aliyun_signature(p1, "other-secret"):
        problems.append("换密钥必须换签名")
    return problems


def describe_state():
    """最近发过哪些（给排查用）。"""
    data = _load_state()
    if not data["sent"]:
        return "（还没有发过）"
    rows = sorted(data["sent"].items(), key=lambda kv: kv[1], reverse=True)[:10]
    return "\n".join("  {}  {}".format(
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), k)
        for k, t in rows)
