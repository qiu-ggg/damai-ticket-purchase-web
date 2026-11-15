# -*- coding: utf-8 -*-
"""多账号抢票任务的起停与状态聚合，「任务面板」的数据层。

和向导里其它部分一样，这里只用标准库：面板要能跑在没装 selenium 的解释器上
（selenium 在 VENV_PYTHON 那个隔离环境里，抢票进程是它起的子进程）。

它和向导上原有按钮是什么关系（两条路并存，互不干扰）：

    原「保存并开始 / 全部账号启动」
        → accounts.bat（打包版是 `exe --runner`），不弹窗口，2026-10-09
          起向导那边也改用 CREATE_NO_WINDOW 了（`damai_wizard.spawn_runner`），
          输出落到同一个 runs/<账号>.<时间>.log。差别只在进程句柄不在面板手里，
          所以没有统一视图，也没法只停其中某一个。

    本模块 start()
        → 直接起 `account_runner.py run <账号>`，不弹窗口，
          输出重定向到 runs/<账号>.<时间>.log，进程句柄留在手里，
          所以能单独停、能聚合看进度。

为什么任务的 stdin 接 DEVNULL：
  抢到票之后 account_runner 会走到 hold_browsers()，那里有一句 input("按回车…")。
  没有控制台的时候这句 input() 立刻读到 EOF，那段代码对这个分支的处理是
  原地守着浏览器，直到用户手动关掉浏览器窗口才收尾退出
  （2026-10-09 用户要求：「跑完后不要自动关闭浏览器，停在最后那页」）。
  两条路现在都是这么起的，行为一致。
  必须是「守着」而不是「说完就退」：chrome 是 chromedriver 起的，
    客户端一退出，chromedriver 跟着退时会把它管理的浏览器一并收走，
    「停在最后页面」就没了。守着 = 连接活着 = 浏览器一定保持打开。

为什么停止用 `taskkill /F /T`：
  Chrome 是任务进程的子进程。只杀父进程会留下一堆孤儿 chrome.exe 占着
  profiles/<账号> 目录，下一轮启动就报 `Chrome instance exited`。
  /T 杀整棵进程树；/F 是因为这些进程通常不响应正常关闭消息。
  这是纯本地操作，不发任何网络请求，不增加风控面（调快刷新才是风控面）。

为什么状态要落盘：
  向导关掉再打开，`Popen` 句柄就没了。这时只能靠残留的 pid + 日志尾部来还原
  「上一轮到底跑到哪了」。所以每次状态变化都写 runs/tasks.json；读不到句柄时
  用 tasklist/ps 查 pid 是否还活着。

写测试 / 冒烟时的坑（第 7 次事故就是这里）：
  这里起的子进程会往真实的 `accounts.log` 写（`accounts.say()` 双写）。
  而 `tools/_probe_env.py` 的 `probe_env()` 重定向的是当前进程的模块变量，
  管不到子进程，子进程是全新解释器，会重新 import 自己那份 `accounts.py`。
  所以：拿这个类起真实 `account_runner.py` 做冒烟时，必须用
  `_probe_env.guard_worker_logs()` 包住，或者把 `RUNS_DIR` / 整个目录先搬到临时目录。
  参考现成写法：`tools/smoke_task_panel.py`（受控、可重复）。
  `tools/check_task_manager.py` 走的是另一条路，它用假 runner，压根不写那个日志。
"""

import json
import os
import subprocess
import sys
import time

# 路径口径见 accounts.py 里的同款说明（`runs/` 日志也跟着数据走）。
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
    PKG_DIR = app_paths.resource_dir()
    RUNS_DIR = os.path.join(app_paths.logs_dir(), "runs")
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PKG_DIR = BASE_DIR
    RUNS_DIR = os.path.join(BASE_DIR, "logs", "runs")

# 判「进程还活着吗」用（kill_tree 等）。容错：它坏了只是停任务时少个确认。
try:
    import live_status
except Exception:                                               # pragma: no cover
    live_status = None
STATE_PATH = os.path.join(RUNS_DIR, "tasks.json")
# 这是「包目录」里的文件（随包分发的资源），不是用户数据，所以用 PKG_DIR。
RUNNER_PATH = os.path.join(PKG_DIR, "account_runner.py")

