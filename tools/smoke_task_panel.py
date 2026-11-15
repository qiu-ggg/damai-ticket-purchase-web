# -*- coding: utf-8 -*-
"""任务面板的真实链路冒烟：用 `task_manager` 起一个真的 `account_runner.py` 子进程。

和 `check_task_manager.py` 的分工：① `check_task_manager.py` 用假 runner 验状态机
（起、停、判定、重开……），不碰真环境，可以进常规回归；② 本脚本验的是接线：真实调度器
能不能被无窗口启动、日志能不能落到文件、退出之后状态判得对不对。

它故意用一个不存在的账号，于是 `account_runner` 会立刻「找不到账号，没有可运行的
账号」然后退出。不开浏览器、不碰真实配置、几秒钟跑完。

它会真的起一个 python 子进程，子进程是全新的解释器，会重新读一遍自己那份 `accounts.py`，
`probe_env()` 对当前进程的重定向管不到它（这就是第 7 次事故的形状）。两道保险：① 给子
进程一个隔离的日志路径（`DAMAI_ACCOUNTS_LOG`），它从头到尾不碰真实文件；② 外面还套着
`guard_worker_logs()` 当绊线，万一隔离没生效，收尾会按字节还原并报出来，而不是悄悄写脏。

用法：
    cd D:/damai-ticket-purchase        # tools/ 在仓库根，不在 web 里了
    python tools/smoke_task_panel.py
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _probe_env import probe_env, guard_worker_logs                  # noqa: E402

guard = probe_env()                    # 必须先于 import accounts / task_manager

import task_manager as tm                                            # noqa: E402

FAKE_ID = "smoke_no_such_account"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             ("  <<< " + str(extra)) if extra else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="smoke_task_")
    # runs/ 也指到临时目录：冒烟不在项目里留任何东西
    tm.RUNS_DIR = os.path.join(tmp, "runs")
    # 子进程的账号日志也指到临时目录（隔离，而不是「写完再还原」）
    child_log = os.path.join(tmp, "child-accounts.log")

    mgr = tm.TaskManager()
    check("runs/ 被隔离到临时目录", mgr.state_path.startswith(tmp), mgr.state_path)

    with guard_worker_logs(guard) as wl:
        ok, msg = mgr.start({"account_id": FAKE_ID},
                            env_extra={"DAMAI_ACCOUNTS_LOG": child_log})
        check("面板能启动真实的 account_runner.py", ok, msg)

        row, deadline = None, time.time() + 40
        while time.time() < deadline:
            row = {r["aid"]: r for r in mgr.snapshot([{"account_id": FAKE_ID}])}[FAKE_ID]
            if row["state"] in tm.TERMINAL:
                break
            time.sleep(0.3)

        check("跑完了，没挂住（没控制台时 stdin=DEVNULL 生效）",
              bool(row) and row["state"] in tm.TERMINAL, row and row["state"])
        check("判成「已失败」而不是「已抢到」",
              row and row["state"] == tm.S_FAILED, row and row["state"])
        log = mgr.read_log(FAKE_ID)

        # 子进程起来后第一件事就是读账号清单，这个假账号不在里面，所以报「找不到账号」。
        hit_run = ("找不到账号" in log) or ("没有可运行的账号" in log)
        check("子进程的输出真的落进了日志文件（找到了「找不到账号」）",
              hit_run, log[:160].replace("\n", " | "))
        check("日志文件在 runs/ 下（不是别处）",
              os.path.dirname(mgr.tasks[FAKE_ID].log_path) == os.path.join(tmp, "runs"),
              mgr.tasks[FAKE_ID].log_path)
        last = row.get("last_line") or ""
        check("「最近一行」取到的是有信息量的那行，不是分隔线",
              "账号" in last, last)

    guard_line = wl.check()
    print("\n  [worker guard] " + guard_line)
    check("真实 accounts.log 一行都没被写（子进程日志已隔离）",
          guard_line.startswith("干净"), guard_line)
    check("子进程确实写了日志 —— 只是写进了隔离文件（不是把日志关了）",
          os.path.exists(child_log) and os.path.getsize(child_log) > 0,
          os.path.getsize(child_log) if os.path.exists(child_log) else "文件不存在")

    # 下面两条现在是冗余的第二意见：隔离生效的话它们必然通过。
    # 留着是因为「隔离万一失效」时，它们是最后一道能发现问题的网。
    for p, was in guard.watched.items():
        if os.path.basename(p) != "accounts.log":
            continue
        want = was[0] if was else None
        now = os.path.getsize(p) if os.path.exists(p) else None
        check("真实 accounts.log 仍是原字节数（%r）" % want, now == want, "现在 %r" % now)
        check("并且仍是完整行（末尾是换行）",
              (not os.path.exists(p)) or open(p, "rb").read().endswith((b"\n", b"\r\n")))

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 58)
    print("通过 {} / {} 项".format(len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("  - " + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
