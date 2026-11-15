# -*- coding: utf-8 -*-
"""多开减负自检：设置 / 内存估算 / Chrome 参数 / 排队启动。

跑法（在 web 目录下）：
    python tools/check_multi_open.py                 # 全部
    python tools/check_multi_open.py --only a,c      # 只跑某几节
    python tools/check_multi_open.py --only e        # 只看排队那段

它全程用临时目录：`run_settings.SETTINGS_PATH` 会被重定向。
  不重定向的话，跑一次自检就会把用户真实的 wizard_settings.json 改成测试值
这个项目因为「忘了重定向」已经出过 8 次事故（见 tools/_probe_env.py 开头）。
  末尾会按内容哈希断言真实文件一个字节都没变。

这一套里唯一会真起进程的是 [E]：为了验证「同时最多几个」真的被遵守，
  必须有几个「活着」的进程让状态判定有东西可判。起的是
  `python -c "import time; time.sleep(600)"` 这种空转进程，跑完全部杀掉。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import _probe_env                                                     # noqa: E402

GUARD = _probe_env.probe_env()          # 必须先于 import accounts

import chrome_flags                                                   # noqa: E402
import resource_guard                                                 # noqa: E402
import run_settings                                                   # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             ("  <<< " + str(extra)) if extra else ""))


def skip(name):
    SKIP.append(name)
    print("  [SKIP] " + name)


# [A] 多开设置

def a_settings():
    print("\n[A] 多开设置：坏文件不许把程序弄崩，字段各自兜底")

    check("默认值里「多开优化」是开的", run_settings.DEFAULTS["enabled"] is True)
    check("默认不限数量（0 = 按内存自动）",
          run_settings.DEFAULTS["max_at_once"] == 0)
    check("默认错开秒数 > 0（实测单个浏览器启动约 1 秒）",
          run_settings.DEFAULTS["stagger_seconds"] >= 1.0)
    check("默认用轻量化参数", run_settings.DEFAULTS["light_flags"] is True)
    check("默认不关后台降速（那会更耗 CPU）",
          run_settings.DEFAULTS["keep_awake"] is False)

    # 读：坏输入一律回默认，绝不抛
    for bad in (None, [], "abc", 42, {"enabled": "??", "max_at_once": "x"}):
        try:
            got = run_settings.clean(bad)
            check("坏输入 %r 也能读出一份可用设置" % (bad,),
                  isinstance(got, dict) and got.get("enabled") in (True, False), got)
        except Exception as exc:
            check("坏输入 %r 不该抛异常" % (bad,), False, exc)

    # 字段各自兜底：一个坏不牵连其它
    got = run_settings.clean({"max_at_once": "这不是数字", "stagger_seconds": 5})
    check("一个字段坏掉，不该把其它字段也重置",
          got["max_at_once"] == run_settings.DEFAULTS["max_at_once"]
          and got["stagger_seconds"] == 5, got)

    # 越界夹取
    check("负数上限夹到 0", run_settings.clean({"max_at_once": -5})["max_at_once"] == 0)
    check("超大上限夹到 64", run_settings.clean({"max_at_once": 999})["max_at_once"] == 64)
    check("负数间隔夹到 0",
          run_settings.clean({"stagger_seconds": -1})["stagger_seconds"] == 0.0)
    check("超大间隔夹到 60",
          run_settings.clean({"stagger_seconds": 999})["stagger_seconds"] == 60.0)
    check("字符串数字也能认", run_settings.clean({"max_at_once": "3"})["max_at_once"] == 3)

    # 布尔的各种写法
    for raw, want in (("true", True), ("1", True), ("开", True), ("on", True),
                      ("false", False), ("0", False), ("", False)):
        check("布尔写法 %r → %s" % (raw, want),
              run_settings.clean({"enabled": raw})["enabled"] is want)

    # 存 / 读往返
    tmp = tempfile.mkdtemp(prefix="settings-")
    path = os.path.join(tmp, "s.json")
    want = {"enabled": True, "max_at_once": 3, "stagger_seconds": 1.5,
            "light_flags": False, "keep_awake": True}
    check("保存成功", run_settings.save(want, path=path))
    got = run_settings.load(path=path)
    check("存进去再读出来一模一样", got == want, got)
    check("存盘不留临时文件（写临时文件再原子替换）",
          not [f for f in os.listdir(tmp) if f.endswith(".tmp")], os.listdir(tmp))

    check("文件不存在时读默认值",
          run_settings.load(path=os.path.join(tmp, "nope.json"))["max_at_once"] == 0)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    check("文件被手改坏时读默认值，不抛",
          run_settings.load(path=path)["enabled"] is True)

    # describe 要能看懂
    txt = run_settings.describe({"enabled": False})
    check("关掉时 describe 如实说「关」", "关" in txt, txt)
    txt = run_settings.describe({"enabled": True, "max_at_once": 3,
                                 "stagger_seconds": 2, "light_flags": True})
    check("开着时 describe 带上限和间隔",
          ("3" in txt) and ("2" in txt), txt)

    # 给子进程的参数
    check("关掉多开优化 → 一个参数都不加（回到改造前）",
          run_settings.chrome_args({"enabled": False, "light_flags": True}) == [])
    check("关掉轻量化 → 也不加参数",
          run_settings.chrome_args({"enabled": True, "light_flags": False}) == [])
    args = run_settings.chrome_args({"enabled": True, "light_flags": True,
                                     "keep_awake": False})
    check("默认情况下会给出参数", len(args) > 0, args[:3])
    check("参数里带上了 --user-data-dir 之外的策略参数（不该越权）",
          not any(a.startswith("--user-data-dir") for a in args), args[:3])


# [B] 内存估算

def b_memory():
    print("\n[B] 内存估算：说得出「还能开几个」，而且读不到时也安全")

    mem = resource_guard.memory_info()
    check("能读到本机总内存", mem["total"] and mem["total"] > 0, mem)
    check("能读到可用内存", mem["avail"] and mem["avail"] > 0, mem)
    check("可用 ≤ 总", mem["avail"] <= mem["total"], mem)
    check("说了数据来源", bool(mem["source"]), mem["source"])

    check("format_bytes(0) = 0 B", resource_guard.format_bytes(0) == "0 B")
    check("format_bytes(1GB) 带单位",
          resource_guard.format_bytes(1024 ** 3).endswith("GB"),
          resource_guard.format_bytes(1024 ** 3))
    check("读不到时写「未知」，不写 0（0 会被当成「没内存」）",
          resource_guard.format_bytes(None) == "未知")

    # 上限计算
    GB = 1024 ** 3
    check("内存管够时受上限约束",
          resource_guard.max_windows(avail=32 * GB, total=32 * GB,
                                     per_window_mb=500, cap=8) == 8)
    check("内存中等时按可用内存算（3GB 可用、总 4GB → 留 1.5GB 给系统，剩 1.5GB 够 3 个）",
          resource_guard.max_windows(avail=3 * GB, total=4 * GB,
                                     per_window_mb=500, cap=8) == 3,
          resource_guard.max_windows(avail=3 * GB, total=4 * GB,
                                     per_window_mb=500, cap=8))
    check("系统余量要按比例放到大内存机器上（16GB 的机器不该只留 1.5GB）",
          resource_guard.max_windows(avail=3 * GB, total=16 * GB,
                                     per_window_mb=500, cap=8) == 2,
          resource_guard.max_windows(avail=3 * GB, total=16 * GB,
                                     per_window_mb=500, cap=8))
    check("内存快没了也要给 1（不是 0）—— 拒绝开第一个浏览器是没有意义的",
          resource_guard.max_windows(avail=200 * 1024 * 1024, total=16 * GB,
                                     per_window_mb=500, cap=8) == 1)
    check("读不到可用内存 → 不限制（返回上限）",
          resource_guard.max_windows(avail=None, per_window_mb=500, cap=8) == 8)
    check("给系统留的余量取「固定值」和「按比例」里大的那个",
          resource_guard.reserve_bytes(total=64 * GB)
          > resource_guard.reserve_bytes(total=4 * GB))

    # preflight 的话术
    allow, reason = resource_guard.preflight(3, per_window_mb=500,
                                             avail=32 * GB, total=32 * GB)
    check("够开时如实说够", allow == 3 and "够" in reason, reason)
    allow, reason = resource_guard.preflight(8, per_window_mb=500,
                                            avail=2 * GB, total=8 * GB)
    check("不够时给出的是一个更小的数，不是一句「不行」",
          1 <= allow < 8, (allow, reason))
    check("不够时的话里带上了可用内存和估算口径",
          ("可用内存" in reason) and ("500" in reason), reason)
    check("不够时提示可以关掉别的程序", "关掉" in reason, reason)
    # 注意 `avail=None` 的含义是「你自己去读」，不是「读不到」。
    #   要测「读不到」这条分支，得让 memory_info() 真的读不到，打桩成返回 None。
    real_mi = resource_guard.memory_info
    resource_guard.memory_info = lambda: {"total": None, "avail": None,
                                          "percent": None, "source": "读不到"}
    try:
        allow, reason = resource_guard.preflight(4)
        check("读不到内存时如实说「不做限制」，而不是假装算过了",
              allow == 4 and "读不到" in reason, (allow, reason))
        check("读不到时也说明了「按每个多少估」",
              "MB" in reason, reason)
    finally:
        resource_guard.memory_info = real_mi


# [C] Chrome 参数

def c_flags():
    print("\n[C] Chrome 参数：只关后台杂活，绝不碰会改变网页行为的东西")

    args = chrome_flags.build_args(light=True)
    joined = " ".join(args)

    # 这一节最重要的是「不许出现什么」：抢票是一整套 H5 + 画布交互，
    #   为了省内存去禁图片 / 禁 JS / headless，换来的很可能是「选不了座」。
    for forbidden, why in (("--headless", "无头模式会改变页面行为，也看不到付款页面"),
                           ("--disable-images", "禁图片可能直接搞坏选座画布"),
                           ("--blink-settings", "会改页面渲染行为"),
                           ("--disable-javascript", "整站就没法用了"),
                           ("--no-sandbox", "没必要，且削弱隔离")):
        check("参数里没有 {}（{}）".format(forbidden, why),
              forbidden not in joined, joined[:120])

    for forbidden, why in (("--log-level", "写日志少了，出问题就没现场"),
                           ("--disable-logging", "同上")):
        check("参数里没有 {}（{}）".format(forbidden, why),
              forbidden not in joined, joined[:120])

    check("关掉了后台网络（省流量也省启动时间）",
          "--disable-background-networking" in args)
    check("关掉了扩展", "--disable-extensions" in args)
    check("关掉了组件更新（不后台下载东西）",
          "--disable-component-update" in args)
    check("限了磁盘缓存（N 份 Profile 会乘 N）",
          any(a.startswith("--disk-cache-size=") for a in args), args)
    check("缓存上限能调",
          "--disk-cache-size=1048576" in chrome_flags.build_args(light=True, cache_mb=1))

    # `--disable-features` 只能出现一条：重复出现时 Chrome 的合并行为
    #   历来不统一（有的取最后一个、有的合并），拼两条就是看版本脸色的玄学。
    check("--disable-features 只出现一条",
          len([a for a in args if a.startswith("--disable-features")]) == 1,
          [a for a in args if a.startswith("--disable-features")])
    check("多组 feature 会合并成一条",
          len(chrome_flags.disable_features_args(["A"], ["B", "A"])) == 1
          and chrome_flags.disable_features_args(["A"], ["B", "A"])[0]
          == "--disable-features=A,B")
    check("空 feature 组不产生参数", chrome_flags.disable_features_args([], []) == [])

    # keep_awake
    plain = chrome_flags.build_args(light=True, keep_awake=False)
    awake = chrome_flags.build_args(light=True, keep_awake=True)
    check("默认不关后台降速", "--disable-background-timer-throttling" not in plain)
    check("开了之后才关后台降速",
          "--disable-background-timer-throttling" in awake
          and "--disable-backgrounding-occluded-windows" in awake)

    check("light=False 时后台参数一个都不加",
          chrome_flags.build_args(light=False) == [],
          chrome_flags.build_args(light=False))

    # 窗口位置
    w0 = " ".join(chrome_flags.window_args(0))
    w1 = " ".join(chrome_flags.window_args(1))
    check("窗口带明确尺寸（省一次重排，也保证布局一致）",
          "--window-size=" in w0, w0)
    check("多开时窗口错开位置（别让后面的被完全遮住）",
          "--window-position=" in w0 and w0 != w1, (w0, w1))
    sw, sh = chrome_flags.screen_size()
    check("屏幕分辨率读得到合理的数", sw >= 640 and sh >= 480, (sw, sh))

    # add_to
    class FakeOpts(object):
        def __init__(self):
            self.args = []

        def add_argument(self, a):
            self.args.append(a)

    opt = chrome_flags.add_to(FakeOpts(), ["--a", "", None, "--b"])
    check("add_to 跳过空值", opt.args == ["--a", "--b"], opt.args)

    check("describe 会说清楚在干什么",
          "缓存" in chrome_flags.describe() and "扩展" in chrome_flags.describe(),
          chrome_flags.describe())
    check("light=False 的 describe 如实说「没有轻量化」",
          "未启用" in chrome_flags.describe(light=False))


# [D] 参数真的接到了驱动上

def d_wired_into_driver():
    print("\n[D] 接线：这些参数真的进了 Chrome 的启动参数（不是写了个没人用的模块）")

    tmp = tempfile.mkdtemp(prefix="multiwire-")
    settings_path = run_settings.SETTINGS_PATH
    old_settings = run_settings.load()

    try:
        try:
            import account_runner as ar
            import concert  # noqa: F401
        except ImportError as exc:
            skip("当前解释器没有 selenium（{}），跳过接线用例"
                 "（用带 selenium 的那个解释器跑本套件即可覆盖）".format(exc))
            return
        import accounts as acc_store

        acc = {"account_id": "__multi_wire__", "note": "", "enabled": True,
               "profile_dir": "", "target_url": "https://detail.damai.cn/item.htm?id=1",
               "city": "北京", "dates": ["2026-12-05"], "prices": ["380"],
               "quantity": 1, "users": ["某人"], "if_listen": True,
               "if_commit_order": False, "max_retries": 1000,
               "dismiss_mask": True, "no_seat": True, "random_seat": True}

        # ① build_config 会把参数放进 Config
        cfg = ar.build_config(acc)
        check("build_config 给 Config 放了 chrome_args",
              isinstance(getattr(cfg, "chrome_args", None), list)
              and len(cfg.chrome_args) > 0, getattr(cfg, "chrome_args", None))
        check("没有 --user-data-dir（那是 Concert 自己按账号加的）",
              not any(a.startswith("--user-data-dir") for a in cfg.chrome_args))

        # ② Concert 真的把它们加给了 ChromeOptions
        import concert as C
        real_chrome = C.webdriver.Chrome
        real_path = C.get_chromedriver_path
        captured = []

        class FakeChrome(object):
            def __init__(self, service=None, options=None):
                captured.append(list(getattr(options, "arguments", [])))
                raise RuntimeError("capture-only")

        C.webdriver.Chrome = FakeChrome
        C.get_chromedriver_path = lambda: "C:/fake/chromedriver.exe"
        try:
            cfg.user_data_dir = os.path.join(tmp, "prof")
            try:
                C.Concert(cfg)
            except RuntimeError:
                pass
        finally:
            C.webdriver.Chrome = real_chrome
            C.get_chromedriver_path = real_path

        check("Concert 真去建了浏览器（抓到了参数）", len(captured) == 1, len(captured))
        if captured:
            got = captured[0]
            check("Concert 把 chrome_args 加进去了",
                  "--disable-background-networking" in got, got[:4])
            check("同时也保留了本账号的 --user-data-dir",
                  any(a.startswith("--user-data-dir=") for a in got))

        # ③ status/login 那条路（build_driver）也带上了：驱动路径要一起换掉。`build_driver`
        #   用的是上游 `check_environment` 里的 `get_chromedriver_path()`，它不扫 Selenium
        #   缓存、只认 CHROMEDRIVER_PATH / PATH / 当前目录；不换掉用例就取决于有没有设环境变量。
        real_chrome2 = ar.webdriver.Chrome
        real_path2 = ar.get_chromedriver_path
        captured2 = []

        class FakeChrome2(object):
            def __init__(self, service=None, options=None):
                captured2.append(list(getattr(options, "arguments", [])))
                raise RuntimeError("capture-only")

        ar.webdriver.Chrome = FakeChrome2
        ar.get_chromedriver_path = lambda: "C:/fake/chromedriver.exe"
        try:
            try:
                ar.build_driver(acc)
            except RuntimeError:
                pass
        finally:
            ar.webdriver.Chrome = real_chrome2
            ar.get_chromedriver_path = real_path2

        check("build_driver 也建了浏览器（抓到了参数）", len(captured2) == 1, len(captured2))
        if captured2:
            got = captured2[0]
            check("build_driver 也带上了多开参数",
                  "--disable-background-networking" in got, got[:4])
            check("仍然保留 --user-data-dir（账号隔离没被破坏）",
                  any(a.startswith("--user-data-dir=") for a in got))

        # ④ 设置里关掉，真的一点都不加
        run_settings.save({"enabled": False}, path=settings_path)
        cfg2 = ar.build_config(acc)
        check("关掉多开优化后，Config 里一个参数都没有（真的是回到改造前）",
              cfg2.chrome_args == [], cfg2.chrome_args)
        run_settings.save({"enabled": True, "light_flags": False}, path=settings_path)
        check("只关轻量化 → 也不加",
              ar.build_config(acc).chrome_args == [])

        drop = acc_store.profile_path(acc)
        if os.path.isdir(drop):
            import shutil
            shutil.rmtree(drop, ignore_errors=True)
    finally:
        run_settings.save(old_settings, path=settings_path)


# [E] 任务面板的排队（会真起空转进程）

def _acc(aid, **kw):
    base = {
        "account_id": aid, "note": "", "enabled": True, "profile_dir": "",
        "target_url": "https://detail.damai.cn/item.htm?id=11111111",
        "city": "北京", "dates": ["2026-12-05"], "prices": ["380"], "quantity": 1,
        "users": ["王小明"], "if_listen": True, "if_commit_order": False,
        "max_retries": 10000, "dismiss_mask": True, "no_seat": True,
        "random_seat": True,
    }
    base.update(kw)
    return base


def e_queue():
    print("\n[E] 排队：一次只放一个、超过上限就排队、有空位再补")

    try:
        import tkinter as tk                    # noqa: F401
    except Exception:
        skip("当前解释器没有 tkinter，跳过排队用例（用系统那套 Python 跑就能覆盖）")
        return

    tmp = tempfile.mkdtemp(prefix="multiopen-")
    import accounts as acc_store
    import cred_store

    # 真实文件核对用的路径直接取守卫记的那一份（那才是用户真实的路径）。
    real = {p: (os.path.exists(p), os.path.getmtime(p) if os.path.exists(p) else None)
            for p in GUARD.watched}

    ids = ["Account 01", "Account 02", "Account 03", "Account 04", "Account 05"]
    acc_store.ACCOUNTS_PATH = os.path.join(tmp, "accounts.json")
    acc_store.LOG_PATH = os.path.join(tmp, "accounts.log")
    cred_store.STORE_PATH = os.path.join(tmp, "credentials.json")
    cred_store.LOG_PATH = os.path.join(tmp, "accounts.log")

    real_settings = run_settings.SETTINGS_PATH
    run_settings.SETTINGS_PATH = os.path.join(tmp, "wizard_settings.json")
    assert run_settings.SETTINGS_PATH != real_settings, "设置路径没重定向，停手"

    with io.open(acc_store.ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "roster": ["王小明"],
                   "accounts": [_acc(a) for a in ids]}, f,
                  ensure_ascii=False, indent=2)

    import damai_wizard as W

    W.LOG_PATH = os.path.join(tmp, "damai_wizard.log")
    W.LEGACY_PROFILES_PATH = os.path.join(tmp, "profiles.json")
    W.CONFIG_PATH = os.path.join(tmp, "damai", "config.json")
    os.makedirs(os.path.dirname(W.CONFIG_PATH), exist_ok=True)

    import task_manager as tm_mod

    # 真实的空转进程：让「活着」这件事是真的，而不是靠打桩假装
    sleeping = []
    asked = {"yesno": [], "warn": []}
    old_yesno = W.messagebox.askyesno
    old_warn = W.messagebox.showwarning
    W.messagebox.askyesno = lambda title, body="", **k: (asked["yesno"].append((title, body)), True)[1]
    W.messagebox.showwarning = lambda title, body="", **k: asked["warn"].append((title, body))

    panel = None
    panel2 = None
    root = None
    try:
        run_settings.save({"enabled": True, "max_at_once": 2, "stagger_seconds": 0,
                           "light_flags": True, "keep_awake": False})
        check("设置已隔离到临时文件",
              run_settings.load()["max_at_once"] == 2, run_settings.load())

        # runs/ 与任务状态文件都必须落到临时目录，否则会在项目目录里
        #   留一个 runs/ 出来（这个项目为此专门有断言，见 check_wizard_ui 的 [O] 节）。
        tm_mod.RUNS_DIR = os.path.join(tmp, "runs")
        W._TASK_MANAGER = None
        root = tk.Tk()
        root.withdraw()
        wiz = W.Wizard(root)
        root.update_idletasks()

        mgr = tm_mod.TaskManager()
        mgr.state_path = os.path.join(tmp, "task_state.json")
        mgr.runs_dir = os.path.join(tmp, "runs")

        def make_fake(target):
            """替掉「真起 account_runner」那一步，但仍登记一个真的活着的进程。

            为什么非要真进程：`snapshot()` 会去问「这个 pid 还活着吗」，
              一个假 pid 会被判成已结束，于是「在跑几个」永远是 0，
              上限那条逻辑就测不到了，测试会变成一路绿灯的摆设。

            为什么是个工厂而不是一个函数：`start` 要登记到对应那个 TaskManager
              上。第一版写成一个共用闭包，结果第二个面板启动的任务全登记进了第一个
              管理器的 tasks 里，断言看到「一个都没启动」，而实际是记错地方了。
            """
            def _start(account, auto=None, extra_args=None, env_extra=None, **kw):
                aid = str(account.get("account_id"))
                proc = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(600)"])
                sleeping.append(proc)
                target._procs[aid] = proc
                target.tasks[aid] = tm_mod.Task(aid, log_path=os.path.join(tmp, "x.log"),
                                                state=tm_mod.S_RUNNING,
                                                started_at=time.time(), pid=proc.pid)
                return True, "已启动「{}」（测试桩）".format(aid)
            return _start

        mgr.start = make_fake(mgr)

        panel = W.TaskPanel(wiz)
        panel.tm = mgr
        panel._accounts = lambda: [_acc(a) for a in ids]
        panel.refresh()

        # ① 点了「全部启动」：一次只放一个，其余排队
        panel.start_all()
        check("一次只放一个（错峰就靠这个，不是另加一个定时器）",
              len(mgr.tasks) == 1, sorted(mgr.tasks))
        check("其余进了队列", panel._queue == ids[1:], panel._queue)
        check("确认框里说明了「先开几个、其余排队」",
              any(("排队" in b) for _t, b in asked["yesno"]), asked["yesno"][-1:])

        # 再喂两次（相当于后面几次 tick）：补到上限就停住，不会一路放完
        panel._pump_queue(force=True)
        check("补到上限（2 个）就停", len(mgr.tasks) == 2, sorted(mgr.tasks))
        panel._pump_queue(force=True)
        check("已经满了就一个都不放", len(mgr.tasks) == 2, sorted(mgr.tasks))
        check("队列还剩 3 个", panel._queue == ids[2:], panel._queue)

        panel.refresh()
        vals = [panel.tree.item(a, "values")[1] for a in panel.tree.get_children()]
        check("面板上排队的账号显示「排队中」（否则用户以为程序坏了）",
              vals.count("排队中") == 3, vals)
        check("排队提示里写了上限",
              "2" in panel.queue_lbl.cget("text"), panel.queue_lbl.cget("text"))

        # ② 空出一个位置，补进一个
        first = sleeping[0]
        first.kill()
        first.wait()
        time.sleep(0.3)
        panel._queue and panel._pump_queue(force=True)
        check("有空位后补进来 1 个", len(mgr.tasks) == 3, sorted(mgr.tasks))
        check("一次只补一个（不是把剩下的全放出来）",
              len(panel._queue) == 2, panel._queue)

        # ③ 还在满的时候，不放人
        before = len(mgr.tasks)
        panel._pump_queue(force=True)
        check("满了就不放（上限是真被遵守的，不是建议）",
              len(mgr.tasks) == before, (before, len(mgr.tasks)))

        # ④ 错峰：间隔没到就不放
        sleeping[1].kill()
        sleeping[1].wait()
        time.sleep(0.3)
        run_settings.save({"enabled": True, "max_at_once": 2, "stagger_seconds": 30,
                           "light_flags": True, "keep_awake": False})
        panel._last_fill = time.time()
        n_before = len(mgr.tasks)
        panel._pump_queue()                     # 没 force
        check("错峰间隔没到就不放（stagger=30 秒）",
              len(mgr.tasks) == n_before, (n_before, len(mgr.tasks)))
        panel._pump_queue(force=True)
        check("force 时不受错峰限制", len(mgr.tasks) == n_before + 1)

        # ⑤ 全部停止要清空队列
        panel._queue = list(ids)
        panel.stop_all()
        check("全部停止会清空队列（否则浏览器还在往外冒）",
              panel._queue == [], panel._queue)
        check("并且如实说了取消了几个排队",
              "取消" in panel.msg.cget("text"), panel.msg.cget("text"))

        # ⑥ 关面板时如果还有排队，必须先问
        panel._queue = ["Account 04", "Account 05"]
        asked["yesno"].clear()
        panel.close()
        check("有排队时关面板会先确认", len(asked["yesno"]) == 1, asked["yesno"])
        check("确认文案里说了「不会启动」",
              asked["yesno"] and "不会启动" in asked["yesno"][0][1],
              asked["yesno"][:1])
        panel = None

        # ⑦ 设置关掉 = 完全不排队
        run_settings.save({"enabled": False})
        mgr2 = tm_mod.TaskManager()
        mgr2.state_path = os.path.join(tmp, "task_state2.json")
        mgr2.runs_dir = os.path.join(tmp, "runs2")
        mgr2.start = make_fake(mgr2)
        panel2 = W.TaskPanel(wiz)
        panel2.tm = mgr2
        panel2._accounts = lambda: [_acc(a) for a in ids]
        panel2.start_all()
        # 关掉优化时不排队，但仍然「一次放一个」，只是上限大得等于账号数。
        # 这个差别值得留下来：错峰是独立的，不该跟着上限一起消失。
        for _ in range(len(ids)):
            panel2._pump_queue(force=True)
        check("关掉多开优化 → 5 个全都起来、没有排队",
              len(mgr2.tasks) == 5 and panel2._queue == [],
              (sorted(mgr2.tasks), panel2._queue))
        check("并且确认框里说清楚「会立刻各开一个」",
              any("立刻" in b for _t, b in asked["yesno"]), asked["yesno"][-1:])
    finally:
        W.messagebox.askyesno = old_yesno
        W.messagebox.showwarning = old_warn
        for p in sleeping:
            try:
                p.kill()
            except Exception:
                pass
        for p in (panel, panel2):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        W._TASK_MANAGER = None
        # 真实文件核对：这些是用户真实的文件（守卫当初记的路径）
        for path, was in real.items():
            now = (os.path.exists(path),
                   os.path.getmtime(path) if os.path.exists(path) else None)
            check("没动过 {}".format(os.path.basename(path)), now == was, (was, now))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


ALL = {"a": a_settings, "b": b_memory, "c": c_flags,
       "d": d_wired_into_driver, "e": e_queue}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only = ""
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    # 全集就是 ALL 的键，不要再手写一份节名清单：第一版写了 "abcdefg"，而 ALL 里只有
    #   a~e，f、g 两节每次都报「抛异常」，看着像真坏了，其实是清单和实现对不上。
    wanted = only or "".join(ALL.keys())

    print("=" * 62)
    print("多开减负自检（临时目录：见各节输出）")
    print("=" * 62)

    for key in ALL:
        if key not in wanted:
            continue
        try:
            ALL[key]()
        except SystemExit as exc:            # 单个用例不该把整套掀掉
            FAIL.append("{} 触发 SystemExit({})".format(key, exc))
            print("  FAIL [{}] 触发 SystemExit({})".format(key, exc))
        except Exception:
            import traceback
            FAIL.append("{} 抛异常".format(key))
            print("  FAIL [{}] 抛异常：\n{}".format(key, traceback.format_exc()))

    print("\n" + "=" * 62)
    print("通过 {} 项，失败 {} 项{}".format(
        len(PASS), len(FAIL), "，跳过 {} 项".format(len(SKIP)) if SKIP else ""))
    for n in FAIL:
        print("   FAIL: " + n)

    # 收尾兜底：万一哪处漏了守卫，真实日志被追加过就按字节截回去并报出来
    healed = GUARD.heal_appended()
    if healed:
        print("\n!! 有地方漏了 worker 守卫，真实日志被写过（已按字节还原）：")
        for path, info in healed:
            print("   {}  剔掉 {}".format(path, info))
    return GUARD.verify() or (1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
