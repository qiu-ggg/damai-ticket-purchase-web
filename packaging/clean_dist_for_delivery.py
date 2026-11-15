# -*- coding: utf-8 -*-
"""交付前一键盘：把 `dist/大麦抢票/` 里的运行时数据挪走，留下纯交付态。
用法（项目根目录）：`.build-venv/Scripts/python packaging/clean_dist_for_delivery.py`

跑过一次 `大麦抢票.exe` 后，交付目录里就会长出 `accounts.json` / `logs/` /
`profiles/`（登录态！）这些本机数据，直接发给客户等于把自己的账号配置和登录 Cookie
一起送出去。

只挪走不删除：落到 `_backup/delivery-clean-<时间戳>/`，想自用（接着抢票、看日志）
从那恢复就行。可以反复跑（幂等），已经是干净的就说一声。
"""
from __future__ import print_function

import io
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "dist", "\u5927\u9ea6\u62a2\u7968")

# 和 build.py 的 RUNTIME_JUNK / RUNTIME_JUNK_DIRS 保持一致，另外加上「认不出按钮/协议时
#   落盘的诊断快照」，那几份里写着演出名和观演人姓名。
JUNK_FILES = [
    "accounts.json", "accounts.log", "credentials.json",
    "damai_wizard.log", "notify_secret.txt", "notify_state.json", "notify.json",
    "order_page_dump.txt", "order_page_submit_dump.txt",
    "order_page_protocol_dump.txt",
    # 这两份是「认不出场次/日期」时的现场快照：里面写着演出名 + 目标场次 + URL
    "date_pick_dump.txt", "session_pick_dump.txt",
]
JUNK_DIRS = ["profiles", "runs", "logs", "damai"]


def main():
    if not os.path.isdir(APP_DIR):
        print("找不到交付目录：{}".format(APP_DIR))
        return 1

    # 先看看有没有东西要挪，已经是干净的就不用建备份目录了
    pending = [n for n in JUNK_FILES if os.path.exists(os.path.join(APP_DIR, n))]
    pending += [n + os.sep for n in JUNK_DIRS
                if os.path.isdir(os.path.join(APP_DIR, n))]
    if not pending:
        print("交付目录已经是干净的，什么都没动。")
        print("  {}".format(APP_DIR))
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(ROOT, "_backup", "delivery-clean-" + stamp)
    assert dest.startswith(os.path.join(ROOT, "_backup")), dest
    os.makedirs(dest, exist_ok=True)

    print("把运行时数据挪到：{}".format(dest))
    print()
    moved, failed = [], []
    for name in pending:
        src = os.path.join(APP_DIR, name.rstrip(os.sep))
        dst = os.path.join(dest, name.rstrip(os.sep))
        if not os.path.exists(src):
            continue
        try:
            shutil.move(src, dst)
            moved.append(name)
            print("  已挪走  {}".format(name))
        except OSError as e:
            failed.append((name, e))
            print("  ! 挪不动  {}  ({})".format(name, e))

    print()
    if failed:
        print("有几个挪不动，多半是浏览器还开着占着 profiles：")
        print("  先关掉那个（或那些）浏览器窗口，再跑一次这个脚本就行。")
        print("  提示：那些窗口是抢票脚本自己留下的 —— 用「大麦抢票.exe」")
        print("  跑过一次之后，它会一直开着等你手动支付。")
        return 2

    left = os.listdir(APP_DIR)
    print("现在交付目录里只剩：")
    for n in sorted(left):
        print("  {}".format(n))
    print()
    print("可以发给客户了。你的数据都在上面那个目录里，随时能恢复。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
