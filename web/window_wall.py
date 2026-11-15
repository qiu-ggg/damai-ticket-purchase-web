# -*- coding: utf-8 -*-
"""任务画面墙的后端：把每个开着的浏览器窗口截成缩略图。

为什么要有它：多账号一起抢时，每个账号一个浏览器窗口。哪个窗口弹了
验证码 / 滑块 / 要你点一下，人得能一眼看见，而不是抢完了才发现
有个窗口从头到尾停在「请完成安全验证」那儿等人。

为什么不用 selenium 截图：
  抢票跑在子进程里，driver 主进程拿不到；就算想办法拿到，两个线程
  同时用一个 driver 会把「命令 / 响应」错位，那是能毁掉主流程的风险。
  这里走 Windows 原生 API（ctypes，不装任何第三方库）：
    · `PrintWindow` 只读窗口像素，跟 selenium 一点关系没有；
    · 缩略图自己编码成 PNG（zlib 是标准库），Tk 8.6 直接能显示。

只在 Windows 上有效。其它平台 `available()` 返回 False，面板显示一句
  「这个功能只在 Windows 上能用」，不许静默失败。
"""
from __future__ import print_function

import base64
import ctypes
import ctypes.wintypes as w
import struct
import sys
import threading
import time
import zlib

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    _kernel32 = ctypes.windll.kernel32
else:                                            # pragma: no cover
    _user32 = _gdi32 = _kernel32 = None

# PW_RENDERFULLCONTENT：Chrome 这类自己画自己的窗口，不加这个标志会截出一片黑。
_PW_RENDERFULLCONTENT = 2
_SRCCOPY = 0x00CC0020
_HALFTONE = 4
_TH32CS_SNAPPROCESS = 0x00000002

# 最小化窗口的「离屏恢复」截法（2026-10-09 加，见 _grab_rgb_iconic）
_SW_MINIMIZE = 6
_SW_SHOWNOACTIVATE = 4
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_ICONIC_SETTLE = 0.2      # 离屏恢复后等一帧：实测 80ms 就有内容，留 2 倍余量

# 同一个窗口不许两个线程同时做「恢复、截图、还原」（面板和画面墙可能同时在截）。按窗口句柄分别配锁，
# 句柄复用最多让两个窗口共享一把锁，只是稍微串行，无害。
_iconic_locks = {}
_iconic_locks_guard = threading.Lock()


def _iconic_lock(hwnd):
    with _iconic_locks_guard:
        lk = _iconic_locks.get(hwnd)
        if lk is None:
            lk = _iconic_locks[hwnd] = threading.Lock()
        return lk

if IS_WINDOWS:
    # 句柄在 64 位下是 8 字节。这几个函数只写 restype 不写 argtypes，ctypes 会按 32 位 int 传参，直接 OverflowError（第一版就是这么崩的）。
    _gdi32.CreateCompatibleDC.restype = w.HDC
    _gdi32.CreateCompatibleDC.argtypes = [w.HDC]
    _gdi32.CreateCompatibleBitmap.restype = w.HBITMAP
    _gdi32.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.SelectObject.restype = w.HGDIOBJ
    _gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    _gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [w.HDC]
    _gdi32.SetStretchBltMode.argtypes = [w.HDC, ctypes.c_int]
    _gdi32.StretchBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, w.HDC,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.DWORD]
    _gdi32.StretchBlt.restype = ctypes.c_bool
    _gdi32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, ctypes.c_uint,
                                 ctypes.c_uint, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_uint]
    _gdi32.GetDIBits.restype = ctypes.c_int

    _user32.GetWindowDC.restype = w.HDC
    _user32.GetWindowDC.argtypes = [w.HWND]
    _user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
    _user32.PrintWindow.argtypes = [w.HWND, w.HDC, ctypes.c_uint]
    _user32.PrintWindow.restype = ctypes.c_bool
    _user32.GetWindowRect.argtypes = [w.HWND, ctypes.c_void_p]
    _user32.GetWindowTextLengthW.argtypes = [w.HWND]
    _user32.GetWindowTextW.argtypes = [w.HWND, ctypes.c_wchar_p, ctypes.c_int]
    _user32.IsWindowVisible.argtypes = [w.HWND]
    _user32.IsIconic.argtypes = [w.HWND]
    _user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.c_void_p]
    _user32.EnumWindows.argtypes = [ctypes.c_void_p, w.LPARAM]
    _user32.SetForegroundWindow.argtypes = [w.HWND]
    _user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    _user32.BringWindowToTop.argtypes = [w.HWND]
    _user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_uint]
    _user32.SetWindowPos.restype = ctypes.c_bool
    _user32.GetWindowPlacement.argtypes = [w.HWND, ctypes.c_void_p]
    _user32.GetWindowPlacement.restype = ctypes.c_bool
    _user32.SetWindowPlacement.argtypes = [w.HWND, ctypes.c_void_p]
    _user32.SetWindowPlacement.restype = ctypes.c_bool
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int

    _kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    _kernel32.CloseHandle.argtypes = [w.HANDLE]

