# -*- coding: utf-8 -*-
"""只读探针：连上正在运行的自动化 Chrome，勘查演出详情页的现场。

为什么需要它：抢票脚本卡住时，控制台的 print 是看不到的（密码/日志分离），光看
accounts.log 只能知道「卡在 choose_ticket」，不知道页面长什么样。

这个脚本用 CDP 附着到已经开着的那个浏览器，绝不开新窗口、绝不点击任何元素、绝不
quit（不关掉浏览器），只做四件事：① 打印当前 URL / 标题；② dump 所有和
「购买/选座/票档/场次」有关的可见元素及其状态；③ 找出可能挡住点击的浮层/遮罩；
④ 截图存到 img/_probe_detail.png。

用法（项目根目录）：
    python tools/probe_detail_page.py
"""
import io
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
PROJ = os.path.dirname(BASE)      # tools/ 挪到仓库根：仓库根在这
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # tools/ 自己进 sys.path
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "damai"))

from chromedriver_locator import relax_local_proxy  # noqa: E402

# 必须赶在建 driver 之前：本机全局代理会劫走发往本地 ChromeDriver 的请求，症状是每个 driver.xxx 都报 "Message: unhandled request"。
relax_local_proxy()

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

import chromedriver_locator

PORT_FILE = os.path.join(BASE, "profiles", "Account_01", "DevToolsActivePort")
SHOT_PATH = os.path.join(PROJ, "img", "_probe_detail.png")

# 只 dump class 里带这些词的元件，避免把整个页面倒出来
WANT_CLASS_KW = ("buy", "btn", "button", "seat", "sku", "perform", "select",
                 "price", "mask", "dialog", "modal", "popup", "overlay")


def read_port():
    if not os.path.exists(PORT_FILE):
        return None
    with io.open(PORT_FILE, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return lines[0].strip() if lines else None


def attach():
    port = read_port()
    if not port:
        raise SystemExit("找不到 DevToolsActivePort —— 那个自动化浏览器已经关了。\n"
                         "请重新点一次「保存并开始抢票」，让脚本卡住后再跑本探针。")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", "127.0.0.1:%s" % port)
    drv_path = chromedriver_locator.resolve_chromedriver()
    print("chromedriver :", drv_path)
    print("attach 到端口 :", port)
    return webdriver.Chrome(service=Service(drv_path), options=opts)


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def brief(el, limit=28):
    t = safe(lambda: el.text, "") or ""
    t = " ".join(t.split())
    return t[:limit]


def info(el):
    """(可见, 可用, 宽, 高, 文本)"""
    vis = bool(safe(lambda: el.is_displayed(), False))
    en = bool(safe(lambda: el.is_enabled(), True))
    r = safe(lambda: el.rect, {}) or {}
    w = int(r.get("width") or 0)
    h = int(r.get("height") or 0)
    return vis, en, w, h, brief(el)


def dump_elements(drv):
    print("\n" + "=" * 70)
    print("① 和购买/选座/票档相关的可见元素")
    print("=" * 70)
    rows = []
    for tag in ("button", "a", "div", "span", "li"):
        els = safe(lambda t=tag: drv.find_elements(By.TAG_NAME, t), []) or []
        for el in els[:600]:
            vis, en, w, h, t = info(el)
            if not vis or not t:
                continue
            cls = (safe(lambda e=el: e.get_attribute("class"), "") or "")
            low = cls.lower()
            if not any(k in low for k in WANT_CLASS_KW):
                continue
            rows.append((cls[:46], t, en, w, h))
    # 去重
    seen = set()
    for cls, t, en, w, h in rows:
        key = (cls, t)
        if key in seen:
            continue
        seen.add(key)
        flag = "" if en else "  ←禁用"
        print("  [%s] %-26s %dx%d%s" % (cls, t, w, h, flag))
    if not rows:
        print("  （一个都没匹配到 —— 页面结构和预期完全不同）")


def dump_price_and_date(drv):
    print("\n" + "=" * 70)
    print("② 场次 / 票档：有没有可买的")
    print("=" * 70)
    for label, kws in (("场次", ("perform", "date", "calendar")),
                       ("票档", ("sku", "price", "item"))):
        print("\n  --- %s 相关 ---" % label)
        hit = 0
        for tag in ("div", "li", "span", "a"):
            els = safe(lambda t=tag: drv.find_elements(By.TAG_NAME, t), []) or []
            for el in els[:800]:
                vis, en, w, h, t = info(el)
                if not vis or not t or len(t) > 24:
                    continue
                cls = (safe(lambda e=el: e.get_attribute("class"), "") or "").lower()
                if not any(k in cls for k in kws):
                    continue
                flag = "" if en else "  ←禁用/不可选"
                print("    [%s] %-22s %dx%d%s" % (cls[:34], t, w, h, flag))
                hit += 1
                if hit > 25:
                    break
            if hit > 25:
                break
        if hit == 0:
            print("    （没找到 —— 可能未开票，或结构与预期不同）")


def dump_overlays(drv):
    print("\n" + "=" * 70)
    print("③ 可能挡住点击的浮层 / 遮罩")
    print("=" * 70)
    js = """
    var out = [];
    var vw = window.innerWidth, vh = window.innerHeight;
    document.querySelectorAll('div,section,aside').forEach(function(el){
      var cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'absolute' && cs.position !== 'sticky') return;
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      if (parseFloat(cs.opacity) < 0.05) return;
      var r = el.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) return;
      var area = r.width * r.height;
      if (area < vw * vh * 0.18) return;
      out.push({
        cls: (el.className || '').toString().slice(0, 60),
        z: cs.zIndex,
        pos: cs.position,
        w: Math.round(r.width), h: Math.round(r.height),
        text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 60)
      });
    });
    return out;
    """
    hits = safe(lambda: drv.execute_script(js), []) or []
    if not hits:
        print("  没有大面积浮层/遮罩（好消息）")
    for h in hits:
        print("  [%s] z=%s %s %dx%d  %r"
              % (h.get("cls"), h.get("z"), h.get("pos"), h.get("w"), h.get("h"), h.get("text")))


def main():
    drv = attach()
    print("\nURL :", drv.current_url)
    print("标题 :", drv.title)

    dump_elements(drv)
    dump_price_and_date(drv)
    dump_overlays(drv)

    # 截图
    try:
        png = drv.get_screenshot_as_png()
        os.makedirs(os.path.dirname(SHOT_PATH), exist_ok=True)
        with open(SHOT_PATH, "wb") as f:
            f.write(png)
        print("\n截图已保存：%s（%d 字节）" % (SHOT_PATH, len(png)))
    except Exception as e:
        print("\n截图失败：", e)

    print("\n" + "-" * 70)
    print("探针结束。浏览器保持原样，没有点任何东西，也没有被关闭。")
    print("-" * 70)
    # 故意不 quit：attch 模式下退出会连带关掉这个浏览器


if __name__ == "__main__":
    main()