# 打包成 exe 之后没有 python.exe，也没有 account_runner.py 这个文件，
#   子进程只能由 exe 自己再跑一个自己（见 start() 里的 `--runner`）。
_FROZEN = bool(getattr(sys, "frozen", False))

# 装了 selenium 的隔离环境。可用环境变量 DAMAI_PYTHON 覆盖（换机器/换虚拟环境时不用改代码），
# 其次找仓库根目录的 .venv，都没有就用当前解释器。
def _default_python():
    env = os.environ.get("DAMAI_PYTHON")
    if env:
        return env
    venv = os.path.join(os.path.dirname(PKG_DIR), ".venv", "Scripts", "python.exe")
    return venv if os.path.isfile(venv) else sys.executable


DEFAULT_PYTHON = _default_python()

# Windows 上不弹控制台窗口。非 Windows 置 0（那边本来也没有这个概念）。
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 任务在日志里打出的、能说明「走到哪一步」的标记。字符串取自
# account_runner.notify_payment()，改那边的话这里要跟着改。
SUCCESS_MARKS = (
    "***订单已提交，当前停在支付环节***",
    "***订单流程已结束，请到浏览器里确认抢票结果***",
)
FAILURE_MARKS = ("出错：", "Traceback", "配置不完整，跳过该账号", "没有配置完整")

# 对外状态名（面板直接显示这些）
S_NOT_STARTED = "未启动"
S_STARTING = "启动中"
S_RUNNING = "运行中"
S_STOPPING = "停止中"
S_STOPPED = "已停止"
S_SUCCESS = "已抢到"
S_FINISHED = "已结束"
S_FAILED = "已失败"
S_LOST = "已失联"        # 进程没了，但本次向导没记到它的退出码（重启过向导）

TERMINAL = (S_STOPPED, S_SUCCESS, S_FINISHED, S_FAILED, S_LOST)


def _safe_name(account_id):
    """账号号转成能当文件名的形式。和 accounts._safe_name 保持一致的做法。"""
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(account_id or "")).strip("_")
    return safe or "account"


def _now():
    return time.time()


def _stamp(t=None):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t if t else _now()))


def pid_alive(pid):
    """进程还活着吗。拿不到句柄时（向导重开过）只能靠系统命令查。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq {}".format(pid), "/NH", "/FO", "CSV"],
                # errors="replace" 不是可有可无的：本机 PYTHONUTF8=1 时 text=True 会拿 UTF-8 去解 tasklist 的
                # GBK 输出（中文 Windows），读线程抛 UnicodeDecodeError、stdout 变空，活着的进程会被判成「已结束」
                # （面板显示失败，其实浏览器还在抢）。这个坑不报错、只会静默错判，所以宁可解出乱码也不能让它抛。
                capture_output=True, text=True, errors="replace", timeout=8,
                creationflags=CREATE_NO_WINDOW,
            ).stdout or ""
        except Exception:
            # 查不了就不要乱判「死了」，宁可当成活着（免得把运行中的任务标成结束）
            return True
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _alive_fast(pid):
    """快速的判活（ctypes），拿不到就退回上面的 tasklist 版。

    为什么要「快」：kill_tree 要循环等进程死透（每 0.15 秒看一眼），
      循环里每次都起一个 tasklist 子进程的话，停一个任务光判活就好几秒。
    """
    if live_status is not None:
        try:
            return live_status.pid_alive(pid)
        except Exception:
            pass
    return pid_alive(pid)


def kill_tree(pid, wait=6.0):
    """杀一棵进程树（含它起的 Chrome）。返回 True = 确认已经不在运行。

    和 TaskManager.stop 里的杀法是同一套（taskkill /F /T），区别只有一个：
      这里杀的是「不是本面板起的」任务，走启动页那条路的子进程
      （2026-10-09 用户要求面板也要能停它们）。它们的 pid 从
      `logs/live/<账号>.json` 里拿，进程对象（Popen）本来就没有，
      所以没有 terminate 兜底，只有 taskkill + os.kill。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0 or not _alive_fast(pid):
        return True

    killed = False
    if os.name == "nt":
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, errors="replace", timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            killed = r.returncode == 0
        except Exception:
            pass
    if not killed:
        try:
            os.kill(pid, 9)
        except Exception:
            pass

    deadline = _now() + max(0.0, wait)
    while _now() < deadline:
        if not _alive_fast(pid):
            return True
        time.sleep(0.15)
    return not _alive_fast(pid)


