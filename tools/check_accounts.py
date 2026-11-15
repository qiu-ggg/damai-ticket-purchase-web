# -*- coding: UTF-8 -*-
"""多账号登录状态管理，本地验证套件。

分两部分：

  A 段（不需要浏览器）：账号清单 / 字段完整性 / Profile 隔离 / 启用禁用 /
                        路径穿越防护 / 支付守卫 / 日志格式 / 密码不落明文。
  B 段（真 Chrome + 本地假页面）：人工验证识别、登录态识别、
                        跨账号 Cookie 隔离、登录状态可复用。
  C 段（真 Chrome + 本地假登录页）：凭据存取、自动填表登录、
                        有验证时不填表、没凭据时安静退回手动登录。
C 段全程把 cred_store 指到临时目录，不碰你真实的
                           credentials.json。

目标演出通常没开票，也不该拿真实账号反复试，所以这里全部用本地假页面
复刻行为。真实站点上的最终确认仍要靠你自己跑一次 login。

用法（在仓库根目录）：
    python tools/check_accounts.py
退出码 0 = 全过。
"""
import argparse
import http.server
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）
FIX = os.path.join(HERE, "fixtures", "accounts")

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "damai"))
sys.path.insert(0, ROOT)

from chromedriver_locator import relax_local_proxy                    # noqa: E402

# 必须赶在建 driver 之前：本机全局代理会劫走发往本地 ChromeDriver 的请求，症状是
#   每个 driver.xxx 都报 "Message: unhandled request"，看着像驱动坏了，其实是被劫持。
relax_local_proxy()

import accounts as acc_store                                          # noqa: E402
import account_runner as ar                                           # noqa: E402
import cred_store                                                     # noqa: E402
from chromedriver_locator import ensure_chromedriver_env              # noqa: E402
from check_environment import get_chromedriver_path                   # noqa: E402
from selenium import webdriver                                        # noqa: E402
from selenium.webdriver.common.by import By                            # noqa: E402

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  " + extra) if extra else ""))


def url_of(name):
    return "file:///" + os.path.join(FIX, name).replace("\\", "/")


# 趁没人打过补丁先留一份真的 sleep：本地服务里「故意慢 0.6 秒」得用真的睡，不能用被
#   _SleepRecorder 包过的那个，否则那 0.6 秒会被记成「代码在固定 sleep」搞坏断言。
_REAL_SLEEP = time.sleep


class _SleepRecorder(object):
    """记下这段时间里 sleep 都被要了多久。

    性能修复判「有没有睡一大段」而不是「总耗时多少毫秒」：总耗时会被 Chrome 冷启动、
      机器负载、网络抖动带偏，卡时间阈值容易变成偶发失败；而「有没有人固定睡一大段」
      是确定性的，那行 sleep 要么在、要么不在。和 a12 那条「完全不跑 --version 子进程」
      一样：断言机制，不赌时间。
    """

    def __enter__(self):
        self.calls = []
        self._real = time.sleep

        def spy(secs):
            self.calls.append(float(secs))
            return self._real(secs)

        time.sleep = spy
        return self

    def __exit__(self, *exc):
        time.sleep = self._real
        return False

    @property
    def longest(self):
        return max(self.calls) if self.calls else 0.0

    def describe(self):
        if not self.calls:
            return "（一次都没睡）"
        uniq = sorted(set(round(c, 3) for c in self.calls))
        return "睡过的时长：%s" % uniq


# 给数据层用的临时账号

TEST_A = {"account_id": "__test_iso_a", "profile_dir": "profiles/__test_iso_a",
          "target_url": "https://detail.damai.cn/item.htm?id=1", "city": "北京",
          "dates": ["2026-12-05"], "prices": ["380", "580"], "quantity": 1,
          "users": ["王小明"], "enabled": True, "if_commit_order": False,
          "max_retries": 10000}
TEST_B = dict(TEST_A, account_id="__test_iso_b", profile_dir="profiles/__test_iso_b")


def drop_profiles(*accounts):
    for a in accounts:
        try:
            acc_store.forget_login_state(a)
        except Exception:
            pass


# A 段

def a1_password_never_plaintext():
    """需求 1：账号清单里绝不能有密码；密码只躺在 credentials.json（明文，不加密）。

    （注意：这一条在用户明确要求「界面里填账号密码 + 自动登录」之后改写过。
      原来的断言是"全项目不许出现 send_keys"，现在是"密码不许落明文"。）
    """
    print("\n[A1] 密码不进账号清单：只在 credentials.json 里")

    forbidden = ("password", "passwd", "pwd", "secret", "credential", "phone")
    keys = set(acc_store.BLANK_ACCOUNT.keys())
    for a in acc_store.TEMPLATE["accounts"]:
        keys |= set(a.keys())
    bad = [k for k in keys if any(f in k.lower() for f in forbidden)]
    check("账号字段定义里没有密码 / 手机号字段", not bad, str(bad))

    data = acc_store.load()
    bad2 = []
    for a in acc_store.all_accounts(data):
        bad2 += [k for k in a if any(f in k.lower() for f in forbidden)]
    check("实盘 accounts.json 里也没有密码类字段", not bad2, str(bad2))

    src_accounts = open(os.path.join(ROOT, "accounts.py"), encoding="utf-8").read()
    check("数据层 accounts.py 里根本不出现 password 字样",
          "password" not in src_accounts.lower())

    src_run = open(os.path.join(ROOT, "account_runner.py"), encoding="utf-8").read()
    body = src_run[src_run.index("def auto_fill_login"):src_run.index("def _try_auto_login")]
    check("要填密码只能从凭据文件里取（cred_store.get）",
          "cred_store.get(" in body)
    check("填表过程没有把密码打印出去的语句",
          not [l for l in body.splitlines()
               if ("say(" in l or "print(" in l) and "password" in l])

    check("模板里没有规避平台限制的开关（代理/轮换/指纹）",
          not any(
              any(w in k.lower() for k in keys)
              for w in ("proxy", "rotate", "fingerprint", "sign"))
          )


def a2_account_fields_complete():
    """需求 6：每个账号该有的字段一个不少。"""
    print("\n[A2] 账号字段完整性（需求 6 那 9 项）")
    required = {
        "account_id": "account_id",
        "note": "备注",
        "profile_dir": "登录状态保存位置",
        "target_url": "目标演出",
        "city": "城市",
        "dates": "场次",
        "prices": "票档优先级",
        "quantity": "购买数量",
        "users": "观演人",
    }
    missing = [k for k in required if k not in acc_store.BLANK_ACCOUNT]
    check("9 项字段全部存在", not missing, str(missing))

    data = acc_store.load()
    for a in acc_store.all_accounts(data):
        gap = [k for k in required if k not in a]
        check("[{}] 字段齐全".format(a["account_id"]), not gap, str(gap))


def a3_profile_isolation_paths():
    """需求 4：每个账号一个独立 Profile 目录，路径必须互不相同。"""
    print("\n[A3] Profile 目录隔离")
    pa, pb = acc_store.profile_path(TEST_A), acc_store.profile_path(TEST_B)
    check("两个账号的 Profile 目录不同", pa != pb)
    check("A 的目录路径里含自己的账号名", "__test_iso_a" in pa, pa)
    check("B 的目录路径里含自己的账号名", "__test_iso_b" in pb, pb)
    check("默认位置落在 profiles/ 下",
          os.path.abspath(pa).startswith(os.path.abspath(acc_store.PROFILES_ROOT)))

    # 同一个账号重复取，必须稳定（否则每次换目录 = 每次都要重新登录）
    check("同一账号多次解析结果稳定", acc_store.profile_path(TEST_A) == pa)


