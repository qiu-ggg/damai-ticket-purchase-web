# -*- coding: utf-8 -*-
"""任务面板数据层（task_manager.py）的回归测试。

全程不启动浏览器、不碰真账号：每个用例都在临时目录里放一个假的
`account_runner.py`，它只打印几行、睡一会儿、或者按剧本退出。
这样验的是起停与状态机本身，和抢票逻辑无关，抢票那条链路的回归在
check_order_flow.py 里。

重点钉住这几件事（都是「看起来能用、其实会坑人」的地方）：① 无窗口 + 日志重定向真的
生效（面板看不到黑窗口，全指望这个日志文件）；② 「停止」停的是整棵进程树，且只停指定
的那个账号；③ 进程退出后状态要靠「退出码 + 日志尾巴」还原成 已抢到 / 已失败 / 已结束，
而不是一律显示「已结束」；④ 向导关掉再打开（句柄丢失）时，靠残留 pid + 日志仍能给出
如实的状态，查不到就报「已失联」，不编造结果。

用法：
    cd D:/damai-ticket-purchase        # tools/ 在仓库根，不在 web 里了
    <python> tools/check_task_manager.py
    <python> tools/check_task_manager.py --only s3,s5
"""

import argparse
import io
import os
import shutil
import sys
import tempfile
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
PROJ = os.path.dirname(BASE)      # tools/ 挪到仓库根：仓库根在这
sys.path.insert(0, BASE)

import task_manager as tm_mod                                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             ("  <<< " + str(extra)) if extra else ""))


FAKE_RUNNER = '''# -*- coding: utf-8 -*-
"""假抢票进程：只为了测起停，什么都不抢。"""
import os, sys, time

mode = os.environ.get("FAKE_MODE", "sleep")
aid = sys.argv[2] if len(sys.argv) > 2 else "?"
print("[%s] 开始处理" % aid, flush=True)
print("argv: %s" % (sys.argv[1:],), flush=True)

if mode == "success":
    print("***订单已提交，当前停在支付环节***", flush=True)
    sys.exit(0)
elif mode == "end":
    print("nothing happened", flush=True)
    sys.exit(0)
elif mode == "fail":
    print("出错：假装炸了", flush=True)
    sys.exit(3)
elif mode == "boom":
    sys.exit(7)
elif mode == "eof":
    # 模拟 hold_browsers() 里的 input()：stdin 是 DEVNULL 时立刻 EOF
    try:
        line = input("处理完后按回车…")
    except EOFError:
        print("no interactive input, keep browser open", flush=True)
    sys.exit(0)
else:
    time.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
    print("done", flush=True)
    sys.exit(0)
'''