_dpi_done = False


def ensure_dpi_aware():
    """高 DPI 屏上不做这一步，拿到的位图是缩放过的（发虚、坐标也对不上）。"""
    global _dpi_done
    if _dpi_done or not IS_WINDOWS:
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def available():
    """这个平台能不能用画面墙。"""
    return IS_WINDOWS


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def process_table():
    """当前所有进程：{pid: (parent_pid, 小写exe名)}。"""
    out = {}
    if not IS_WINDOWS:
        return out
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == w.HANDLE(-1).value or not snap:
        return out
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = _kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("mbcs", "replace").lower()
            out[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID),
                                             name)
            ok = _kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def descendants(root_pid):
    """root_pid 的所有后代（含孙辈：子进程、chromedriver、chrome）。

    必须走到孙辈：浏览器不是子进程直接开的，中间还夹着一个 chromedriver。
    """
    table = process_table()
    kids = {}
    for pid, (ppid, _name) in table.items():
        kids.setdefault(ppid, []).append(pid)
    seen, stack = set(), list(kids.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(kids.get(pid, []))
    return seen


def list_windows():
    """可见且有标题的顶层窗口：[(hwnd, pid, title)]。"""
    out = []
    if not IS_WINDOWS:
        return out
    ensure_dpi_aware()
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def cb(hwnd, _lparam):
        h = int(hwnd)
        if not _user32.IsWindowVisible(h):
            return True
        n = _user32.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(h, buf, n + 1)
        pid = w.DWORD()
        _user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append((h, int(pid.value), buf.value))
        return True

    _user32.EnumWindows(CB(cb), 0)
    return out


def windows_for_pids(pids):
    """这些进程里的可见窗口：[(hwnd, pid, title)]。顺序按窗口在屏幕上从左到右。"""
    want = set(int(p) for p in pids)
    if not want:
        return []
    found = [x for x in list_windows() if x[1] in want]
    out = []
    for hwnd, pid, title in found:
        r = w.RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            out.append((r.left, hwnd, pid, title))
    out.sort(key=lambda x: x[0])
    return [(hwnd, pid, title) for _left, hwnd, pid, title in out]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD),
                ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
                ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class _WINDOWPLACEMENT(ctypes.Structure):
    """GetWindowPlacement / SetWindowPlacement 的结构。

    `rcNormalPosition` = 窗口「没被最小化时」的位置和真实尺寸。
    最小化窗口的 GetWindowRect 是幽灵值（237x39 @ -32000），
    尺寸只能从这里拿（实测：正常 1350x975 的 Chrome，最小化后
    GetWindowRect 变 237x39，rcNormalPosition 仍完整）。
    """
    _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
                ("ptMinPosition", w.POINT), ("ptMaxPosition", w.POINT),
                ("rcNormalPosition", w.RECT)]


def _get_placement(hwnd):
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    if not _user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return None
    return wp


def _set_placement(hwnd, wp):
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    return bool(_user32.SetWindowPlacement(hwnd, ctypes.byref(wp)))


