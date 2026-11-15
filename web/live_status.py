# -*- coding: utf-8 -*-
"""「现在跑到哪一步了」的实时状态出口，子进程写，面板 / 画面墙读。

为什么要有它（2026-10-09 用户要求）：
  「不管是任务面板启动的，还是启动页启动的，都算到那个账号的状态里；
   要能识别出 登录输错 / 需要验证码 / 待提交订单 / 刷新中 / 轮询中。」

  老路（启动页那两个绿色按钮）的进程句柄不在面板手里，面板路虽然有句柄、
  但任务状态也只有「运行中 / 已停止」这种粗的。两条路缺的是同一件东西：
  一个按账号说话、谁起的都能用的状态出口。

  做法：跑抢票的子进程每处理一个账号，就在 `logs/live/<账号>.json` 里写下
  pid + 细状态；面板每次刷新读一遍，贴到对应账号那一行上。

「文件在」不等于「活着」：任务是被 `taskkill /F` 强杀的（面板停止、
  系统清理都这么干），强杀不会跑任何清理代码，文件会留下。
  所以读的一侧一律用 `pid_alive` 过滤，别信文件本身。

写入走「临时文件 + 原子替换」：面板任何时刻去读，读到的要么是旧的一份
  完整的、要么是新的一份完整的，不会是半截 JSON。
"""

import json
import os
import sys
import time

try:
    import app_paths
    LIVE_DIR = os.path.join(app_paths.logs_dir(), "live")
except Exception:                                          # pragma: no cover
    LIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "logs", "live")

# 同一个状态不必每次输出都重写文件；超过这个间隔补写一次「心跳」，让面板上的「更新时间」不至于停在几分钟前。
HEARTBEAT_SEC = 5.0

_CUR = None          # 本进程当前在处理的账号（一份进程同时只处理一个）


def safe_name(account_id):
    """账号名转成能当文件名的形式（和 accounts / task_manager 同款做法）。"""
    safe = "".join(ch if ch.isalnum() else "_"
                   for ch in str(account_id or "")).strip("_")
    return safe or "account"


def path_for(account_id):
    return os.path.join(LIVE_DIR, safe_name(account_id) + ".json")


# 子进程侧（account_runner 用）

def begin(account, pid=None):
    """开始处理一个账号：把「我在这、我活着」写出去。"""
    global _CUR
    aid = str((account or {}).get("account_id") or "")
    if not aid:
        return
    _CUR = {
        "account": aid,
        "pid": int(pid or os.getpid()),
        "state": "启动中",
        "detail": "",
        "started": time.time(),
        "updated": 0.0,
        "_last": None,
        "_file": path_for(aid),
    }
    _write(force=True)


def update(state, detail=""):
    """记下「现在到哪一步了」。没 begin 过就静默忽略（status/login 不走这条）。"""
    if _CUR is None:
        return
    state = str(state or "").strip()
    if not state:
        return
    _CUR["state"] = state
    _CUR["detail"] = str(detail or "").strip()
    _write(force=False)


def end(account=None):
    """处理完一个账号：把文件撤掉。account 传了就对一下是不是同一个。"""
    global _CUR
    if _CUR is None:
        return
    if account is not None:
        aid = str((account or {}).get("account_id") or "")
        if aid and aid != _CUR.get("account"):
            return
    try:
        os.remove(_CUR["_file"])
    except Exception:
        pass
    _CUR = None


def _write(force):
    if _CUR is None:
        return
    now = time.time()
    unchanged = (_CUR["state"] == _CUR.get("_last"))
    if not force and unchanged and \
            now - (_CUR.get("updated") or 0) < HEARTBEAT_SEC:
        return
    _CUR["updated"] = now
    _CUR["_last"] = _CUR["state"]
    data = {k: v for k, v in _CUR.items() if not k.startswith("_")}
    tmp = _CUR["_file"] + ".tmp"
    try:
        os.makedirs(LIVE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _CUR["_file"])
    except Exception:
        pass


# 读的一侧（面板 / 画面墙用）

def read_all():
    """读出现在还活着的任务：{账号: {...}}。死进程的残留文件不会出现。"""
    out = {}
    try:
        names = os.listdir(LIVE_DIR)
    except Exception:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(LIVE_DIR, n), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue                       # 半截文件 / 坏文件，跳过
        if not isinstance(d, dict):
            continue
        aid = str(d.get("account") or "")
        if not aid or not pid_alive(d.get("pid")):
            continue
        out[aid] = d
    return out


_K32 = None


def _kernel32():
    """kernel32 的懒加载 + 参数类型声明全。

    句柄是 64 位的，不声明 argtypes / restype 就会被截成 32 位，
      现象是「进程明明活着却说不存在」（window_wall 那边踩过同一个坑）。
    """
    global _K32
    if _K32 is not None:
        return _K32
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    _K32 = k
    return k


def pid_alive(pid):
    """这个进程号还活着吗。跨平台（Windows 用 kernel32，其它走 os.kill 0）。"""
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except Exception:
            return False
        return True
    k = _kernel32()
    import ctypes
    from ctypes import wintypes
    h = k.OpenProcess(0x1000, False, pid)          # QUERY_LIMITED_INFORMATION
    if not h:
        h = k.OpenProcess(0x0400, False, pid)      # QUERY_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259                   # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


if __name__ == "__main__":                         # pragma: no cover
    # 命令行看一眼现状（排查用）：python live_status.py
    live = read_all()
    print("live 目录:", LIVE_DIR)
    if not live:
        print("现在没有记录中的活动任务。")
    for aid, d in sorted(live.items()):
        print("  {:<14} pid={:<7} {:<10} {}".format(
            aid, d.get("pid"), d.get("state"), d.get("detail") or ""))
