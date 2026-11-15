# -*- coding: utf-8 -*-
"""多开前先算一算：这台机器现在还能安全开几个浏览器。

多开失败最气人的地方是报错指不到原因。内存不够时你看到的往往不是「内存不足」，而是
`session not created: Chrome instance exited`，或者第 6 个账号的浏览器起来了、页面卡在
半路、最后什么都没抢到，而前 5 个看起来一切正常，事后没有任何线索指向「就是内存爆了」。

所以这里做两件很朴素的事：开之前看本机还有多少可用内存，够开几个就给几个，不够就明说
（「现在只剩 1.2 GB，最多再开 2 个」比一个失败的浏览器有用得多）；开之后能报出每个账号的
浏览器各占了多少，让「最多开几个」这个数是你自己机器上量出来的，不是抄来的。

内存读数零依赖：Windows 走 `GlobalMemoryStatusEx`（ctypes），Linux 读 `/proc/meminfo`。
这个项目已经吃过「以为机器上有某个库」的亏（客户机什么都没有），所以这里连 `psutil`
都不用。
"""

import ctypes
import os
import subprocess
import sys

# 一个 Chrome 实例大概占多少

# 单窗口内存估值（MB），本机实测得来（tools/measure_chrome_cost.py）：about:blank 空页面 456 MB / 8 进程 / 启动 1.0 秒。
# 取 560 往上留余量：实测是空页面，真站抢票页（图片、H5、选座画布）更重；估多了只是少开一个，估少了会开到一半
# 卡死，两种错代价不对称。这个值随机器和页面复杂度变，只算「建议上限」，要更准就跑 measure_chrome_cost.py 按实测改。算的是整套 Chrome 进程树，不是任务管理器里单看 chrome.exe 那一行（看着才 100MB，实际四五百兆、八个进程）。
DEFAULT_PER_WINDOW_MB = 560

# 留给系统和别的东西的余量。低于这个数再开浏览器，机器会开始换页（swap），
# 所有窗口一起变卡，比少开两个账号糟糕得多。
MIN_RESERVE_MB = 1500
RESERVE_RATIO = 0.12          # 内存大的机器按比例留，别一刀切

# 就算内存管够，自动化本身也有上限：CPU、磁盘、以及站点端对同一出口 IP 的
# 并发容忍度都会在某个数量级开始互相拖累。超过这个数收益极小、风险明显上升。
RECOMMENDED_CAP = 8