def mk_env(tmp):
    """在临时目录里造一份「可运行」的骨架，返回 (task_manager 实例, 启动函数)。"""
    with io.open(os.path.join(tmp, "account_runner.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_RUNNER)
    return tm_mod.TaskManager(base_dir=tmp, python_exe=sys.executable)


def acc(aid, **kw):
    a = {"account_id": aid, "enabled": True, "auto_login": True, "users": ["甲"],
         "prices": ["380"], "quantity": 1}
    a.update(kw)
    return a


def wait_state(tm, aid, want, timeout=8.0):
    """等某个账号变成想要的状态（状态推进靠 snapshot 触发的结算）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        rows = {r["aid"]: r for r in tm.snapshot([acc(aid)])}
        last = rows.get(aid, {}).get("state")
        if last in (want if isinstance(want, (list, tuple, set)) else (want,)):
            return True
        time.sleep(0.1)
    return last


# 用例

def s1_start_and_running(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    ok, msg = tm.start(acc("A1"))
    check("s1.1 start 返回成功", ok, msg)
    row = {r["aid"]: r for r in tm.snapshot([acc("A1")])}["A1"]
    check("s1.2 状态是「启动中 / 运行中」", row["state"] in (tm_mod.S_STARTING, tm_mod.S_RUNNING),
          row["state"])
    check("s1.3 记到了 pid", bool(row["pid"]), row)
    check("s1.4 pid 真的存活", tm_mod.pid_alive(row["pid"]))
    got = wait_state(tm, "A1", tm_mod.S_RUNNING, timeout=6)
    check("s1.5 三秒内转为「运行中」", got is True, got)
    tm.stop_all()
    return tm


def s2_log_is_redirected(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    tm.start(acc("A2"))
    wait_state(tm, "A2", tm_mod.S_RUNNING, timeout=6)
    text = tm.read_log("A2")
    check("s2.1 任务输出写进了日志文件", "开始处理" in text, text[:120])
    check("s2.2 日志里带着账号名", "[A2]" in text, text[:120])
    check("s2.3 日志文件在 runs/ 下",
          os.path.dirname(tm.tasks["A2"].log_path) == os.path.join(tmp, "runs"),
          tm.tasks["A2"].log_path)
    check("s2.4 last_line 取到了有意义的一行",
          "argv" in tm.last_line("A2") or "开始处理" in tm.last_line("A2"),
          tm.last_line("A2"))
    tm.stop_all()


def s3_stop_kills_process(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    tm.start(acc("B1"))
    wait_state(tm, "B1", tm_mod.S_RUNNING, timeout=6)
    pid = tm.tasks["B1"].pid
    ok, msg = tm.stop("B1")
    check("s3.1 stop 返回成功", ok, msg)
    check("s3.2 进程真的没了", not tm_mod.pid_alive(pid), pid)
    row = {r["aid"]: r for r in tm.snapshot([acc("B1")])}["B1"]
    check("s3.3 状态是「已停止」", row["state"] == tm_mod.S_STOPPED, row["state"])
    check("s3.4 标记为「用户停的」而不是「跑完的」", row["stopped_by_user"] is True, row)
    ok2, msg2 = tm.stop("B1")
    check("s3.5 重复 stop 不报错", ok2 is True, msg2)


def s4_stop_only_target(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    tm.start(acc("C1"))
    tm.start(acc("C2"))
    wait_state(tm, "C1", tm_mod.S_RUNNING, timeout=6)
    wait_state(tm, "C2", tm_mod.S_RUNNING, timeout=6)
    p1, p2 = tm.tasks["C1"].pid, tm.tasks["C2"].pid
    ok, _ = tm.stop("C1")
    check("s4.1 停 C1 成功", ok)
    check("s4.2 C1 进程没了", not tm_mod.pid_alive(p1), p1)
    check("s4.3 C1 没连累 C2 —— C2 还在跑", tm_mod.pid_alive(p2), p2)
    rows = {r["aid"]: r for r in tm.snapshot([acc("C1"), acc("C2")])}
    check("s4.4 C2 状态仍是「运行中」", rows["C2"]["state"] == tm_mod.S_RUNNING,
          rows["C2"]["state"])
    tm.stop_all()


def s5_success_state(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_MODE"] = "success"
    tm.start(acc("D1"))
    got = wait_state(tm, "D1", tm_mod.S_SUCCESS, timeout=10)
    check("s5.1 日志里出现成功标记 → 判「已抢到」", got is True, got)
    check("s5.2 记了退出码 0", tm.tasks["D1"].exit_code == 0, tm.tasks["D1"].exit_code)
    check("s5.3 结束时间已填", bool(tm.tasks["D1"].ended_at))
    os.environ["FAKE_MODE"] = "sleep"


def s6_failure_state(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_MODE"] = "boom"          # 只退出码非 0，不打错误文字
    tm.start(acc("E1"))
    got = wait_state(tm, "E1", tm_mod.S_FAILED, timeout=10)
    check("s6.1 退出码非 0 → 判「已失败」", got is True, got)
    check("s6.2 退出码是 7", tm.tasks["E1"].exit_code == 7, tm.tasks["E1"].exit_code)

    os.environ["FAKE_MODE"] = "fail"          # 退出码非 0 且日志有「出错：」
    tm.start(acc("E2"))
    got = wait_state(tm, "E2", tm_mod.S_FAILED, timeout=10)
    check("s6.3 日志有「出错：」也判「已失败」", got is True, got)
    os.environ["FAKE_MODE"] = "sleep"


def s7_plain_finish_not_success(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_MODE"] = "end"           # 正常退出但没走到支付环节
    tm.start(acc("F1"))
    got = wait_state(tm, "F1", tm_mod.S_FINISHED, timeout=10)
    check("s7.1 正常退出但没抢到 → 「已结束」，不是「已抢到」", got is True, got)
    check("s7.2 不会被误判成成功", tm.tasks["F1"].state != tm_mod.S_SUCCESS)
    os.environ["FAKE_MODE"] = "sleep"


def s8_reopen_sees_history(tmp):
    """向导关掉再打开：句柄没了，但记录还在，状态要如实。"""
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    tm.start(acc("G1"))
    tm.start(acc("G2"))
    wait_state(tm, "G1", tm_mod.S_RUNNING, timeout=6)
    wait_state(tm, "G2", tm_mod.S_RUNNING, timeout=6)
    tm.stop("G2")                              # G2 已停，G1 还在跑

    tm2 = tm_mod.TaskManager(base_dir=tmp, python_exe=sys.executable)
    rows = {r["aid"]: r for r in tm2.snapshot([acc("G1"), acc("G2")])}
    check("s8.1 新实例读得到历史任务", "G1" in rows and rows["G1"]["pid"],
          rows.get("G1"))
    check("s8.2 重开后仍能认出手上没句柄但还活着的任务",
          rows["G1"]["state"] == tm_mod.S_RUNNING, rows["G1"]["state"])
    check("s8.3 已停的仍显示「已停止」", rows["G2"]["state"] == tm_mod.S_STOPPED,
          rows["G2"]["state"])
    tm.stop_all()

    # 假造一条「pid 早就不在了、也不是用户停的」的记录，必须报「已失联」
    tm3 = tm_mod.TaskManager(base_dir=tmp, python_exe=sys.executable)
    tm3.tasks["G9"] = tm_mod.Task("G9", log_path=os.path.join(tmp, "runs", "G9.log"),
                                  pid=999999, state=tm_mod.S_RUNNING, started_at=time.time() - 9)
    rows = {r["aid"]: r for r in tm3.snapshot([acc("G9")])}
    check("s8.4 进程没了又查不到退出码 → 老实报「已失联」",
          rows["G9"]["state"] == tm_mod.S_LOST, rows["G9"]["state"])


def s9_bad_python(tmp):
    tm = mk_env(tmp)
    tm.python_exe = os.path.join(tmp, "no-such-python.exe")
    ok, msg = tm.start(acc("H1"))
    check("s9.1 解释器不存在 → 启动失败", ok is False, msg)
    row = {r["aid"]: r for r in tm.snapshot([acc("H1")])}["H1"]
    check("s9.2 状态是「未启动」而不是卡在「启动中」", row["state"] == tm_mod.S_NOT_STARTED,
          row["state"])


def s10_stop_unknown(tmp):
    tm = mk_env(tmp)
    ok, msg = tm.stop("从没跑过")
    check("s10.1 停一个没记录的账号 → 返回失败但不抛异常", ok is False, msg)
    check("s10.2 提示里说清了原因", "没有运行记录" in msg, msg)


def s11_pid_alive_semantics(tmp):
    check("s11.1 自己一定活着", tm_mod.pid_alive(os.getpid()))
    check("s11.2 一个不存在的 pid 判死", not tm_mod.pid_alive(999999))
    check("s11.3 非法输入不抛异常", not tm_mod.pid_alive("abc"))
    check("s11.4 pid=0 判死", not tm_mod.pid_alive(0))


def s12_stdin_devnull_does_not_hang(tmp):
    """任务进程里的 input() 必须立刻 EOF，否则面板上的任务会永远「运行中」。"""
    tm = mk_env(tmp)
    os.environ["FAKE_MODE"] = "eof"
    tm.start(acc("I1"))
    got = wait_state(tm, "I1", tm_mod.S_FINISHED, timeout=10)
    check("s12.1 stdin 接 DEVNULL：input() 直接 EOF，进程干净退出",
          got is True, got)
    check("s12.2 日志里能看到走了「无交互输入」那条分支",
          "no interactive input" in tm.read_log("I1"), tm.read_log("I1")[-160:])
    os.environ["FAKE_MODE"] = "sleep"


def s13_stop_all(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    for aid in ("J1", "J2", "J3"):
        tm.start(acc(aid))
        wait_state(tm, aid, tm_mod.S_RUNNING, timeout=6)
    pids = [tm.tasks[a].pid for a in ("J1", "J2", "J3")]
    done, bad = tm.stop_all()
    check("s13.1 三个都被停掉", sorted(done) == ["J1", "J2", "J3"] and not bad,
          (done, bad))
    check("s13.2 三个进程都不在了", not any(tm_mod.pid_alive(p) for p in pids), pids)


def s14_start_twice_guard(tmp):
    tm = mk_env(tmp)
    os.environ["FAKE_SLEEP"] = "30"
    ok1, _ = tm.start(acc("K1"))
    wait_state(tm, "K1", tm_mod.S_RUNNING, timeout=6)
    ok2, msg = tm.start(acc("K1"))
    check("s14.1 同一个账号不会起出两个任务", ok1 and ok2 is False, msg)
    tm.stop_all()


TESTS = [
    ("s1", "启动与运行状态", s1_start_and_running),
    ("s2", "无窗口 + 日志重定向", s2_log_is_redirected),
    ("s3", "停止：杀掉进程并记为「已停止」", s3_stop_kills_process),
    ("s4", "停止只影响目标账号", s4_stop_only_target),
    ("s5", "成功状态识别", s5_success_state),
    ("s6", "失败状态识别", s6_failure_state),
    ("s7", "正常退出 ≠ 抢到", s7_plain_finish_not_success),
    ("s8", "重开向导后状态如实", s8_reopen_sees_history),
    ("s9", "解释器缺失", s9_bad_python),
    ("s10", "停未知账号", s10_stop_unknown),
    ("s11", "存活判定语义", s11_pid_alive_semantics),
    ("s12", "stdin=DEVNULL 不挂住", s12_stdin_devnull_does_not_hang),
    ("s13", "全部停止", s13_stop_all),
    ("s14", "重复启动保护", s14_start_twice_guard),
]

ALL_CASES = tuple(TESTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑部分用例，逗号分隔，如 s3,s5")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    print("=" * 62)
    print("任务面板数据层回归（不启浏览器）")
    print("=" * 62)

    for key, title, fn in TESTS:
        if only and key not in only:
            continue
        tmp = tempfile.mkdtemp(prefix="taskmgr-{} -".format(key).replace(" ", ""))
        print("\n[{}] {}".format(key, title))
        try:
            fn(tmp)
        except Exception as e:
            import traceback
            traceback.print_exc()
            check("{}.x 用例本身没跑完".format(key), False, repr(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 {} / 失败 {}".format(len(PASS), len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("  FAIL  " + n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
