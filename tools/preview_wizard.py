# -*- coding: utf-8 -*-
"""把向导真的画出来截图（每个分页各一段），用来肉眼检查版式。跑完就关。

只读：不点任何按钮，也不落盘（不触发 _on_close）。
截图前会把手机号遮成 138****1234，免得把真实号码留在图片里。

默认用你真实的 accounts.json（只读），所以画出来就是你平时看到的那个样子。
另外会额外画两种边界形态，「一个账号都没有」和「窗口拖到最窄」，
并打印关键样式的实际取值：改过配色之后，靠这些数字确认真的生效了，
而不是「看起来好像变了」。

用法：
    cd D:/damai-ticket-purchase
    <有 PIL 和 tkinter 的解释器> tools/preview_wizard.py
图片落在 img/_wizard_*.png。
"""
import os
import re
import sys
import tempfile
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
PROJ = os.path.dirname(BASE)      # tools/ 挪到仓库根：仓库根在这
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "damai"))

import tkinter as tk
from PIL import ImageGrab

import cred_store
import damai_wizard as W

# 只读预览也要把日志指到临时目录：向导里的 log_line 平时不写，遇上名单迁移一类分支
#   就会写，不该为了一次预览往用户真实 damai_wizard.log 里塞东西。
W.LOG_PATH = os.path.join(tempfile.gettempdir(), "_preview_wizard.log")

OUT_DIR = os.path.join(PROJ, "img")
os.makedirs(OUT_DIR, exist_ok=True)

root = tk.Tk()
wiz = W.Wizard(root)
root.update()
root.lift()
root.attributes("-topmost", True)
root.update()
time.sleep(1.0)

# 手机号脱敏（只在内存里改，不落盘）
for _aid, page in wiz.pages.items():
    phone = page["phone"].get().strip()
    if phone:
        page["phone"].delete(0, "end")
        page["phone"].insert(0, cred_store.mask_phone(phone))

# 账号栏「选中的那个」也显示手机号（`Wizard._chip_label` 从 cred_store 直接读，不是从上面的输入框），
#   要单独遮且包住取号函数本身，否则预览重建标签栏时被真号冲掉。关键在往哪写：真实现「存进
#   `self._chip_phones` 顺带 return 一份」，包装只改 return 等于没遮；正确做法是先填一遍再覆盖实例属性。
_real_refresh_phones = wiz._refresh_chip_phones


def _masked_refresh_phones():
    real = _real_refresh_phones()
    masked = {aid: cred_store.mask_phone(p) for aid, p in (real or {}).items()}
    wiz._chip_phones = masked              # 覆盖实例属性（返回值没人接）
    return masked


wiz._refresh_chip_phones = _masked_refresh_phones
wiz._refresh_chip_phones()
wiz._paint_tabbar()
root.update()


def _assert_masked(tag):
    """遮完当场验一次：标签栏上现在写的是什么。

    为什么要真读界面文字、而不是相信「函数已经包住了」：
    这个遮罩前后修过两次都没遮住，第一次是改完缓存又被重建冲掉，
    第二次是包了函数却只改了返回值（界面读的是实例属性）。
    判据只能是结果本身。11 位连号出现在标签栏上就算漏。
    """
    texts = []
    for chip in (getattr(wiz, "tab_chips", None) or {}).values():
        kids = chip.winfo_children()
        if len(kids) > 1:
            texts.append(str(kids[1].cget("text")))
    leaked = re.findall(r"\d{11}", " ".join(texts))
    if leaked:
        raise SystemExit("脱敏没生效：{0} 的标签栏里还有完整手机号 {1}".format(
            tag, leaked))
    return texts


print("标签栏文字（已脱敏）：", _assert_masked("启动时"))


def shoot(tag, geo=None):
    if geo:
        root.geometry(geo)
    root.update_idletasks()
    root.update()
    time.sleep(0.5)
    root.update()
    _assert_masked(tag)                    # 每张图存盘前都验，重建过也逃不掉
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    out = os.path.join(OUT_DIR, "_wizard_{}.png".format(tag))
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(out)
    print("saved %s  (%dx%d)" % (out, w, h))


print("页签顺序：", wiz.tab_order)
for tag, frac in (("a", 0.0), ("b", 0.45), ("c", 1.0)):
    wiz.canvas.yview_moveto(frac)
    shoot(tag)

# 切到第二个账号，确认分页切换 + 高度自适应没问题
for idx in range(1, min(3, len(wiz.tab_order))):
    wiz.nb.select(idx)
    root.update()
    time.sleep(0.4)
    print("切到第 %d 个分页 -> %s，Notebook 高度=%s"
          % (idx + 1, wiz._current_aid(), wiz.nb.winfo_height()))
    wiz.canvas.yview_moveto(0.0)
    shoot(chr(ord("d") + idx - 1))