class Task(object):
    """一个账号的一轮抢票任务。"""

    def __init__(self, aid, log_path=None, pid=None, state=S_NOT_STARTED,
                 started_at=None, ended_at=None, exit_code=None,
                 stopped_by_user=False, note="", **extra):
        self.aid = str(aid)
        self.log_path = log_path
        self.pid = pid
        self.state = state
        self.started_at = started_at
        self.ended_at = ended_at
        self.exit_code = exit_code
        self.stopped_by_user = bool(stopped_by_user)
        self.note = note
        self.extra = extra

    # 序列化（只存成 JSON 能表达的东西）

    def to_dict(self):
        d = {
            "aid": self.aid,
            "log_path": self.log_path,
            "pid": self.pid,
            "state": self.state,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "stopped_by_user": self.stopped_by_user,
            "note": self.note,
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        known = {k: d.pop(k, None) for k in (
            "aid", "log_path", "pid", "state", "started_at", "ended_at",
            "exit_code", "stopped_by_user", "note")}
        return cls(extra=d, **known)


    @property
    def elapsed(self):
        """已运行秒数；结束了的就固定成总耗时。"""
        if not self.started_at:
            return 0.0
        end = self.ended_at or _now()
        return max(0.0, end - self.started_at)

    @property
    def elapsed_text(self):
        s = int(self.elapsed)
        if s < 60:
            return "{} 秒".format(s)
        if s < 3600:
            return "{} 分 {:02d} 秒".format(s // 60, s % 60)
        return "{} 时 {:02d} 分".format(s // 3600, (s % 3600) // 60)


class TaskManager(object):
    """任务的起停与状态聚合。

    一个实例管一份 runs/tasks.json；向导里持有一个全局实例就够了。
    """

    def __init__(self, base_dir=BASE_DIR, python_exe=None, on_log=None):
        self.base_dir = base_dir
        self.runs_dir = os.path.join(base_dir, "runs") if base_dir != BASE_DIR else RUNS_DIR
        self.state_path = os.path.join(self.runs_dir, "tasks.json")
        self.runner = os.path.join(base_dir, "account_runner.py") \
            if base_dir != BASE_DIR else RUNNER_PATH
        self.python_exe = python_exe or DEFAULT_PYTHON
        self.on_log = on_log                      # 可选：把动作写进向导日志
        self._procs = {}                          # aid -> Popen（只在本进程内有效）
        self.tasks = {}                           # aid -> Task
        self._load()


    def _log(self, text):
        if self.on_log:
            try:
                self.on_log(text)
            except Exception:
                pass

    def _ensure_runs_dir(self):
        try:
            os.makedirs(self.runs_dir, exist_ok=True)
        except Exception:
            pass


    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return
        for d in (raw.get("tasks") or []):
            try:
                t = Task.from_dict(d)
            except Exception:
                continue
            self.tasks[t.aid] = t

    def _save(self):
        self._ensure_runs_dir()
        payload = {"version": 1, "saved_at": _now(),
                   "tasks": [t.to_dict() for t in self.tasks.values()]}
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as e:
            self._log("任务状态落盘失败：%r" % (e,))


    def start(self, account, auto=None, extra_args=None, env_extra=None,
              yes=False, no_hold=False):
        """给一个账号起一轮抢票。account 是 accounts.json 里的那个 dict。

        返回 (ok: bool, message: str)。
        """
        aid = str((account or {}).get("account_id") or "").strip()
        if not aid:
            return False, "账号没有 account_id"

        cur = self.tasks.get(aid)
        if cur is not None and cur.state in (S_STARTING, S_RUNNING, S_STOPPING):
            if self._alive(aid):
                return False, "「{}」已经在跑了".format(aid)

        # 这两项检查只在源码形态下做：打包版根本没有 python.exe，
        #   也没有 account_runner.py 这个文件，子进程是 exe 自己扮演的。
        #   在这里拦住只会让打包版一个账号都起不来，而且报错还驴唇不对马嘴。
        if not _FROZEN:
            if not os.path.exists(self.python_exe):
                return False, "找不到装了 selenium 的解释器：\n{}".format(self.python_exe)
            if not os.path.exists(self.runner):
                return False, "找不到 account_runner.py：\n{}".format(self.runner)

        self._ensure_runs_dir()
        log_path = os.path.join(
            self.runs_dir, "{}.{}.log".format(_safe_name(aid), _stamp()))

        if auto is None:
            auto = bool(account.get("auto_login", True))
        if _FROZEN:
            # 让 exe 以「调度器」的身份再跑一个自己，参数原样转交。
            args = [sys.executable, "--runner", "run", aid]
        else:
            args = [self.python_exe, "-X", "utf8", self.runner, "run", aid]
        if auto:
            args.append("--auto")
        if yes:
            args.append("--yes")
        if no_hold:
            args.append("--no-hold")
        args += list(extra_args or [])

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # 本机代理会拦掉发往本地 ChromeDriver 的请求（症状：unhandled request）
        env["NO_PROXY"] = "localhost,127.0.0.1,::1"
        env["no_proxy"] = "localhost,127.0.0.1,::1"
        env.update(env_extra or {})

        try:
            fh = open(log_path, "w", encoding="utf-8", errors="replace")
        except Exception as e:
            return False, "建不出日志文件：{}".format(e)

        task = Task(aid, log_path=log_path, state=S_STARTING,
                    started_at=_now(), pid=None)
        try:
            # stdin=DEVNULL 见文件头说明：让 hold_browsers() 的 input() 立刻 EOF。
            proc = subprocess.Popen(
                args, cwd=self.base_dir, env=env,
                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            try:
                fh.close()
            except Exception:
                pass
            task.state = S_FAILED
            task.ended_at = _now()
            task.note = "启动失败：{}".format(e)
            self.tasks[aid] = task
            self._save()
            return False, "启动失败：{}".format(e)
        finally:
            try:
                fh.close()          # 句柄交给子进程，这边关掉不影响它写
            except Exception:
                pass

        self._procs[aid] = proc
        task.pid = proc.pid
        self.tasks[aid] = task
        self._save()
        self._log("任务面板：已启动「{}」（pid={}）日志 {}".format(aid, proc.pid, log_path))
        return True, "已启动「{}」".format(aid)


    def _alive(self, aid):
        """这个账号的任务还在跑吗。有句柄用句柄，没句柄查 pid。"""
        proc = self._procs.get(aid)
        if proc is not None:
            return proc.poll() is None
        t = self.tasks.get(aid)
        if t is None or not t.pid:
            return False
        return pid_alive(t.pid)

    def stop(self, aid, wait=6.0):
        """停一个账号的任务：杀它和它起的 Chrome。不影响其它账号。

        返回 (ok: bool, message: str)。
        """
        aid = str(aid)
        t = self.tasks.get(aid)
        if t is None:
            return False, "「{}」还没有运行记录".format(aid)
        if t.state in (S_STOPPED,):
            return True, "「{}」已经停了".format(aid)
        if not self._alive(aid):
            # 进程已经没了（抢完/崩了），这时只把状态结算一下，不必报错
            self._settle(aid)
            return True, "「{}」已经不在运行了".format(aid)

        pid = t.pid
        proc = self._procs.get(aid)
        t.state = S_STOPPING
        t.stopped_by_user = True
        t.note = "用户手动停止"
        self._save()

        killed = False
        if pid and os.name == "nt":
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    # errors="replace" 同上：taskkill 的提示是中文，UTF-8 模式解不动，
                    # 解崩了会污染 stderr（这里看的是 returncode，所以只是噪音，
                    # 但没理由留一个会崩的读线程）。
                    capture_output=True, text=True, errors="replace", timeout=15,
                    creationflags=CREATE_NO_WINDOW,
                )
                killed = r.returncode == 0
            except Exception as e:
                self._log("taskkill 失败：%r" % (e,))

        if not killed:
            # 非 Windows，或 taskkill 没成功，退回直接 terminate，再不行 kill
            try:
                if proc is not None:
                    proc.terminate()
                elif pid:
                    os.kill(int(pid), 9)
            except Exception:
                pass

        deadline = _now() + max(0.0, wait)
        while _now() < deadline:
            if not self._alive(aid):
                break
            time.sleep(0.15)

        still = self._alive(aid)
        if still:
            try:
                if proc is not None:
                    proc.kill()
            except Exception:
                pass
            time.sleep(0.3)
            still = self._alive(aid)

        if still:
            t.state = S_RUNNING
            t.stopped_by_user = False
            t.note = "停止失败，进程仍在"
            self._save()
            return False, "没能停掉「{}」（pid={}），请到任务管理器里结束它".format(aid, pid)

        t.state = S_STOPPED
        t.ended_at = _now()
        self._procs.pop(aid, None)
        self._save()
        self._log("任务面板：已停止「{}」（pid={}）".format(aid, pid))
        return True, "已停止「{}」".format(aid)

    def stop_all(self, wait=6.0):
        """停掉所有还在跑的任务。返回 (停掉的账号列表, 失败的账号列表)。"""
        done, bad = [], []
        for aid, t in list(self.tasks.items()):
            if t.state in TERMINAL:
                continue
            if not self._alive(aid):
                continue
            ok, _msg = self.stop(aid, wait=wait)
            (done if ok else bad).append(aid)
        return done, bad


    def _tail_lines(self, log_path, n=40):
        """读日志文件最后 n 行。文件可能正被写，读不到就返回空。"""
        if not log_path or not os.path.exists(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.readlines()
        except Exception:
            return []
        return [ln.rstrip("\r\n") for ln in data[-n:]]

    def last_line(self, aid, width=None):
        """该账号任务日志里最后一行还说得上话的内容（面板「最近一行」用）。"""
        t = self.tasks.get(aid)
        if t is None:
            return ""
        for ln in reversed(self._tail_lines(t.log_path, 60)):
            s = ln.strip()
            if not s:
                continue
            if s.startswith("=") or s.startswith("-") or s.startswith("*"):
                continue
            if width and len(s) > width:
                s = s[:width - 1] + "…"
            return s
        return ""

    def read_log(self, aid, max_chars=200000):
        t = self.tasks.get(aid)
        if t is None or not t.log_path or not os.path.exists(t.log_path):
            return ""
        try:
            with open(t.log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
        except Exception as e:
            return "（读日志失败：{}）".format(e)
        if len(data) > max_chars:
            data = "…（前面省略 {} 字符）\n".format(len(data) - max_chars) + data[-max_chars:]
        return data

    def _settle(self, aid):
        """进程已经不在了，用退出码 + 日志尾巴判定终态。"""
        t = self.tasks.get(aid)
        if t is None:
            return t
        if t.state in TERMINAL and t.state != S_LOST:
            return t

        prev_state = t.state

        proc = self._procs.get(aid)
        code = None
        known_exit = False
        if proc is not None:
            code = proc.poll()
            known_exit = code is not None
        elif t.stopped_by_user:
            code = -1
            known_exit = True

        if t.stopped_by_user and t.state != S_STOPPING:
            # 上一次向导里点过停止，但状态没来得及写完就退出了
            t.state = S_STOPPED

        tail = "\n".join(self._tail_lines(t.log_path, 80))
        if t.stopped_by_user:
            t.state = S_STOPPED
        elif any(m in tail for m in SUCCESS_MARKS):
            t.state = S_SUCCESS
        elif code not in (None, 0):
            t.state = S_FAILED
        elif any(m in tail for m in FAILURE_MARKS):
            t.state = S_FAILED
        elif known_exit:
            t.state = S_FINISHED
        else:
            # 向导重开过、句柄丢了、pid 也查不到了，不编造结果
            t.state = S_LOST

        t.exit_code = code
        if t.ended_at is None:
            t.ended_at = _now()
        if prev_state != t.state:
            self._notify_terminal(aid, t)
        return t

    def _notify_terminal(self, aid, t):
        """任务落到终态时补一条短信（兜底，不是主要来源）。

        为什么两边都发：真正知道「订单提交成功、停在支付页」的是
        `account_runner`（它手上有页面标题，短信里能带上演出名）。
        但命令行直接跑 `run` 的时候没有面板，只有它这条路。

        反过来，面板能看到「已失联」这种 account_runner 根本来不及报的终态
        （向导重开过、pid 也查不到）。

        两边都调同一个 `notify()`，冷却期保证同一条消息不会发两遍；
        而且通知失败只写日志，绝不影响面板刷新。
        """
        try:
            import notify as nf
            import notify_settings as nset
        except Exception:
            return                    # 通知模块没随包打全 → 静默跳过，不影响面板
        if t.state == S_SUCCESS:
            event = nset.EVENT_GRABBED
        elif t.state in (S_FAILED, S_LOST):
            event = nset.EVENT_FAILED
        else:
            return                    # 已停止 / 已结束：用户自己干的，不用通知
        try:
            nf.notify(event, account=aid, wait=False, status=t.state,
                      reason=("退出码 {}".format(t.exit_code)
                              if t.exit_code not in (None, 0) else ""))
        except Exception:
            pass

    # 快照（面板每次刷新都调它）

    def snapshot(self, accounts=None):
        """返回按账号排列的状态行。accounts 传账号 dict 列表则包含未启动的账号。

        每行 dict：aid / state / pid / elapsed_text / last_line / log_path / action
        """
        changed = False
        for aid, t in list(self.tasks.items()):
            if t.state in (S_STARTING, S_RUNNING) and not self._alive(aid):
                self._settle(aid)
                changed = True
            elif t.state == S_STARTING and _now() - (t.started_at or 0) > 3.0:
                if self._alive(aid):
                    t.state = S_RUNNING
                    changed = True
        if changed:
            self._save()

        rows = []
        seen = set()
        for acc in (accounts or []):
            aid = str((acc or {}).get("account_id") or "")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            rows.append(self._row(aid, acc))
        for aid in self.tasks:
            if aid not in seen:
                rows.append(self._row(aid, None))
        return rows

    def _row(self, aid, acc):
        t = self.tasks.get(aid)
        if t is None:
            return {"aid": aid, "state": S_NOT_STARTED, "pid": None,
                    "elapsed_text": "-", "last_line": "", "log_path": None,
                    "enabled": bool((acc or {}).get("enabled", True)),
                    "busy": False}
        return {
            "aid": aid,
            "state": t.state,
            "pid": t.pid,
            "elapsed_text": t.elapsed_text if t.started_at else "-",
            "last_line": self.last_line(aid),
            "log_path": t.log_path,
            "enabled": bool((acc or {}).get("enabled", True)),
            "busy": t.state in (S_STARTING, S_RUNNING, S_STOPPING),
            "stopped_by_user": t.stopped_by_user,
            "note": t.note,
        }


    def forget(self, aid):
        """清掉一个账号的历史记录（日志文件留着）。"""
        self.tasks.pop(str(aid), None)
        self._procs.pop(str(aid), None)
        self._save()


# 命令行（方便单独调试，面板不依赖它）

def _cli(argv):
    import argparse
    p = argparse.ArgumentParser(prog="task_manager.py", description="抢票任务的起停与状态")
    p.add_argument("cmd", choices=["list", "start", "stop", "stop-all", "log"])
    p.add_argument("ids", nargs="*")
    args = p.parse_args(argv)

    tm = TaskManager()
    try:
        import accounts as acc_store
    except Exception:
        acc_store = None

    accounts = []
    if acc_store is not None:
        try:
            accounts = acc_store.all_accounts(acc_store.load())
        except Exception:
            accounts = []

    def find(aid):
        for a in accounts:
            if str(a.get("account_id")) == aid:
                return a
        return None

    if args.cmd == "list":
        for r in tm.snapshot(accounts):
            print("{:<12} {:<6} {:<10} {}".format(
                r["aid"], r["state"], r["elapsed_text"], r["last_line"]))
        return 0
    if args.cmd == "start":
        rc = 0
        for aid in args.ids:
            acc = find(aid)
            if acc is None:
                print("找不到账号：{}".format(aid))
                rc = 1
                continue
            ok, msg = tm.start(acc)
            print(("[OK] " if ok else "[失败] ") + msg)
            rc = rc or (0 if ok else 1)
        return rc
    if args.cmd == "stop":
        rc = 0
        for aid in args.ids:
            ok, msg = tm.stop(aid)
            print(("[OK] " if ok else "[失败] ") + msg)
            rc = rc or (0 if ok else 1)
        return rc
    if args.cmd == "stop-all":
        done, bad = tm.stop_all()
        print("已停止：{}".format("、".join(done) or "（无）"))
        if bad:
            print("没停掉：{}".format("、".join(bad)))
            return 1
        return 0
    if args.cmd == "log":
        for aid in args.ids:
            print(tm.read_log(aid))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