def a4_driver_uses_own_profile():
    """需求 4：build_driver 真的把账号自己的目录塞进 --user-data-dir。"""
    print("\n[A4] 启动浏览器时用的是本账号的 Profile")
    real = ar.webdriver.Chrome
    captured = []

    class FakeChrome(object):
        def __init__(self, service=None, options=None):
            captured.append(list(getattr(options, "arguments", [])))
            raise RuntimeError("capture-only")

    ar.webdriver.Chrome = FakeChrome
    try:
        for acc in (TEST_A, TEST_B):
            try:
                ar.build_driver(acc)
            except RuntimeError:
                pass
    finally:
        ar.webdriver.Chrome = real

    check("两个账号各启动过一次浏览器", len(captured) == 2, str(len(captured)))
    if len(captured) == 2:
        arg_a = [a for a in captured[0] if a.startswith("--user-data-dir=")]
        arg_b = [a for a in captured[1] if a.startswith("--user-data-dir=")]
        check("A 带了 --user-data-dir", bool(arg_a), str(arg_a))
        check("B 带了 --user-data-dir", bool(arg_b), str(arg_b))
        check("两者不是同一个目录（账号隔离）",
              bool(arg_a) and bool(arg_b) and arg_a[0] != arg_b[0])
        check("目录就是该账号 profile_dir 的解析结果",
              bool(arg_a) and arg_a[0].endswith(acc_store.profile_path(TEST_A).split(os.sep)[-1]))
    drop_profiles(TEST_A, TEST_B)


def a5_toggle_persists():
    """需求 7：启用/禁用要能落盘。

    这一节必须自己把日志也指到临时目录。
      `cmd_toggle` 内部会 `acc_store.say()`，也就是说它会往
      `accounts.log` 里写「[Account 02] 已启用 / 已禁用」这种只有测试才会有的行。
      只重定向 ACCOUNTS_PATH 是不够的，真踩过：跑一次 A 段，
      用户真实的 accounts.log 里就多出「未找到 accounts.json，已生成模板：
      ...\\Temp\\wb_acc_xxx\\accounts.json」这样一眼假的东西。
      规矩：会写日志的那一节自己重定向，不依赖 main() 或调用方。
    """
    print("\n[A5] 启用 / 禁用账号")
    orig = acc_store.ACCOUNTS_PATH
    orig_log = acc_store.LOG_PATH
    tmpdir = tempfile.mkdtemp(prefix="wb_acc_")
    tmp = os.path.join(tmpdir, "accounts.json")
    _tmp_log = os.path.join(tmpdir, "accounts.log")
    # 先记下真实日志的 (size, mtime)，末尾自查「一个字节都没动」
    try:
        _real_stat = (os.path.getsize(orig_log), os.path.getmtime(orig_log))
    except OSError:
        _real_stat = None

    acc_store.ACCOUNTS_PATH = tmp
    acc_store.LOG_PATH = _tmp_log
    try:
        acc_store.load()                       # 生成模板
        check("模板里 Account 02 默认禁用",
              acc_store.find(acc_store.load(), "Account 02")["enabled"] is False)

        ar.cmd_toggle(argparse.Namespace(ids=["Account 02"]), True)
        check("启用后写回了文件",
              ar.acc_store.find(ar.acc_store.load(), "Account 02")["enabled"] is True)

        ar.cmd_toggle(argparse.Namespace(ids=["Account 02"]), False)
        check("禁用后写回了文件",
              acc_store.find(acc_store.load(), "Account 02")["enabled"] is False)

        check("禁用的账号不进 enabled_accounts",
              all(a["account_id"] != "Account 02" for a in acc_store.enabled_accounts(acc_store.load())))
        check("cmd_toggle 的日志落在临时目录（没往你真实日志里灌）",
              os.path.exists(_tmp_log), _tmp_log)
    finally:
        acc_store.ACCOUNTS_PATH = orig
        ar.acc_store.ACCOUNTS_PATH = orig
        acc_store.LOG_PATH = orig_log
        ar.acc_store.LOG_PATH = orig_log
        shutil.rmtree(tmpdir, ignore_errors=True)
        if _real_stat is not None:
            try:
                same = (os.path.getsize(orig_log), os.path.getmtime(orig_log)) == _real_stat
            except OSError:
                same = False
            check("这一节没有碰你真实的 accounts.log", same,
                  "" if same else "文件被改动了")


def a6_forget_is_guarded():
    """需求 5/14：删登录状态只能删 profiles 里的东西，别的地方一律拒绝。"""
    print("\n[A6] 删除登录状态的安全边界")
    outside = {"account_id": "evil", "profile_dir": os.path.join(tempfile.gettempdir(), "wb_must_not_delete")}
    try:
        acc_store.forget_login_state(outside)
        refused = False
    except ValueError:
        refused = True
    check("拒绝删除 profiles 之外的目录", refused)

    try:
        acc_store.forget_login_state({"account_id": "root", "profile_dir": "profiles"})
        refused_root = False
    except ValueError:
        refused_root = True
    check("拒绝把 profiles 根目录整个删掉", refused_root)

    d = acc_store.ensure_profile_dir(TEST_A)
    os.makedirs(os.path.join(d, "Default"), exist_ok=True)
    open(os.path.join(d, "Default", "Cookies"), "w").close()
    check("profile 建立后检测到已有登录状态", acc_store.login_state_exists(TEST_A))
    check("删除返回成功", acc_store.forget_login_state(TEST_A) is True)
    check("目录确实没了", not os.path.isdir(d))
    check("没登录状态时删除返回 False（幂等）", acc_store.forget_login_state(TEST_A) is False)


def a7_validate_rules():
    """需求 6：数量与观演人必须对得上。"""
    print("\n[A7] 配置校验")
    check("登录前只要求 account_id（首次登录还没填演出信息）",
          acc_store.validate({"account_id": "X"}, for_login=True) == [])

    bad = dict(TEST_A, quantity=3, users=["只有一个人"])
    errs = acc_store.validate(bad)
    check("抓到「购票数 > 观演人数」", any("观演人数量" in e for e in errs), str(errs))

    check("抓到空 target_url", any("target_url" in e for e in acc_store.validate(dict(TEST_A, target_url=""))))
    # 回归：票档校验的尺子必须和运行时（concert._price_int_list）一致。曾经只认
    #   `isdigit()`，「全价票188元（站席）」被判「配置不完整」跳过账号，运行时其实能匹配。
    check("抓到一个数字都没有的票档",
          any("找不到票价数字" in e for e in acc_store.validate(dict(TEST_A, prices=["内场"]))))
    check("放行票价页原文（全价票188元（站席））",
          acc_store.validate(dict(TEST_A, prices=["全价票188元（站席）"])) == [],
          str(acc_store.validate(dict(TEST_A, prices=["全价票188元（站席）"]))))
    check("放行带前缀/后缀的票价（¥680 / 680元）",
          acc_store.validate(dict(TEST_A, prices=["¥680", "680元"])) == [])
    check("放行数字型票档（int / float）",
          acc_store.validate(dict(TEST_A, prices=[188, 288])) == []
          and acc_store.validate(dict(TEST_A, prices=[188.0])) == [])
    check("取数口径：取第一个数字串，取不出返回 None",
          acc_store.price_number("全价票188元（站席）") == 188
          and acc_store.price_number("") is None
          and acc_store.price_number(None) is None
          and acc_store.price_number("内场") is None)
    check("抓到重复的 account_id",
          any("重复" in e for e in acc_store.validate(TEST_A, other_ids=["__test_iso_a"])))
    check("合法配置无错", acc_store.validate(TEST_A) == [], str(acc_store.validate(TEST_A)))