# 边界形态 1：一个账号都没有时的占位页
# （放在窄窗口之前，免得被上一步改过的 geometry 影响）
if wiz.accounts:
    wiz.nb.select(0)
    _saved = wiz.accounts
    _saved_data = wiz.acc_data.get("accounts")
    wiz.accounts = []
    wiz.acc_data["accounts"] = []
    wiz._render_tabs(recollect=False)
    shoot("empty")
    wiz.accounts = _saved
    wiz.acc_data["accounts"] = _saved_data
    wiz._render_tabs(recollect=False)

# 边界形态 2：窗口拖到最窄（底部那排按钮必须整整齐齐全都看得见）
def find_bar(widget):
    """递归找底部那排按钮的容器（层级不固定，别写死几层）。"""
    for child in widget.winfo_children():
        if len([k for k in child.winfo_children() if isinstance(k, tk.Button)]) >= 5:
            return child
        got = find_bar(child)
        if got is not None:
            return got
    return None


wiz.nb.select(0)
root.geometry("%dx520" % root.minsize()[0])
root.update_idletasks()
root.update()
bar = find_bar(root)
if bar is not None:
    need = bar.winfo_reqwidth()
    avail = root.winfo_width() - 40
    print("底部按钮栏需要 %d px，最小窗口可用 %d px -> %s"
          % (need, avail, "放得下" if need <= avail else "会被挤出边界"))
else:
    print("没找到底部按钮栏，宽度检查跳过")
shoot("narrow")

# 边界形态 3：账号很多时，账号栏必须能横向滑动、标签宽度不被压缩。量每个标签的实际宽度，
#   只要它们互不相同、且不随账号数变小，就说明宽度是按标题自己算的、没被平均分配。
def tab_widths():
    return [wiz.tab_chips[i].winfo_width() for i in sorted(wiz.tab_chips)]


root.geometry("1020x760")
root.update()
before = tab_widths()
print("\n【账号栏】改前 %d 个账号，标签宽度 = %s" % (len(before), before))

_saved_acc = wiz.accounts
_saved_data = wiz.acc_data.get("accounts")
_many = []
_cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "重庆"]
for i, city in enumerate(_cities):
    a = dict(_saved_acc[0]) if _saved_acc else {}
    a.update({
        "account_id": "Account %02d" % (i + 1),
        "note": "", "city": city,
        "dates": ["2026-12-%02d" % (5 + i)],
        "prices": [str(380 + i * 100)],
        "enabled": (i != 3),
    })
    _many.append(a)

wiz.accounts = _many
wiz.acc_data["accounts"] = _many
wiz._render_tabs(recollect=False)
root.update()
time.sleep(0.4)
root.update()

after = tab_widths()
print("【账号栏】改后 %d 个账号，标签宽度 = %s" % (len(after), after))
overflow = wiz._tabbar_overflow()
print("  内容宽度 %d / 可视宽度 %d -> 需要横向滚动：%s"
      % (wiz.tab_strip.winfo_reqwidth(), wiz.tb_canvas.winfo_width(),
         "是" if overflow else "否"))
print("  左右箭头已显示：左=%s 右=%s（内容不溢出时应当都不显示）"
      % (wiz.tb_left.winfo_ismapped(), wiz.tb_right.winfo_ismapped()))
print("  标签宽度是否被压缩（越小说明越挤）：最小 %d / 最大 %d"
      % (min(after), max(after)) if after else "  （没有标签）")
shoot("many")

# 横向滚到中间 / 末尾，确认溢出部分真的能滚出来
wiz.tb_canvas.xview_moveto(0.5)
root.update()
shoot("many_mid")
wiz.tb_canvas.xview_moveto(1.0)
root.update()
shoot("many_end")

# 切到最后一个账号：它必须被自动滚进可视区（否则用户看到的是「界面没反应」）
wiz.nb.select(len(_many) - 1)
root.update()
time.sleep(0.4)
root.update()
print("  切到最后一个账号后，画布滚动位置 = %.2f（0=最左，1=最右）"
      % wiz.tb_canvas.xview()[0])
shoot("many_last")

wiz.accounts = _saved_acc
wiz.acc_data["accounts"] = _saved_data
wiz._render_tabs(recollect=False)
root.update()

# 样式实况：改配色之后，靠这些数字确认真的生效了
st = W.ttk.Style(root)
print("主题 =", st.theme_use(), "| 窗口底色 =", root.cget("bg"))
for key in ("TEntry", "TNotebook.Tab", "Vertical.TScrollbar"):
    cfg = dict(st.configure(key))
    keep = {k: cfg[k] for k in ("background", "foreground", "fieldbackground",
                                "bordercolor", "padding") if k in cfg}
    print("  %-18s %s" % (key, keep))

root.destroy()