def _offscreen_point(wp):
    """虚拟桌面之外的一个点，把窗口挪到那里，任何显示器布局下都看不见。"""
    xv = _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    yv = _user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    r = wp.rcNormalPosition
    ww_, hh_ = max(1, int(r.right - r.left)), max(1, int(r.bottom - r.top))
    return xv - ww_ - 64, yv - hh_ - 64


def grab_rgb(hwnd, max_w=260, max_h=None):
    """把一个窗口截成缩略图，返回 (rgb_bytes, w, h)；失败是 (None, 0, 0)。

    窗口最小化时自动走「离屏恢复」那条路（见 `_grab_rgb_iconic`），
    调用方不用关心窗口状态，直接调这个就行。

    缩放是在 GDI 里做的（`StretchBlt`），不是截完大图再用 Python 缩，
      否则 2560x1600 的窗口每次要传 16 MB 字节过来，8 个账号一起刷就卡死了。

    max_h（2026-10-09 加）：任务面板的行高是固定的，图必须按高度缩
      才塞得进，只卡宽度的话，一个 1000x760 的窗口缩到 260 宽就是
      198 高，行会被撑爆或被裁。两个约束一起卡。
    """
    if not IS_WINDOWS:
        return None, 0, 0
    try:
        if _user32.IsIconic(hwnd):
            return _grab_rgb_iconic(hwnd, max_w, max_h)
    except Exception:
        pass
    return _grab_rgb_visible(hwnd, max_w, max_h)


