# -*- coding: utf-8 -*-
"""真站实测：附着到已开着的浏览器，验证 scan_layer 相关的修复真的有效。

做两件事（都在真实 DOM 上）：① 只读确认：能认出浮层、能排除浮层干扰
（_overlay_elements 不再把 .qrcode-wrapper 当遮罩）；② 真点一下浮层里的
「不，选座购票」，看页面是不是真的往下走了。

第 2 步会改变页面状态（这正是用户要的那一下：把扫码浮层点掉继续买票），
但不会点任何支付按钮、不会提交订单。

用法： python tools/verify_scan_layer_live.py
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

import chromedriver_locator
from concert import Concert
from config import Config

PORT_FILE = os.path.join(BASE, "profiles", "Account_01", "DevToolsActivePort")
SHOT = os.path.join(PROJ, "img", "_probe_after_scan.png")


def main():
    with io.open(PORT_FILE, encoding="utf-8") as f:
        port = f.read().splitlines()[0].strip()

    opts = Options()
    opts.add_experimental_option("debuggerAddress", "127.0.0.1:%s" % port)
    drv = webdriver.Chrome(service=Service(chromedriver_locator.resolve_chromedriver()),
                           options=opts)

    cfg = Config(index_url="about:blank", login_url="about:blank",
                 target_url=drv.current_url, users=["王小明"],
                 city=None, dates=None, prices=None,
                 if_listen=True, if_commit_order=False,
                 max_retries=3, fast_mode=True, page_load_delay=0.2)
    cfg.dismiss_mask = True
    cfg.no_seat = True
    cfg.random_seat = True

    con = object.__new__(Concert)
    con.config = cfg
    con.driver = drv
    con.status = 2
    con.login_method = 1

    print("URL :", drv.current_url)
    print("标题 :", drv.title)
    print()

    print("=" * 64)
    print("① 只读确认")
    print("=" * 64)
    present = con.scan_layer_present()
    print("  认出扫码浮层 scan_layer_present() :", present)
    n_scan = len(con._scan_layer_elements())
    print("  浮层元素个数 :", n_scan)
    n_over = len(con._overlay_elements())
    print("  _overlay_elements() 个数 :", n_over, "（应该 0：浮层不该被当成遮罩）")
    print("  认识「不，选座购票」 :", con._is_reject_scan_text("不，选座购票"))

    if not present:
        print("\n  当前页面上没有扫码浮层，无法验证点击那一步。")
        print("  可能是：浮层已经被你手动点掉了，或页面刷新过。")
        return

    print()
    print("=" * 64)
    print("② 真点一下「不，选座购票」（这一步会改变页面状态）")
    print("=" * 64)
    before = drv.current_url
    ok = con.dismiss_scan_layer()
    print("  dismiss_scan_layer() 返回 :", ok)
    import time
    time.sleep(2.0)
    after = drv.current_url
    print("  点击前 URL :", before[:110])
    print("  点击后 URL :", after[:110])
    print("  页面标题   :", drv.title)
    print("  浮层还在吗 :", con.scan_layer_present())
    print("  是否选座页 :", "选座购买" in (drv.title or ""))
    print("  是否订单页 :", con._is_order_confirmation_page())

    try:
        png = drv.get_screenshot_as_png()
        with open(SHOT, "wb") as f:
            f.write(png)
        print("\n  截图：%s" % SHOT)
    except Exception as e:
        print("  截图失败：", e)

    print()
    print("-" * 64)
    print("说明：没有点任何支付按钮、没有提交订单。浏览器保持打开。")
    print("-" * 64)


if __name__ == "__main__":
    main()