def a8_config_mapping():
    """需求 6/10：账号配置，Config，且默认不自动付款。"""
    print("\n[A8] 账号配置映射到原仓库 Config")
    cfg = ar.build_config(dict(TEST_A, quantity=1, users=["王小明", "另一个人"]))
    check("quantity 决定实际购票人数（只取前 N 位观演人）", cfg.users == ["王小明"], str(cfg.users))
    # quantity 必须单独传进 Config：选座页在下单前按它决定「选几个座位」，那时观演人
    #   还没勾、没法反推；只靠 cfg.users 长度猜，users 裁剪逻辑一变就会少选/多选。
    check("quantity 自己也传进了 Config（选座页要靠它决定选几个座位）",
          cfg.quantity == 1, str(getattr(cfg, "quantity", "<没有这个属性>")))
    cfg2 = ar.build_config(dict(TEST_A, quantity=2, users=["甲", "乙", "丙"]))
    check("quantity=2 时 Config 里也是 2", cfg2.quantity == 2, str(cfg2.quantity))
    check("票档保住优先级顺序", cfg.prices == ["380", "580"], str(cfg.prices))
    check("场次正确", cfg.dates == ["2026-12-05"], str(cfg.dates))
    check("城市正确", cfg.city == "北京")
    check("目标演出正确", cfg.target_url == TEST_A["target_url"])
    check("Cookie 文件也按账号隔离", "profiles" in (cfg.cookie_file or ""), str(cfg.cookie_file))
    check("默认不自动提交订单", cfg.if_commit_order is False)

    tpl_flag = acc_store.TEMPLATE["accounts"][0]["if_commit_order"]
    check("模板默认也是不自动提交订单", tpl_flag is False)
    check("账号里没有出现任何「自动支付」开关",
          not any("pay" in k.lower() for k in acc_store.BLANK_ACCOUNT))


def a9_log_format():
    """需求 11：日志形如 [Account 01] 登录状态有效。"""
    print("\n[A9] 日志格式")
    orig = acc_store.LOG_PATH
    tmp = os.path.join(tempfile.mkdtemp(prefix="wb_log_"), "accounts.log")
    acc_store.LOG_PATH = tmp
    try:
        line = acc_store.say("登录状态有效", {"account_id": "Account 01"})
        check("控制台行格式正确", line == "[Account 01] 登录状态有效", line)
        check("日志文件同步写入", open(tmp, encoding="utf-8").read().strip().endswith(line))
        check("tag() 格式正确", acc_store.tag({"account_id": "Account 03"}) == "[Account 03]")
    finally:
        acc_store.LOG_PATH = orig
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)


