# -*- coding: utf-8 -*-
"""真站验证：新版 H5 选座页（canvas）上，随机点坐标能不能选中座位。

默认只读（只打印识别结果，不点东西）：
    python tools/verify_seat_h5_live.py
真执行（会点座位 + 点「立即购买」，因此会跳订单确认页）：
    python tools/verify_seat_h5_live.py --go

无论哪种模式都不会点支付、不会提交订单。
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
SHOT = os.path.join(PROJ, "img", "_probe_h5_seat_after.png")


def main():
    go = "--go" in sys.argv
    with io.open(PORT_FILE, encoding="utf-8") as f:
        port = f.read().splitlines()[0].strip()

    o = Options()
    o.add_experimental_option("debuggerAddress", "127.0.0.1:%s" % port)
    drv = webdriver.Chrome(service=Service(chromedriver_locator.resolve_chromedriver()),
                           options=o)

    cfg = Config(index_url="about:blank", login_url="about:blank",
                 target_url=drv.current_url, users=["王小明"],
                 city=None, dates=None, prices=None,
                 if_listen=True, if_commit_order=False,
                 max_retries=3, fast_mode=True, page_load_delay=0.2)
    con = object.__new__(Concert)
    con.config = cfg
    con.driver = drv
    con.status = 2
    con.login_method = 1

    print("URL :", drv.current_url[:100])
    print("标题 :", drv.title)
    print()
    print("=== 识别结果 ===")
    print("  _is_seat_page()     :", con._is_seat_page())
    print("  _is_h5_seat_page()  :", con._is_h5_seat_page())
    print("  _h5_seat_box()      :", con._h5_seat_box())
    print("  _h5_picked_amount() :", con._h5_picked_amount())
    btn = con._h5_confirm_button()
    print("  _h5_confirm_button():",
          "找到 <%s class='%s'> %r" % (btn.tag_name, (btn.get_attribute('class') or '')[:40],
                                      con._safe_text(btn)) if btn is not None else "没找到")

    if not go:
        print()
        print("（只读模式。加 --go 才会真点座位 + 点「立即购买」）")
        return

    print()
    print("=== 真执行 pick_seat_on_h5() ===")
    ok = con.pick_seat_on_h5()
    print("  返回 :", ok)
    import time
    time.sleep(2.0)
    print("  当前 URL :", drv.current_url[:110])
    print("  当前标题 :", drv.title)
    print("  已选金额 :", con._h5_picked_amount())
    print("  还在选座页吗 :", con._is_seat_page())
    print("  是订单确认页吗 :", con._is_order_confirmation_page())

    try:
        with open(SHOT, "wb") as f:
            f.write(drv.get_screenshot_as_png())
        print("\n  截图：%s" % SHOT)
    except Exception as e:
        print("  截图失败：", e)
    print("\n（没点支付、没提交订单）")


if __name__ == "__main__":
    main()
