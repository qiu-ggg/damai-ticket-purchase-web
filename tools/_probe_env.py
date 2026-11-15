# -*- coding: UTF-8 -*-
"""探针 / 一次性脚本的公共前置：把用户真实数据挡在外面。

这个项目里「跑测试或量性能，把用户真实的 `accounts.log` / `credentials.json` 写脏」已经
发生过 8 次，形状都一样：某个调用方漏了重定向。前 7 次具体是：漏了 `damai_wizard.log`；
用 `newline=''` 重写日志，127 行 CRLF 被悄悄改成 LF；直接 `import 模块; 模块.用例()` 绕过
`main()` 里的重定向；临时性能脚本调 `build_driver()`；用例 A5 单独跑；另建的一次性「前后
对照」脚本调了 `auto_fill_login()`；手写冒烟脚本 Popen 了真实的 `account_runner.py`。第 8
次是某个套件里某处漏了 worker 守卫，反复二分都没能复现出是哪一处。

靠人记这条规矩记不住，连写下它的都又踩了一次；「写脏了再按字节还原」也不该是终点。所以第
8 次之后加了两道，把它从「事后还原」变成「根本不会发生」：① 给子进程隔离日志路径。
`accounts.py` 的 `LOG_PATH` 现在认环境变量 `DAMAI_ACCOUNTS_LOG`（默认一字不改），子进程被
指到临时文件就从头到尾不碰真实日志，`guard_worker_logs()` 于是从「主力」退居绊线。② 收尾
自愈 `Guard.heal_appended()`：跑完兜一道，真实日志若只是被纯追加就按字节截回去，并报出哪份
文件被剔掉了多少字节；不是纯追加就一律不碰、只报告。兜底没有测试，就只是安慰剂。

用法：在 import accounts / account_runner 之前 `guard = probe_env()`，收尾
`raise SystemExit(guard.verify())` 做逐字节自查；起子进程前再套 `guard_worker_logs(guard)`，
收尾 `wl.check()`（它记下日志完整字节，纯追加且截断点落在行边界才截断还原，否则宁可不碰）。
只要探针是从既有探针复制出来的，重定向就跟着来了。

守卫管不到子进程：`probe_env()` 重定向的是当前进程里 `accounts.LOG_PATH` 这个模块变量，而
`Popen` 一个真实的 `account_runner.py` 是全新解释器，会重读自己那份 `accounts.py` 常量，
照样写真实的 `accounts.log`，所以必须配 `guard_worker_logs(guard)`。

它还把 `sys.path` 挂好（项目根 / `damai` / `tools`，每个探针都在重复这三行）；调一次
`relax_local_proxy()`（本机全局代理会劫走发往本地 ChromeDriver 的请求，症状是每个
`driver.xxx` 都报 `Message: unhandled request`，看着像驱动坏了）；把凭据存储也指到
临时目录，不隔离的话探针存一次密码就写进了用户真实的 `credentials.json`。
"""

import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）


