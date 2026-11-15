# -*- coding: utf-8 -*-
"""实时状态链路测试（live_status + runner 的 print 转发 + 状态识别）。

覆盖的心智模型（2026-10-09 用户要求）：不管是任务面板还是启动页起的任务，都算到那个
  账号的状态里，并要识别出登录输错 / 需要验证码 / 待提交订单 / 刷新中 / 轮询中。

链路的两条腿：子进程侧 `account_runner._RunnerTee` 把 print 补进日志，并按
  `_STATE_BY_OUTPUT` 把「正在干什么」写进 logs/live/<账号>.json；面板侧
  `live_status.read_all()` 读出来（死进程残留不算），`task_manager.kill_tree` 用来停
  「不是面板起的」任务。面板侧在 check_wizard_ui 的 [Q3] 组，这里钉子进程侧的机制。

所有落盘（live 目录 / accounts.log）都重定向到临时目录，真的那份一个字节不动。
用法：`.build-venv/Scripts/python.exe tools/check_live_status.py`
"""

import io
import json
import os
import sys
import tempfile
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "damai"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             ("  <<< " + str(extra)) if extra else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="live-status-")
    print("临时工作目录:", tmp)

    import accounts as acc_store
    import live_status as ls
    import task_manager as tm_mod

    # 落盘重定向：先把"真的那份"记下来，结尾对账
    real = {
        "acc_log": acc_store.LOG_PATH,
        "live_dir": ls.LIVE_DIR,
    }
    real_stat = {k: (os.path.exists(v), os.path.getmtime(v) if os.path.exists(v) else None)
                 for k, v in real.items()}

    # 铁律：要往磁盘写东西的测试，先断言目标路径在临时目录里。
    acc_store.LOG_PATH = os.path.join(tmp, "accounts.log")
    ls.LIVE_DIR = os.path.join(tmp, "live")
    assert ls.LIVE_DIR.startswith(tmp) and acc_store.LOG_PATH.startswith(tmp), \
        "落盘没重定向，停手"

    import account_runner as AR

    print("\n[A] live_status：写进去、读出来、死进程不算")
    ls.begin({"account_id": "Account 01"}, pid=os.getpid())
    got = ls.read_all()
    check("begin 之后读得到自己（pid 活着）",
          "Account 01" in got and got["Account 01"]["state"] == "启动中", got)

    ls.update("轮询中", "正在盯按钮")
    got = ls.read_all()
    check("update 之后状态/详情都读得到",
          got["Account 01"]["state"] == "轮询中"
          and got["Account 01"]["detail"] == "正在盯按钮", got)

    # 读到的必须是完整 JSON（写入走临时文件+原子替换，不会有半截）
    raw = io.open(ls.path_for("Account 01"), encoding="utf-8").read()
    try:
        json.loads(raw)
        whole = True
    except Exception:
        whole = False
    check("状态文件永远是完整 JSON（原子替换，不写半截）", whole, raw[:60])

    ls.end({"account_id": "Account 01"})
    check("end 之后文件撤掉、读不到了", ls.read_all() == {}, ls.read_all())

    # 关键契约：文件在 ≠ 活着（强杀不留遗嘱）。
    #   伪造一个「文件在、pid 死了」的残留，读的时候必须过滤掉。
    dead = ls.path_for("Account 09")
    os.makedirs(ls.LIVE_DIR, exist_ok=True)
    with io.open(dead, "w", encoding="utf-8") as f:
        json.dump({"account": "Account 09", "pid": 999999999,
                   "state": "轮询中", "detail": "", "started": time.time()}, f)
    check("文件在但 pid 早没了 → 不算在跑（强杀不留遗嘱）",
          "Account 09" not in ls.read_all(), ls.read_all())
    os.remove(dead)

    print("\n[B] runner 的 print 转发：原来只进控制台的，现在也进日志 + 认状态")
    buf = io.StringIO()
    tee = AR._RunnerTee(buf)
    tee.account = {"account_id": "Account 01"}
    ls.begin({"account_id": "Account 01"}, pid=os.getpid())

    tee.write("***抢票未开始，刷新等待开始***\n")
    check("print 照常转发给控制台（行为不变）",
          buf.getvalue() == "***抢票未开始，刷新等待开始***\n", buf.getvalue())
    check("这句被认成「刷新中」，写进了实时状态",
          ls.read_all()["Account 01"]["state"] == "刷新中", ls.read_all())

    tee.write("***开始轮询检测预订按钮***\n")
    check("「轮询检测」这句被认成「轮询中」",
          ls.read_all()["Account 01"]["state"] == "轮询中", ls.read_all())

    tee.write("***准备提交订单***\n")
    check("「准备提交订单」这句被认成「待提交订单」",
          ls.read_all()["Account 01"]["state"] == "待提交订单", ls.read_all())

    tee.write("***订单已提交***\n")
    check("「订单已提交」这句被认成「已提交，待支付」",
          ls.read_all()["Account 01"]["state"] == "已提交，待支付", ls.read_all())

    # 日志：print 的行走 tee 补进日志（带 [账号] 前缀）
    log_text = io.open(acc_store.LOG_PATH, encoding="utf-8").read()
    for line in ("***抢票未开始，刷新等待开始***", "***开始轮询检测预订按钮***",
                 "***准备提交订单***"):
        check("补进日志：{}".format(line),
              "[Account 01] {}".format(line) in log_text, log_text[-120:])

    # say 的行不许被补第二遍（它自己已经写过）
    before = log_text.count("这条是 say 的")
    tee.write(acc_store.say("这条是 say 的输出", {"account_id": "Account 01"}) + "\n")
    after = io.open(acc_store.LOG_PATH, encoding="utf-8").read().count("这条是 say 的")
    check("say 的行不会被补成两条（行首 [xx] 的跳过）", after == before + 1,
          (before, after))

    # 不带账号时什么都不写（status / login 不装 tee 就是这个道理）
    tee.account = None
    buf2 = io.StringIO()
    tee2 = AR._RunnerTee(buf2)
    tee2.write("***抢票未开始，刷新等待开始***\n")
    check("没设账号时只转发、不记状态（不给别的账号乱贴标签）",
          buf2.getvalue().startswith("***") and
          ls.read_all()["Account 01"]["state"] == "已提交，待支付")
    ls.end({"account_id": "Account 01"})

    print("\n[C] 状态词表里的每一句，都必须在真实源码里存在")
    runner_src = io.open(os.path.join(BASE, "account_runner.py"),
                         encoding="utf-8").read()
    concert_src = io.open(os.path.join(BASE, "damai", "concert.py"),
                          encoding="utf-8").read()
    for mark, state in AR._STATE_BY_OUTPUT:
        where = ("account_runner" if mark in runner_src else
                 "concert" if mark in concert_src else None)
        check("「{}」→「{}」：词在 {} 里".format(mark, state, where or "???"),
              where is not None)

    print("\n[D] 「登录输错 / 需要验证码」的识别（假 driver，只读不算真站）")

    class _FakeDriver(object):
        def __init__(self, text):
            self.text = text

        def execute_script(self, js, words):
            return [w for w in words if w in self.text]

    d1 = _FakeDriver("账号或密码错误，请重新输入")
    check("页面上写着「账号或密码错误」→ 识别出来（只报最长的那句）",
          AR.detect_login_error(d1) == ["账号或密码错误"],
          AR.detect_login_error(d1))
    d2 = _FakeDriver("请拖动滑块完成验证")
    check("页面出现滑块 → 人工验证识别出来",
          AR.detect_manual_check(d2) == ["滑块", "拖动滑块"],
          AR.detect_manual_check(d2))
    d3 = _FakeDriver("正常页面，什么都没有")
    check("正常页面一个都不误报",
          AR.detect_login_error(d3) == [] and AR.detect_manual_check(d3) == [])
    # 防撞车：词表里不许有光杆短词（「密码」两个字不该被别的提示撞上）
    check("输错词表里没有光杆「密码」「验证」这类短词",
          all(len(w) >= 4 for w in AR.LOGIN_ERROR_HINTS), AR.LOGIN_ERROR_HINTS)

    print("\n[E] kill_tree：连它起的子孙一起收（面板停「启动页起的任务」用的就是它）")
    _real_popen = AR.subprocess.Popen
    sleeper = _real_popen([sys.executable, "-c", "import time; time.sleep(600)"])
    check("前提：靶子进程活着", tm_mod._alive_fast(sleeper.pid), sleeper.pid)
    ok = tm_mod.kill_tree(sleeper.pid, wait=8.0)
    check("kill_tree 之后进程真没了", ok and not tm_mod._alive_fast(sleeper.pid),
          (ok, sleeper.poll()))
    try:
        sleeper.kill()
    except Exception:
        pass

    # [G] 「跑完不自动关浏览器」（2026-10-09 用户要求）：面板路径得守着，等用户手动关。
    #   chrome 由 chromedriver 起，客户端一退出 chromedriver 跟着退时会把浏览器一并收走，
    #   老写法「说完一句就 return」正是「跑完浏览器没了」的原因。
    print("\n[G] 跑完不自动关浏览器：面板路径守着，等用户手动关")

    class _FakeCon(object):
        class _D(object):
            def __init__(self, seq):
                self.seq = list(seq)      # True=窗口还在；"err"=驱动断了；False=关了
                self.probes = []

            @property
            def window_handles(self):
                v = self.seq.pop(0) if self.seq else False
                self.probes.append(v)
                if v == "err":
                    raise Exception("invalid session")
                return ["w1"] if v else []

            @property
            def current_url(self):
                return "https://example.test/"

        def __init__(self, seq):
            self.driver = self._D(seq)
            self.finished = 0

        def finish(self):
            self.finished += 1

    # ① 窗口还开着就一直等：第 3 拍才关掉，恰好在第 3 拍返回
    c1 = _FakeCon([True, True, False])
    AR._hold_until_all_closed([(c1, {})], poll=0.01)
    check("窗口还开着就一直守着（探测到关了才走）",
          c1.driver.probes == [True, True, False], c1.driver.probes)
    check("守候本身不会关浏览器（finish 一次都没调）", c1.finished == 0, c1.finished)

    # ② 驱动连接断了（浏览器被关了）也算「已关闭」
    c2 = _FakeCon(["err"])
    AR._hold_until_all_closed([(c2, {})], poll=0.01)
    check("驱动连接断了也算浏览器已关（立刻收尾）",
          c2.driver.probes == ["err"], c2.driver.probes)

    # ③ 多个账号：全部关掉才算完
    ca = _FakeCon([False])
    cb = _FakeCon([True, False])
    AR._hold_until_all_closed([(ca, {}), (cb, {})], poll=0.01)
    check("多个浏览器要全部关掉才收尾（一个先关、另一个后关）",
          cb.driver.probes == [True, False], cb.driver.probes)

    # ④ 面板路径端到端：hold_browsers 在没有交互输入时
    #    不主动关浏览器、守到用户关了才收尾（monkeypatch input 抛 EOF）
    c3 = _FakeCon([True, False])
    _saved_input = AR.__dict__.get("input")
    def _eof(*_a, **_k):
        raise EOFError()
    AR.input = _eof
    try:
        AR.hold_browsers([(c3, {"account_id": "Account 01"})])
    finally:
        if _saved_input is None:
            AR.__dict__.pop("input", None)
        else:
            AR.input = _saved_input
    check("面板路径跑完：守到用户关窗后才收尾（finish 那一刻浏览器早关了）",
          c3.finished == 1 and c3.driver.probes == [True, False],
          (c3.finished, c3.driver.probes))

    print("\n[F] 用户真实文件没被测试碰过")
    for k, path in real.items():
        now = (os.path.exists(path),
               os.path.getmtime(path) if os.path.exists(path) else None)
        check("没动过 {}".format(os.path.basename(path) or path),
              now == real_stat[k], (real_stat[k], now))

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 56)
    print("通过 {} / {} 项".format(len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print("  -", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
