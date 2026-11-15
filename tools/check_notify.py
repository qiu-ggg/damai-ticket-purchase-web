# -*- coding: utf-8 -*-
"""短信通知自检。在 web/ 目录跑：`python tools/check_notify.py`

通知是一条旁路，旁路最怕两件事，这个脚本就是为它们写的：① 它把主流程拖死，抢票是主任务，
短信是附属品，所以有一整组用例（F 组）专门钉「provider 炸了 / 网络超时 / 状态文件写不了 /
异步」，要求通知一律不能抛、不能阻塞；② 它把不该露的东西写进日志，手机号、AccessKey、
Secret 一个都不能明文出现在日志或配置里，B/A6 组会真去读文件搜明文。另外还有两组：频控
（抢票是死循环，没冷却一晚几千条短信）、签名（阿里云 RPC 签名算错只会回一句
SignatureDoesNotMatch，比配置还难查）。

全程不联网、不发真短信、不碰真实文件，所有路径都重定向到临时目录。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
                          # tools/ 挪到仓库根之后得显式拼这一层
if WEB not in sys.path:
    sys.path.insert(0, WEB)

import notify as nf                                                   # noqa: E402
import notify_settings as ns                                          # noqa: E402

_results = []

# 开跑前把真实文件的哈希记下来，收尾要证明自检没碰过它们
_WATCH = {}


def _hash_file(path):
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None


def _remember_real_files():
    for path in (nf.STATE_PATH, ns.CONFIG_PATH, ns.SECRET_PATH):
        _WATCH[path] = _hash_file(path)


def _verify_untouched(real_log):
    """真实文件必须一个都没变。变了就是自检在污染用户数据，得让它红。"""
    for path, before in _WATCH.items():
        if before is None:
            continue                     # 本来就没有的文件不管
        now = _hash_file(path)
        check("J 真实文件没被自检改过：{}".format(os.path.basename(path)),
              now == before, "{} → {}".format(before, now))
    if real_log and os.path.exists(real_log):
        print("  [--] 真实 accounts.log 哈希：{}（本脚本全程写到临时目录）"
              .format(_hash_file(real_log)))


def check(name, passed, detail=""):
    _results.append((name, bool(passed), detail))
    print("  [{}] {}{}".format("PASS" if passed else "!!", name,
                               ("  <<< " + detail) if detail else ""))
    return passed


def skip(name, why):
    print("  [SKIP] {}  <<< {}".format(name, why))


# A. 配置

def group_a_config(tmp):
    print("\n[A] 配置：默认是关的、坏了也不能炸")

    cfg = ns.default_config()
    check("A1.1 默认是关闭的（没配好之前不许发短信）",
          cfg["enabled"] is False)
    check("A1.2 默认走 console（不联网、不花钱）",
          cfg["provider"] == "console", cfg["provider"])
    check("A1.3 默认开着的事件里有「抢到票/出错/要登录」",
          all(cfg["events"][e] for e in (ns.EVENT_GRABBED, ns.EVENT_FAILED,
                                         ns.EVENT_NEED_LOGIN)))
    check("A1.4 默认关着「启动」（5 个账号就是 5 条短信，纯刷屏）",
          cfg["events"][ns.EVENT_STARTED] is False)

    # A2：文件不在，默认，不抛
    missing = os.path.join(tmp, "___没有___.json")
    got = ns.load(missing)
    check("A2.1 配置文件不存在时返回默认配置（不抛异常）",
          got["enabled"] is False and got["version"] == ns.FORMAT_VERSION)

    # A3：文件是坏的，默认，不抛
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{这不是 JSON")
    got = ns.load(bad)
    check("A3.1 配置文件坏掉时也返回默认（旁路不许把主流程带崩）",
          got["enabled"] is False)

    # A4：往返
    path = os.path.join(tmp, "notify.json")
    cfg = ns.default_config()
    cfg["enabled"] = True
    cfg["provider"] = "aliyun"
    cfg["phone_numbers"] = ["13800138000"]
    cfg["aliyun"]["sign_name"] = "测试签名"
    cfg["aliyun"]["template_code"] = "SMS_1234"
    ns.save(cfg, path)
    back = ns.load(path)
    check("A4.1 save/load 往返一致",
          back["enabled"] and back["phone_numbers"] == ["13800138000"]
          and back["aliyun"]["sign_name"] == "测试签名")

    # A5：老配置缺字段（以后加新字段时不至于 KeyError）
    old = os.path.join(tmp, "old.json")
    with open(old, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "enabled": True, "provider": "console"}, f)
    got = ns.load(old)
    check("A5.1 缺 aliyun / events / cooldown 的老配置能自动补齐",
          isinstance(got.get("aliyun"), dict)
          and got["events"].get(ns.EVENT_GRABBED) is True
          and got["cooldown_seconds"].get(ns.EVENT_FAILED))
    check("A5.2 老配置里已经写了的开关要保留（不能补成默认把用户的设置覆盖掉）",
          got["enabled"] is True)

    # A6：describe 不能泄露完整信息
    ns.save(cfg, path)
    text = ns.describe(ns.load(path), secret_present=True)
    check("A6.1 describe 里手机号是脱敏的", "13800138000" not in text,
          text.replace("\n", " | ")[:110])
    check("A6.2 脱敏形态能看出是哪条号", "138****8000" in text)
    check("A6.3 describe 里没有模板号之外的多余东西也不会崩",
          "签名" in text)


# B. 密钥（明文文件）

def group_b_secret(tmp):
    print("\n[B] AccessKeySecret 存取")

    path = os.path.join(tmp, "notify_secret.txt")
    secret = "LTAI5tSomeFakeSecretValue123456"
    ns.set_secret(secret, path)

    check("B1.1 存了之后能取回来", ns.get_secret(path) == secret)
    check("B1.2 has_secret 判得对", ns.has_secret(path))

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    check("B2.1 文件里就是那一行密钥，没有别的内容", raw.strip() == secret, raw[:40])

    # B4：空串 = 删掉
    ns.set_secret("", path)
    check("B4.1 传空串等于删掉那份密钥", not ns.has_secret(path))
    check("B4.2 删掉之后 get_secret 返回空串（不抛）", ns.get_secret(path) == "")

    # B5：mask
    m = ns.mask_secret(secret)
    check("B5.1 mask_secret 不泄露中间部分",
          secret not in m and m.startswith("LTAI") and m.endswith("3456"), m)
    check("B5.2 短串整个打星", ns.mask_secret("abc") == "***")
    check("B5.3 手机号脱敏和 cred_store 一个形态",
          ns.mask_phone("13800138000") == "138****8000", ns.mask_phone("13800138000"))

    # B6：文件不在（上面 B4 已经删掉）就返回空串，不抛（调用方是旁路）
    check("B6.1 文件不在了 get_secret 返回空串（不抛）", ns.get_secret(path) == "")


# C. 频控

def group_c_cooldown(tmp):
    print("\n[C] 频控：抢票是死循环，没冷却一晚上几千条短信")

    state = os.path.join(tmp, "notify_state.json")
    real_state = nf.STATE_PATH
    nf.STATE_PATH = state
    try:
        cfg = ns.default_config()
        cfg["enabled"] = True
        cfg["cooldown_seconds"] = {ns.EVENT_FAILED: 600}
        call = {"n": 0}

        def fake(cfg, secret, fields, body, timeout):
            call["n"] += 1
            return True, "假的，没真发"

        real_aliyun = nf.PROVIDERS["aliyun"]
        nf.PROVIDERS["aliyun"] = fake
        cfg["provider"] = "aliyun"
        try:
            check("C1 一开始冷却已过（可以发）",
                  nf.cooldown_left(ns.EVENT_FAILED, "A1", cfg) == 0)

            ok, msg = nf.send_sync(ns.EVENT_FAILED, account="A1", cfg=cfg,
                                   reason="第一次")
            check("C2 第一条发出去了", ok, msg)
            check("C3 发完立刻再发 → 被冷却拦住", call["n"] == 1,
                  "真调用次数 {}".format(call["n"]))
            left = nf.cooldown_left(ns.EVENT_FAILED, "A1", cfg)
            check("C4 冷却剩余时间是正数", left > 0, "{:.0f} 秒".format(left))

            ok2, msg2 = nf.send_sync(ns.EVENT_FAILED, account="A1", cfg=cfg,
                                     reason="第二次")
            check("C5 冷却期内根本没调用 provider（不是发完才丢）",
                  call["n"] == 1, "真调用次数 {}".format(call["n"]))
            check("C6 并且如实说明是冷却中", "冷却" in msg2, msg2)

            # 换个账号不受影响（按 账号+事件 分别计时）
            ok3, _ = nf.send_sync(ns.EVENT_FAILED, account="A2", cfg=cfg)
            check("C7 换一个账号不受影响", ok3 and call["n"] == 2,
                  "真调用次数 {}".format(call["n"]))

            # 换个事件不受影响
            cfg["cooldown_seconds"][ns.EVENT_GRABBED] = 600
            ok4, _ = nf.send_sync(ns.EVENT_GRABBED, account="A1", cfg=cfg)
            check("C8 换一个事件不受影响（冷却按 事件+账号 分别计）",
                  ok4 and call["n"] == 3, "真调用次数 {}".format(call["n"]))

            # C9：发送失败要撤掉冷却标记，别让一次网络抖动堵掉十分钟
            nf.STATE_PATH = os.path.join(tmp, "state2.json")

            def failing(cfg, secret, fields, body, timeout):
                call["n"] += 1
                return False, "假装网络不通"

            nf.PROVIDERS["aliyun"] = failing
            ok5, _ = nf.send_sync(ns.EVENT_NEED_LOGIN, account="A3", cfg=cfg)
            check("C9.1 发送失败返回 False", ok5 is False)
            check("C9.2 失败不留冷却标记（下次还能立刻重试）",
                  nf.cooldown_left(ns.EVENT_NEED_LOGIN, "A3", cfg) == 0)

            # C10：冷却设 0 = 立刻可再发
            nf.STATE_PATH = os.path.join(tmp, "state3.json")
            nf.PROVIDERS["aliyun"] = fake
            cfg["cooldown_seconds"] = {ns.EVENT_FAILED: 0}
            nf.send_sync(ns.EVENT_FAILED, account="A4", cfg=cfg)
            ok6, _ = nf.send_sync(ns.EVENT_FAILED, account="A4", cfg=cfg)
            check("C10 冷却设成 0 时可以连着发", ok6)
        finally:
            nf.PROVIDERS["aliyun"] = real_aliyun
    finally:
        nf.STATE_PATH = real_state


# D. 开关

def group_d_switches(tmp):
    print("\n[D] 开关：关着的时候要真的什么都不做")

    real_state = nf.STATE_PATH
    nf.STATE_PATH = os.path.join(tmp, "d_state.json")
    call = {"n": 0}

    def fake(cfg, secret, fields, body, timeout):
        call["n"] += 1
        return True, "假的"

    real = nf.PROVIDERS.get("aliyun")
    nf.PROVIDERS["aliyun"] = fake
    try:
        cfg = ns.default_config()
        cfg["provider"] = "aliyun"

        cfg["enabled"] = False
        ok, msg = nf.send_sync(ns.EVENT_GRABBED, account="A", cfg=cfg)
        check("D1 总开关关着 → 不发，且 provider 一次都没被调",
              ok is False and call["n"] == 0, msg)

        cfg["enabled"] = True
        cfg["events"][ns.EVENT_STARTED] = False
        ok, msg = nf.send_sync(ns.EVENT_STARTED, account="A", cfg=cfg)
        check("D2 单个事件关着 → 不发，且 provider 没被调",
              ok is False and call["n"] == 0, msg)

        cfg["provider"] = "不存在的通道"
        ok, msg = nf.send_sync(ns.EVENT_GRABBED, account="A", cfg=cfg)
        check("D3 provider 名字不认识 → 返回 False 而不是抛异常", ok is False)
        check("D3.2 而且把可选项列出来",
              "console" in msg and "aliyun" in msg, msg)
    finally:
        if real is not None:
            nf.PROVIDERS["aliyun"] = real
        nf.STATE_PATH = real_state


# E. provider / 阿里云签名

class _FakeResponse(object):
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload


def group_e_aliyun(tmp):
    print("\n[E] 阿里云：签名算法 + 请求长什么样（用假 HTTP，不联网）")

    problems = nf.self_check()
    check("E1.1 签名自检通过（编码规则 / 排序 / 确定性）", not problems,
          "；".join(problems))

    # E2：缺配置时要说清缺什么，不能报一句网络错误
    cfg = ns.default_config()
    cfg["enabled"] = True
    cfg["provider"] = "aliyun"
    ok, msg = nf.PROVIDERS["aliyun"](cfg, "", nf.build_fields(
        ns.EVENT_GRABBED, "A"), "", 5)
    check("E2.1 没配好时返回失败", ok is False)
    for key in ("phone_numbers", "access_key_id", "AccessKeySecret",
                "sign_name", "template_code"):
        check("E2.2 报缺项时点了名：{}".format(key), key in msg, msg[:100])

    # E3：真发一条（URL 被替换成假的）
    cfg["phone_numbers"] = ["13800138000", "13900139000"]
    cfg["aliyun"].update({
        "access_key_id": "LTAI_FAKE_ID",
        "sign_name": "假签名",
        "template_code": "SMS_0000",
        "template_params": {ns.EVENT_GRABBED: {
            "account": "account", "show": "show", "time": "time"}},
    })

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        captured["headers"] = dict(req.headers or {})
        captured["timeout"] = timeout
        return _FakeResponse({"Code": "OK", "Message": "OK", "BizId": "1234^5678"})

    real_urlopen = nf.urllib.request.urlopen
    nf.urllib.request.urlopen = fake_urlopen
    try:
        fields = nf.build_fields(ns.EVENT_GRABBED, "Account 01",
                                 show="某场演唱会", city="北京")
        ok, msg = nf.PROVIDERS["aliyun"](cfg, "FAKE_SECRET", fields, "正文", 7)
        check("E3.1 正常路径返回成功", ok, msg)
        check("E3.2 打到的是 dysmsapi 端点",
              "dysmsapi.aliyuncs.com" in captured.get("url", ""),
              captured.get("url", ""))
        check("E3.3 超时用的是配置里的值", captured.get("timeout") == 7,
              str(captured.get("timeout")))

        body = captured.get("body", "")
        for need in ("Action=SendSms", "Version=2017-05-25",
                     "SignatureMethod=HMAC-SHA1", "Signature=",
                     "PhoneNumbers=", "SignName=", "TemplateCode=",
                     "TemplateParam=", "AccessKeyId="):
            check("E3.4 请求体里有 {}".format(need), need in body)

        check("E3.5 两个号码都带上了（逗号连接）",
              "13800138000%2C13900139000" in body or "13800138000,13900139000" in body,
              str([p for p in body.split("&") if p.startswith("PhoneNumbers")][:1]))

        # 参数必须按 key 排序（POP 签名规范），顺序错了对面会报 SignatureDoesNotMatch
        keys = [p.split("=", 1)[0] for p in body.split("&") if "=" in p]
        check("E3.6 参数按 key 排序（签名规范要求）", keys == sorted(keys),
              "、".join(keys[:5]))

        # 签名是算出来的，不是空的
        sig = dict(p.split("=", 1) for p in body.split("&") if "=" in p).get("Signature", "")
        check("E3.7 Signature 非空且是 base64", len(sig) > 20, sig[:20])

        # E4：阿里云报错时给人话
        def fake_err(req, timeout=None):
            return _FakeResponse({"Code": "isv.SMS_TEMPLATE_ILLEGAL",
                                  "Message": "template not exist"})

        nf.urllib.request.urlopen = fake_err
        ok, msg = nf.PROVIDERS["aliyun"](cfg, "FAKE_SECRET", fields, "正文", 5)
        check("E4.1 业务失败时返回 False", ok is False)
        check("E4.2 把错误码翻成人话（模板不对）",
              "模板" in msg and "isv.SMS_TEMPLATE_ILLEGAL" in msg, msg)

        # E5：网络异常，(False, 说明)，不抛
        def boom(req, timeout=None):
            raise OSError("网络不通")

        nf.urllib.request.urlopen = boom
        try:
            ok, msg = nf.PROVIDERS["aliyun"](cfg, "FAKE_SECRET", fields, "正文", 5)
            check("E5.1 网络异常时返回 False 而不是抛异常", ok is False)
            check("E5.2 并且说明了是连不上", "连不上" in msg, msg)
        except Exception:
            check("E5.1 网络异常时返回 False 而不是抛异常", False,
                  traceback.format_exc()[-120:])

        # E6：模板参数映射有问题时要明确报错，不能静默发一条残缺短信
        cfg2 = json.loads(json.dumps(cfg))
        cfg2["aliyun"]["template_params"] = {
            ns.EVENT_NEED_LOGIN: {"x": "___不存在的字段___"}}
        nf.urllib.request.urlopen = fake_urlopen
        fields2 = nf.build_fields(ns.EVENT_NEED_LOGIN, "A")
        ok, msg = nf.PROVIDERS["aliyun"](cfg2, "S", fields2, "正文", 5)
        check("E6.1 映射到不认识的字段名 → 明确报错", ok is False, msg[:110])
        check("E6.2 并且把可用字段列出来", "可用字段" in msg, msg[:110])

        # E6.3：映射的字段这次是空的（例如 need_login 场景下城市 / 票档都是空）
        cfg3 = json.loads(json.dumps(cfg))
        cfg3["aliyun"]["template_params"] = {
            ns.EVENT_NEED_LOGIN: {"x": "city", "y": "price"}}
        fields3 = nf.build_fields(ns.EVENT_NEED_LOGIN, "A")   # city / price 都是空
        ok, msg = nf.PROVIDERS["aliyun"](cfg3, "S", fields3, "正文", 5)
        check("E6.3 映射的值全是空 → 不发（不然是条残缺短信）", ok is False,
              msg[:110])
        check("E6.4 并且说清是「空」不是「网络错误」",
              "空" in msg, msg[:110])

        # E6.5：有值的字段照常能发（别把正常路径也拦掉了）
        fields4 = nf.build_fields(ns.EVENT_NEED_LOGIN, "Account 02",
                                  status="登录态失效", city="北京")
        ok, msg = nf.PROVIDERS["aliyun"](cfg3, "S", fields4, "正文", 5)
        check("E6.5 有值的字段照常发得出去", ok, msg[:110])
    finally:
        nf.urllib.request.urlopen = real_urlopen

    # E7：console 通道永远可用
    ok, msg = nf.PROVIDERS["console"](ns.default_config(), "", {}, "测试正文", 5)
    check("E7.1 console 通道不用任何配置也能用", ok, msg)


# F. 绝不打扰主流程

def group_f_never_bother(tmp):
    print("\n[F] 通知是旁路：不许抛、不许阻塞、不许改退出码")

    real_state = nf.STATE_PATH
    real = dict(nf.PROVIDERS)
    try:
        nf.STATE_PATH = os.path.join(tmp, "f_state.json")
        cfg = ns.default_config()
        cfg["enabled"] = True
        cfg["provider"] = "aliyun"
        cfg["cooldown_seconds"] = {}

        def exploding(cfg, secret, fields, body, timeout):
            raise RuntimeError("provider 故意炸了")

        nf.PROVIDERS["aliyun"] = exploding
        try:
            ok, msg = nf.send_sync(ns.EVENT_GRABBED, account="A", cfg=cfg)
            check("F1.1 provider 抛异常时 send_sync 不抛，返回 False", ok is False)
            check("F1.2 而且说了是发送时出错", "出错" in msg, msg[:80])
        except Exception:
            check("F1.1 provider 抛异常时 send_sync 不抛，返回 False", False,
                  traceback.format_exc()[-120:])

        try:
            out = nf.notify(ns.EVENT_GRABBED, account="A", wait=True, cfg=cfg)
            check("F2.1 notify(wait=True) 遇到 provider 炸了也不抛", True)
            check("F2.2 并且如实返回 (False, 说明)", out[0] is False, str(out)[:80])
        except Exception:
            check("F2.1 notify(wait=True) 遇到 provider 炸了也不抛", False,
                  traceback.format_exc()[-120:])

        # F3：默认异步，不能阻塞
        def slow(cfg, secret, fields, body, timeout):
            time.sleep(1.5)
            return True, "慢"

        nf.PROVIDERS["aliyun"] = slow
        t0 = time.time()
        nf.notify(ns.EVENT_GRABBED, account="A", cfg=cfg)
        spent = time.time() - t0
        check("F3.1 notify() 默认不阻塞（耗时 < 0.3 秒）", spent < 0.3,
              "{:.3f} 秒".format(spent))

        # F4：状态文件写不了也不能抛
        nf.STATE_PATH = os.path.join(tmp, "不存在的目录", "x", "state.json")
        nf.PROVIDERS["aliyun"] = lambda *a, **k: (True, "ok")
        try:
            ok, msg = nf.send_sync(ns.EVENT_GRABBED, account="A", cfg=cfg)
            check("F4.1 状态文件写不进去也不抛（只影响频控）", True, msg)
        except Exception:
            check("F4.1 状态文件写不进去也不抛（只影响频控）", False,
                  traceback.format_exc()[-120:])

        # F5：notify() 参数烂掉也不抛
        try:
            nf.notify("__不存在的事件__", account=None, cfg=cfg, wait=True)
            check("F5.1 事件名不认识也不抛", True)
        except Exception:
            check("F5.1 事件名不认识也不抛", False,
                  traceback.format_exc()[-120:])
    finally:
        nf.PROVIDERS.clear()
        nf.PROVIDERS.update(real)
        nf.STATE_PATH = real_state


# G. 挂点（静态检查：通知真的接在架构上了）

def group_g_hooks():
    print("\n[G] 挂点：通知真的接在各个关键位置上了吗")

    def read(name):
        with open(os.path.join(WEB, name), "r", encoding="utf-8") as f:
            return f.read()

    runner = read("account_runner.py")
    check("G1.1 account_runner：抢到票时发通知（notify_payment 里）",
          "_notify(EV_GRABBED" in runner)
    check("G1.2 account_runner：出错时发通知", "_notify(EV_FAILED" in runner)
    check("G1.3 account_runner：登录态失效时发通知",
          "_notify(EV_NEED_LOGIN" in runner)
    check("G1.4 account_runner：开抢时发通知（默认关）",
          "_notify(EV_STARTED" in runner)
    check("G1.5 抢到票那条必须是 wait=True（进程马上要退了）",
          "_notify(EV_GRABBED" in runner
          and "wait=True" in runner.split("_notify(EV_GRABBED", 1)[1][:200])
    check("G1.6 有 notify 子命令（看配置 / 存密钥 / 发测试）",
          'sub.add_parser("notify"' in runner and "def cmd_notify(" in runner)
    check("G1.7 import notify 是兜着的（漏打进去也不该拖垮抢票）",
          "import notify as notifier" in runner and "notifier = None" in runner)

    # 授权体系的 `machine` / `days` 两个字段已经删了（`FIELDS` 里没有）。`build_fields` 用
    #   `kw.get` 取值，多传一个不存在的字段不会报错，只会静默发一条少了两项的短信 ——
    #   2026-10-31 就漏了 `notify --test` 那一处，靠肉眼看没发现。这里按语法树查调用点。
    import ast
    stale, sites = [], 0
    tree = ast.parse(runner)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", "") or getattr(fn, "id", "")
        if "send" not in name and name != "_notify":
            continue
        sites += 1
        stale += ["{}() 传了 {}".format(name, kw.arg)
                  for kw in node.keywords if kw.arg in ("machine", "days")]
    check("G1.8 通知调用点没有还在传已删的字段（machine / days）",
          not stale, "；".join(stale) or "共查了 {} 处".format(sites))
    check("G1.9 上面那条不是空转（真的数到了调用点）", sites >= 2, str(sites))

    tm = read("task_manager.py")
    check("G2.1 task_manager：终态跃迁时发通知（面板那条路的兜底）",
          "_notify_terminal" in tm and "自._settle" or "_notify_terminal(aid, t)" in tm)
    check("G2.2 只在状态真的变了的时候发（不然每次刷新都会发）",
          "prev_state != t.state" in tm)

    wiz = read("damai_wizard.py")
    spec_path = os.path.join(os.path.dirname(WEB), "packaging", "damai_app.spec")
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = f.read()
    for mod in ("notify", "notify_settings"):
        check("G4.1 打包配置里列了 {}".format(mod),
              '"{}",'.format(mod) in spec)
    for mod in ("notify", "notify_settings"):
        check("G4.2 随包自检清单里也有 {}".format(mod),
              '"{}"'.format(mod) in wiz)


# H. 隔离：所有文件都在数据目录里

def group_h_isolation():
    print("\n[H] 文件落点：三个文件都该在数据目录（跟着 exe 走）")

    for label, path in (("notify.json", ns.CONFIG_PATH),
                        ("notify_secret.txt", ns.SECRET_PATH),
                        ("notify_state.json", nf.STATE_PATH)):
        same = os.path.dirname(os.path.abspath(path)) == \
            os.path.dirname(os.path.abspath(ns.CONFIG_PATH))
        check("H1 {} 和数据文件放一起".format(label), same, path)


# I. 通知必须真的落进 accounts.log

def group_i_logfile(tmp):
    """这一组是为一个真 bug 写的。

    `_log()` 最初把账号号字符串传给了 `accounts.say(msg, account)`，
    而那个参数要的是账号 dict（内部 `account.get("account_id")`），
    于是每次都抛 `AttributeError`，被 except 吞掉，退化成一句普通 `print`。

    表现：控制台上看得见通知，`accounts.log` 里一个字都没有。
    而客户报障时发的正是 `accounts.log`，等于「通知到底发没发」查不了。
    所以这里真去读文件，不看控制台。
    """
    print("\n[I] 通知要真的落进 accounts.log（客户报障发的是这个文件）")

    import accounts as acc_store

    nf._log("这是一条测试通知日志", "Account 09")
    with open(acc_store.LOG_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    check("I1 通知真的写进了 accounts.log",
          "这是一条测试通知日志" in text, text.strip()[-110:])
    check("I2 带账号时前缀是 [账号号]（和别的日志一个格式）",
          "[Account 09]" in text, text.strip()[-110:])

    nf._log("没有账号的通知")
    with open(acc_store.LOG_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    check("I3 不带账号时也能写", "没有账号的通知" in text)

    # I4：整条链路，走 send_sync 之后日志里要留下记录
    real_state = nf.STATE_PATH
    nf.STATE_PATH = os.path.join(tmp, "i_state.json")
    try:
        cfg = ns.default_config()
        cfg["enabled"] = True
        cfg["provider"] = "console"
        cfg["cooldown_seconds"] = {}
        nf.send_sync(ns.EVENT_GRABBED, account="Account 09", cfg=cfg,
                     show="某场演出")
    finally:
        nf.STATE_PATH = real_state
    with open(acc_store.LOG_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    check("I4 走 send_sync 那条路也会在两个地方都留痕",
          "抢到票" in text and "某场演出" in text, text.strip()[-110:])
    check("I5 日志里没有完整手机号（脱敏规矩）",
          "13800138000" not in text)
    check("I6 日志里没有密钥（万一以后有人顺手打进去）",
          "FAKE_SECRET" not in text)


# K. 真调一次挂点：抢到票时通知有没有真的发出去

def group_k_hook_really_fires(tmp):
    """静态检查（G 组）只能证明「代码里有这行」。

    这一组更进一步：拿一个假的 `con` 直接调 `account_runner.notify_payment()`，
    看它到底有没有走进通知。这是唯一一条「丢了就白抢」的通知，
    值得单独钉一次。

    需要 selenium（`account_runner` 顶层就 import 它），
    所以跑在没有 selenium 的解释器上时整组跳过。
    """
    print("\n[K] 真调一次 notify_payment()，看通知有没有发出去")

    try:
        sys.path.insert(0, WEB)
        import account_runner
    except Exception as exc:
        skip("K 组（挂点实测）", "import account_runner 失败：{}".format(exc))
        return

    real_beep = account_runner.beep
    caught = []
    real_notify = nf.notify
    try:
        account_runner.beep = lambda *a, **k: None      # 别在测试里真的响

        def fake_notify(event, account=None, wait=False, cfg=None, **kw):
            caught.append({"event": event, "account": account,
                           "wait": wait, "kw": kw})
            return True, "测试用的假发送"

        nf.notify = fake_notify

        class _FakeDriver(object):
            title = "订单确认页-大麦网"
            current_url = "https://m.damai.cn/shows/order-pay.html"

        class _FakeCon(object):
            driver = _FakeDriver()

            def _is_payment_page(self):
                return True

        acc = {"account_id": "Account 07", "prices": ["380"], "city": "北京"}
        account_runner.notify_payment(_FakeCon(), acc)

        check("K1 notify_payment 真的调了通知（不是写在注释里）", len(caught) == 1,
              "调用次数 {}".format(len(caught)))
        if caught:
            c = caught[0]
            check("K2 事件是 grabbed", c["event"] == ns.EVENT_GRABBED, c["event"])
            check("K3 账号号带上了", c["account"] == "Account 07", str(c["account"]))
            check("K4 wait=True（进程马上要退，后台线程会被收走）",
                  c["wait"] is True, str(c["wait"]))
            check("K5 短信里带上了演出名（好判断要不要马上处理）",
                  "订单确认页" in str(c["kw"].get("show", "")),
                  str(c["kw"].get("show")))
            check("K6 带上了停在支付页这个状态",
                  "支付" in str(c["kw"].get("status", "")),
                  str(c["kw"].get("status")))
            check("K7 带上了票档", str(c["kw"].get("price")) == "380",
                  str(c["kw"].get("price")))

        # K8：不在支付页时状态要如实说
        del caught[:]

        class _FakeCon2(_FakeCon):
            def _is_payment_page(self):
                return False

        account_runner.notify_payment(_FakeCon2(), acc)
        check("K8 流程结束但没停在支付页时，状态如实改口",
              len(caught) == 1 and "支付" not in str(caught[0]["kw"].get("status")),
              str(caught[0]["kw"].get("status")) if caught else "没调到")

        # K9：通知炸了也不能影响 notify_payment 的收尾（beep / 日志）
        del caught[:]
        reached = {"beep": 0}

        def boom(*a, **k):
            raise RuntimeError("通知故意炸了")

        nf.notify = boom

        def counted_beep(*a, **k):
            reached["beep"] += 1

        account_runner.beep = counted_beep
        try:
            account_runner.notify_payment(_FakeCon(), acc)
            check("K9.1 通知炸了也不影响 notify_payment 走完（不抛）", True)
            check("K9.2 而且后面的提醒（beep）照常执行", reached["beep"] == 1,
                  "beep 次数 {}".format(reached["beep"]))
        except Exception:
            check("K9.1 通知炸了也不影响 notify_payment 走完（不抛）", False,
                  traceback.format_exc()[-140:])
    finally:
        nf.notify = real_notify
        account_runner.beep = real_beep



def main():
    print("=" * 62)
    print("短信通知自检")
    print("=" * 62)

    tmp = tempfile.mkdtemp(prefix="notify-check-")
    # 全程把路径改到临时目录，绝不碰 web/ 下真实文件。accounts.LOG_PATH 也要改：通知走
    #   accounts.say 落盘，不拦就会往真实 accounts.log 里写一堆测试行。
    import accounts as acc_store
    real_cfg, real_secret = ns.CONFIG_PATH, ns.SECRET_PATH
    real_state = nf.STATE_PATH
    real_log = acc_store.LOG_PATH
    # 先记住真实文件长什么样，再改路径，顺序反了就记成临时文件的了，
    #   那样这条收尾自证会变成恒真（它第一次就是这么写错的）。
    _remember_real_files()
    ns.CONFIG_PATH = os.path.join(tmp, "notify.json")
    ns.SECRET_PATH = os.path.join(tmp, "notify_secret.txt")
    nf.STATE_PATH = os.path.join(tmp, "notify_state.json")
    acc_store.LOG_PATH = os.path.join(tmp, "accounts.log")
    try:
        group_a_config(tmp)
        group_b_secret(tmp)
        group_c_cooldown(tmp)
        group_d_switches(tmp)
        group_e_aliyun(tmp)
        group_f_never_bother(tmp)
        group_g_hooks()
        group_h_isolation()
        group_i_logfile(tmp)
        group_k_hook_really_fires(tmp)
    finally:
        ns.CONFIG_PATH, ns.SECRET_PATH = real_cfg, real_secret
        nf.STATE_PATH = real_state
        acc_store.LOG_PATH = real_log
        # 收尾自证：真实文件一个都没被动过（按内容哈希比，不看 mtime）
        _verify_untouched(real_log)
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 62)
    if bad:
        print("失败 {} 项：".format(len(bad)))
        for n in bad:
            print("  - " + n)
    else:
        print("全部通过（{} 项）".format(len(_results)))
    print("=" * 62)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