def a10_no_hardcoded_credentials():
    """需求 1：源码里不该出现手机号/邮箱这类会被误填的硬编码凭据。"""
    print("\n[A10] 源码里没有硬编码凭据")
    import re
    phone = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    mail = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    for path in ("accounts.py", "account_runner.py", "cred_store.py"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        check("{} 里没有写死的手机号".format(path), not phone.search(src),
              str(phone.search(src).group(0) if phone.search(src) else ""))
        hits = [m for m in mail.findall(src) if "example" not in m]
        check("{} 里没有写死的邮箱账号".format(path), not hits, str(hits))


def a11_profile_busy_is_explained():
    """Profile 被上一个浏览器占着时，要给一句能看懂的话，而不是裸的 Selenium 异常。

    真站事故（2026-10-07）：这套脚本的设计是「提交成功后把浏览器留在支付环节，
    让你自己付」。你支付完如果没关那个窗口，它会一直占着这个账号的 Profile 目录。
    下次再跑时，新起的 Chrome 发现 Profile 已被占用，会把请求转交给已有的那个
    实例、然后自己退出，chromedriver 只看到「进程没了」，于是抛出：

        Message: session not created: Chrome instance exited.

    这句话完全指不到真正的原因（Profile 被占用），用户只会以为程序坏了。

    实测的现场：那个孤儿 Chrome（父进程早已退出的自动化实例）从 04:30 一直挂到
    07:00，占着 profiles\\Account_01；Default/Network/Cookies 被它锁住，
    连复制一份都报 WinError 32。
    """
    print("\n[A11] Profile 被上一个浏览器占用时的处理")
    import ctypes
    from ctypes import wintypes

    # 这一节会调 ar.build_driver()，它内部用 acc_store.say() 写日志。别把安全性寄托在
    #   调用方：直接 import 本模块、单独调 a11() 的脚本会漏掉重定向，往真实 accounts.log 灌假记录。
    _orig_log = acc_store.LOG_PATH
    _real_stat = None
    try:
        _st = os.stat(_orig_log)
        _real_stat = (_st.st_size, _st.st_mtime_ns)
    except OSError:
        pass
    _tmp_log = tempfile.mkdtemp(prefix="wb_a11log_")
    acc_store.LOG_PATH = os.path.join(_tmp_log, "accounts.log")

    tmp = tempfile.mkdtemp(prefix="wb_busy_")
    prof = os.path.join(tmp, "prof")                 # 用绝对路径，绕开 BASE_DIR 拼接
    os.makedirs(os.path.join(prof, "Default", "Network"))
    cookies = os.path.join(prof, "Default", "Network", "Cookies")
    with open(cookies, "wb") as f:
        f.write(b"SQLite format 3\x00" + b"\x00" * 64)

    try:
        check("前提：没人占用时不该误报", ar.chrome_profile_in_use(prof) is False)

        # 用一个完全独占的句柄占住它，复刻 Chrome 锁 Profile 库文件的方式
        # （share mode = 0，别的进程连打开都会失败）
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL = 3, 0x80
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.CreateFileW(cookies, GENERIC_READ | GENERIC_WRITE, 0, None,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        bad = (None, 0, -1, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF)
        check("前提：造得出「被独占占用」的状态（复刻 Chrome 锁文件）",
              h not in bad, str(h))

        if h not in bad:
            try:
                check("认得出这个 Profile 正被占用",
                      ar.chrome_profile_in_use(prof) is True)

                real_chrome = ar.webdriver.Chrome
                real_close = ar.close_own_chrome
                called = []
                ar.webdriver.Chrome = lambda *a, **k: called.append(1)
                ar.close_own_chrome = lambda p: 0        # 假装关不掉，走兜底分支
                try:
                    msg = ""
                    try:
                        ar.build_driver(dict(TEST_A, profile_dir=prof))
                    except RuntimeError as e:
                        msg = str(e)
                    except Exception as e:
                        msg = "抛的是 {}：{}".format(type(e).__name__, e)
                    flat = msg.replace("\n", " ")
                    check("抛的是带中文说明的 RuntimeError，而不是裸的 Selenium 异常",
                          isinstance(msg, str) and "仍被占用" in msg,
                          flat[:110])
                    check("说明里告诉用户该怎么办",
                          "请手动关掉" in msg, flat[:110])
                    check("说明里给出了具体路径（好照着去任务管理器找）",
                          prof in msg or os.path.basename(prof) in msg)
                    check("占用状态下不会硬着头皮去启动 Chrome", called == [])
                finally:
                    ar.webdriver.Chrome = real_chrome
                    ar.close_own_chrome = real_close
            finally:
                k32.CloseHandle(h)

        check("释放之后不再报占用", ar.chrome_profile_in_use(prof) is False)

        # 清理逻辑的安全边界：绝不能无差别杀 chrome 进程
        src = open(os.path.join(ROOT, "account_runner.py"), encoding="utf-8").read()
        # 「代码里不该出现 X」的断言要匹配调用/语法形式，不能匹配裸字面量，否则注释里
        #   对 X 的说明本身会被判成违规（taskkill、re.escape 都踩过）。
        import re as _re
        check("清理只认 --user-data-dir 指向本账号 Profile 的进程",
              "--user-data-dir" in src and "-match" in src)
        # 匹配要卡在路径边界：否则 Account_01 会连带认领 Account_01_backup，关掉就是事故。
        check("匹配卡在路径边界上（Account_01 不会连带认领 Account_01_backup）",
              r"(?:\\s|$)" in src)
        # 没直接用 re.escape：它给 '-' ':' 也加反斜杠，而 .NET（PowerShell -match）
        # 对这类未定义转义会报错，账号目录名里 '-' 又常见；自写 _ps_regex_escape 只转义有含义的。
        check("用了自写的 .NET 安全转义函数，没有直接调 re.escape",
              "def _ps_regex_escape" in src
              and not _re.search(r"\bre\.escape\s*\(", src))
        check("没有「按进程名杀掉所有 chrome」这种会误伤用户自己浏览器的写法",
              not _re.search(r'"taskkill"|\'taskkill\'', src, _re.I)
              and "Stop-Process -Name" not in src)
        check("结束进程是按具体 PID 精确操作",
              "Stop-Process -Id" in src)
        check("提供了手动出口 close 子命令（万一自动清理不合意）",
              '"close"' in src or "'close'" in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # 还原，并把这一节自己造的日志连同临时目录一起丢掉
        acc_store.LOG_PATH = _orig_log
        shutil.rmtree(_tmp_log, ignore_errors=True)
        # 断言真实日志没被动过（双重保险：上面已经重定向过，这里证明它确实有效）
        if _real_stat is not None:
            try:
                _st = os.stat(_orig_log)
                same = (_st.st_size, _st.st_mtime_ns) == _real_stat
            except OSError:
                same = False
            check("这一节没有碰你真实的 accounts.log", same,
                  "" if same else "文件被改动了")


def a12_chrome_version_probe_is_fast():
    """解析 Chrome 版本时，不许跑 `chrome.exe --version`，那一下要白等 5 秒。

    真站性能事故（2026-10-07 实测）：
        get_chromedriver_path()     5.17 s   ← 其中 5.09 s 就是这一次子进程超时
        webdriver.Chrome() + get()  0.68 s   ← 真正把浏览器拉起来只要这么点
    也就是说「解析驱动」比「起浏览器」贵了 7 倍，而且每次启动都要付一遍。

    原因：Windows 上 `chrome.exe --version` 不打印版本，它会真的把浏览器启动起来
    然后一直不退出，于是干等满 subprocess 的 5 秒超时。
    读安装目录（`_get_windows_chrome_version`）只要几毫秒，结果还更准。

    所以这条断言查的是顺序：能便宜地拿到版本时，就不该再去跑那个子进程。
    这是「性能修复有没有真的落地」唯一可靠的判据，只测返回值的话，
    慢的那条路返回的东西是一模一样的，测不出来。
    """
    print("\n[A12] 解析 Chrome 版本不许跑那个要白等 5 秒的子进程")

    import check_environment as CE

    calls = []
    real = (CE._get_chrome_paths, CE._run_command_get_version,
            CE._get_windows_chrome_version,
            CE.os.path.exists, CE.os.path.isdir, CE.os.listdir)
    tmp = tempfile.mkdtemp(prefix="wb_probe_")
    app = os.path.join(tmp, "Google", "Chrome", "Application")
    exe = os.path.join(app, "chrome.exe")
    ver = os.path.join(app, "155.0.8059.12")
    os.makedirs(ver, exist_ok=True)
    with open(exe, "wb"):
        pass

    try:
        CE._get_chrome_paths = lambda: [exe]
        CE._run_command_get_version = lambda cmd: (calls.append(list(cmd)), None)[1]
        # 只让这个假安装目录「存在」，别的候选路径一概不存在
        CE.os.path.exists = lambda p: os.path.abspath(p) == os.path.abspath(exe)
        CE.os.path.isdir = lambda p: os.path.abspath(p) == os.path.abspath(ver)
        CE.os.listdir = lambda p: [os.path.basename(ver)] if os.path.abspath(p) == os.path.abspath(app) else []

        got = CE._find_chrome()
        check("前提：从安装目录读到了版本", got[2] == "155", str(got))
        check("完全没有跑 `chrome.exe --version`（那一下要白等 5 秒）",
              calls == [], "却跑了：%s" % (calls,))
        check("版本串仍然形如 Google Chrome 155.0.8059.12",
              got[1] == "Google Chrome 155.0.8059.12", str(got[1]))

        # 便宜的路走不通时必须能退回跑子进程，否则「优化」可能悄悄变成「功能丢了」。
        #   得连 `_get_windows_chrome_version` 一起挡掉，不能只清空安装目录：它内部还有
        #   注册表退路（winreg 读 BLBeacon），只清 listdir 的话它照样能拿到真版本、误报。
        calls.clear()
        CE._get_windows_chrome_version = lambda p: None
        CE._run_command_get_version = lambda cmd: (
            calls.append(list(cmd)), "Google Chrome 155.0.8059.12")[1]
        got2 = CE._find_chrome()
        check("兜底还在：便宜的路都读不到时，仍会跑子进程拿版本",
              len(calls) == 1, str(calls))
        check("兜底拿到的结果同样正确", got2[2] == "155", str(got2))
    finally:
        (CE._get_chrome_paths, CE._run_command_get_version,
         CE._get_windows_chrome_version,
         CE.os.path.exists, CE.os.path.isdir, CE.os.listdir) = real
        shutil.rmtree(tmp, ignore_errors=True)


def a13_profile_guard_covers_run_path():
    """起浏览器前的「Profile 被占」守卫，必须两条路都接上。

    两条路：`status` / `login`，account_runner.build_driver()；`run`，AccountConcert，
      Concert.__init__（damai/concert.py）。只在 build_driver 加守卫会漏掉用户实际用的
      `run`，报错里那句「✓ 已启用独立浏览器 Profile:」正出自 Concert.__init__。
    """
    print("\n[A13] 「Profile 被占」守卫覆盖到 run 路径")
    src = open(os.path.join(ROOT, "account_runner.py"), encoding="utf-8").read()
    check("守卫被抽成了公用函数（两条路共用一份实现）",
          "def ensure_profile_free(" in src)
    check("build_driver 里调了守卫",
          "ensure_profile_free(profile_dir, account)" in src)
    check("cmd_run 里也调了守卫（之前漏的就是这条）",
          "ensure_profile_free(acc_store.ensure_profile_dir(acc), acc)" in src)

    # 守卫本身的开销必须可以忽略：它挂在启动热路径上
    tmp = tempfile.mkdtemp(prefix="wb_free_")
    try:
        prof = os.path.join(tmp, "P")
        os.makedirs(os.path.join(prof, "Default", "Network"), exist_ok=True)
        with open(os.path.join(prof, "Default", "Network", "Cookies"), "wb") as f:
            f.write(b"x")
        t0 = time.perf_counter()
        ar.ensure_profile_free(prof, {"account_id": "x"})
        dt = time.perf_counter() - t0
        check("没被占用时几乎不花时间（挂热路径上没关系）", dt < 0.05, "%.4f s" % dt)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# B 段，需要真 Chrome

def _headless(profile_dir):
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--user-data-dir=" + profile_dir)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Chrome(service=webdriver.ChromeService(get_chromedriver_path()), options=opts)


def _serve_fixtures():
    """起一个本地 http 服务：file:// 下浏览器不允许种 Cookie，隔离测试需要真实 http 源。"""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    import functools
    handler = functools.partial(Quiet, directory=FIX)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _serve_redirect_probe():
    """起一个会 302 的本地服务：复刻「未登录访问 myinfo 被踢回登录页」。

    静态文件服务做不到 302，而登录态判定全靠这一次跳转，
    所以这条必须自己起一个小服务。返回 (httpd, base_url, delay)。
    delay 是个可变字典，测「服务端慢 0.6 秒才跳」时往里塞秒数。
    """
    delay = {"v": 0.0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/accountinfo/myinfo_out":
                if delay["v"]:
                    _REAL_SLEEP(delay["v"])       # ← 用真 sleep，别被 _SleepRecorder 记上
                self.send_response(302)
                self.send_header("Location", "/login?ru=%2Faccountinfo%2Fmyinfo")
                self.end_headers()
                return

            if path == "/accountinfo/myinfo":
                body = "<title>账号信息</title><div class='name-user'>测试昵称</div>"
            else:
                body = ("<title>登录 - 大麦网</title>"
                        "<div class='login-user show'><span>登录</span></div>")
            raw = ("<!doctype html><html><head><meta charset='utf-8'></head><body>"
                   + body + "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1], delay


def b1_manual_check_detected_not_bypassed():
    """需求 3：识别到验证码/滑块就只提示、只等待，绝不动手。"""
    print("\n[B1] 人工验证：只识别，不绕过")
    d = _headless(acc_store.ensure_profile_dir(TEST_A))
    try:
        d.get(url_of("captcha.html"))
        hints = ar.detect_manual_check(d)
        check("识别出人工验证字样", bool(hints), str(hints))
        check("识别到「滑块」", "滑块" in hints, str(hints))
        check("识别到「短信验证」", "短信验证" in hints, str(hints))

        # 只读文本，不点、不填、不滚动
        check("识别过程没有改动页面（沙盒里没留下任何操作痕迹）",
              d.execute_script("return document.querySelectorAll('input').length") == 0)
        check("源码里没有绕过验证的痕迹",
              not any(w in open(os.path.join(ROOT, "account_runner.py"), encoding="utf-8").read()
                      for w in ("破解", "识别验证码", "打码", "bypass_captcha", "2captcha")))
    finally:
        d.quit()
        drop_profiles(TEST_A)


def b2_login_state_dom_probe():
    """需求 8：能分辨登录态。未登录，False；已登录，True。"""
    print("\n[B2] 登录状态识别（DOM）")
    d = _headless(acc_store.ensure_profile_dir(TEST_B))
    try:
        d.get(url_of("signed_out.html"))
        check("未登录页面 → 判定为未登录", ar.quick_login_probe(d) is False,
              str(ar.quick_login_probe(d)))

        # 未登录时那个「登录」其实是 span，不是 a，这正是曾经踩过的坑
        shown = d.execute_script(
            "var e=document.querySelector('.span-user');"
            "return !!e && e.tagName.toLowerCase()==='span' && e.offsetParent!==null;")
        check("未登录入口确实是可见的 span.span-user（不是 a 标签）", bool(shown))

        d.get(url_of("signed_in.html"))
        check("已登录页面 → 判定为已登录", ar.quick_login_probe(d) is True,
              str(ar.quick_login_probe(d)))
    finally:
        d.quit()
        drop_profiles(TEST_B)


def b3_wait_for_manual_login_times_out():
    """需求 2/3：等待人工登录不能无限卡死，超时要能退出来。"""
    print("\n[B3] 等待人工登录可以超时退出")
    d = _headless(acc_store.ensure_profile_dir(TEST_A))
    try:
        d.get(url_of("signed_out.html"))
        t0 = time.time()
        ok = ar.wait_for_manual_login(d, TEST_A, poll=0.4, timeout=2)
        spent = time.time() - t0
        check("超时后返回 False（没有卡死）", ok is False, "耗时 %.1fs" % spent)
        check("确实等到了超时点（不是立刻退出）", spent >= 1.5, "%.1fs" % spent)
    finally:
        d.quit()
        drop_profiles(TEST_A)


def b4_cookies_isolated_between_accounts():
    """需求 4/5：A 账号的登录态不会漏给 B；A 自己的下次还在。"""
    print("\n[B4] 账号间登录状态隔离 + 可复用（真 Chrome）")
    httpd, port = _serve_fixtures()
    pa, pb = acc_store.ensure_profile_dir(TEST_A), acc_store.ensure_profile_dir(TEST_B)
    base = "http://127.0.0.1:%d/ping.html" % port
    try:
        d1 = _headless(pa)
        try:
            d1.get(base)
            # 键名必须是 expiry（W3C）；写成 expires 会被静默当成会话 Cookie，
            # 浏览器一关就没了，这个坑踩过，见注释里的教训。
            d1.add_cookie({"name": "probe", "value": "A",
                           "domain": "127.0.0.1", "path": "/",
                           "expiry": int(time.time()) + 86400})
            names = {c["name"] for c in d1.get_cookies()}
            check("A 浏览器里种下了 cookie", "probe" in names, str(names))
            check("种的是持久化 Cookie（不是关掉就没的会话 Cookie）",
                  all(c.get("expiry") for c in d1.get_cookies() if c["name"] == "probe"))
        finally:
            time.sleep(2)      # 给 Chrome 把 cookie 刷到磁盘
            d1.quit()
        time.sleep(2)

        # 磁盘上确实写进了该账号自己的 Profile
        db = os.path.join(pa, "Default", "Network", "Cookies")
        check("登录状态落在本账号的 Profile 目录里", os.path.exists(db), db)
        if os.path.exists(db):
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            rows = con.execute("select name,is_persistent from cookies").fetchall()
            con.close()
            check("数据库里确实有一条持久 Cookie", ("probe", 1) in rows, str(rows))

        d2 = _headless(pb)
        try:
            d2.get(base)
            names = {c["name"] for c in d2.get_cookies()}
            check("B 浏览器看不到 A 的 cookie（账号完全隔离）", "probe" not in names, str(names))
        finally:
            d2.quit()

        d3 = _headless(pa)
        try:
            d3.get(base)
            ck = {c["name"]: c["value"] for c in d3.get_cookies()}
            check("A 重开浏览器后 cookie 还在（登录状态可复用）",
                  ck.get("probe") == "A", str(ck))
        finally:
            d3.quit()
    finally:
        httpd.shutdown()
        drop_profiles(TEST_A, TEST_B)


def b5_login_required_page_detector():
    """需求 8：主判断路径的地址匹配逻辑（不联外网，只验判定函数）。"""
    print("\n[B5] 登录态判定的地址规则")
    src = open(os.path.join(ROOT, "account_runner.py"), encoding="utf-8").read()
    check("用「会被踢回登录页」来判定，而不是靠猜 Cookie 名",
          "passport.damai.cn/accountinfo/myinfo" in src)
    check("判据里包含登录页地址", "passport.damai.cn/login" in src)

    body = src[src.index("def check_login_state"):src.index("def wait_for_manual_login")]
    check("判断顺序：先查「要不要登录」，Cookie 只在后面兜底",
          body.index("_probe_login_required_page") < body.index("_cookie_names"))
    check("拿不准时返回 None，交给用户人工确认", "return None" in body)


def b6_login_probe_has_no_fixed_sleep():
    """登录态判定不许再「跳完页固定睡 2.5 秒」。

    真站性能事故（2026-10）：`_probe_login_required_page` 里 `driver.get()` 之后
    跟着一句 `time.sleep(2.5)`。但 `driver.get` 在页面加载完就返回了，而服务端 302
    是加载过程的一部分，也就是说跳没跳早就定下来了（实测这一跳 0.75 秒完成），
    却还要干等 2.5 秒才去看地址栏。
    这个函数在 run / status 里每个账号都要跑一次，六个账号就是白等十五秒，
    而且是在「离抢票开始最近的时刻」白等。

    断言分两半，缺一不可：
      ① 已经定下来时，不许再睡一大段（判据是 sleep 的时长，不是墙钟耗时）；
      ② 但该等的时候还得等：服务端慢 0.6 秒才跳，照样要判对。
         少了 ②，「不睡」就可以靠「根本不看结果」来实现，那就成了功能丢失。
    """
    print("\n[B6] 登录态判定：不固定睡，但该等还得等")
    httpd, base, delay = _serve_redirect_probe()
    d = _headless(acc_store.ensure_profile_dir(TEST_B))
    saved = ar.LOGIN_REQUIRED_URL
    try:
        ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo_out"
        with _SleepRecorder() as rec:
            v = ar._probe_login_required_page(d)
        check("被 302 踢回登录页 → 判定为未登录", v is False, str(v))
        check("跳转已经定下来了，就不再固定睡 2.5 秒",
              rec.longest <= 0.5, rec.describe())

        ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo"
        with _SleepRecorder() as rec:
            v = ar._probe_login_required_page(d)
        check("停在 myinfo → 判定为已登录", v is True, str(v))
        check("已登录这条路上同样不固定睡", rec.longest <= 0.5, rec.describe())

        # ② 服务端慢 0.6 秒才跳：必须老老实实等到，不能提前下结论
        delay["v"] = 0.6
        ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo_out"
        t0 = time.time()
        v = ar._probe_login_required_page(d)
        spent = time.time() - t0
        check("慢跳转（0.6 秒）也照样判对，没有提前收手", v is False, str(v))
        check("确实等到了跳转发生（不是 0 秒就返回）", spent >= 0.55, "%.2fs" % spent)
    finally:
        delay["v"] = 0.0
        ar.LOGIN_REQUIRED_URL = saved
        d.quit()
        httpd.shutdown()
        drop_profiles(TEST_B)


# C 段：凭据存取 + 自动填表登录。这段把 cred_store.STORE_PATH 指到临时目录，
# 绝不碰真实的 credentials.json，跑完再指回来。

PROBE_ACCOUNT = "__test_cred"
PROBE_PHONE = "13900001111"
PROBE_PWD = "Test#Passw0rd-2026"
PROBE_PROFILE = "profiles/__test_cred"


def _probe_account():
    return dict(TEST_A, account_id=PROBE_ACCOUNT, profile_dir=PROBE_PROFILE)


class _RedirectedCredStore(object):
    """把 cred_store 的落盘位置临时挪到临时目录，用完恢复。"""

    def __enter__(self):
        self._store = cred_store.STORE_PATH
        self._log = cred_store.LOG_PATH
        self._tmp = tempfile.mkdtemp(prefix="wb_cred_")
        cred_store.STORE_PATH = os.path.join(self._tmp, "credentials.json")
        cred_store.LOG_PATH = os.path.join(self._tmp, "accounts.log")
        return cred_store

    def __exit__(self, *exc):
        cred_store.STORE_PATH = self._store
        cred_store.LOG_PATH = self._log
        shutil.rmtree(self._tmp, ignore_errors=True)
        return False


def c1_plain_json_store():
    """密码存本地：读写一致、文件坏了会明确报错、日志脱敏。"""
    print("\n[C1] 凭据明文存取（credentials.json）")
    with _RedirectedCredStore():
        check("存储可用", cred_store.supported(), cred_store.unsupported_reason())
        check("初始没有凭据", not cred_store.has(PROBE_ACCOUNT))

        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        check("存完 has 为 True", cred_store.has(PROBE_ACCOUNT))
        got = cred_store.get(PROBE_ACCOUNT) or {}
        check("手机号取回一致", got.get("phone") == PROBE_PHONE)
        check("密码取回一致", got.get("password") == PROBE_PWD)

        raw = open(cred_store.STORE_PATH, encoding="utf-8").read()
        check("落盘的就是一个 JSON 对象", raw.lstrip().startswith("{"))
        data = json.loads(raw)
        check("文件里就是这个账号的那条记录",
              (data.get("creds") or {}).get(PROBE_ACCOUNT, {}).get("password") == PROBE_PWD)

        # 坏文件必须报错，不能静默当空表：静默当空表的话，下一次保存会把别的账号全抹掉。
        with open(cred_store.STORE_PATH, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        try:
            cred_store.get(PROBE_ACCOUNT)
            check("文件坏了读取会报错（而不是悄悄当空表）", False)
        except Exception:
            check("文件坏了读取会报错（而不是悄悄当空表）", True)

        with open(cred_store.STORE_PATH, "w", encoding="utf-8") as f:
            f.write(raw)
        check("drop 能删掉", cred_store.drop(PROBE_ACCOUNT) is True)
        check("删完 has 为 False", not cred_store.has(PROBE_ACCOUNT))
        check("日志里手机号是脱敏的",
              cred_store.mask_phone(PROBE_PHONE) == "139****1111",
              cred_store.mask_phone(PROBE_PHONE))


def c2_auto_fill_login_fills_and_clicks():
    """在本地假登录页上验证：能切到密码页签、填上、点得动。"""
    print("\n[C2] 自动填表：填得上、点得动（真 Chrome + 本地假登录页）")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login.html" % port)
            check("默认停在扫码页（密码表单是隐藏的）",
                  not d.find_element(By.ID, "pwdPane").is_displayed())

            ok = ar.auto_fill_login(d, acc)
            check("auto_fill_login 返回 True（已经点了登录）", ok is True, str(ok))

            filled = d.execute_script("return window.__filled") or {}
            check("账号框填上了保存的手机号", filled.get("id") == PROBE_PHONE,
                  str(filled.get("id")))
            check("密码框填上了保存的密码", filled.get("pwd") == PROBE_PWD)
            check("确实点到了登录按钮（表单已提交）",
                  d.execute_script("return window.__submitted") is True)
            check("自动切到了「密码登录」页签",
                  d.find_element(By.ID, "pwdPane").is_displayed())
            check("提交后出现的滑块验证没被自动处理（还在页面上）",
                  d.find_element(By.ID, "captcha").is_displayed())
            check("同时识别到人工验证，转交用户",
                  bool(ar.detect_manual_check(d)), str(ar.detect_manual_check(d)))
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()


def c3_auto_fill_refuses_when_captcha_present():
    """页面一开始就挂着验证时，连填表都不该做。"""
    print("\n[C3] 页面已在要人工验证时，不填表")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login.html?captcha=1" % port)
            check("页面上一开始就挂着验证", bool(ar.detect_manual_check(d)),
                  str(ar.detect_manual_check(d)))

            check("auto_fill_login 返回 False（不去填表）",
                  ar.auto_fill_login(d, acc) is False)
            check("账号框仍然是空的（确实没填）",
                  d.execute_script("return document.getElementById('fm-login-id').value") == "")
            check("表单没有被提交",
                  d.execute_script("return window.__submitted") is False)
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()


def c4_auto_fill_without_credentials_is_safe():
    """没存过密码、或页面结构不认识时，安静退回手动登录，不抛异常。"""
    print("\n[C4] 没凭据 / 认不出表单时安静退回手动登录")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            # 故意不 put：没有凭据
            d.get("http://127.0.0.1:%d/login.html" % port)
            check("没存过密码 → 返回 False，且不碰页面",
                  ar.auto_fill_login(d, acc) is False
                  and d.execute_script("return window.__submitted") is False)

            # 认不出登录表单的普通页面
            d.get(url_of("signed_out.html"))
            check("认不出登录表单的页面 → 返回 False，不抛异常",
                  ar.auto_fill_login(d, acc) is False)

            # _try_auto_login 是包了一层外壳的版本，出错也必须吞掉
            d.get("http://127.0.0.1:%d/login.html" % port)
            check("_try_auto_login 出任何错都不会影响后面的手动登录",
                  ar._try_auto_login(d, acc) is False)
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()


def c5_password_never_written_to_log():
    """跑完之后，日志文件里不能出现密码或完整手机号。"""
    print("\n[C5] 日志里不留密码、不留完整手机号")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore() as cs:
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login.html" % port)
            ar.auto_fill_login(d, acc)
        finally:
            d.quit()
        drop_profiles(acc)
        logtext = ""
        if os.path.exists(cs.LOG_PATH):
            logtext = open(cs.LOG_PATH, encoding="utf-8").read()
        check("凭据日志文件里没有明文密码", PROBE_PWD not in logtext)
        check("凭据日志文件里没有完整手机号", PROBE_PHONE not in logtext)
        # 日志现在收在 web/logs/ 下（app_paths.logs_dir）。路径写死成
        #   ROOT/accounts.log 的话，文件不存在就读到空串，断言变成恒真。
        _real_log = os.path.join(ROOT, "logs", "accounts.log")
        mainlog = open(_real_log, encoding="utf-8").read() \
            if os.path.exists(_real_log) else ""
        check("accounts.log 里也没有明文密码", PROBE_PWD not in mainlog)
    httpd.shutdown()


def c6_cli_auto_flag_wires_through():
    """命令行 --auto 真的把「取凭据，填表，点登录」接上了（不联外网）。"""
    print("\n[C6] --auto 从命令行一路接到填表（不联外网）")
    httpd, port = _serve_fixtures()
    tmpdir = tempfile.mkdtemp(prefix="wb_accjson_")
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)

        orig_path = acc_store.ACCOUNTS_PATH
        acc_store.ACCOUNTS_PATH = os.path.join(tmpdir, "accounts.json")
        acc_store.save({"version": 1, "accounts": [_probe_account()]})

        orig = (ar.LOGIN_URL, ar.build_driver, ar.check_login_state, ar.wait_for_manual_login)
        holder = {}

        def fake_build(acc):
            d = _headless(acc_store.ensure_profile_dir(acc))
            holder["driver"] = d
            return d

        def fake_wait(d, acc, **kw):
            # 走到这里说明浏览器已经打开了登录页
            holder["filled"] = d.execute_script("return window.__filled") or {}
            holder["submitted"] = d.execute_script("return window.__submitted")
            holder["captcha"] = d.find_element(By.ID, "captcha").is_displayed()
            return True

        ar.LOGIN_URL = "http://127.0.0.1:%d/login.html" % port
        ar.build_driver = fake_build
        ar.check_login_state = lambda d, retries=2: False      # 假装登录态已失效
        ar.wait_for_manual_login = fake_wait
        try:
            args = ar.build_parser().parse_args(["login", "--auto", PROBE_ACCOUNT])
            rc = ar.cmd_login(args)
            check("cmd_login 正常跑完（返回 0）", rc == 0, str(rc))
            filled = holder.get("filled") or {}
            check("凭据被自动填进了表单", filled.get("id") == PROBE_PHONE, str(filled.get("id")))
            check("密码也填进去了（长度对得上）",
                  len(filled.get("pwd") or "") == len(PROBE_PWD))
            check("登录按钮被点到（表单已提交）", holder.get("submitted") is True)
            check("提交后出现的验证码没有被处理，仍在等用户",
                  holder.get("captcha") is True)
        finally:
            ar.LOGIN_URL, ar.build_driver, ar.check_login_state, ar.wait_for_manual_login = orig
            acc_store.ACCOUNTS_PATH = orig_path
            shutil.rmtree(tmpdir, ignore_errors=True)
        drop_profiles(_probe_account())
    httpd.shutdown()


def c7_login_form_inside_iframe_is_reached():
    """登录表单在跨域 iframe 里时也要能填上，真站就是这个结构。

    2026-10 实测大麦登录页：主文档里 input 数量 = 0，表单全在
    https://ipassport.damai.cn/mini_login.htm 这个 iframe 里。老代码从不切 frame，
    所以 fm-login-id 这些选择器再准也命中不了，日志里只会看到
    「没认出账号输入框，请手动登录」。这条测试就是把这个坑焊死。
    """
    print("\n[C7] 表单在 iframe 里：切进去、填上、点登录")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login_iframe.html" % port)

            check("主文档里一个 input 都没有（复刻真站结构）",
                  d.execute_script("return document.querySelectorAll('input').length") == 0)
            check("不切 iframe 时主文档里找不到账号框（老代码就死在这）",
                  ar._login_field_present(d) is None, str(ar._login_field_present(d)))

            ok, where = ar.enter_login_frame(d, timeout=10)
            check("enter_login_frame 切进了 iframe", bool(ok) and where == "iframe",
                  "{} / {}".format(ok, where))
            # _login_field_present 把 5 条选择器合成一条 CSS 一次问完，返回值变成
            #   「合并后的那条」；断言改成「合并串里含 fm-login-id」，判的还是同一件事。
            check("切进去后账号框选择器立刻命中",
                  "fm-login-id" in (ar._login_field_present(d) or ""),
                  str(ar._login_field_present(d)))

            d.switch_to.default_content()
            check("auto_fill_login 端到端跑通（返回 True）",
                  ar.auto_fill_login(d, acc) is True)
            # auto_fill_login 会把上下文留在表单所在 frame，这里正好在里面读结果
            check("表单确实被提交了（点到了 iframe 里的登录按钮）",
                  d.execute_script("return window.__submitted") is True)
            filled = d.execute_script("return window.__filled") or {}
            check("iframe 里填上了保存的手机号", filled.get("id") == PROBE_PHONE,
                  str(filled.get("id")))
            check("iframe 里填上了保存的密码", filled.get("pwd") == PROBE_PWD)
            check("提交后弹出的滑块验证没被自动处理（仍在页面上）",
                  d.find_element(By.ID, "captcha").is_displayed())
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()


def c8_slow_login_page_is_waited_for():
    """登录页慢慢加载时，要等表单出现再填，不能立刻放弃。

    线上日志的证据：19:37:51「登录状态失效」，19:37:52「没认出账号输入框」，
    只隔 1 秒，老代码 driver.get 完立刻就找元素，页面根本没渲染完。
    """
    print("\n[C8] 表单 3 秒后才出现：要等，不能立刻放弃")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login_lazy.html" % port)
            check("刚打开时表单还没出来", ar._login_field_present(d) is None)

            t0 = time.time()
            ok = ar.auto_fill_login(d, acc)
            spent = time.time() - t0
            check("一直等到表单出现才动手（返回 True）", ok is True, "耗时 %.1fs" % spent)
            check("确实等了，不是 0 秒就返回", spent >= 2.5, "%.1fs" % spent)
            filled = d.execute_script("return window.__filled") or {}
            check("等到之后把手机号填上了", filled.get("id") == PROBE_PHONE, str(filled.get("id")))
            check("表单已被提交", d.execute_script("return window.__submitted") is True)
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()


def c9_fill_path_has_no_fixed_sleep():
    """填账号密码这条路不许再靠固定 sleep 撑着。

    原来这条路上有两处固定 sleep，本地夹具实测占了整条路九成时间：`_switch_to_password_tab`
      点完「密码登录」页签固定睡 1.5 秒；`auto_fill_login` 点完登录按钮固定睡 2.0 秒。
      auto_fill_login 端到端 3.81 秒、其中 3.5 秒纯白等，而它真正等的事（密码框可见 /
      页面开始变）都在 100~300 毫秒内就好了。
    断言两半：① 不再睡大段；② 该等的事还得等到（页签点完密码框得真可见，不然 _fill_field
      填不进去；慢慢加载的登录页得真等到，后者由 C8 守着）。
    """
    print("\n[C9] 自动填表：不再固定 sleep（但页签点完密码框得真可见）")
    httpd, port = _serve_fixtures()
    acc = _probe_account()
    with _RedirectedCredStore():
        cred_store.put(PROBE_ACCOUNT, PROBE_PHONE, PROBE_PWD)
        d = _headless(acc_store.ensure_profile_dir(acc))
        try:
            d.get("http://127.0.0.1:%d/login.html" % port)
            check("前提：默认停在扫码页，密码框不可见（不然测不到切页签这一步）",
                  ar._visible_field(d, ar.LOGIN_PWD_SELECTORS) is None)

            with _SleepRecorder() as rec:
                tab = ar._switch_to_password_tab(d)
            check("点中了「密码登录」页签", tab == "密码登录", str(tab))
            check("不靠固定睡 1.5 秒（改成等「密码框真的可见」）",
                  rec.longest <= 0.5, rec.describe())
            check("该等的事等到了：密码框现在可见",
                  ar._visible_field(d, ar.LOGIN_PWD_SELECTORS) is not None)

            d.get("http://127.0.0.1:%d/login.html" % port)
            with _SleepRecorder() as rec:
                ok = ar.auto_fill_login(d, acc)
            check("auto_fill_login 端到端跑通（返回 True）", ok is True, str(ok))
            check("提交之后不靠固定睡 2.0 秒", rec.longest <= 0.5, rec.describe())

            filled = d.execute_script("return window.__filled") or {}
            check("账号仍然填对了", filled.get("id") == PROBE_PHONE, str(filled.get("id")))
            check("密码仍然填对了", filled.get("pwd") == PROBE_PWD)
            check("仍然识别到提交后弹出的验证并转交用户",
                  bool(ar.detect_manual_check(d)), str(ar.detect_manual_check(d)))
        finally:
            d.quit()
        drop_profiles(acc)
    httpd.shutdown()



def main():
    ap = argparse.ArgumentParser(
        description="多账号登录状态管理 —— 本地验证套件",
        epilog="例：python tools/check_accounts.py --only c7,c8,c9,b6")
    # 加这个参数是因为整套跑不完：a4 / a6 收尾会连删上百个临时文件，
    #   在某些环境里会撞上「批量删除保护」把进程直接掐掉。想只验一段时就靠它。
    ap.add_argument("--only", default="",
                    help="只跑名字里含这些片段的用例（逗号分隔，子串匹配）")
    args = ap.parse_args()
    wanted = [p.strip() for p in args.only.split(",") if p.strip()]

    print("=" * 62)
    print("多账号登录状态管理 —— 本地验证套件")
    print("=" * 62)

    print("\n准备 ChromeDriver…")
    path = ensure_chromedriver_env()
    print("  " + (path or "未找到（B 段会失败）"))

    # 别把测试日志写进真正的 accounts.log
    orig_log = acc_store.LOG_PATH
    tmp_dir = tempfile.mkdtemp(prefix="wb_acclog_")
    acc_store.LOG_PATH = os.path.join(tmp_dir, "accounts.log")

    cases = [a1_password_never_plaintext, a2_account_fields_complete,
             a3_profile_isolation_paths, a4_driver_uses_own_profile,
             a5_toggle_persists, a6_forget_is_guarded,
             a7_validate_rules, a8_config_mapping,
             a9_log_format, a10_no_hardcoded_credentials,
             a11_profile_busy_is_explained,
             a12_chrome_version_probe_is_fast,
             a13_profile_guard_covers_run_path,
             b1_manual_check_detected_not_bypassed, b2_login_state_dom_probe,
             b3_wait_for_manual_login_times_out, b4_cookies_isolated_between_accounts,
             b5_login_required_page_detector,
             b6_login_probe_has_no_fixed_sleep,
             c1_plain_json_store, c2_auto_fill_login_fills_and_clicks,
             c3_auto_fill_refuses_when_captcha_present,
             c4_auto_fill_without_credentials_is_safe,
             c5_password_never_written_to_log, c6_cli_auto_flag_wires_through,
             c7_login_form_inside_iframe_is_reached, c8_slow_login_page_is_waited_for,
             c9_fill_path_has_no_fixed_sleep]

    if wanted:
        cases = [fn for fn in cases
                 if any(w in fn.__name__ for w in wanted)]
        print("\n按 --only 过滤，只跑 {} 个用例：{}".format(
            len(cases), "、".join(fn.__name__ for fn in cases) or "（没有匹配的用例）"))
        if not cases:
            return 1

    for fn in cases:
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " 执行未抛异常", False, str(e))

    acc_store.LOG_PATH = orig_log
    shutil.rmtree(tmp_dir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 62)
    print("结果: {}/{} 通过".format(passed, total))
    for name, ok, extra in RESULTS:
        if not ok:
            print("  ✗ {} {}".format(name, extra))
    print("=" * 62)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