# 读本机内存

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_info():
    """返回 {'total': 字节, 'avail': 字节, 'percent': 已用百分比, 'source': 来源}。

    读不到时 avail 为 None，调用方要能接受「不知道」，而不是拿到一个瞎编的数。
    """
    if sys.platform == "win32":
        try:
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return {"total": int(stat.ullTotalPhys),
                        "avail": int(stat.ullAvailPhys),
                        "percent": int(stat.dwMemoryLoad),
                        "source": "GlobalMemoryStatusEx"}
        except Exception:
            pass
        return {"total": None, "avail": None, "percent": None, "source": "读不到"}
    try:
        total = avail = None
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if line.startswith("MemTotal:"):
                    total = int(parts[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(parts[1]) * 1024
        if total:
            used = total - (avail or 0)
            return {"total": total, "avail": avail,
                    "percent": int(round(used * 100.0 / total)),
                    "source": "/proc/meminfo"}
    except Exception:
        pass
    return {"total": None, "avail": None, "percent": None, "source": "读不到"}


def format_bytes(n):
    """把字节数写成人话。读不到就写「未知」，不要写 0，那会被当成「没内存」。"""
    if n is None:
        return "未知"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("{:.0f} {}" if unit in ("B", "KB") else "{:.1f} {}").format(n, unit)
        n /= 1024.0


# 算「还能开几个」

def reserve_bytes(total=None):
    """该给系统留多少。两个口径取大的，小内存机器按比例留不够，大内存机器按固定值留浪费。"""
    fixed = MIN_RESERVE_MB * 1024 * 1024
    if not total:
        return fixed
    return max(fixed, int(total * RESERVE_RATIO))


def max_windows(avail=None, per_window_mb=DEFAULT_PER_WINDOW_MB, total=None,
                cap=RECOMMENDED_CAP):
    """按可用内存算还能开几个。

    返回值永远是至少 1。为什么：
      「明明只剩 300MB 可用」时，拒绝开第一个浏览器是没有意义的，
      用户点「开始抢票」就是要跑起来，卡在门口的脚本等于坏了。
      内存紧张时正确的做法是只开 1 个，并在提示里说清楚现在很紧张。
    """
    if avail is None:
        return max(1, int(cap))
    per = max(64, int(per_window_mb)) * 1024 * 1024
    usable = int(avail) - reserve_bytes(total)
    if usable <= 0:
        return 1
    return max(1, min(int(cap), usable // per))


def preflight(wanted, per_window_mb=DEFAULT_PER_WINDOW_MB, avail=None, total=None,
              cap=RECOMMENDED_CAP, source=""):
    """开浏览器之前的一件事：现在到底开得动几个。

    返回 (allow_n, reason)。`allow_n` 是这次允许启动的数量（≤ wanted），
    `reason` 是一句可以直接显示给用户的话。

    `avail=None` 的含义是「你自己去读本机」，不是「读不到」。
      想表达「读不到」，只能让 `memory_info()` 真的返回 None
      （自检里就是这么打桩的，见 check_multi_open 的 [B] 节）。
      把这两个意思混在一起，就会出现「明明能读到内存，却被当成读不到，
      于是无限制地开」，那正是最该避免的那种错。
    """
    wanted = max(0, int(wanted))
    mem = memory_info() if avail is None else {
        "avail": avail, "total": total, "percent": None, "source": source or "指定"}
    allow = max_windows(avail=mem.get("avail"), per_window_mb=per_window_mb,
                        total=mem.get("total"), cap=cap)

    avail_txt = format_bytes(mem.get("avail"))
    per_txt = "{} MB".format(int(per_window_mb))

    if mem.get("avail") is None:
        return wanted, ("读不到本机可用内存（{}），不做限制。\n"
                        "如果一个账号的浏览器占约 {}，请自己估着来。").format(
                            mem.get("source") or "未知", per_txt)

    if wanted <= allow:
        return wanted, ("内存够：可用 {}，每个浏览器按 {} 算，开 {} 个没问题。"
                        .format(avail_txt, per_txt, wanted))

    extra = ""
    if allow >= cap:
        extra = "（已经到建议上限 {} 个）".format(cap)
    if allow == 1 and mem.get("avail") is not None and mem["avail"] < 800 * 1024 * 1024:
        extra = "（可用内存已经很紧张了，先只开 1 个，别的排队）"
    return allow, ("可用内存只有 {}，按每个浏览器 {} 算，这次最多先开 {} 个{}。\n"
                   "剩下的会排队，等前面有空位自动补上。\n"
                   "想多开就关掉一些别的程序（尤其是别的 Chrome / 浏览器窗口）。"
                   .format(avail_txt, per_txt, allow, extra))


# 量每个账号的浏览器各占了多少

def _ps_escape(path):
    """把路径原样交给 PowerShell，让 .NET 的 `[Regex]::Escape` 去转义。"""
    return os.path.abspath(path).replace("'", "''")


# PowerShell 输出只回纯 ASCII 短行，三个原因都踩过：① 折行：Write-Output 长串被控制台按约 120 列折断，而 chrome 命令行有几百
# 字符（第一版就这么坏：--user-data-dir 落在被折掉的尾巴上，一个进程都没匹配上），改用 [Console]::Out.WriteLine() 直写 stdout。
# ② 编码：中文账号名穿越进程边界会乱码，匹配在 PowerShell 里做完、只回简短 ASCII。③ 速度：PowerShell 冷启动一两秒，8 个账号各查一次卡十几秒，所以一次查询把全部算完。
_PS_QUERY = r"""
$dirs = @({dirs});
$n = $dirs.Count;
$per = @(); $cnt = @(); $pids = @();
for ($i = 0; $i -lt $n; $i++) {{ $per += [int64]0; $cnt += 0; $pids += '' }};
$total = [int64]0; $tcount = 0;
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ForEach-Object {{
  $cl = $_.CommandLine;
  if (-not $cl) {{ return }};
  $ws = [int64]$_.WorkingSetSize;
  $total = $total + $ws; $tcount = $tcount + 1;
  for ($i = 0; $i -lt $n; $i++) {{
    $pat = '--user-data-dir="?' + [Regex]::Escape($dirs[$i]) + '"?(\s|$)';
    if ($cl -match $pat) {{ $per[$i] = $per[$i] + $ws; $cnt[$i] = $cnt[$i] + 1; if ($pids[$i] -eq '') {{ $pids[$i] = $_.ProcessId.ToString() }} else {{ $pids[$i] = $pids[$i] + ',' + $_.ProcessId.ToString() }}; break }}
  }}
}};
[Console]::Out.WriteLine('T|' + $total.ToString() + '|' + $tcount.ToString());
for ($i = 0; $i -lt $n; $i++) {{ $row = 'P|' + $i.ToString() + '|' + $per[$i].ToString() + '|' + $cnt[$i].ToString() + '|' + $pids[$i]; [Console]::Out.WriteLine($row) }}
"""

# 上面这段是踩过坑才长这样的：① 每条语句都写在一行内（PowerShell 不像 Python 会自动续行，把 .ToString() 折到下一行
# 会得到 MissingEndParenthesisInMethodCall，脚本一行输出都没有）；② 用 r 开头的三引号原始字符串，否则 \s 会触发
# Python 的 SyntaxWarning；③ 匹配在 PowerShell 里做完，只回 `P|序号|内存|进程数|pid列表` 这种纯 ASCII 短行。

_PS_NONE = r"""
$total = [int64]0; $tcount = 0;
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ForEach-Object {
  $total = $total + [int64]$_.WorkingSetSize; $tcount = $tcount + 1
};
[Console]::Out.WriteLine('T|' + $total.ToString() + '|' + $tcount.ToString());
"""


def _decode(raw):
    """把 PowerShell 的输出字节解成文本。

    为什么要试几种编码：PowerShell 的错误信息走的是控制台代码页（中文系统上是
      cp936），不是 UTF-8。第一版统一按 UTF-8 解，报错就变成一串 `����λ�� ��:25`
，有报错跟没报错一样，根本看不懂它在说什么。先试 UTF-8，再试系统代码页。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _run_ps(script, timeout=30):
    """跑一段 PowerShell，返回 (stdout, stderr)。

    一定要把 stderr 带回来。第一版丢了它，于是「脚本语法错、一行都没输出」
      跟「查到了、结果是 0」在调用方看起来一模一样，界面上显示「0 B（0 个进程）」，
      看着像个正常结果。把失败伪装成数据的写法，比直接报错难查得多。
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout)
    except Exception as exc:
        return "", "起不了 PowerShell：{}".format(exc)
    return _decode(r.stdout), _decode(r.stderr).strip()


def chrome_usage(profile_dirs=None):
    """一次查完：每个 Profile 的内存/进程数/pid 列表，以及本机 chrome 总量。

    返回 {
      'total': 本机所有 chrome.exe 内存合计（含用户自己开的浏览器）,
      'count': 本机 chrome.exe 进程数,
      'per':   [{'mem':, 'count':, 'pids': [int]}，跟 profile_dirs 同序],
    }

    查不到就返回 total=None，调用方要能接受「不知道」。不要用 0 冒充 0，
    那会被显示成「没占用」，比「未知」更误导。
    """
    dirs = [d for d in (profile_dirs or []) if d]
    if os.name != "nt":
        return {"total": None, "count": None, "per": [], "note": "非 Windows 不支持"}
    if dirs:
        script = _PS_QUERY.format(dirs=",".join("'" + _ps_escape(d) + "'" for d in dirs))
    else:
        script = _PS_NONE
    out, err = _run_ps(script)
    per = [{"mem": 0, "count": 0, "pids": []} for _ in dirs]
    total = count = None
    rows = 0
    for line in out.splitlines():
        parts = line.strip().split("|")
        if not parts or not parts[0]:
            continue
        if parts[0] == "T" and len(parts) >= 3:
            try:
                total, count = int(parts[1]), int(parts[2])
                rows += 1
            except ValueError:
                pass
        elif parts[0] == "P" and len(parts) >= 5:
            try:
                i = int(parts[1])
            except ValueError:
                continue
            if 0 <= i < len(per):
                per[i] = {
                    "mem": int(parts[2] or 0),
                    "count": int(parts[3] or 0),
                    "pids": [int(x) for x in parts[4].split(",") if x.strip().isdigit()],
                }
    result = {"total": total, "count": count, "per": per, "error": ""}
    if not rows:
        # 一行都没解析出来 = 查询失败了。不要留成 total=None 却不说原因，
        # 那会一路显示成「读不到」而没人知道是脚本坏了还是真读不到。
        result["error"] = ("PowerShell 没给出可用输出"
                           + ("：{}".format(err.splitlines()[0]) if err else "（且没有报错信息）"))
    return result


def close_by_profile(profile_dir):
    """按 Profile 目录认领并结束自动化 Chrome 进程，返回关掉几个。

    口径跟 `account_runner._own_chrome_processes` 一致：只认 `--user-data-dir`
    指向这个 Profile 的进程，绝不按进程名无差别杀 chrome.exe，
    那会把用户自己开的浏览器一起端了。
    """
    if os.name != "nt" or not profile_dir:
        return 0
    usage = chrome_usage([profile_dir])
    pids = []
    for row in usage["per"]:
        pids.extend(row["pids"])
    if not pids:
        return 0
    ps = "; ".join("Stop-Process -Id %d -Force -ErrorAction SilentlyContinue" % p
                   for p in pids)
    _run_ps(ps, timeout=30)
    return len(pids)


def footprint(profiles=None):
    """按 Profile 汇总内存占用。

    `profiles` 是 {账号: Profile 目录}；不传就只统计本机所有 chrome.exe 的总量。
    """
    names = list((profiles or {}).keys())
    dirs = [profiles[n] for n in names]
    usage = chrome_usage(dirs)
    by_profile = {}
    for i, name in enumerate(names):
        if i < len(usage["per"]):
            by_profile[name] = usage["per"][i]["mem"]
    procs = {}
    for i, name in enumerate(names):
        if i < len(usage["per"]):
            procs[name] = usage["per"][i]["count"]
    return {"by_profile": by_profile, "by_count": procs,
            "total": usage["total"], "count": usage["count"],
            "error": usage.get("error") or ""}


def report(profiles=None, per_window_mb=DEFAULT_PER_WINDOW_MB, cap=RECOMMENDED_CAP):
    """给人看的体检报告。界面上直接把字符串塞进文本框。"""
    mem = memory_info()
    fp = footprint(profiles)
    lines = []
    lines.append("本机内存：共 {}，可用 {}，已用 {}%（来源：{}）".format(
        format_bytes(mem["total"]), format_bytes(mem["avail"]),
        mem["percent"] if mem["percent"] is not None else "?", mem["source"]))
    allow = max_windows(avail=mem["avail"], per_window_mb=per_window_mb,
                        total=mem["total"], cap=cap)
    lines.append("按每个浏览器 {} MB 估算，现在还能安全开 {} 个（上限 {}）".format(
        int(per_window_mb), allow, cap))
    lines.append("")
    if fp["total"] is None:
        lines.append("Chrome 占用：读不到（{}）".format(
            fp.get("error") or fp.get("note") or "原因不明"))
        lines.append("  → 这不影响抢票，只是体检少了一项。")
    else:
        lines.append("Chrome 总占用：{}（{} 个进程，含你自己开的浏览器）".format(
            format_bytes(fp["total"]), fp["count"]))
    if profiles:
        lines.append("")
        lines.append("按账号看：")
        live = []
        for name in sorted(profiles):
            used = fp["by_profile"].get(name) or 0
            n = (fp.get("by_count") or {}).get(name) or 0
            if used:
                live.append(used)
                lines.append("  {:<14} {}（{} 个进程）".format(name, format_bytes(used), n))
            else:
                lines.append("  {:<14} 没在跑".format(name))
        if live:
            lines.append("")
            lines.append("正在跑的账号平均每个占 {}（实测口径：整套 Chrome 进程树）"
                         .format(format_bytes(sum(live) // len(live))))
    return "\n".join(lines)