def _grab_rgb_iconic(hwnd, max_w=260, max_h=None):
    """最小化窗口的截法：临时「离屏恢复」，截图，再最小化回去。

    为什么不能直接截（2026-10-09 真 Chrome 实测）：
      · 最小化窗口的 GetWindowRect 是幽灵值（237x39 @ -32000），尺寸不可用；
      · PrintWindow 拿到的是黑图（平均亮度 32，正常内容 ~120）。
      任务面板里看起来就是「这个账号没有画面」，用户报的「多开时只有一个
      显示画面」就是这么来的（最小化的那个窗口变成了黑块）。

    怎么做的（每一步都实测过）：
      1. 记下当前 placement（正常位置 + 状态），截完要还原；
      2. `ShowWindow(SW_SHOWNOACTIVATE)` + 立刻 `SetWindowPos(虚拟桌面外)`，
         这两个调用 6.8ms 内完成（同一 DWM 帧），窗口只在屏幕外恢复，
         用户看不见闪；
      3. 等 `_ICONIC_SETTLE`（0.2s，实测 80ms 就有内容）让 Chrome 出一帧；
      4. 按正常路径截；
      5. 还原：`ShowWindow(SW_MINIMIZE)` 放回任务栏，再 `SetWindowPlacement`
         把「正常位置」写回去，`SetWindowPos` 会把它改成离屏坐标，
         不复位的话用户之后手动恢复窗口会出现在屏幕外（找不到窗口）。

    为什么不用 `SetWindowPlacement` 直接「预订」离屏位置再恢复：
      系统会把 rcNormalPosition 修正回屏幕内（实测设 -10000 读回 0,0），
      那条路会让窗口闪到用户眼前。
    为什么不用 `SetWindowPos(SWP_SHOWWINDOW)` 直接对最小化窗口调：
      实测无效（窗口还是最小化，位置也没动），必须先 ShowWindow。

    同一个窗口用一把锁：面板和画面墙可能同时截同一个窗口，
      两个线程同时「恢复→最小化」会把状态搅乱。
    """
    if not IS_WINDOWS:
        return None, 0, 0
    hwnd = int(hwnd)
    with _iconic_lock(hwnd):
        # 等锁期间窗口可能已经被恢复（用户点的，或另一个线程刚走完）
        try:
            if not _user32.IsIconic(hwnd):
                return _grab_rgb_visible(hwnd, max_w, max_h)
        except Exception:
            return None, 0, 0
        wp = _get_placement(hwnd)
        if wp is None:
            return None, 0, 0
        x, y = _offscreen_point(wp)
        try:
            _user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            _user32.SetWindowPos(hwnd, 0, x, y, 0, 0,
                                 _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            time.sleep(_ICONIC_SETTLE)
            return _grab_rgb_visible(hwnd, max_w, max_h)
        finally:
            # 收尾一步都不能少：否则用户的任务栏里会留一个「看不见的窗口」。
            try:
                _user32.ShowWindow(hwnd, _SW_MINIMIZE)
                _set_placement(hwnd, wp)
            except Exception:
                pass


def _grab_rgb_visible(hwnd, max_w=260, max_h=None):
    """普通（没最小化）窗口的截法：PrintWindow + GDI 缩放 + GetDIBits。"""
    if not IS_WINDOWS:
        return None, 0, 0
    r = w.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None, 0, 0
    sw, sh = r.right - r.left, r.bottom - r.top
    if sw < 8 or sh < 8:
        return None, 0, 0
    scale = min(1.0, float(max_w) / sw)
    if max_h:
        scale = min(scale, float(max_h) / sh)
    tw, th = max(1, int(sw * scale)), max(1, int(sh * scale))

    hdc_win = _user32.GetWindowDC(hwnd)
    hdc_src = _gdi32.CreateCompatibleDC(hdc_win)
    bmp_src = _gdi32.CreateCompatibleBitmap(hdc_win, sw, sh)
    old_src = _gdi32.SelectObject(hdc_src, bmp_src)
    hdc_dst = _gdi32.CreateCompatibleDC(hdc_win)
    bmp_dst = _gdi32.CreateCompatibleBitmap(hdc_win, tw, th)
    old_dst = _gdi32.SelectObject(hdc_dst, bmp_dst)
    try:
        _user32.PrintWindow(hwnd, hdc_src, _PW_RENDERFULLCONTENT)
        _gdi32.SetStretchBltMode(hdc_dst, _HALFTONE)
        _gdi32.StretchBlt(hdc_dst, 0, 0, tw, th, hdc_src, 0, 0, sw, sh, _SRCCOPY)

        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth = tw
        bi.biHeight = -th                      # 负 = 自上而下，省得再翻一次
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0                   # BI_RGB
        buf = ctypes.create_string_buffer(tw * th * 4)
        if not _gdi32.GetDIBits(hdc_dst, bmp_dst, 0, th, buf,
                                ctypes.byref(bi), 0):
            return None, 0, 0
        raw = buf.raw
        # BGRA 转 RGB。切片一次取整个通道，比逐像素循环快几十倍。
        rgb = bytearray(tw * th * 3)
        rgb[0::3] = raw[2::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[0::4]
        return bytes(rgb), tw, th
    finally:
        _gdi32.SelectObject(hdc_src, old_src)
        _gdi32.SelectObject(hdc_dst, old_dst)
        _gdi32.DeleteObject(bmp_src)
        _gdi32.DeleteObject(bmp_dst)
        _gdi32.DeleteDC(hdc_src)
        _gdi32.DeleteDC(hdc_dst)
        _user32.ReleaseDC(hwnd, hdc_win)


def png_encode(width, height, rgb):
    """RGB 字节流编码成 PNG 字节流。纯标准库（zlib + struct），不依赖 Pillow。

    为什么是 PNG 而不是 PPM：Tk 的 `PhotoImage` 在 Windows 这一版上
      不认 PPM（实测报 couldn't recognize image data），PNG 是它内置支持的。
      顺带 PNG 有压缩：260 宽的缩略图 PPM 要 130 KB，PNG 只要 20 KB 上下。
    """
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)                          # 每行的 filter 字节，0 = 不滤波
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8bit RGB
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def thumb_b64(hwnd, max_w=260, max_h=None):
    """一步到位：窗口截成 base64(PNG) + 尺寸。失败返回 (None, 0, 0)。"""
    rgb, tw, th = grab_rgb(hwnd, max_w, max_h=max_h)
    if rgb is None:
        return None, 0, 0
    return base64.b64encode(png_encode(tw, th, rgb)).decode("ascii"), tw, th


def focus_window(hwnd):
    """把某个窗口拉到最前并激活，点了缩略图就跳过去处理它。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        if _user32.IsIconic(hwnd):             # 最小化的先还原
            _user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