def _snapshot(path):
    """一个文件当前的样子：(字节数, 内容的 sha256)。不存在返回 None。

    为什么用内容哈希而不是 mtime：
      子进程冒烟会出现「写进去、再按字节还原」这种情况，内容一个字节没变，
      但 mtime 必然变了。用 mtime 判就会把这种正确还原报成「被改动了」，
      守卫于是变成狼来了，下次真出问题也没人信。
      这些文件都很小（几十 KB），哈希一次的代价可以忽略。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return (len(data), hashlib.sha256(data).hexdigest()[:16])


class Guard(object):
    """收尾自查：用户真实文件一个字节都不能变。"""

    def __init__(self, watched, originals=None):
        self.watched = watched          # {路径: _snapshot(路径)}
        self.originals = originals or {}   # {路径: 原始字节}（小文件才留，用于自愈）

    def touched(self):
        out = []
        for p, was in self.watched.items():
            now = _snapshot(p)
            if now != was:
                out.append((p, was, now))
        return out

    def heal_appended(self):
        """兜底自愈：真实日志如果被「纯追加」了内容，按字节截断回原样。

        每处长写都该包 `guard_worker_logs()`，漏一处的代价是往用户真实日志里混进假
        记录，而这种日志是用来追查真实运行的，混进去就分不清了。整套跑完时兜一道并
        大声说出来，它同时是「哪处漏了守卫」的证据。不是纯追加就只报告、一律不碰，
        宁可留一条没还原的记录，也不把文件改得更坏。

        返回 [(路径, 剔掉多少字节或说明), ...]；空列表 = 不需要兜。
        """
        fixed = []
        for path, was in self.watched.items():
            if was is None:
                continue                       # 原本就不存在：不碰
            origin = self.originals.get(path)
            if origin is None or _snapshot(path) == was:
                continue                       # 没留原始内容，或者压根没变
            # 只对 accounts.log 自动动手（只追加的文本日志，截回去安全）；别的文件
            #   （凭据）一律只报告，自动改写它们比留着一条脏记录危险得多。
            if os.path.basename(path) != "accounts.log":
                fixed.append((path, "（不是日志，只报告不动手）"))
                continue
            try:
                with open(path, "rb") as f:
                    current = f.read()
            except OSError:
                continue
            if (len(current) > len(origin) and current.startswith(origin)
                    and origin.endswith((b"\n", b"\r\n"))):
                with open(path, "wb") as f:
                    f.write(origin)
                fixed.append((path, len(current) - len(origin)))
            else:
                fixed.append((path, "不是纯追加，无法自动还原"))
        return fixed

    def verify(self):
        """返回 0 = 干净，1 = 被改动了。直接当退出码用。"""
        bad = self.touched()
        if bad:
            print("\n!! 用户真实文件的内容被改动了 —— 某个调用方漏了重定向：")
            for p, was, now in bad:
                print("   %s\n     之前 (字节数 %r, 内容哈希 %r)"
                      "\n     现在 (字节数 %r, 内容哈希 %r)"
                      % (p, was[0] if was else None, was[1] if was else None,
                         now[0] if now else None, now[1] if now else None))
            print("   → 先按内容特征把那几行剔除（用字节级读写，保住 CRLF），"
                  "再给漏掉的那个调用方补上重定向。")
            return 1
        print("\n[OK] 用户真实数据未被改动（按内容哈希比对，不看 mtime）：")
        for p, was in self.watched.items():
            print("   %s（%s 字节）" % (p, was[0] if was else "不存在"))
        return 0


def probe_env(tmp_prefix="dmp_probe_"):
    """把探针需要的前置一次做完，返回一个用于收尾自查的 Guard。

    必须在 `import accounts` / `import account_runner` 之前调用，
      重定向要在任何一次 `say()` 之前生效。
    """
    for p in (HERE, os.path.join(ROOT, "damai"), ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)

    from chromedriver_locator import relax_local_proxy
    relax_local_proxy()

    import accounts as acc_store
    import cred_store

    # ① 先记录用户真实文件的当前状态（这是自查的基准）
    watched = {}
    originals = {}
    for p in (acc_store.LOG_PATH,
              getattr(cred_store, "LOG_PATH", None),
              getattr(cred_store, "STORE_PATH", None)):
        if not p or p in watched:
            continue
        watched[p] = _snapshot(p)
        # 日志的原始字节留着，收尾兜底时按字节还原（见 Guard.heal_appended）。
        # 只在文件不大时留，别为了兜底把几百 MB 塞进内存。
        try:
            if p.endswith(".log") and os.path.getsize(p) <= 8 * 1024 * 1024:
                with open(p, "rb") as f:
                    originals[p] = f.read()
        except OSError:
            pass

    # ② 全部指到临时目录
    tmp = tempfile.mkdtemp(prefix=tmp_prefix)
    real_log = acc_store.LOG_PATH
    acc_store.LOG_PATH = os.path.join(tmp, "accounts.log")
    # 断言真的换掉了，上一行要是写错（比如变量名敲错），
    #   后面每一次 say() 都会原样落进用户的真实日志，而且一声不响。
    assert acc_store.LOG_PATH != real_log, "日志重定向失败，绝不能拿真实日志跑探针"

    if getattr(cred_store, "LOG_PATH", None):
        cred_store.LOG_PATH = os.path.join(tmp, "cred.log")
    if getattr(cred_store, "STORE_PATH", None):
        cred_store.STORE_PATH = os.path.join(tmp, "credentials.json")

    return Guard(watched, originals)


def guard_worker_logs(guard=None, log_path=None):
    """给「会 `Popen` 真实 `account_runner.py` 子进程」的冒烟用的守卫。

    为什么 `probe_env()` 不够：它重定向的是当前进程里的模块变量。子进程是全新的解释器，
    会重新读一遍 `accounts.py` 的常量，照样写真实的 `accounts.log`，第 7 次就是这么发生的。

    用法见模块顶部说明。收尾行为：① 纯追加（新内容以旧字节开头）且截断点在行边界，按原
    字节数还原；② 其它任何情况（变短了、中间被改了、截断点不在行边界）一律不碰，只报告。
    宁可让用户看到一条没还原的日志，也不能把文件改得更坏。

    路径从 `guard.watched` 里取，不能读 `acc_store.LOG_PATH`：`probe_env()` 早就把它改成
    临时目录了，那样守卫会去监视一个空文件、对真实日志的写入毫无察觉，还「检查通过」。
    这个坑在写这个函数的当天就踩了一次。
    """
    if log_path is None:
        if guard is not None:
            for p in guard.watched:
                if os.path.basename(p) == "accounts.log":
                    log_path = p
                    break
        if log_path is None:
            raise RuntimeError(
                "拿不到真实 accounts.log 的路径：请显式传 log_path=，"
                "或先用 probe_env() 拿到 guard（真实路径记在 guard.watched 里）")
    return _WorkerLogGuard(log_path, guard)


class _WorkerLogGuard(object):
    def __init__(self, path, guard=None):
        self.path = path
        self.guard = guard
        self.before = b""
        self.existed = False       # 文件「原本在不在」，见 check() 里的两个分支
        self.result = "（还没检查）"
        self._done = False

    def __enter__(self):
        try:
            with open(self.path, "rb") as f:
                self.before = f.read()
            self.existed = True
        except OSError:
            self.before = b""
        return self

    def __exit__(self, *exc):
        self.check()
        return False

    def check(self):
        """子进程跑完调一次。返回一句人话，结论存在 self.result。

        只认第一次的检查结论。`__exit__` 里已经检查并还原过一遍，
          外面再调一次时如果再算一遍，就已经「没有新增内容」了，
          于是结论会被覆盖成「干净：一行都没写」，把真正发生的写入抹掉。
          这个「二次检查把结论刷成假好消息」的坑，写这个类的当天就踩到了。
        """
        if self._done:
            return self.result
        self._done = True

        try:
            with open(self.path, "rb") as f:
                now = f.read()
        except OSError:
            if not self.existed:
                # 原本不存在、跑完还是不存在，就是干净。2026-10-09 修：用户清完运行时
                #   数据后再跑套件，这里曾把「干净」误报成「日志文件不见了」。
                self.result = "干净：子进程一行都没写进真实日志"
            else:
                self.result = "日志文件不见了（原本 %d 字节）" % len(self.before)
            return self.result

        old = self.before
        if now == old:
            self.result = "干净：子进程一行都没写进真实日志"
            return self.result

        if not (len(now) > len(old) and now.startswith(old)):
            self.result = ("被改动了，但**不是纯追加**（旧 %d → 新 %d 字节）—— "
                           "不敢自动还原，请人工处理" % (len(old), len(now)))
            return self.result

        added = now[len(old):]
        if old and not old.endswith((b"\n", b"\r\n")):
            self.result = ("被追加了 %d 字节，但**截断点不在行边界** —— "
                           "不敢自动还原，请人工处理" % len(added))
            return self.result

        if not self.existed:
            # 原本不存在，恢复成「不存在」：删掉子进程新建的这个文件，
            # 而不是留一个 0 字节空壳（那等于把「原本不存在」的状态也改了）。
            os.remove(self.path)
            self.result = ("已回收子进程新建的日志（原本不存在；它写了 %d 字节）"
                           % len(added))
            return self.result

        with open(self.path, "wb") as f:
            f.write(old)
        self.result = ("已按字节还原：剔除子进程写进去的 %d 字节（约 %d 行），"
                       "文件回到 %d 字节" % (len(added), added.count(b"\n"), len(old)))
        return self.result
