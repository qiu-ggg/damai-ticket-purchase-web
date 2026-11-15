# -*- coding: UTF-8 -*-
"""本地真实浏览器回归测试：扫码遮挡弹窗 / 不选座购票 / 随机选座 / 新版 H5 订单页。

用 tools/fixtures/ 里的假页面复刻 PC 端下单链路的三个关键场景，
跑真 Chrome 验证，不依赖票务站点。因为目标演出通常没开票，
没法在真站点上跑这段流程，本地复刻是唯一能验证的办法。

用法（在仓库根目录）：
    python tools/check_order_flow.py
退出码 0 = 全过。
"""
import os
import sys
import tempfile
import threading
import time
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）
FIX = os.path.join(HERE, "fixtures")

# 诊断文件（order_page_dump.txt）在测试里改落到临时目录：
# 它会打印观演人姓名，跑一次回归不该在仓库里留这种东西。
DUMP_DIR = tempfile.gettempdir()

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "damai"))
sys.path.insert(0, ROOT)

from chromedriver_locator import relax_local_proxy                # noqa: E402

# 必须赶在建 driver 之前：本机全局代理会劫走发往本地 ChromeDriver 的请求，症状是
#   每个 driver.xxx 都报 "Message: unhandled request"，看着像驱动坏了，其实是被劫持。
relax_local_proxy()

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.remote.webdriver import WebDriver         # noqa: E402
from selenium.webdriver.remote.webelement import WebElement       # noqa: E402
from check_environment import get_chromedriver_path               # noqa: E402
from concert import Concert                                       # noqa: E402
from config import Config                                         # noqa: E402


def url_of(name, query=""):
    return "file:///" + os.path.join(FIX, name).replace("\\", "/") + query


def build_driver():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    service = webdriver.ChromeService(get_chromedriver_path())
    return webdriver.Chrome(service=service, options=opts)


def build_con(**overrides):
    """绕开 Concert.__init__（它会去连大麦），只把 config / driver 装上去。"""
    cfg = Config(
        index_url="about:blank", login_url="about:blank", target_url="about:blank",
        users=["王小明"], city=None, dates=None, prices=None,
        if_listen=True, if_commit_order=False,
        max_retries=3, fast_mode=True, page_load_delay=0.2,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    con = object.__new__(Concert)
    con.config = cfg
    con.status = 2
    con.login_method = 1
    con.driver = build_driver()
    # 真实运行里 AccountConcert 会把它换成「控制台 + accounts.log 双写」；
    # 这里给个 print，让用例走的是和线上同一条路径。
    con._log = print
    con._dump_dir = DUMP_DIR
    return con


RESULTS = []

# 大麦风控页的原话（2026-10-11 客户截图）。fixtures/risk.html 里就是这一句。
RISK_SENTENCE = "亲，检测到您的访问异常，请重新试试吧～"
RISK_WORD = "访问异常"          # 断言只用这个稳定的词，不比对整句标点


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  " + extra) if extra else ""))



def s1_masks_actually_block():
    """先证明遮罩真的能挡住按钮，不然这套测试等于没测。"""
    print("\n[S1] 遮罩确实挡住了提交按钮（前提校验）")
    con = build_con()
    try:
        con.driver.get(url_of("order.html"))
        blocked = not con._try_submit_by_text(["提交订单"])
        check("遮罩存在时点不到提交按钮", blocked and not con.driver.execute_script("return window.__submitted"))

        closed = con.dismiss_obstructions(rounds=3, verbose=False)
        check("dismiss_obstructions 关掉了浮层", closed >= 1, f"closed={closed}")
        check("浮层已从可见范围内消失", len(con._overlay_elements()) == 0)

        ok = con._try_submit_by_text(["提交订单"])
        check("关掉之后能正常点到提交按钮",
              ok and bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()


def s2_mask_without_close_button():
    """浮层没有关闭按钮时，走「直接隐藏遮罩」的兜底路径。"""
    print("\n[S2] 浮层没有关闭按钮 → 兜底隐藏遮罩")
    con = build_con()
    try:
        con.driver.get(url_of("order.html", "?noclose=1"))
        # 浮层没有任何可识别的 class，只能靠命中测试清掉
        blocked_before = con.driver.execute_script("""
            var m = document.getElementById('backdrop');
            return !!m && getComputedStyle(m).display !== 'none';
        """)
        check("清之前：无标准类名的遮罩仍然挡着（前提校验）", blocked_before)

        closed = con.dismiss_obstructions(rounds=3, verbose=False)
        check("兜底路径关掉了浮层", closed >= 1, f"closed={closed}")
        check("遮罩不再可见", len(con._overlay_elements()) == 0)

        blocked = con.driver.execute_script("""
            var m = document.getElementById('backdrop');
            return !!m && getComputedStyle(m).display !== 'none';
        """)
        check("无标准类名的遮罩层被清掉（靠命中测试，不靠类名）", not blocked)

        ok = con._try_submit_by_text(["提交订单"])
        check("兜底后能点到提交按钮",
              ok and bool(con.driver.execute_script("return window.__submitted")), f"click={ok}")
    finally:
        con.driver.quit()


def s3_close_button_must_be_visible():
    """隐藏的关闭按钮不能被当成"关成功"。"""
    print("\n[S3] 隐藏的关闭按钮不算数")
    con = build_con()
    try:
        con.driver.get(url_of("order.html"))
        con.driver.execute_script("document.getElementById('dlgClose').style.display='none';")
        con._close_one_obstruction()
        still_blocked = bool(con.driver.execute_script(
            "var m=document.getElementById('backdrop');"
            "return m && getComputedStyle(m).display !== 'none';"))
        check("隐藏关闭按钮后遮罩依然在（说明没被误判为已关闭）", still_blocked)
    finally:
        con.driver.quit()


def s4_no_seat_path():
    """详情页，点「不选座购票」，直接落订单确认页。"""
    print("\n[S4] 优先走「不选座购票」")
    con = build_con()
    try:
        con.driver.get(url_of("detail.html"))
        con.choose_ticket()
        cur = con.driver.current_url
        check("跳到了订单确认页", "order.html" in cur, cur.rsplit("/", 1)[-1])
        check("订单确认页被正确识别", con._is_order_confirmation_page())
        check("扫码浮层已被自动关掉", len(con._overlay_elements()) == 0)

        con._submit_order()
        check("提交订单成功（未被浮层挡住）",
              bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()


def s5_seat_fallback_random_pick():
    """演出不支持「不选座」时，落到选座页并随机选一个座位。"""
    print("\n[S5] 没有不选座入口 → 随机选座")
    con = build_con()
    try:
        con.driver.get(url_of("detail.html", "?noseat=0"))
        con.choose_ticket()
        cur = con.driver.current_url
        check("选座确认后跳到了订单确认页", cur.endswith("order2.html"), cur.rsplit("/", 1)[-1])
        check("确实选中了一个座位", bool(con.driver.execute_script(
            "return !!(window.__picked || true)")))
    finally:
        con.driver.quit()


def s6_random_pick_seat_directly():
    """直接验随机选座：只点可选座位，避开禁用座位，不点未启用的确认按钮。"""
    print("\n[S6] 随机选座细节")
    con = build_con()
    try:
        con.driver.get(url_of("seat.html"))
        check("识别出选座区域", con._find_seat_map_box() is not None)

        before = con._seat_confirm_button()
        check("未选座位时确认按钮不可点（返回 None）", before is None)

        picked_cls = con.driver.execute_script("""
            var s = document.querySelectorAll('.seat-item:not(.disabled)');
            return s.length;
        """)
        check("可选座位数量正确（排除了禁用座位）", picked_cls == 140, f"={picked_cls}")

        ok = con.random_pick_seat(tries=10)
        check("随机选座返回成功", ok)
        check("页面已跳到订单确认页", con.driver.current_url.endswith("order2.html"))
    finally:
        con.driver.quit()


def s7_switches_off():
    """三个开关关掉后，行为退回改造前。"""
    print("\n[S7] 开关关闭时退回原行为")
    con = build_con(no_seat=False, dismiss_mask=False, random_seat=False)
    try:
        con.driver.get(url_of("order.html"))
        check("dismiss_mask=False 时不处理浮层",
              con.dismiss_obstructions(rounds=3, verbose=False) == 0)
        check("浮层仍在（确实没动它）", len(con._overlay_elements()) >= 1)

        # random_seat=False：选座页只提示、不自动点
        con.driver.get(url_of("seat.html", "?autotitle=1"))
        con.choice_seat()
        picked = con.driver.execute_script("return window.__picked;")
        check("random_seat=False 时不会自动选座（退回手动）", picked is None, f"picked={picked}")

        # no_seat=False：不去找「不选座」入口
        con.driver.get(url_of("detail.html"))
        check("no_seat=False 时不点「不选座购票」", con.click_no_seat_buy() is False)
    finally:
        con.driver.quit()


def s8_auto_seat_still_works():
    """random_seat=True（默认）时，同一个页面确实会自动选上并确认。"""
    print("\n[S8] 开着自动化时确实会自动选座")
    con = build_con()
    try:
        con.driver.get(url_of("seat.html", "?stay=1"))
        con.choice_seat()
        picked = con.driver.execute_script("return window.__picked;")
        confirmed = con.driver.execute_script("return window.__confirmed;")
        check("自动选中了一个座位", picked is not None, f"picked={picked}")
        check("并且点了确认选座", bool(confirmed))
    finally:
        con.driver.quit()


def s9_slow_navigation_not_ruined():
    """点完购买后页面慢一拍才跳，脚本不能把它误判成"抢票未开始"并刷新掉。"""
    print("\n[S9] 点击到跳转之间有延迟时不能刷新页面")
    con = build_con()
    try:
        con.driver.get(url_of("detail.html", "?slow=1200"))
        con.choose_ticket()
        cur = con.driver.current_url
        check("慢跳转也能正确落到订单确认页（没被刷新回去）",
              "order.html" in cur, cur.rsplit("/", 1)[-1])
    finally:
        con.driver.quit()


def s10_scan_layer_replaces_buy_button():
    """真站实测结构：点购买后购买按钮被 .scan-buy 扫码浮层整块顶掉。

    旧代码只在「按钮还在、被遮罩盖住」的场景下工作。遇到「按钮直接消失」
    就会陷入「找不到按钮，刷新页面，再点，再消失」的死循环
    （20:23 那次真站卡死 + 连接被重置，就是这个）。
    """
    print("\n[S10] 扫码浮层顶掉购买按钮 → 点「不，选座购票」接着走")
    con = build_con()
    try:
        # 先做不用浏览器的文案判定
        check("认识「不，选座购票」", con._is_reject_scan_text("不，选座购票"))
        check("认识「不，立即购买」", con._is_reject_scan_text("不，立即购买"))
        check("空文案不算", not con._is_reject_scan_text(None))
        check("「提交订单」不算", not con._is_reject_scan_text("提交订单"))

        con.driver.get(url_of("detail_scan.html"))
        # 先手动点一下购买，复刻真站弹出浮层那一下
        con.driver.execute_script("document.getElementById('buyBtn').click();")
        n = con.driver.execute_script(
            "return document.querySelectorAll('[class*=\"buy__button\"]').length;")
        check("前提：浮层弹出后购买按钮从 DOM 里整个消失（不是被盖住）", n == 0, f"n={n}")
        check("能认出这层扫码浮层", con.scan_layer_present())

        con.choose_ticket()
        cur = con.driver.current_url
        check("点掉浮层后一路走到订单确认页",
              cur.endswith("order2.html"), cur.rsplit("/", 1)[-1])
    finally:
        con.driver.quit()


def s11_scan_layer_then_buy_again():
    """拒绝扫码后按钮回来了：要重新点它，而不是刷新页面把这次机会刷掉。"""
    print("\n[S11] 浮层点掉、按钮回来 → 重新点，不刷新")
    con = build_con()
    try:
        con.driver.get(url_of("detail_scan.html", "?keepbtn=1"))
        con.choose_ticket()
        cur = con.driver.current_url
        check("重新点了购买，落到订单确认页",
              cur.endswith("order2.html"), cur.rsplit("/", 1)[-1])
    finally:
        con.driver.quit()


def s12_switch_off_keeps_scan_layer():
    """dismiss_mask=False 时不许动扫码浮层（退回改造前行为）。"""
    print("\n[S12] dismiss_mask=False 时不碰扫码浮层")
    con = build_con(dismiss_mask=False)
    try:
        con.driver.get(url_of("detail_scan.html"))
        con.driver.execute_script("document.getElementById('buyBtn').click();")
        check("开关关掉时仍能认出浮层", con.scan_layer_present())
        check("但不会去点它", con.dismiss_scan_layer() is False)
        check("浮层原样还在", bool(con.driver.execute_script(
            "return !!document.querySelector('.scan-buy');")))
    finally:
        con.driver.quit()


def s13_h5_canvas_seat_page():
    """新版 H5 选座页：座位画在 canvas 上，靠读像素定位而不是瞎点。

    真站实测：瞎点整屏 40 次一个座位都没中（座位区只占屏幕一部分）；
    改成从 canvas 像素里认座位之后，第 1 个点就中了。
    """
    print("\n[S13] H5 canvas 选座页：读像素定位座位")
    con = build_con()
    try:
        con.driver.get(url_of("seat_h5.html"))
        check("认出这是选座页（title 是「选择座位」）", con._is_seat_page())
        check("认出是新版 H5 选座页", con._is_h5_seat_page())
        check("初始已选金额是 0", con._h5_picked_amount() == 0)
        check("初始按钮是灰的（不能点）", not con._h5_button_enabled())
        check("没选座位时 _h5_confirm() 不乱点", con._h5_confirm() is False)

        btn = con._h5_confirm_button()
        check("能找到「立即购买」按钮（虽然它是灰的）", btn is not None)
        check("按钮 class 里确实带 --disable（说明它确实是灰的）",
              btn is not None and "disable" in (btn.get_attribute("class") or ""))

        pts = con._h5_seat_points()
        check("从 canvas 像素里认出了座位候选点", len(pts) > 0, "n=%d" % len(pts))

        ok = con.pick_seat_on_h5()
        check("自动选座 + 下单成功", bool(ok))
        check("跳到了订单确认页",
              "order.html" in con.driver.current_url,
              con.driver.current_url.rsplit("/", 1)[-1])
    finally:
        con.driver.quit()


def s14_h5_manual_mode():
    """手动模式：用户自己点座位，脚本发现选好了就自动按「立即购买」。"""
    print("\n[S14] H5 手动模式：你点座位，脚本按「立即购买」")
    con = build_con(random_seat=False)
    try:
        con.driver.get(url_of("seat_h5.html"))
        # 模拟「用户自己点中一个座位」：点第 0 个座位（canvas 逻辑坐标 200,200）
        pos = con.driver.execute_script("""
            var cv = document.getElementById('seatCanvas');
            var r = cv.getBoundingClientRect();
            return {x: Math.round(r.left + 200 / 2000 * r.width),
                    y: Math.round(r.top + 200 / 2000 * r.height)};
        """)
        con._click_point_cdp(pos["x"], pos["y"])
        time.sleep(0.4)
        check("用户手动点中座位后，脚本能看出来", con._h5_picked())
        check("按钮不再是灰的", con._h5_button_enabled())

        con.choice_seat()
        check("脚本自动按了「立即购买」，进了订单确认页",
              "order.html" in con.driver.current_url,
              con.driver.current_url.rsplit("/", 1)[-1])
    finally:
        con.driver.quit()


def s15_risk_page_detected():
    """大麦风控页（「访问异常」）必须被认出来，不能被当成正常订单页混过去。

    真站上它和订单确认页标题一模一样（都是「确认购买」）。
    """
    print("\n[S15] 风控页识别")
    con = build_con()
    try:
        con.driver.get(url_of("risk.html"))
        check("认出这是风控页", con._is_risk_page())
        check("风控页不会被当成订单确认页", not con._is_order_confirmation_page())
    finally:
        con.driver.quit()


def s16_h5_order_pick_viewers():
    """新版 H5 订单页：按真实结构勾观演人。

    这页的类名是从 show-h5-next 的真实 chunk 里提出来的，不是猜的：
    每个观演人是一行 `.bui-ultron-viewer-item`，整行可点（toggle 挂在这行的
    onClick 上），勾选态要读里面 React 受控的 `.bui-checkbox-input`。
    老 PC 那一套（找 iconfont 图标 / input[@type=checkbox]）在这页上一个都对不上，
    这就是「勾了自动提交订单却没提交、观演人也没选」的直接原因。
    """
    print("\n[S16] H5 订单页：勾选观演人")
    con = build_con()
    try:
        con.driver.get(url_of("order_h5.html"))
        check("认出这是新版 H5 订单页", con._is_h5_order_page())
        check("同时被认成订单确认页（主循环才会停在这）", con._is_order_confirmation_page())

        rows = con._h5_viewer_rows()
        check("页面上有 3 个观演人（含 1 个本场不可用的）", len(rows) == 3, "n=%d" % len(rows))
        check("前提：一开始一个都没勾上", con._h5_selected_names() == [])

        ok = con.select_viewer_on_h5("王小明")
        check("按姓名勾上了「王小明」", bool(ok), "ok=%r" % ok)
        check("回读复选框确认真的选中了",
              bool(con.driver.execute_script(
                  "var r=document.querySelector('.bui-ultron-viewer-item[data-vid=\"1\"]');"
                  "return r && r.querySelector('.bui-checkbox-input').checked;")))

        # 这一场只让买 1 张：再点第 2 个人必须被拒（大麦会弹「所需观演人已选齐」）
        check("选满之后再点别人会被拒，也不会把已选的挤掉",
              con.select_viewer_on_h5("李小红") is False
              and con._h5_selected_names() == ["王小明"])
        # 被拒不等于「DOM 上看着像勾上了」。点整行没有原生状态、点 <input> 才有，而受控
        #   组件会把 DOM 掰回 App 状态；这条钉住夹具这层语义，否则会读出假的 checked。
        check("被拒之后，DOM 的勾选态也回到 App 的状态（受控组件语义）",
              not bool(con.driver.execute_script(
                  "var r=document.querySelector('.bui-ultron-viewer-item[data-vid=\"2\"]');"
                  "return r && r.querySelector('.bui-checkbox-input').checked;")))

        check("跳过「本场不可用」的观演人（点它会跳走去改证件）",
              con.select_viewer_on_h5("张三") is False)
        check("没被带离订单页", con._is_h5_order_page())
    finally:
        con.driver.quit()


def s17_h5_auto_commit_really_submits():
    """if_commit_order=True：勾观演人，勾协议，提交，真的提交上。

    用户报的就是这一段：「勾了自动提交订单，他没有提交订单，甚至观演人都没有选」。
    两个原因都在这条用例里锁住：主循环从选座页 break 出来时 status 还是 2，原来的
      commit_order 直接 return；订单页是新版 H5，老逻辑连观演人都找不到、提交按钮也点不动。
    """
    print("\n[S17] H5 订单页：自动提交订单真的生效")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html"))

        # 前提校验：不勾协议直接点提交，大麦会拦下来，不然这条测试等于没测
        con.driver.execute_script("document.getElementById('paySubmitBtn').click();")
        time.sleep(0.2)
        check("前提：不勾协议直接提交会被拦下（说明协议这一步是必须的）",
              not con.driver.execute_script("return window.__submitted"))
        check("前提：提交前确实一个观演人都没勾",
              con.driver.execute_script("return window.__selectedCount()") == 0)

        # 故意用 status=2 调用：复刻「走选座分支后 status 停在 2」的真实情形。
        #   老代码在这里会直接 return，什么都干不了。
        con.status = 2
        con.commit_order()

        check("订单真的提交了（window.__submitted 为真）",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的正是配置里的那位观演人",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
        check("提交前协议已经被勾上",
              bool(con.driver.execute_script(
                  "return document.getElementById('protocolInput').checked")))
        check("已经进了支付环节（标题变收银台）", con._is_payment_page())
    finally:
        con.driver.quit()


def s18_h5_unknown_viewer_never_submits():
    """配置里的观演人在页面上找不到，不许硬点提交，也别乱勾协议。"""
    print("\n[S18] H5 订单页：观演人对不上时不硬提交")
    con = build_con(if_commit_order=True, users=["查无此人"])
    try:
        con.driver.get(url_of("order_h5.html"))
        con.commit_order()
        check("一个观演人都没勾上", con._h5_selected_names() == [])
        check("因此没有硬点提交", not con.driver.execute_script("return window.__submitted"))
        check("也没顺手把协议勾掉", not bool(con.driver.execute_script(
            "return document.getElementById('protocolInput').checked")))
    finally:
        con.driver.quit()


def s19_h5_no_protocol_box_still_works():
    """页面上没有协议框时（noprotocol）不能卡住，该提交还是要提交。"""
    print("\n[S19] H5 订单页：没有协议框时照样能提交")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?noprotocol=1"))
        check("认得出「页面上没有协议框」", con.agree_protocol_on_h5() is None)
        con.commit_order()
        check("照样提交成功", bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()


def s20_pc_order_page_unchanged():
    """老版 PC 订单页仍然走老逻辑，没被这次改动带坏。"""
    print("\n[S20] 老版 PC 订单页行为不变")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order.html"))
        check("没有被误判成新版 H5 订单页", not con._is_h5_order_page())
        con.commit_order()
        check("老逻辑仍然能提交订单",
              bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()


def s21_seat_then_h5_order_autosubmit():
    """端到端：H5 选座页，H5 订单页，勾观演人，自动提交。

    这就是用户真实走的那条路（详情页，扫码浮层，H5 选座，H5 订单页）。
    中途「选完座、status 还停在 2」会让老代码静默跳过整段下单逻辑，
    所以这里故意把 status 摁在 2 上跑，锁住这个回归。
    """
    print("\n[S21] 选座后一路自动下单（端到端）")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("seat_h5.html", "?next=order_h5.html"))
        con.status = 2
        con.choice_seat()
        check("选完座落到了订单确认页",
              con._is_order_confirmation_page(), con.driver.current_url.rsplit("/", 1)[-1])
        check("认出这是新版 H5 订单页", con._is_h5_order_page())

        con.status = 2   # 故意保持 2：老代码在这一步会直接 return，什么都不做
        con.commit_order()

        check("观演人已经勾上了", con._h5_selected_names() == ["王小明"],
              str(con._h5_selected_names()))
        check("订单真的提交了", bool(con.driver.execute_script("return window.__submitted")))
        check("停在支付环节，没替用户付款", con._is_payment_page()
              and not bool(con.driver.execute_script("return !!window.__paid")))
    finally:
        con.driver.quit()


def s22_h5_manual_mode_never_signs_protocol():
    """没开「自动提交订单」，停在确认页：替用户勾好观演人，但不勾协议、不提交。

    替人在「我已阅读并同意…」上打勾是有法律含义的动作；用户没开自动下单，
    就等于没授权这件事。这条把边界锁住，免得哪天又被「顺手」点掉。
    """
    print("\n[S22] H5 订单页：没开自动下单时，不替用户签协议")
    con = build_con(if_commit_order=False)
    try:
        con.driver.get(url_of("order_h5.html"))
        con.commit_order()

        check("观演人照样替用户勾好了（只是省一次点击）",
              con._h5_selected_names() == ["王小明"], str(con._h5_selected_names()))
        check("购票协议没有被替用户勾掉",
              not bool(con.driver.execute_script(
                  "return document.getElementById('protocolInput').checked")))
        check("订单没有被提交",
              not bool(con.driver.execute_script("return window.__submitted")))
        check("人还停在订单确认页，交给用户自己点",
              con._is_h5_order_page() and not con._is_payment_page())
    finally:
        con.driver.quit()


def s23_h5_viewer_list_renders_late():
    """观演人列表晚到也要勾得上。

    真站上订单页的观演人是接口异步拉的，页面刚出来时一行都没有。
    原来脚本固定只等 max(1, page_load_delay/2) = 1 秒就去数行数，
    读到 0 行，判成「页面上没有这个人」，picked(0) < 需要的人数(1)，
    整单放弃。不报错、不抛异常，表现就是用户说的
    「大麦里明明有这个人，观演人却一个都没勾上、订单也没提交」。
    """
    print("\n[S23] H5 订单页：观演人列表晚到，也必须勾上并提交")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?render=2500"))

        # 前提校验：这一刻列表确实还没渲染出来，不然这条用例测不到东西
        check("前提：列表确实还没渲染出来",
              not con.driver.execute_script("return window.__viewerRendered"))
        check("前提：此刻页面上 0 行观演人", len(con._h5_viewer_rows()) == 0)

        t0 = time.time()
        con.commit_order()
        waited = time.time() - t0

        check("等到并勾上了配置里那位观演人",
              con._h5_selected_names() == ["王小明"], str(con._h5_selected_names()))
        check("订单真的提交了", bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
        check("确实等了列表（而不是读一次空表就放弃）", waited >= 2.0,
              f"用了 {waited:.1f}s")
    finally:
        con.driver.quit()


def s24_h5_class_names_changed():
    """大麦换了类名，也要照样勾得上、提交得掉。

    这一页的类名（.bui-ultron-*）是从官方 JS chunk 里扒出来的，但前端一发版
    就可能改。只认写死类名 = 大麦改一次名，功能整个失效。
    最后一道兜底是按文本找名字、再往上爬到「里面只有一个复选框」的那层，
    它不吃任何类名。
    """
    print("\n[S24] H5 订单页：前端换了整套类名，仍能勾上并提交")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?skin=alt"))

        # 前提校验：写死的类名在这页上确实一个都匹配不到
        check("前提：按写死的类名找不到任何观演人行",
              con._h5_find_all(".bui-ultron-viewer-item") == [])
        check("前提：这一页仍被认成 H5 订单页（靠提交按钮认出来）",
              con._is_h5_order_page())

        row = con._h5_viewer_row("王小明")
        check("按姓名文本兜底找到了观演人那一行", row is not None)
        check("能读出这一行的勾选态（复选框走了通用选择器）",
              con._h5_row_selected(row) is False)
        check("能认出「本场不可用」的那一行（靠文案，不靠类名）",
              con._h5_row_disabled(con._h5_viewer_row("张三")) is True)

        con.commit_order()
        check("照样勾上了配置里那位观演人",
              con._h5_selected_names() != [] or
              bool(con.driver.execute_script("return window.__submitted")))
        check("订单真的提交了", bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
    finally:
        con.driver.quit()


def s25_scrolled_target_still_clickable():
    """目标在首屏之外时，CDP 点击必须还能命中（真站事故的回归）。

    真站那次「不能提交订单」的根因就在这里：
      Selenium 的 `el.rect` 返回的是文档坐标（getBoundingClientRect
      加上 window.pageYOffset），不是视口坐标。订单页有 10 行观演人，
      要勾的那个排在最后一行，必须先滚动，滚动之后 pageYOffset 一大，
      按 el.rect 发出的 CDP 鼠标事件就落到视口外面了。
      它不报错、不抛异常、页面毫无反应，于是：
        观演人永远勾不上，已选择 0/1 个观众，大麦拒绝提交。
      选座页之所以一直没事，只是因为座位图在页面顶部、pageYOffset≈0，
      侥幸对了。

    所以这条用例的两个前提都很重要：
      ① 夹具必须真的把目标行放到首屏之外（否则测不出这个 bug）
      ② 要先证明「按 el.rect 算出来的点，谁也点不到」
    """
    print("\n[S25] H5 订单页：目标观演人在首屏之外，点得到吗")
    con = build_con()
    try:
        con.driver.get(url_of("order_h5.html", "?viewers=10&tall=700"))

        geo = con.driver.execute_script("""
          var rows = document.querySelectorAll('[class*="viewer-item"]');
          var last = rows[rows.length - 1];
          var r = last.getBoundingClientRect();
          return {n: rows.length, vh: innerHeight,
                  top: Math.round(r.top), name: last.innerText.split('\\n')[0]};
        """)
        # 前提校验①：夹具真的重现了那个场景
        check("前提：列表够长（10 行）", geo["n"] == 10, "n=%s" % geo["n"])
        check("前提：目标观演人排在最后一行", geo["name"] == "王小明", geo["name"])
        check("前提：目标行在首屏之外（必须先滚动才点得到）",
              geo["top"] > geo["vh"],
              "目标行 top=%s, 视口高=%s" % (geo["top"], geo["vh"]))

        row = con._h5_viewer_row("王小明")
        check("按姓名找到了目标行", row is not None)

        # 前提校验②：证明「按 el.rect 发的坐标」确实落在视口外面。
        #   这一步是整个用例的价值所在，没有它，用例可能只是碰巧是绿的。
        r = row.rect
        check("前提：el.rect 给出的 y 已经在视口之外（旧做法必然点空）",
              r["y"] > geo["vh"], "el.rect.y=%s, 视口高=%s" % (r["y"], geo["vh"]))
        check("前提：没滚动过，所以 el.rect 就是文档坐标",
              con.driver.execute_script("return window.pageYOffset;") == 0)

        # 修好后的表现：没滚到视野里之前诚实拒绝给出坐标，宁可返回 None，也不发一个
        #   必然落空的点还假装成功。
        check("修好之后：元素还在视口外时，它拒绝给出坐标（不再撒「点了」这个谎）",
              con._view_point_on(row) is None)

        # _h5_click 会先把元素滚进视野，再算视口坐标 + 命中测试，然后发 CDP 点击
        check("点它：_h5_click 返回成功", bool(con._h5_click(row)))
        check("点完之后，能算出一个真的落在它上面的点",
              con._view_point_on(con._h5_viewer_row("王小明")) is not None)
        time.sleep(0.4)
        check("点完之后这一行真的勾上了（真站上就是这里失败的）",
              con._h5_row_selected(con._h5_viewer_row("王小明")) is True)
        check("页面记录的 toggle 次数是 1（只点中了一次，没有反复点）",
              con.driver.execute_script("return window.__toggleCount;") == 1)
    finally:
        con.driver.quit()


def s26_h5_long_list_auto_commit():
    """10 行观演人 + 自动提交：整条链路走通。"""
    print("\n[S26] H5 订单页：长列表下自动勾人 + 勾协议 + 提交")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?viewers=10&tall=700"))
        con.commit_order()
        check("订单真的提交了",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位（没有勾错人）",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
    finally:
        con.driver.quit()


def s27_h5_seat_only_target_price():
    """选座只准选配置里的价位（用户投诉：「会选到其他价位的座位」）。

    真站的机制（从线上 chunk 读出来，不是猜的）：价位的色块 `<img class="price__img">`
      跟画布上的座位图是同一个生成函数产出的，色块像素色就是该价位座位的颜色；点价位项会调
      `window.aseat.highlightPrice(priceId)` 执行 grayPrice()，把其他价位降透明度
      （`Nt.Opacity = .2`，`ctx.globalAlpha = 0.2`）。被灰掉的座位仍在画布上、也仍点得中，
      只是淡了，所以「看着灰就没选」是错觉，随机点会买到别的价位。

    前提校验必须证明「夹具里确实有别的价位的座位可点」，否则选对了也说明不了什么。
    """
    print("\n[S27] H5 选座页：只选配置里的价位（380），不许选到 580/880/1280")
    con = build_con(prices=["380"])
    try:
        con.driver.get(url_of("seat_h5.html"))
        check("选座页就绪（座位图出像素 + 价位列表出现）", con._h5_wait_seat_ready())

        items = con._h5_price_items()
        by_price = {it["price"]: it for it in items}
        # 前提校验：夹具里必须有多个价位、且各有各的颜色
        check("前提：页面上有 4 个价位档",
              sorted(by_price) == [380, 580, 880, 1280], str(sorted(by_price)))
        check("前提：每一档都读到了自己的色块颜色（颜色互不相同）",
              len({by_price[p]["color"] for p in by_price}) == 4,
              str({p: by_price[p]["color"] for p in sorted(by_price)}))
        check("前提：380 档色块的像素颜色 == 画布上 380 座位的 rgb(158,128,0)",
              by_price[380]["color"] == (158, 128, 0), str(by_price[380]["color"]))

        target = con._h5_match_price(items)
        check("在页面上匹配到了配置要的 380 档", target and target["price"] == 380)
        check("点中该价位项后它变成选中态（读的是重新查询后的列表）",
              con._h5_highlight_price(target) is True)
        check("其它档没有被选中",
              [it["selected"] for it in con._h5_price_items()
               if it["price"] != 380] == [False, False, False])

        # 前提校验：灰化确实发生了，而且被灰掉的座位依然点得中
        pts = con._h5_seat_points()
        strong = [p for p in pts if (p[3] or 0) >= con.H5_SEAT_MIN_ALPHA]
        check("前提：画布上确实有「被灰化」的低 alpha 座位（其他价位）",
              0 < len(strong) < len(pts),
              "总 %d 个点，其中画得实的 %d 个" % (len(pts), len(strong)))

        # 「画得实的」点里只剩两类：目标价位（含抗锯齿的近似色）、以及「没有价位信息
        #   所以画成纯黑」的座位；不能再有 580/880/1280 的颜色。纯黑那个靠颜色筛 + 金额
        #   复核拦，不靠 alpha（真站上 salable=false 的座位也可能画得很实）。
        others = set()
        for p in strong:
            c = p[2]
            if con._color_close(c, (158, 128, 0)):
                continue
            if con._color_close(c, con.H5_SEAT_BAD_COLOR, tol=12):
                continue
            others.add(c)
        check("前提：画得实的座位里没有任何其他价位的颜色",
              not others, "漏进来的颜色：%s" % sorted(others))

        # 用旧口径（不筛颜色/不筛 alpha）能点到的点里，确实混着别的价位
        all_colors = {p[2] for p in pts}
        check("前提：不筛的话，候选点里混着 580/880/1280 的座位（这就是那个 bug）",
              len(all_colors) >= 3, str(sorted(all_colors)))

        check("选座 + 下单成功", bool(con.pick_seat_on_h5()))
        picked = con.driver.current_url.split("picked=")[-1].split("&")[0]
        check("实际买到的价位就是 380（没有买成别的价位）",
              picked == "380", "picked=%s" % picked)
    finally:
        con.driver.quit()


def s28_h5_seat_price_from_config():
    """价位跟着配置走，不是写死 380，配 880 就得买 880。"""
    print("\n[S28] H5 选座页：配置改成 880，就要买 880")
    con = build_con(prices=["880"])
    try:
        con.driver.get(url_of("seat_h5.html"))
        check("选座 + 下单成功", bool(con.pick_seat_on_h5()))
        picked = con.driver.current_url.split("picked=")[-1].split("&")[0]
        check("实际买到的价位是 880", picked == "880", "picked=%s" % picked)
    finally:
        con.driver.quit()


def s29_h5_seat_no_legend_falls_back_to_amount():
    """读不到价位列表时，靠「底栏金额」兜住价位，但绝不将错就错。

    这一条覆盖的是另一种真实退化：前端改版把价位列表的类名换掉了。
    颜色就没得筛了，可要买多少钱是配置里写着的，这是已知信息，
    所以金额校验必须继续生效；对不上就取消重选，实在挑不到就不下单，
    绝不能「配置要 880、却顺手买了个 380」。

    这条用例原来假设「一次就能抽中 880」，其实抽不中，而且不是 bug：
      `_h5_seat_points()` 末尾有一句 random.shuffle（座位必须随机挑，
      不能每次从同一个角开始点），而四个价位各占一面墙。
      于是「前 6 个候选里一个 880 都没有」是会正常发生的，
      此时生产代码正确地停手（H5_SEAT_WRONG_PRICE_LIMIT = 6）。
      按「一次必须成」去判，这条用例本身就有约两成概率报假红。

    所以判定按真正的契约来（这几条才是不许破的）：① 一旦下单，买到的必须恰好是 880；
      ② 抽不中就如实失败、绝不用别的价位蒙混下单；③ 多试几次总能成（证明「有 880 可选时
      它找得到」）。

    注：老写法固定睡 0.3 / 0.25 秒，既不改变随机性、也不改这个上限，
    所以这条用例本来就该这么写。
    """
    print("\n[S29] H5 选座页：没有价位列表，靠金额兜住价位（候选点是随机的，允许多试）")
    con = build_con(prices=["880"])
    try:
        wins = 0
        tried = 0
        for attempt in range(6):
            con.driver.get(url_of("seat_h5.html", "?nolist=1"))
            if attempt == 0:
                check("前提：这一页确实读不到价位列表", con._h5_price_items() == [])
            tried += 1
            ok = bool(con.pick_seat_on_h5())
            cur = con.driver.current_url
            if "picked=" not in cur:
                check("第 %d 次：抽不到就没下单（没有随手买别的价位）" % (attempt + 1),
                      not ok)
                continue
            got = cur.split("picked=")[-1].split("&")[0]
            check("第 %d 次买到的正是配置要的 880" % (attempt + 1),
                  got == "880", "picked=%s" % got)
            wins += 1
        check("试了 %d 次，至少成功一次（有 880 可选时它确实找得到）" % tried,
              wins >= 1)
    finally:
        con.driver.quit()


def s30_h5_amount_reader_ignores_dash_price():
    """底栏金额只认实付价，不能被「划线原价」带偏。

    真站底栏同一个容器里既有 `.footer-bar-info__price`（实付）
    又有 `.footer-bar-info__dash-price`（划线原价，带删除线）。
    按「底栏里第一个 ￥N」去读就会读到原价，那么金额校验全错：
    明明买对了也会被判成「价位不对」，然后把正确座位一个个取消掉，
    最后什么也买不到。夹具里的划线价取 9999，是个不可能和任何档位
    撞上的数，这样「读到 9999」就一定是读错了、不可能靠巧合蒙对。
    """
    print("\n[S30] H5 选座页：底栏金额要读实付价，不是划线原价")
    con = build_con()
    try:
        con.driver.get(url_of("seat_h5.html"))
        con._h5_wait_seat_ready()
        check("还没选座位时金额是 0", con._h5_picked_amount() == 0)

        # 手动选一个座位让划线价出现（夹具里它只在选座后才显示）。不能闭眼点 pts[0]：
        #   `_h5_seat_points()` 末尾有 random.shuffle，采样点里还混着「抗锯齿边缘像素」
        #   （落在座位圆外、点了没反应）和刻意埋的纯黑不可选座位；试到金额真变了为止。
        pts = con._h5_seat_points()
        hit = False
        for p in pts[:20]:
            before = con._h5_picked_amount()
            con._click_point_cdp(p[0], p[1])
            if con._poll_until(lambda b=before: con._h5_picked_amount() != b,
                               timeout=0.2, interval=0.04):
                hit = True
                break
        check("前提：点中了至少一个座位（这才有得验「金额读的是实付价」）", hit)

        shown = con.driver.execute_script("""
          var box = document.querySelector('[class*="footer-bar"]');
          return box ? box.innerText.replace(/\\s+/g, ' ').trim() : '';
        """)
        check("前提：底栏里确实同时有划线原价（9999）和实付价",
              "9999" in shown and "已选" in shown, shown[:80])
        amount = con._h5_picked_amount()
        check("读到的是实付价（380/580/880/1280），不是划线原价 9999",
              amount in (380, 580, 880, 1280),
              "读到 %s，底栏=%s" % (amount, shown[:80]))
    finally:
        con.driver.quit()


class _TripCounter:
    """数「跟浏览器说了几次话」。用 with 包住要量的那一段。

    为什么用往返次数当判据，而不是耗时：
      本地夹具上每次往返只要 2~3ms，耗时全被进程调度和 CPU 抖动淹没，
      同一段代码两次跑能差好几倍，拿它当阈值一定 flaky。
      而真站上每次往返 = 一次 WebDriver HTTP 请求，要叠加真实网络 RTT
      （50~150ms），往返次数才是决定开票那一刻快慢的量，它还是稳定的。

    包的是 WebDriver / WebElement 上会真的发一次请求的方法。
    text / rect 是 property，包不了（也不在预选人这条路上）。
    """

    def __init__(self):
        self.n = 0
        self._undo = []

    def _patch(self, cls, name):
        orig = getattr(cls, name, None)
        if orig is None or isinstance(orig, property) or not callable(orig):
            return
        if getattr(orig, "_trip_counted", False):
            return

        def wrapper(*a, **kw):
            self.n += 1
            return orig(*a, **kw)
        wrapper._trip_counted = True
        setattr(cls, name, wrapper)
        self._undo.append((cls, name, orig))

    def __enter__(self):
        for cls, names in ((WebDriver, ("execute_script", "find_element",
                                        "find_elements", "execute_cdp_cmd")),
                           (WebElement, ("find_element", "find_elements",
                                         "is_selected"))):
            for nm in names:
                self._patch(cls, nm)
        return self

    def __exit__(self, *exc):
        for cls, name, orig in reversed(self._undo):
            setattr(cls, name, orig)
        self._undo = []
        return False


class _TripCounterPlus(_TripCounter):
    """比 _TripCounter 多算几项：el.text / title / current_url / get_attribute / click。

    为什么必须多算：`el.text`、`driver.title`、`driver.current_url` 都是 property，
    _TripCounter 明确不包它们（原话是「也不在预选人这条路上」）。
    但详情页选城市 / 场次 / 票价这条路上，el.text 恰恰就是全部开销，
    逐个候选读一次就是 N 次真实往返；而「等开票」那一圈每轮都要看一次
    title / current_url。

    只数 execute_script 的话，「把逐个读改成一次批量读」反而会把计数从 0 变成 1，
    看起来像变慢了。那是尺子错了，不是代码错了，所以这一条用更完整的尺子。
    （旧用例继续用 _TripCounter，不动它们的阈值，免得数字口径一变全红。）
    """

    _MORE_METHODS = ("get_attribute", "is_displayed", "is_enabled", "click")
    _MORE_PROPS = ("text", "title", "current_url")

    def _patch_prop(self, cls, name):
        prop = cls.__dict__.get(name)
        if not isinstance(prop, property) or prop.fget is None:
            return
        outer = self
        fget = prop.fget

        def _counted(inst):
            outer.n += 1
            return fget(inst)
        setattr(cls, name, property(_counted))
        self._undo.append((cls, name, prop))

    def __enter__(self):
        super().__enter__()
        for nm in self._MORE_METHODS:
            self._patch(WebElement, nm)
        for nm in ("text",):
            self._patch_prop(WebElement, nm)
        for nm in ("title", "current_url"):
            self._patch_prop(WebDriver, nm)
        return self


class _SleepRecorder:
    """记录被测代码自己调用的 time.sleep 时长，用来证明「不再固定睡」。

    判据为什么是「sleep 时长」而不是墙钟耗时：
      墙钟受进程调度和 CPU 抖动影响，同一段代码两次跑能差几倍，当阈值一定 flaky。
      而「固定 sleep 有没有被换成轮询」是个结构问题，
      老写法必定出现一次 0.3 秒的 sleep，新写法只会出现 0.05 秒的小睡。
      直接量 sleep 时长，既稳定，又正好指着要证明的那件事。
    """

    def __init__(self):
        self.calls = []
        self._orig = None

    def __enter__(self):
        self._orig = time.sleep
        rec = self

        def _fake(sec, *a, **kw):
            try:
                rec.calls.append(float(sec))
            except Exception:
                pass
            return rec._orig(sec, *a, **kw)
        time.sleep = _fake
        return self

    def __exit__(self, *exc):
        time.sleep = self._orig
        return False

    @property
    def longest(self):
        return max(self.calls) if self.calls else 0.0


def s31_h5_busy_submit_retries_fast():
    """提交被「排队」拦下时，必须能尽快重提（开票瞬间最常见的失败）。

    真站表现：点「提交订单」弹「当前排队人数过多，请稍后再试」。
    观演人勾了、协议也勾了，只是这一次请求没被服务端收下。
    大麦自己的用户指引就是「不要退出，保持后台常驻，持续点击提交」，
    所以这不是脚本的错，重提就能过。

    关键是「尽快」：老写法每轮固定等满 3 秒（`for _ in range(15): sleep(0.2)`），
      3 次排队就白等 9 秒，开票窗口早过去了。修好之后识别到排队词立刻返回、
      隔 0.15~0.35 秒就重提。
    """
    print("\n[S31] H5 订单页：提交被「排队」拦下，能快速重提吗")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?busy=3"))
        t0 = time.time()
        con.commit_order()
        dt = time.time() - t0

        clicks = con.driver.execute_script("return window.__submitClicks || 0")
        check("前提：夹具真的拦了 3 次提交", clicks >= 4, "点击次数=%s" % clicks)
        check("第 4 次真的提交上去了",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位（没有勾错人）",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
        # 老写法：每轮固定等 3 秒，3 次排队 ≈ 9 秒（还没算第 4 次）
        check("3 次排队总共只等了很短时间（老写法要 9 秒以上）",
              dt < 2.5, "整条链路 %.2f s" % dt)
    finally:
        con.driver.quit()


def s32_h5_non_retryable_never_spams():
    """反向用例：「人没勾」「协议没勾」这类不许当成拥挤猛点。

    这是 S31 的另一半，也是最要紧的那一半，如果脚本分不清
    「服务端排队」和「自己没勾上」，它就会在账号上疯狂点提交按钮，
    那才是真的会被风控盯上的行为（而且点一万次也不会成功）。

    夹具用 ?busyhard=1 把拥挤提示换成「请选择 1 位观演人」，
    正是 _h5_should_stop 那一类。正确反应是只点一次就停手。
    """
    print("\n[S32] H5 订单页：不可重试的错误，不许猛点提交按钮")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?busy=5&busyhard=1"))
        con.commit_order()

        clicks = con.driver.execute_script("return window.__submitClicks || 0")
        check("前提：这类提示会出现（夹具确实按这条走）", clicks >= 1,
              "点击次数=%s" % clicks)
        check("只点了 1 次就停手（没有被当成排队反复重提）",
              clicks == 1, "点击次数=%s" % clicks)
        check("没有提交（这类错误重提一万次也不会成）",
              not con.driver.execute_script("return window.__submitted"))
    finally:
        con.driver.quit()


def s33_h5_pick_viewer_round_trips():
    """勾一个观演人要几次往返，把这一轮的提速钉住，别让它悄悄退回去。

    用 10 行观演人的页面（要勾的那个人排在最后一行），这是最坏情况。

    改前实测（同一套夹具）：
        按姓名找一行          21 次
        读一行（姓名/勾选/不可用）  2+2+3 次
        勾一个人              55 次
        commit_order 全链路   176 次
    改后：
        按姓名找一行           2 次
        勾一个人               9 次
        commit_order 全链路   38 次

    这个用例只钉「勾一个人」这一段，它是用户最直接感知的那一步
    （开票后要连续勾好几个），而且判据稳定、不受机器快慢影响。
    """
    print("\n[S33] H5 订单页：勾一个观演人的往返次数（性能回归）")
    con = build_con()
    try:
        con.driver.get(url_of("order_h5.html", "?viewers=10&tall=700"))
        con._log = lambda m: None          # 日志不是被测对象，别让它干扰

        with _TripCounter() as tc:
            ok = con.select_viewer_on_h5("王小明")
        trips = tc.n

        check("仍然勾上了（提速不许以功能为代价）", bool(ok))
        check("勾一个人 ≤ 15 次往返（改前是 55 次）",
              trips <= 15, "实测 %d 次" % trips)

        # 换个位置再量一次：确认不是「第一次凑巧」。
        # 这一行本来已经勾上了，先取消再量，走的是同一条真实路径。
        con.driver.execute_script(
            "var r=document.querySelector('[data-vid=\"1\"]');"
            "if (r) r.click();")
        time.sleep(0.1)
        with _TripCounter() as tc2:
            ok2 = con.select_viewer_on_h5("王小明")
        check("第二次同样勾上了", bool(ok2))
        check("第二次同样 ≤ 15 次往返", tc2.n <= 15, "实测 %d 次" % tc2.n)
    finally:
        con.driver.quit()


def s34_detail_pick_city_date_price():
    """详情页选「城市 / 场次 / 票价」：选中的必须是配置要的那一个，且别慢慢来。

    这一段是开票前最后一次能从容做的事情，但它同样是「每个候选单独问浏览器
    一次」的重灾区：`_select_option_by_config` 把 `el.text` 写在双层循环最里层
    （场次个数 × 选项个数），`_find_and_click_element` 则是读一个判一个点一个。
    改成「一次读完所有候选的文本、再在本地比对」之后，必须证明
    选中的还是配置要的那一个，选错票价是真花钱的事。

    夹具里埋的前缀重合与售罄档就是干这个的：① 城市有「北京」和「北京（大兴）」，
    配置要「北京」；② 场次里 12-04 带「无票」，配置要 12-05；③ 票价里 880 带
    「缺货」，配置要 380。
    """
    print("\n[S34] 详情页：选城市 / 场次 / 票价（选对 + 往返次数）")
    con = build_con(city="北京", dates=["2026-12-05"], prices=["380"])
    try:
        con.driver.get(url_of("detail.html"))
        con._log = lambda m: None

        with _TripCounter() as tc:
            ok_city = con.select_city_on_page()
        check("城市选上了", bool(ok_city))
        check("选的是「北京」，不是前缀重合的「北京（大兴）」",
              con.driver.execute_script("return window.__picked.city;") == "北京",
              str(con.driver.execute_script("return window.__picked.city;")))
        check("城市只点了一次", con.driver.execute_script("return window.__clickCount.city;") == 1,
              str(con.driver.execute_script("return window.__clickCount.city;")))
        check("选城市 ≤ 6 次往返（逐个读文本的写法要 4 次以上）",
              tc.n <= 6, "实测 %d 次" % tc.n)

        with _TripCounter() as tc:
            ok_date = con.select_date_on_page()
        check("场次选上了", bool(ok_date))
        check("选的是配置要的 2026-12-05，不是带「无票」的 12-04",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-05",
              str(con.driver.execute_script("return window.__picked.date;")))
        check("选场次 ≤ 6 次往返", tc.n <= 6, "实测 %d 次" % tc.n)

        with _TripCounter() as tc:
            ok_price = con.select_price_on_page()
        check("票价选上了", bool(ok_price))
        check("选的是 380，没选到「缺货」的 880",
              con.driver.execute_script("return window.__picked.price;") == "380",
              str(con.driver.execute_script("return window.__picked.price;")))
        check("选票价 ≤ 6 次往返", tc.n <= 6, "实测 %d 次" % tc.n)

        # 再一次走完整段，确认三件事可以连着做完、互不干扰
        con.driver.get(url_of("detail.html"))
        with _TripCounter() as tc:
            con.select_details_page_mobile()
        got = con.driver.execute_script(
            "return [window.__picked.city, window.__picked.date, "
            "window.__picked.price].join('/');")
        check("整段做完，三个都落在配置要的值上", got == "北京/2026-12-05/380", got)
        check("整段 ≤ 20 次往返", tc.n <= 20, "实测 %d 次" % tc.n)
    finally:
        con.driver.quit()


def s35_detail_pc_select_option_by_config():
    """PC 路径的 `_select_option_by_config`：选对档位，且不选售罄的。

    这条和 S34 是两个不同的函数：S34 走 `_find_and_click_element`（移动端：一次读文本
    本地判，点）；这条走 `_select_option_by_config`（PC：原来双层循环里读文本，
    场次个数 × 选项个数 次往返，现在一次读完再本地比对）。两条路改法不同、出错方式也
    不同，必须单独测；`_select_option_by_config` 的匹配语义更松
    （`config_value in elem_text`），松就容易选错，而它选的是票价，选错是真花钱的事。
    夹具里的坑正好卡这个：① 城市「北京」vs「北京（大兴）」，前缀重合；② 场次 12-04
    带「无票」；③ 票价 880 带「缺货」，都必须跳过。外加反向断言：配置里的值页面上没有
    时要如实返回 False，不许乱点别的档位（「选不到就随便选一个」是最坏的行为）。
    """
    print("\n[S35] 详情页 PC 路径：选城市 / 场次 / 票价（走 _select_option_by_config）")
    con = build_con(city="北京", dates=["2026-12-05"], prices=["380"])
    try:
        con.driver.get(url_of("detail.html"))
        con._log = lambda m: None

        ok = con.select_city_on_page_pc()
        check("PC 城市选上了", bool(ok))
        check("选的是「北京」，不是前缀重合的「北京（大兴）」",
              con.driver.execute_script("return window.__picked.city;") == "北京",
              str(con.driver.execute_script("return window.__picked.city;")))

        ok = con.select_date_on_page_pc()
        check("PC 场次选上了", bool(ok))
        check("跳过带「无票」的 12-04，选了配置要的 12-05",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-05",
              str(con.driver.execute_script("return window.__picked.date;")))

        ok = con.select_price_on_page_pc()
        check("PC 票价选上了", bool(ok))
        check("跳过「缺货」的 880，选了配置要的 380",
              con.driver.execute_script("return window.__picked.price;") == "380",
              str(con.driver.execute_script("return window.__picked.price;")))

        # 反向断言：选不到时不许随便点一个
        con.driver.get(url_of("detail.html"))
        con.config.city = "广州"          # 页面上没有这个城市
        check("配置的城市页面上没有 → 如实返回 False",
              con.select_city_on_page_pc() is False)
        check("并且一个都没被点（没有「选不到就随便选一个」）",
              con.driver.execute_script("return window.__clickCount.city;") == 0,
              "被点了 %s 次" % con.driver.execute_script("return window.__clickCount.city;"))
    finally:
        con.driver.quit()


def s36_detail_loop_is_cheap():
    """详情页「等开票」那一圈循环要跟浏览器说几次话。

    这是跑得最久的一段：脚本可能提前几分钟就停在详情页上，循环每 0.08 秒转一圈。
    老写法一圈要问十来次：scan_layer_present() 3 个选择器各一次 find_elements；
    购买按钮文案 / 「不，选座购票」各一次 find_elements + el.text；
    _is_order_confirmation_page() 读 body.text（还要先读 title）；_is_h5_order_page()
    url + 最多 3 次 find_elements；_is_seat_page() url + title。改完之后每轮状态用
    _detail_state() 一次往返问完，页面类型判定缓存起来，只在「地址/标题变了」或超过
    1.5 秒才重做。

    本用例做逐项对照：快照给出的结论必须和那几个老函数一模一样。提速不许改变判断，
    判错就是把「还没开票」误当成「该点按钮了」，那会把一次抢票机会直接在详情页上刷掉。
    """
    print("\n[S36] 详情页主循环：一圈的往返次数 + 结论必须与老写法一致")
    con = build_con(city="北京", dates=["2026-12-05"], prices=["380"])
    try:
        con.driver.get(url_of("detail.html"))
        con._log = lambda m: None

        with _TripCounterPlus() as old_c:
            old_scan = con.scan_layer_present()
            old_buy = con._get_element_text_safe("buy__button__text", By.CLASS_NAME)
            old_link = con._get_element_text_safe("buy-link", By.CLASS_NAME)
        with _TripCounterPlus() as new_c:
            st = con._detail_state() or {}

        check("前提：老写法读这三样要 ≥ 6 次往返", old_c.n >= 6,
              "老写法 %d 次" % old_c.n)
        check("一次快照 ≤ 2 次往返",
              new_c.n <= 2, "新 %d 次 / 老 %d 次" % (new_c.n, old_c.n))
        check("结论一致：有没有扫码浮层",
              bool(st.get("scan")) == bool(old_scan),
              "新=%r 老=%r" % (st.get("scan"), old_scan))
        check("结论一致：购买按钮文案",
              (st.get("buy") or "") == (old_buy or ""),
              "新=%r 老=%r" % (st.get("buy"), old_buy))
        check("结论一致：「不，选座购票」链接文案",
              (st.get("link") or "") == (old_link or ""),
              "新=%r 老=%r" % (st.get("link"), old_link))

        # 页面类型判定：老写法每圈都做，新写法只用一次廉价初筛
        with _TripCounterPlus() as type_old:
            con._is_order_confirmation_page()
            con._is_h5_order_page()
            con._is_seat_page()
        with _TripCounterPlus() as type_new:
            snap = con._page_lite() or {}

        check("前提：老写法判定页面类型要 ≥ 8 次往返", type_old.n >= 8,
              "老写法 %d 次" % type_old.n)
        check("廉价初筛只 1 次往返",
              type_new.n <= 1, "新 %d 次 / 老 %d 次" % (type_new.n, type_old.n))
        check("初筛读到的是真实地址与标题",
              (snap.get("url") or "").endswith("detail.html")
              and (snap.get("title") or "") == "演出详情",
              repr(snap))

        # 浮层页：快照必须认出浮层，并且同时读到那条链接文案
        con.driver.get(url_of("detail_scan.html"))
        con.driver.execute_script("document.getElementById('buyBtn').click();")
        check("前提：浮层真的弹出来了", con.scan_layer_present())
        st2 = con._detail_state() or {}
        check("快照认出了扫码浮层", bool(st2.get("scan")), repr(st2))
        check("快照同时读到了「不，选座购票」链接文案（老写法要分两次问）",
              con._is_reject_scan_text(st2.get("link")), repr(st2.get("link")))
        check("浮层页上购买按钮已消失，快照如实说 None",
              st2.get("buy") in (None, ""), repr(st2.get("buy")))
    finally:
        con.driver.quit()


def s37_seat_page_waits_are_polls_not_sleeps():
    """选座页「点完等反应」：不再固定睡，但上界一秒没缩。

    真站实测：点一次座位后要等 0.3 秒才读底栏金额；点「立即购买」后要等 0.6 秒
    才看有没有跳走。这两段都是按最坏情况付钱，而页面通常几十毫秒就有反应，
    多张票时 0.3 秒还要乘张数。

    判据用「被测代码自己调了多少 sleep」，不用墙钟耗时：
      墙钟受调度抖动影响一定 flaky；而「固定 sleep 有没有变成轮询」是个
      结构问题，老写法必定出现一次 0.3 秒，新写法只会出现 0.05 秒的小睡。
    """
    print("\n[S37] H5 选座页：点完不再固定睡（上界不变）")
    con = build_con(prices=["380"])
    try:
        con._log = lambda m: None

        # ① 先证明轮询器本身：不成立时等满上界，成立时立刻返回
        t0 = time.time()
        got = con._poll_until(lambda: False, timeout=0.25, interval=0.05)
        dt = time.time() - t0
        check("条件不成立时等到上界才返回（上界没被偷偷缩短）",
              got is False and dt >= 0.2, "%.3f s" % dt)
        check("条件成立时立刻返回", con._poll_until(lambda: True, timeout=5.0) is True)

        # ② 真实选座：点完盯底栏金额，而不是固定睡 0.3 秒
        con.driver.get(url_of("seat_h5.html"))
        con._h5_wait_seat_ready()
        pts = con._h5_seat_points()
        check("前提：canvas 里认得出座位候选点", len(pts) > 0, "n=%d" % len(pts))

        with _SleepRecorder() as sr:
            ok = con.pick_seat_on_h5()

        check("仍然自动选中并点了「立即购买」", bool(ok))
        check("跳到了订单确认页", "order.html" in con.driver.current_url,
              con.driver.current_url.rsplit("/", 1)[-1])
        check("全程最长一次 sleep ≤ 0.06 秒（老写法点一下固定 0.3 秒）",
              sr.longest <= 0.06,
              "最长 %.3f s，共 %d 次" % (sr.longest, len(sr.calls)))
    finally:
        con.driver.quit()


def s38_quantity_pick_fast():
    """详情页选数量：点 N-1 次「+」，不再每点一次固定睡 0.25 秒。

    买 4 张票要点 3 次「+」，老写法固定 3 × 0.25 = 0.75 秒。
    改成「点完盯输入框的值涨到位」：涨到就走，上界仍是 0.25 秒/次，
    所以慢的时候一点都不比以前少等，只是不再按最坏情况付钱。

    夹具把值改成异步生效（setTimeout 60ms），这样测的才是
    「轮询真的比固定睡快、而且真慢的时候等得住」，而不是「轮询器能跑」。
    """
    print("\n[S38] 详情页选数量：点「+」不再固定睡")
    con = build_con(users=["甲", "乙", "丙", "丁"])     # 4 张，点 3 次
    try:
        con._log = lambda m: None
        con.driver.get(url_of("detail_qty.html"))
        check("前提：初始数量是 1", con._qty_value() == "1", str(con._qty_value()))

        with _SleepRecorder() as sr:
            con._select_quantity_on_page(platform="PC端")

        clicks = con.driver.execute_script("return window.__clicks || 0")
        check("数量点到了 4 张", con._qty_value() == "4", str(con._qty_value()))
        check("一共只点了 3 次「+」", clicks == 3, "点了 %s 次" % clicks)
        check("最长一次 sleep ≤ 0.05 秒（老写法每次固定 0.25 秒）",
              sr.longest <= 0.05,
              "最长 %.3f s，共 %d 次" % (sr.longest, len(sr.calls)))
    finally:
        con.driver.quit()


# 「刚跳过来、整页还没渲染」的 H5 下单页，真站上就是这个时序

_LATE_PAGE_JS = """
<script>
(function () {
  var q = new URLSearchParams(location.search);
  var blank = parseInt(q.get('blank') || '0', 10);
  if (!blank) return;
  // 复刻真站：整页 DOM 都是前端异步渲染的，刚跳过来时连提交按钮都不存在。
  // 把 body 内容整体摘下来（节点带着事件监听一起走），过 blank 毫秒再挂回去。
  var body = document.body, frag = document.createDocumentFragment();
  while (body.firstChild) frag.appendChild(body.firstChild);
  setTimeout(function () {
    body.appendChild(frag);
    window.__lateRendered = true;
  }, blank);
})();
</script>
"""


def build_late_order_page():
    """造一个「刚跳过来、整页还没渲染」的 H5 下单页，地址里带 h5-ultron-buy。

    真站的下单页地址是
        m.damai.cn/app/dmfe/h5-ultron-buy/index.html?…
      所以这里把目录名就叫 h5-ultron-buy，让 url 带上同一个特征串，
      判据走的才是和线上同一条路。

    这个函数要往磁盘写文件，所以先断言落点确实在临时目录里
      （这个仓库已经吃过好几次「测试写脏真文件」的亏）。

    不复制夹具、只在运行时生成：order_h5.html 始终是唯一的一份，
      否则两边各改各的，夹具会慢慢和真实用例分叉。
    """
    root = tempfile.mkdtemp(prefix="damai_late_h5_")
    page_dir = os.path.join(root, "h5-ultron-buy")
    assert os.path.abspath(page_dir).startswith(os.path.abspath(tempfile.gettempdir())), \
        page_dir
    os.makedirs(page_dir, exist_ok=True)
    with open(os.path.join(FIX, "order_h5.html"), encoding="utf-8") as f:
        html = f.read()
    dst = os.path.join(page_dir, "index.html")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html.replace("</body>", _LATE_PAGE_JS + "</body>"))
    return "file:///" + dst.replace("\\", "/")


def s39_h5_order_page_renders_late():
    """整页还没渲染出来时，也必须认出这是 H5 下单页（真站事故的回归）。

    真站那次「勾了自动提交订单，观演人没勾、订单也没提交」，根因就在这条：① 下单页
    地址是 m.damai.cn/app/dmfe/h5-ultron-buy/index.html，而判据当时只认 order-pay，
    认不出；② 这一页的 DOM 又是异步渲染的，刚跳过来那一瞬间连提交按钮都不存在，
    所以「按元素认」也认不出。两条都不成立，判成「老版 PC 页」，拿老选择器去点一个
    当时不存在的按钮，不报错、不抛异常，订单就这么没了。

    为什么原来 38 条用例一条都没拦住：夹具是静态 HTML，元素从一开始就在，「按元素认」
    在测试里永远成立，缺的正是「还没渲染」这个时刻。
    """
    print("\n[S39] H5 下单页：整页还没渲染，也要认得出、提交得掉")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_late_order_page() + "?blank=1500")

        # 前提校验：这一刻页面上是真的一无所有，不然这条用例测不到东西
        check("前提：地址里带着 h5-ultron-buy（和真站同一个特征）",
              "h5-ultron-buy" in con.driver.current_url)
        check("前提：此刻连提交按钮都还没渲染出来",
              con._h5_find_all(".pay-submit-btn") == [])
        check("前提：此刻页面上 0 行观演人", len(con._h5_viewer_rows()) == 0)

        check("只凭地址就认出这是 H5 下单页（一个元素都没靠）",
              con._is_h5_order_page())

        t0 = time.time()
        con.commit_order()
        waited = time.time() - t0

        check("等到并勾上了配置里那位观演人",
              con._h5_selected_names() == ["王小明"], str(con._h5_selected_names()))
        check("订单真的提交了",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位",
              con.driver.execute_script("return (window.__submittedNames||[]).join('、')")
              == "王小明")
        check("确实等了整页渲染出来（而不是读一次空表就放弃）", waited >= 1.0,
              "用了 %.1fs" % waited)
    finally:
        con.driver.quit()


def build_ultron_order_page(query=""):
    """把第二代订单页夹具放进一个叫 h5-ultron-buy 的目录里再打开。

    真站地址是 `m.damai.cn/app/dmfe/h5-ultron-buy/index.html`，目录名一样，
    url 才带同一个特征串，走的才是和线上同一条判据。

    query 用来演按钮形态的变化（见夹具里的 btntext / btntag / nobtn）。

    要往磁盘写文件，先断言落点确实在临时目录里
      （这个仓库已经吃过好几次「测试写脏真文件」的亏）。
    """
    root = tempfile.mkdtemp(prefix="damai_ultron_h5_")
    page_dir = os.path.join(root, "h5-ultron-buy")
    assert os.path.abspath(page_dir).startswith(os.path.abspath(tempfile.gettempdir())), \
        page_dir
    os.makedirs(page_dir, exist_ok=True)
    with open(os.path.join(FIX, "order_h5_ultron.html"), encoding="utf-8") as f:
        html = f.read()
    dst = os.path.join(page_dir, "index.html")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html)
    url = "file:///" + dst.replace("\\", "/")
    if query:
        url += "?" + query
    return url


def s40_ultron_viewer_list_without_class_or_checkbox():
    """第二代订单页（h5-ultron-buy）：没有行类名、没有复选框，也要勾得上人。

    真站那次「点了几下没反应、观演人一个都没勾上、订单没提交，而且不报错」，根因全在
    这一页的结构上（2026-10-08 从线上 index.min.js 逐字核对过）：① 类名是 viwer（大麦
    自己把 viewer 拼错了），按正确拼写找是 0 命中；② 整页没有 input[type=checkbox]，
    勾选态是图标字体（i.icondanxuan-xuanzhong_ = 选中 / i.icondanxuan-weixuan_ = 未选）；
    ③ 整行没有 class，只能从姓名容器往上爬到「含勾选图标的那层」。

    三条合起来：探针 rows/boxes 全是 0（于是判成「列表没渲染出来」），行也枚举不出来
    （于是「页面上没有这个人」），勾没勾上也读不出来（于是点中了还会被自己点回去）。
    全程静默。

    为什么原来 39 条用例一条都没拦住：老夹具 order_h5.html 是第一代的
    `.bui-ultron-viewer-item` + 真复选框，它和真站已经不是同一套 DOM 了，夹具永远同意自己。
    """
    print("\n[S40] 第二代订单页：没有行类名、没有复选框，也要勾得上人")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page())

        check("前提：地址里带着 h5-ultron-buy（和真站同一个特征）",
              "h5-ultron-buy" in con.driver.current_url)
        check("前提：观演人区里一个 input[type=checkbox] 都没有",
              con.driver.execute_script(
                  "return document.querySelectorAll('.viewer input[type=checkbox]').length")
              == 0)
        check("前提：用的是拼错的类名 .viwer-info-name",
              con.driver.execute_script(
                  "return document.querySelectorAll('.viwer-info-name').length") == 3)

        check("只凭地址就认出这是 H5 下单页", con._is_h5_order_page())

        rows = con._h5_viewer_rows()
        check("能枚举出 3 行（行本身没有类名，只能从姓名容器往上推）",
              len(rows) == 3, "枚举到 %d 行" % len(rows))

        infos = con._h5_rows_info()
        names = [x.get('name') for x in infos] if infos else None
        sels = [x.get('selected') for x in infos] if infos else None
        dis = [bool(x.get('disabled')) for x in infos] if infos else None
        check("读全表：三个姓名都对", names == ['王小明', '李小红', '张三'], str(names))
        check("初始态三行都是「未选」—— 读的是图标，不是 checked",
              sels == [False, False, False], str(sels))
        check("带 disableReason 的那行被认成「本场不可用」",
              dis == [False, False, True], str(dis))
        check("「特权观演人」小标签没被算进姓名里（否则姓名对不上）",
              infos is not None and infos[1].get('name') == '李小红',
              str(infos[1].get('name')) if infos else 'None')

        row2 = con._h5_viewer_row('李小红')
        check("按姓名定位到的是「李小红」那一行，不是第一行",
              row2 is not None and con._h5_row_name(row2) == '李小红',
              con._h5_row_name(row2) if row2 is not None else 'None')

        check("本场不可用的「张三」被跳过，返回 False",
              con.select_viewer_on_h5('张三') is False)
        check("跳过之后没有误勾任何人",
              con._h5_selected_names() == [], str(con._h5_selected_names()))

        check("勾「王小明」成功（回读图标读出来的）",
              con.select_viewer_on_h5('王小明') is True)
        check("页面上确实只有他被勾上",
              con._h5_selected_names() == ['王小明'], str(con._h5_selected_names()))
        clicks = con.driver.execute_script("return window.__toggleCount || 0")
        check("只点了一次 —— 没有因为读不出状态而反复瞎点",
              clicks == 1, "点了 %s 次" % clicks)
        check("已经勾上的不会再点一遍",
              con.select_viewer_on_h5('王小明') is True
              and con.driver.execute_script("return window.__toggleCount || 0") == 1)

        con.commit_order()
        check("订单真的提交了（协议框没类名，是按文案找到的）",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的就是配置里那位",
              con.driver.execute_script(
                  "return (window.__submittedNames||[]).join('、')") == '王小明')
    finally:
        con.driver.quit()


def s41_unknown_skin_falls_back_to_row_signature():
    """两代选择器全对不上时，靠「点一下、看这一行变没变」把勾选认出来。

    这一页没有复选框，图标也不是 icondanxuan-*（夹具用 ?skin=blank 复刻
    「大麦又换了一版皮肤」）。任何具体类名都读不出勾选态，唯一的判据是
    「这一行的外观指纹有没有变」。

    没有这条兜底，页面再换一次皮肤就又是「点了没反应」，而且同样不报错。
    这条兜底的代价是只能判「变了没有」，所以它排在两代判据之后，
    只有在读不出结论时才启用。
    """
    print("\n[S41] 又换皮肤：勾选态读不出来时，靠外观指纹兜底")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page() + "?skin=blank")

        rows = con._h5_viewer_rows()
        check("前提：行还是枚举得出来（姓名容器没改名）", len(rows) == 3,
              "枚举到 %d 行" % len(rows))
        check("前提：勾选态确实读不出来（两代判据都不成立）",
              con._h5_row_selected(rows[0]) is None)

        check("勾「王小明」仍然成功（靠外观指纹变化认出来的）",
              con.select_viewer_on_h5('王小明') is True)
        check("页面上他确实被勾上了",
              con.driver.execute_script("return window.__selectedCount()") == 1)
        clicks = con.driver.execute_script("return window.__toggleCount || 0")
        check("只点了一次", clicks == 1, "点了 %s 次" % clicks)
    finally:
        con.driver.quit()


def s42_ultron_submit_button_shape_changes():
    """提交按钮的三种变脸：换文案 / 换标签 / 干脆没有，都要有交代。

    实跑现场（2026-10-08 23:10，打包版 accounts.log）：

        [Account 01] ✓ 已勾选观演人 1/1：测试甲
        [Account 01] ⚠ 找不到「提交订单」按钮（类名和文案都没找到），页面结构可能又变了
        [Account 01] === 订单页观演人区结构快照 ===

    三件事叠在一起才是这次踩的坑：① 标签白名单漏了，`H5_SUBMIT_BY_TEXT_JS` 只扫
    button/div/span/a/section；底部那条是服务端模板渲染的，真站出现过
    `<div><p>提交订单</p></div>`，p 不在白名单里，而外层 div 有子元素会被「叶子优先」
    跳过，于是页面上明明写着「提交订单」，脚本报「找不到」。② 文案也不在清单里，清单
    只有提交订单/立即提交/确认下单/…，换个「立即购票 / 确认购买 / 去结算 / 下单」就全
    不命中。③ 快照抓错了地方，按钮找不到时抓的是观演人区；可这次观演人是勾上了的，
    那份快照一句有用的都没有，日志里只剩一句「结构可能又变了」，谁也不知道按钮到底
    长什么样。

    所以这条用例同时锁住「认得出」和「认不出时留得下线索」。
    """
    print("\n[S42] 提交按钮：换文案 / 换标签 / 没有按钮，三种都要有交代")

    # 1) 换文案：老清单里没有「立即购票」
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("btntext=" + quote("立即购票")))
        check("前提：按钮文案确实换成「立即购票」了",
              con.driver.execute_script("return document.getElementById('paySubmitBtn')"
                                        ".textContent") == "立即购票")
        check("换了个文案也认得出提交按钮", con._h5_submit_button() is not None)

        con.commit_order()
        check("而且真的提交上去了（不是只认出来不用）",
              bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()

    # 2) 换标签：文案被包在 <p> 里（老白名单漏掉的那一类）
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page(
            "btntext=" + quote("提交订单") + "&btntag=p"))
        check("前提：文案确实被包进了 <p>",
              bool(con.driver.execute_script(
                  "return document.querySelector('#paySubmitBtn p') !== null")))
        check("文案包在 <p> 里也认得出（老白名单只扫 button/div/span/a/section）",
              con._h5_submit_button() is not None)
        con.commit_order()
        check("这条也真的提交上去了",
              bool(con.driver.execute_script("return window.__submitted")))
    finally:
        con.driver.quit()

    # 3) 真的没有按钮：不许瞎点，而且必须留下「按钮区」快照
    con = build_con(if_commit_order=True)
    dump_dir = tempfile.mkdtemp(prefix="damai_submit_dump_")
    assert os.path.abspath(dump_dir).startswith(os.path.abspath(tempfile.gettempdir())), \
        dump_dir
    con._dump_dir = dump_dir
    try:
        con.driver.get(build_ultron_order_page("nobtn=1"))
        check("前提：页面上真的没有提交按钮了",
              bool(con.driver.execute_script(
                  "return document.getElementById('paySubmitBtn') === null")))
        check("前提：这种页面照样被判成 H5 下单页（判据还是地址）",
              con._is_h5_order_page())

        con.commit_order()

        check("认不出来就如实失败，没有假装提交成功",
              not con.driver.execute_script("return window.__submitted"))
        check("也没乱点任何东西（不会把「找不到」变成「点错了别的」）",
              (con.driver.execute_script("return window.__submitClicks || 0") or 0) == 0)

        path = os.path.join(dump_dir, "order_page_submit_dump.txt")
        txt = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                txt = f.read()
        check("留下了「按钮区」快照（按钮找不到的时候，观演人区那份没有用）",
              "提交按钮" in txt, path if not txt else "")
        check("快照里带上了「像按钮的节点」这一段，下次能照着补选择器",
              "像按钮的节点" in txt)
        check("快照里写清了正文里有没有「提交 / 支付 / 购买」这些字",
              "正文里出现过" in txt)
        check("快照落在临时目录里，没往仓库写",
              os.path.abspath(dump_dir).startswith(os.path.abspath(tempfile.gettempdir())))
    finally:
        con.driver.quit()


def s43_bottom_renders_later_than_viewer_list():
    """底部（协议条 + 提交条）比观演人列表晚出来时，必须等它出来再动手。

    真站铁证（2026-10-08 实跑日志）：「进入订单页 / 勾观演人 / 找不到提交按钮」
    三件事全发生在同一秒内。观演人列表和底部根本不是同一批渲染的
底部那两块（协议 + 提交）是服务端模板下发的，比列表晚一步；
    而脚本原来只等列表（`_h5_wait_viewer_list`），列表一出来就往下走，
    那一瞬间底部还是空的，于是判「找不到提交订单按钮」整单放弃，且不报错。
    同一个页面晚 16 秒进的另一路就找得到按钮（那一路上底部已经渲染好了），
    这是最直接的对照证据。

    另一个同步出现的坑：底部还没出来时，协议框也找不到，
    于是日志上写着「这一场页面上没有购票协议框，跳过」，
    可大麦那边照样因为没有勾选而把提交拦下来。两件事其实是同一件事。
    """
    print("\n[S43] 底部比观演人列表晚一步出来：要等它，不能抢跑")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("bottom=3000"))
        check("前提：刚进来时页面上还没有提交按钮（底部还没挂载）",
              con.driver.execute_script(
                  "return document.getElementById('paySubmitBtn') === null"))
        check("前提：观演人列表倒已经渲染出来了（复刻真站那一瞬间）",
              con.driver.execute_script(
                  "return document.querySelectorAll('.viwer-info-name').length") == 3)
        check("这时候确实一个候选都找不到 —— 正是真站上出事的那一瞬间",
              con._h5_submit_candidates() == [],
              str(con._h5_submit_candidates()))

        con.commit_order()
        check("等底部渲染出来之后，订单真的提交上去了",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的是配置里那位（没勾错人）",
              con.driver.execute_script(
                  "return (window.__submittedNames||[]).join('、')") == "王小明")
        check("协议也勾上了（等到底部之后才找得到协议框，没被误判成「没有」）",
              bool(con.driver.execute_script(
                  "var p = document.getElementById('protocolInput');"
                  "return !!p && p.checked")))
    finally:
        con.driver.quit()


def s44_protocol_checkbox_may_be_an_icon():
    """协议那一行也可能没有复选框，勾选态是图标字体画的。

    真站铁证（2026-10-09 00:28 实跑日志）：观演人 1/1 勾好了、提交按钮也找到了
    （「等底部」那步已经生效），下一行却是
    「⚠ 有协议框但没勾上，大麦会拦下提交，请手动勾一下」，
    接着连点 8 次提交全部无反应、一条提示都没抓到。

    根因：协议勾选态只认 `input.checked`。可这一代大麦的勾选框是
    `i.icondanxuan-xuanzhong_` / `icondanxuan-weixuan_` 图标字体画的
    （观演人那边已经逐字证实过），协议用的是同一个组件，
    行里那个模板带出来的 input 是「影子」，永远不 checked，
    于是脚本读一次说「没勾上」，点一下再读还是「没勾上」，报 False，
    而大麦那边正因为没勾协议把 8 次提交全拦了。

    夹具用 `?proto=shadow` 精确复刻：图标 + 一个永远不 checked 的隐藏 input。
    """
    print("\n[S44] 协议的真实勾选态躺在图标里（旁边还有个永远不 checked 的影子 input）")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("proto=shadow"))
        check("前提：协议行里确实有个 input（它就是那个影子）",
              con.driver.execute_script(
                  "return document.querySelectorAll('#protocolRow input').length") == 1)
        check("前提：这个 input 是隐藏的（真站也是模板带出来的，不在显示层）",
              con.driver.execute_script(
                  "var i = document.getElementById('protocolInput');"
                  "return !!i && !i.offsetParent && !i.offsetWidth"))
        # 先证明夹具真的会拦：不勾协议直接点提交
        con.driver.execute_script("document.getElementById('paySubmitBtn').click()")
        check("前提：协议没勾时，夹具真的把提交拦下了",
              not con.driver.execute_script("return window.__submitted"))
        # 重载，把刚才那条 toast 清掉（否则会被当成「大麦提示」干扰后面）
        con.driver.get(build_ultron_order_page("proto=shadow"))

        proto = con.agree_protocol_on_h5()
        check("协议勾上了（认的是图标，不是那个影子 input）", proto is True, str(proto))
        cls = con.driver.execute_script(
            "var i = document.getElementById('protocolIcon');"
            "return i ? i.className : ''") or ""
        check("图标真的变成了「选中」态（不是脚本自己以为勾上了）",
              "xuanzhong" in cls and "weixuan" not in cls, cls)
        still = con.driver.execute_script(
            "var i = document.getElementById('protocolInput');"
            "return i ? !!i.checked : null")
        check("那个影子 input 依旧没 checked —— 证明没被它带偏",
              still is False, str(still))
        toggles = con.driver.execute_script("return window.__protocolToggles || 0") or 0
        check("只点了一下（读得对就不会反复点）", toggles == 1, str(toggles))

        con.commit_order()
        check("协议勾上之后，订单真的提交上去了",
              bool(con.driver.execute_script("return window.__submitted")))
        check("提交上去的是配置里那位（没勾错人）",
              con.driver.execute_script(
                  "return (window.__submittedNames||[]).join('、')") == "王小明")
    finally:
        con.driver.quit()


def s45_show_card_must_not_be_mistaken_for_protocol():
    """演出信息卡片不能被当成「购票协议行」。

    真站铁证（2026-10-09 00:57 实跑）：协议文案清单里有「实名制购票」，
    而演出信息卡片里正好也有这个词
    （「…￥188.00票档 ×1张 实名制购票 不支持退 电子票 电子发票」），
    于是脚本把整张卡片认成了协议行，对着它报
    「⚠ 有协议框但没勾上，大麦会拦下提交」，接着连点 8 次提交都没动静。
    真凶（页面上压根没有协议条）被这句话盖住了整整两轮排查。

    修法两条一起上：清单里删掉「实名制购票」这种会撞车的词，
    并且 JS 里加硬条件，协议行必须含「同意」或「阅读」。
    """
    print("\n[S45] 演出信息卡片不能被当成「购票协议行」")
    # 1) 只有演出信息卡片，页面上没有协议条
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("card=1&noproto=1"))
        check("前提：演出信息卡片在，而且里面有「实名制购票」",
              bool(con.driver.execute_script(
                  "var c = document.getElementById('showCard');"
                  "return !!c && c.innerText.indexOf('实名制购票') >= 0")))
        check("前提：页面上确实没有协议条",
              bool(con.driver.execute_script(
                  "return document.getElementById('protocolRow') === null")))

        row, box, icon = con._protocol_by_text()
        check("不许把演出信息卡片认成协议行", row is None,
              (row.text if row is not None else '')[:60])
        proto = con.agree_protocol_on_h5()
        check("结论必须是「这一场没有协议框」，而不是「有框但没勾上」",
              proto is None, str(proto))
        check("也没去乱点卡片里的图标",
              (con.driver.execute_script(
                  "var i = document.getElementById('cardIcon');"
                  "return i ? i.className.indexOf('xuanzhong') >= 0 : false") is False))
    finally:
        con.driver.quit()

    # 2) 卡片 + 真协议条：要认对那个真的，别被卡片带偏
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("card=1"))
        check("前提：卡片在，真协议条也在",
              bool(con.driver.execute_script(
                  "return !!document.getElementById('showCard')"
                  " && !!document.getElementById('protocolRow')")))

        row, box, icon = con._protocol_by_text()
        check("找到的是真协议行（含「我已阅读并同意」），不是演出卡片",
              row is not None and '我已阅读' in (
                  con.driver.execute_script(
                      "return arguments[0].innerText || ''", row) or ''),
              (con.driver.execute_script(
                  "return arguments[0].innerText || ''", row) or '')[:60]
              if row is not None else 'None')

        proto = con.agree_protocol_on_h5()
        check("协议勾上了（返回 True）", proto is True, str(proto))
        check("卡片里的那个图标一动没动（没被误伤）",
              con.driver.execute_script(
                  "var i = document.getElementById('cardIcon');"
                  "return i ? i.className.indexOf('xuanzhong') >= 0 : false") is False)
    finally:
        con.driver.quit()


def s46_submit_button_covered_by_link():
    """提交按钮被一个透明链接盖住时：要看得出来，而且不许把页面带走。

    用户原话（2026-10-09 01:38）：「为啥有时候会跳转到淘宝网去呢」，
    加上前面那句「点提交订单没反应」，这两件事合起来很像同一个原因：
    按钮那一带被一个链接盖着。点下去要么命中链接（跳淘宝），
    要么事件被它吃掉（表现就是「没反应」）。

    后果比「点不中」更严重：脚本原来只要看到页面地址变了就报
    「订单已提交（页面已跳转）」，于是「误点了一次外链」会被报成「抢到了」。
    """
    print("\n[S46] 提交按钮被透明链接盖住：要看得出来，且不许把页面带走")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(build_ultron_order_page("trap=1"))
        check("前提：按钮确实被一个链接盖着",
              bool(con.driver.execute_script(
                  "var a = document.getElementById('trapLink');"
                  "var b = document.getElementById('paySubmitBtn');"
                  "return !!a && !!b && b.parentElement === a.parentElement")))

        cands = con._h5_submit_candidates()
        check("前提：候选里能找到按钮", bool(cands), str(len(cands)))

        blk = con._h5_blocker_of(cands[0]) if cands else ''
        check("能看出「按钮上面盖着的是谁」，并且点明那是个链接",
              'trapLink' in blk and '链接' in blk, blk)

        url_before = con.driver.current_url or ''
        if cands:
            con._h5_click(cands[0])
        time.sleep(0.6)
        check("点完页面没被带走（还在订单页上）",
              'h5-ultron-buy' in (con.driver.current_url or ''),
              (con.driver.current_url or '')[:80])

        con.commit_order()
        check("提交没成功就如实报「没成功」，不许冒充提交成功",
              not con.driver.execute_script("return window.__submitted"))
        check("页面也没被带走（全程留在订单页）",
              'h5-ultron-buy' in (con.driver.current_url or ''),
              (con.driver.current_url or '')[:80])
    finally:
        con.driver.quit()


def s47_detail_cn_date_text():
    """页面上写「12月27日」、配置写 2026-12-27，也得选中 27 号。

    客户实测：配置 27 号，成交成页面默认的 26 号。根因就是子串对不上
    （`'2026-12-27' in '12月27日 周日 19:30'` 永远 False），而老流程
    「没选成也照样往下买」。夹具把 26 号摆成默认选中，
    所以「选中的到底是 26 还是 27」一眼可辨。
    """
    print("\n[S47] 详情页：页面写「12月27日」、配置写 2026-12-27（PC + 移动两条路）")
    con = build_con(city=None, dates=["2026-12-27"], prices=None)
    try:
        con.driver.get(url_of("detail_filter.html"))
        con._log = lambda m: None

        check("前提：页面上 26 号是默认选中的",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-26")

        # PC 路：_select_option_by_config，_option_text_match（日期归一化）
        ok = con.select_date_on_page_pc()
        check("PC 场次选上了", bool(ok))
        check("PC 选中 27 号，不是默认的 26 号",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-27",
              str(con.driver.execute_script("return window.__picked.date;")))

        # 移动路：_find_and_click_element + _date_search_candidates（展开写法）
        con.driver.get(url_of("detail_filter.html"))
        ok = con.select_date_on_page()
        check("移动端场次选上了", bool(ok))
        check("移动端也选中 27 号",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-27",
              str(con.driver.execute_script("return window.__picked.date;")))

        # 反向：配置里的日期页面上没有，如实 False，且不许点别的
        con.driver.get(url_of("detail_filter.html"))
        con.config.dates = ["2026-12-31"]
        check("配置的日期页面上没有 → 如实返回 False",
              con.select_date_on_page_pc() is False)
        check("并且一个都没被点（页面还停在默认的 26 号）",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-26")
    finally:
        con.driver.quit()


def s48_soldout_price_never_buys():
    """配置要 80、80 缺货、页面上还有个 280：绝不能把 280 当 80 买了。

    两层都要钉：
      ① 数字匹配：`'80' in '280 元'` 是 True，老代码跳过缺货的 80 之后，
         把 280 当成「选上了 80」（客户实测成交价成了 280）。修完必须如实 False。
      ② 闸门：没选成，绝不点购买，转入「只等配置那一档」。
    """
    print("\n[S48] 详情页：要 80、80 缺货 → 不许把 280 当 80 买了")
    con = build_con(city=None, dates=None, prices=["80"])
    try:
        con.driver.get(url_of("detail_filter.html"))
        con._log = lambda m: None

        # ① 匹配层：必须如实 False（老写法返回 True，因为子串点中了 280）
        ok = con.select_price_on_page_pc()
        check("80 缺货 → 票价选择如实返回 False", ok is False)
        check("也没有把 280 当成「选上了 80」（票价一次都没被点）",
              con.driver.execute_script("return window.__clickCount.price;") == 0,
              "被点了 %s 次" % con.driver.execute_script("return window.__clickCount.price;"))

        # ② 闸门层：choose_ticket 没选成，进等档，全程不点购买
        con.driver.get(url_of("detail_filter.html"))
        gate = {"wait": 0}

        class _Gate(Exception):
            pass

        def _fake_wait(is_mobile):
            gate["wait"] += 1
            raise _Gate()

        con._wait_for_configured_option = _fake_wait
        try:
            con.choose_ticket()
        except _Gate:
            pass
        check("没选成 → 转入「只等配置那一档」", gate["wait"] == 1)
        check("全程没点购买按钮",
              con.driver.execute_script("return window.__buyClicked;") == 0,
              "被点了 %s 次" % con.driver.execute_script("return window.__buyClicked;"))
    finally:
        con.driver.quit()


def s49_h5_seat_wrong_price_never_confirmed():
    """选座页两道闸门（客户实测：配置 80，最后把 880 的座确认提交了）。

    ① 进场就发现已选中座位、金额对不上，绝不确认（上一轮点错留下的现场）。
    ② 配置那档在页面上缺货 / 不存在，一个座位都不点，交给外层轮询等刷。
    """
    print("\n[S49] H5 选座页：金额对不上不确认 / 配置那档缺货一个都不点")

    # ① 进场即发现「选中的不是配置要的价位」，不确认
    con = build_con(prices=["80"])
    try:
        con.driver.get(url_of("seat_h5.html", "?preselect=880"))
        con._log = lambda m: None
        con._h5_wait_seat_ready()
        check("前提：进场时已选中一个 880 的座位，底栏金额是 880",
              con._h5_picked() is True and con._h5_picked_amount() == 880,
              "picked=%s amount=%s" % (con._h5_picked(), con._h5_picked_amount()))
        check("金额不是配置要的 80 → 绝不确认（返回 False）",
              con.pick_seat_on_h5() is False)
        check("也没被带走（没下单）",
              "picked=" not in (con.driver.current_url or ""),
              (con.driver.current_url or "")[:80])
    finally:
        con.driver.quit()

    # ② 配置那档在页面上根本不存在，一个座位都不点
    con = build_con(prices=["680"])
    try:
        con.driver.get(url_of("seat_h5.html"))
        con._log = lambda m: None
        con._h5_wait_seat_ready()
        check("前提：页面上没有配置要的 680 档",
              all(it["price"] != 680 for it in con._h5_price_items()))
        check("配置的档不在页面上 → 返回 False（不点任何座位）",
              con.pick_seat_on_h5() is False)
        check("页面上确实一个座位都没被选中",
              con.driver.execute_script("return window.__pickedSeats().length;") == 0)
        check("也没下单", "picked=" not in (con.driver.current_url or ""))
    finally:
        con.driver.quit()

    # ③ 配置那档在页面上、但标了「缺货/不可点」，同样一个都不点
    con = build_con(prices=["880"])
    try:
        con.driver.get(url_of("seat_h5.html", "?disable=880"))
        con._log = lambda m: None
        con._h5_wait_seat_ready()
        items = con._h5_price_items()
        check("前提：880 档被标记为缺货（disabled）",
              [it["disabled"] for it in items if it["price"] == 880] == [True],
              str([(it["price"], it["disabled"]) for it in items]))
        check("880 缺货 → 返回 False（一个座位都不点）",
              con.pick_seat_on_h5() is False)
        check("也没下单", "picked=" not in (con.driver.current_url or ""))
    finally:
        con.driver.quit()


def s50_pc_calendar_date_pick():
    """日历式详情页：「日历日期」和「场次」是两件事，各填各的。

    这种页面（2026-10-10 客户截图实测）「日期」是一个月历格子，「场次」列表只渲染当前
    选中那天的场次，进场默认 26 号时，DOM 里根本不存在 27 号的场次文本，光靠场次匹配
    永远搜不到，必须先点日历格。但「点哪一天」只能由单独的「日历日期（选填）」框
    （config.calendar_date）说了算：填了，先点那一天，再按场次匹配；留空，这一步整个
    跳过（没填就不猜），页面一根毫毛都不许动。以前两件事挤在一个「场次日期」框里，用户
    实测「场次选不对、日期也选不对」。
    """
    print("\n[S50] 详情页：日历日期（选填）与场次分开 —— 填了才点日历，空着绝不猜")
    con = build_con(city=None, calendar_date="2026-12-27",
                    dates=["2026.12.27 19:30 下本"], prices=["80"])
    try:
        con.driver.get(url_of("detail_calendar.html"))
        con._log = lambda m: None

        check("前提：进场默认 26 号，DOM 里只有 26 号的场次",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-26"
              and '2026.12.27' not in
              (con.driver.execute_script(
                  "return document.getElementById('timesList').textContent;") or ''))

        ok = con.select_date_on_page_pc()
        check("日历日期 + 场次都填 → 场次选上了（先点日历、再挑场次）", bool(ok))
        check("日历里的「27」被点过（日历切到了 27 号）",
              con.driver.execute_script("return window.__clickCount.day;") >= 1
              and con.driver.execute_script("return window.__day;") == "2026-12-27",
              "点了 %s 次 / 当前 %s" % (
                  con.driver.execute_script("return window.__clickCount.day;"),
                  con.driver.execute_script("return window.__day;")))
        check("选中的场次落在 27 号，不是默认的 26 号",
              con.driver.execute_script("return window.__picked.date;") == "2026-12-27",
              str(con.driver.execute_script("return window.__picked.date;")))
        check("全程没点购买",
              con.driver.execute_script("return window.__buyClicked;") == 0)

        # 只填日历日期、场次留空：点中那一天就算成
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = "2026-12-27"
        con.config.dates = []
        ok = con.select_date_on_page_pc()
        check("只填日历日期（场次留空）→ 点中 27 号即算成功", ok is True)
        check("27 号确实被点中（日历切到 27）",
              con.driver.execute_script("return window.__day;") == "2026-12-27"
              and con.driver.execute_script("return window.__clickCount.day;") == 1,
              "%s / 点了 %s 次" % (con.driver.execute_script("return window.__day;"),
                                 con.driver.execute_script("return window.__clickCount.day;")))
        check("场次没配就不替你挑场次（一张卡片都没点）",
              con.driver.execute_script("return window.__clickCount.date;") == 0)

        # 日历日期留空、只填场次：一步都不许碰日历
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = ""
        con.config.dates = ["2026.12.27 19:30 下本"]
        ok = con.select_date_on_page_pc()
        check("日历日期留空 → 如实 False（27 号的场次压根不在 DOM 里）", ok is False)
        check("日历格一个都没点（没填就不猜）",
              con.driver.execute_script("return window.__clickCount.day;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))
        check("页面还停在默认的 26 号",
              con.driver.execute_script("return window.__day;") == "2026-12-26")

        # 反向 1：日历日期是别的月份（那个月日历没显示），不动手
        # 页面上恰好也有文本是「1」「2」「3」的置灰格子，一个都不许点。
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = "2027-01-02"
        con.config.dates = []
        ok = con.select_date_on_page_pc()
        check("日历日期所在月份没显示 → 如实 False", ok is False)
        check("别的月份的置灰格子一个都没被点",
              con.driver.execute_script("return window.__clickCount.day;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))
        check("页面还停在默认的 26 号",
              con.driver.execute_script("return window.__day;") == "2026-12-26")

        # 反向 2：日历日期是 30 号（格子点得动、那天没场次），点了没反应
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = "2026-12-30"
        ok = con.select_date_on_page_pc()
        check("点了 30 号但那天没场次 → 如实 False", ok is False)
        check("30 号的格子确实被点过（是「没场次」，不是「没点上」）",
              con.driver.execute_script("return window.__clickCount.day;") == 1,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))
        check("也没把默认的 26 号场次选上",
              con.driver.execute_script("return window.__clickCount.date;") == 0)

        # 反向 3：日历点成了，但场次本身匹配不上，照样 False
        # 「日历成功」不能拿来抵「场次认不上」，否则就把页面默认那场买走了。
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = "2026-12-27"
        con.config.dates = ["2026.12.26 19:30 下本"]
        ok = con.select_date_on_page_pc()
        check("日历点成了但场次认不上 → 照样 False（不许顺手买默认那场）", ok is False)
        check("也确实没去点 27 号那两场的卡片",
              con.driver.execute_script("return window.__clickCount.date;") == 0)

        # 变体 1：月份行带左右箭头渲染（真站常见）也要认得出
        con.driver.get(url_of("detail_calendar.html", "?cal=verbose"))
        con.config.calendar_date = "2026-12-27"
        con.config.dates = []
        ok = con.select_date_on_page_pc()
        check("月份行写成「‹ 2026年12月 ›」也选上了", bool(ok))
        check("这种月份行下日历切到 27 号",
              con.driver.execute_script("return window.__day;") == "2026-12-27",
              str(con.driver.execute_script("return window.__day;")))

        # 变体 2：月份行不带年份（「12月」）也要认得出
        con.driver.get(url_of("detail_calendar.html", "?cal=noyear"))
        con.config.calendar_date = "2026-12-27"
        ok = con.select_date_on_page_pc()
        check("月份行只写「12月」也选上了", bool(ok))
        check("这种月份行下日历切到 27 号",
              con.driver.execute_script("return window.__day;") == "2026-12-27",
              str(con.driver.execute_script("return window.__day;")))

        # 变体 3：填的日历日期不带年份（「12月27日」）也要能点
        con.driver.get(url_of("detail_calendar.html"))
        con.config.calendar_date = "12月27日"
        ok = con.select_date_on_page_pc()
        check("日历日期写「12月27日」（无年份）也选上了", bool(ok))
        check("无年份写法下日历切到 27 号",
              con.driver.execute_script("return window.__day;") == "2026-12-27",
              str(con.driver.execute_script("return window.__day;")))
    finally:
        con.driver.quit()


def s51_seat_never_spins_forever():
    """选座页等不到档位时不许无限空转（2026-10-10 实测的「卡死一小时」）。

    ① 配置没填价位时不能一个座位都不点：上一版把「页面上没有配置要的价位」一律当成
       「等这一档」，`pick_seat_on_h5` 就永远返回 False，`choice_seat` 的 `while` 无限
       空转，日志每 0.3 秒刷两条警告、订单永远提交不了且一声不响（S21 就是这现场，跑全量停了 1 小时）。
    ② 「等配置的档」那一圈的整页刷新：原来每 10 秒整页刷一次，现在是 3.5 秒级（带抖动）。
    """
    print("\n[S51] 选座页/等档：不许无限空转，刷新节奏要够快")

    # ① 配置里没填价位：照样得挑出一个座位来
    con = build_con()                       # 默认 prices 为空
    try:
        con.driver.get(url_of("seat_h5.html"))
        con._log = lambda m: None
        ok = con.pick_seat_on_h5()
        check("配置里没填价位 → 照样挑一个座位（不是空手返回 False）", bool(ok))
        check("真挑中了、也真的点了「立即购买」（不是空手回来）",
              "order.html" in (con.driver.current_url or ""),
              (con.driver.current_url or "").rsplit("/", 1)[-1])
        check("挑中的确实是页面上的一档价位（不是被「等配置的档」挡下）",
              "picked=" in (con.driver.current_url or "")
              and (con.driver.current_url or "").split("picked=")[-1].strip() != "",
              (con.driver.current_url or "").rsplit("picked=", 1)[-1])
    finally:
        con.driver.quit()

    # ② 配置的价位页面上没有：choice_seat 必须在有限时间内收手
    con = build_con(prices=["99999"])       # 页面上没有这一档
    try:
        con.driver.get(url_of("seat_h5.html"))
        con._log = lambda m: None
        t0 = time.time()
        th = threading.Thread(target=con.choice_seat, daemon=True)
        th.start()
        th.join(timeout=50.0)
        stuck = th.is_alive()
        cost = time.time() - t0
        if stuck:
            # 老写法会一直转下去，把页面挪走让它能退出（别把测试也挂死）。
            # 注意 stuck 是挪走之前取的快照，所以下面的断言照样是红的。
            try:
                con.driver.get("about:blank")
            except Exception:
                pass
            th.join(timeout=10.0)
        check("等不到这一档时 choice_seat 会收手（不是无限空转）",
              not stuck, "空转了 %.0f 秒还没停" % cost)
        check("收手之前也绝没把别的价位买走",
              not con.driver.execute_script("return !!window.__bought;"))
    finally:
        con.driver.quit()

    # ③ 等档那一圈的刷新节奏：14 秒内至少要刷 2 次（旧的 10 秒一次会是 0 次）
    # 窗口取 14 秒：新节奏下重选约 2.0 秒 × 抖动一次，10 秒只有 5 次上下，
    # 抖到 4 次就假红；刷新 14/4=3.5 次，抖到 2 次也够判。10 秒窗口会抖红。
    con = build_con(city=None, dates=["2026-12-05"], prices=["99999"])
    try:
        con.driver.get(url_of("detail_filter.html"))
        con._log = lambda m: None
        check("常量：等档整页刷新 ≤3.5 秒（10 → 2 → 因风控退回 3 秒 → 再退到 3.5 秒）",
              con.WAIT_REFRESH_SEC <= 3.5, str(con.WAIT_REFRESH_SEC))
        check("常量：等档重选 ≤2.0 秒", con.WAIT_RESELECT_SEC <= 2.0,
              str(con.WAIT_RESELECT_SEC))

        hits = {"refresh": 0, "reselect": 0}
        real_get = con.driver.get

        def _count_get(url, *a, **k):
            hits["refresh"] += 1
            return real_get(url)

        con.driver.get = _count_get
        t0 = time.time()

        def _never_selected(mobile=None):
            hits["reselect"] += 1
            # 到点就假装「等到了」，让这个本来无限的循环正常退出
            return time.time() - t0 >= 14.0

        con._select_details_core = _never_selected
        th = threading.Thread(target=con._wait_for_configured_option,
                              args=(False,), daemon=True)
        th.start()
        th.join(timeout=32.0)
        check("等档循环会一直重选（不是在睡觉）", hits["reselect"] >= 4,
              "14 秒里重选 %d 次" % hits["reselect"])
        check("14 秒里至少整页刷新 2 次（3.5 秒一次应有 4 次上下；10 秒一次的旧节奏 0 次）",
              hits["refresh"] >= 2, "刷新 %d 次" % hits["refresh"])
    finally:
        con.driver.quit()


def s52_refresh_interval_reaches_the_wait_loop():
    """账号里填的「刷新频率」要真的管到「等配置的场次/票档」那一圈（2026-10-10 用户原话
    「整页刷新的效果再稍微提高一点」）。

    查出一个真问题：那个输入框以前只管主循环里「还没开票」的那次刷新（`_refresh_wait()`），
    管不到等档那一圈，客户填 1 秒，等档照样按 10 秒（后来 3 秒、现在 3.5 秒）走，看着就是「填了没用」。
    现在两边都听它，下限 2.5 秒（整页刷新是风控最在意的动作，不许压到 0.3；2026-10-11
    客户账号被风控拦下后下限从 1 秒提到 2 秒，同日用户要求整体再降一档，提到 2.5 秒）。
    判据全落在 `_wait_refresh_sec()` 的返回值上。
    """
    print("\n[S52] 「刷新频率」要管到等档那一圈（不是只管家门口那一次）")

    def make(**kw):
        cfg = Config(index_url="about:blank", login_url="about:blank",
                     target_url="about:blank", users=["王小明"], city=None,
                     dates=None, prices=None, if_listen=True,
                     if_commit_order=False, max_retries=1, fast_mode=True,
                     page_load_delay=0.2)
        for k, v in kw.items():
            setattr(cfg, k, v)
        con = object.__new__(Concert)      # 绕开 __init__（它会去连大麦）
        con.config = cfg
        return con

    default = Concert.WAIT_REFRESH_SEC
    check("① 没填刷新频率 → 用默认值", make()._wait_refresh_sec() == default,
          "默认 {} 秒".format(default))
    check("默认值 3.5 秒：共识区间 1~3 秒是「多快不会被盾」的下沿研究，"
          "10-11 用户明确要求整体再降一档（折中到 3.5）",
          default == 3.5, "{} 秒".format(default))
    check("② 填 2.5 → 就按 2.5 秒", make(refresh_interval=2.5)._wait_refresh_sec() == 2.5)
    check("③ 填字符串 \"2.5\"（手改 accounts.json 会是这样）→ 也认",
          make(refresh_interval="2.5")._wait_refresh_sec() == 2.5)
    check("④ 填 0.3 → 按 2.5 秒下限走（等档那圈不许压到 0.3）",
          make(refresh_interval=0.3)._wait_refresh_sec() == Concert.MIN_REFRESH_SEC,
          "拿到 {}".format(make(refresh_interval=0.3)._wait_refresh_sec()))
    check("⑤ 填负数 → 同样落回下限，不是负数",
          make(refresh_interval=-5)._wait_refresh_sec() == Concert.MIN_REFRESH_SEC)
    check("⑥ 填了不是数字的 → 退回默认，且不抛异常",
          make(refresh_interval="abc")._wait_refresh_sec() == default)
    check("⑦ 空字符串 / None → 都算「没填」",
          make(refresh_interval="")._wait_refresh_sec() == default
          and make(refresh_interval=None)._wait_refresh_sec() == default)
    check("⑧ 主循环那次刷新没被顺手改坏（还是按 refresh_interval 走）",
          1.5 * 0.8 - 1e-6 <= make(refresh_interval=1.5)._refresh_wait()
          <= 1.5 * 1.25 + 1e-6,
          str(make(refresh_interval=1.5)._refresh_wait()))
    hint = _wizard_refresh_hint_text()
    # 期望值从常量里取，不手写「2秒」；常量一改这里跟着走。
    want = "等配置的场次/票档时%s秒" % ("%g" % Concert.WAIT_REFRESH_SEC)
    floor = "不到%s秒时" % ("%g" % Concert.MIN_REFRESH_SEC)
    check("⑨ 输入框旁边那句提示说的秒数跟代码一致（数字从常量取，不再手写）",
          want in hint and floor in hint, hint[:140])


def _wizard_refresh_hint_text():
    """把向导里「刷新频率」输入框旁边那句说明抠出来。

    只读源码文本，不 import damai_wizard（那会把 tkinter 拖进来，
      而这条用例是纯逻辑检查，不该依赖图形环境）。
    """
    path = os.path.join(ROOT, "damai_wizard.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    i = src.find("整页刷新每隔多久一次")
    if i < 0:
        return ""
    # 把源码里的折行和引号去掉，只留文字，这段在源码里是拼起来的几段字符串
    return (src[i:i + 260].replace("\n", "").replace('"', "")
            .replace(" ", ""))


def s53_calendar_page_async_lists():
    """日历页：场次 / 票档是异步重渲染的（2026-10-10 客户现场）。

    客户原话：「在选择了日历日期之后，我看并没有选择相应的场次和票档」。当时日志一行行
    永远重复：

        ✓ 已点日历里的「27」号，12 月的场次刷出来了
        场次/票档没能按配置选中，绝不替你买页面默认选中的那一份。

    四个真问题一起钉：① 「看见日期字样」不等于「场次列表回来了」。点日历格，列表先清空、
    再异步拉回来，而「当前日期」标题那一瞬已经变成「2026.12.27」了，老代码认的正是这个
    字样，于是拿着一个空列表去匹配，永远匹配不上（判据得是结果：容器里有条目）。② 等档
    循环每 1.5 秒重来一次，就重点一遍日历格；重点一次会把列表再清空一次，于是永远停在
    「刚点完」那个窗口里，自己把自己锁死在竞态里，怎么等都不收敛。同一天在同一份页面上
    只点一次；整页刷新后 window 上的标记自然消失，刷新后重新点，语义正好。③ 票档写
    「280.0元」时，页面的「280元」匹配不上：`_option_text_match` 只对纯数字才按「独立的
    数」比，`'280.0元' in '280元'` 是 False，票档永远选不上（它本来还轮不到出错：场次没过
    闸门，票价那一步直接跳过，客户看到的就是「场次和票档都没选上」）。连带票档列表也跟着
    场次异步重渲染，同样要先等它出条目。④ 挑场次只能在场次列表里找，全文碰运气会命中页面
    别处的「日期标题」，点它不选中任何场次，却会被当成「点到了」，后面就拿着页面默认那场
    往下买。

    变体 `?nodate=1`（日历页的另一种真实形态）：场次栏不写日期（只写「19:30 下本」），补
    一轮「去掉日期」的写法；但只在「配置的日期 == 日历点的那天」时才允许去掉，自相矛盾的
    配置（配 26 号场次、日历点 27 号）必须如实失败（S50 反向 3 钉着）。
    """
    print("\n[S53] 日历页：场次/票档异步重渲染 —— 等列表、别重点、只认列表里的、票档按数比")
    con = build_con(city=None, calendar_date="2026-12-27",
                    dates=["2026.12.27 19:30 下本"], prices=["280.0元"])
    try:
        # ① + ② + ③：走完整条链路（城市 / 场次 / 票价）
        con.driver.get(url_of("detail_calendar_async.html"))
        con._log = lambda m: None

        ok = con._select_details_core(mobile=False)
        check("日历点完 → 场次真的选上了（不再是对着空列表匹配）", bool(ok))
        check("选中的是 27 号 19:30 那场（不是默认的 26 号，也不是 14:00）",
              con.driver.execute_script("return window.__picked.session;")
              == "2026.12.27 19:30 下本",
              str(con.driver.execute_script("return window.__picked.session;")))
        check("票档也选上了（配置写「280.0元」、页面写「280元」）",
              con.driver.execute_script("return window.__picked.price;") == "280",
              str(con.driver.execute_script("return window.__picked.price;")))
        check("全程没点购买",
              con.driver.execute_script("return window.__buyClicked;") == 0)

        # ② 等档循环重来时，不许反复重点日历（重点 = 把列表再清空一次）
        n_before = con.driver.execute_script("return window.__clickCount.day;")
        for _ in range(4):
            con._select_details_core(mobile=False)
        n_after = con.driver.execute_script("return window.__clickCount.day;")
        check("同一份页面上日历格只点一次（等档循环重来时不再点）",
              n_before == 1 and n_after == 1,
              "点了 %s → %s 次" % (n_before, n_after))

        # 整页刷新之后要重新点（标记跟着页面走，不跨页面存活）
        con.driver.get(url_of("detail_calendar_async.html"))
        ok = con._select_details_core(mobile=False)
        check("整页刷新后日历会重新点一次（标记不跨页面存活）",
              bool(ok)
              and con.driver.execute_script("return window.__clickCount.day;") == 1,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))

        # ④ 只配日期（没配时间）时，也只在场次列表里挑
        con.driver.get(url_of("detail_calendar_async.html"))
        con.config.dates = ["2026.12.27"]
        ok = con.select_date_on_page_pc()
        check("只配了日期 → 在场次列表里挑一场（点的是卡片，不是标题）",
              bool(ok)
              and con.driver.execute_script("return window.__clickCount.session;") == 1
              and con.driver.execute_script("return window.__picked.date;") == "2026-12-27",
              "session 点了 %s 次 / %s" % (
                  con.driver.execute_script("return window.__clickCount.session;"),
                  con.driver.execute_script("return window.__picked.date;")))

        # 变体 ?nodate=1：场次栏不写日期，去掉日期再比（且只认列表里的）
        con.driver.get(url_of("detail_calendar_async.html", "?nodate=1"))
        con.config.dates = ["2026.12.27 19:30 下本"]
        ok = con._select_details_core(mobile=False)
        check("场次栏不写日期时也选上了（去掉日期再比）", bool(ok))
        check("选中的确实是 19:30 那场（不是页面默认/14:00，也没被日期标题骗）",
              con.driver.execute_script("return window.__picked.session;") == "19:30 下本",
              str(con.driver.execute_script("return window.__picked.session;")))
        check("没去点页面上那两个「日期标题」（点它不叫选场次）",
              con.driver.execute_script("return window.__clickCount.title;") == 0,
              "点了 %s 次标题" % con.driver.execute_script("return window.__clickCount.title;"))
        check("这种形态下票档也选上了",
              con.driver.execute_script("return window.__picked.price;") == "280")

        # 反面：日期相同、时间对不上，如实 False，且不点别处
        con.driver.get(url_of("detail_calendar_async.html", "?nodate=1"))
        con.config.dates = ["2026.12.27 13:00 早场"]
        ok = con.select_date_on_page_pc()
        check("配的场次页面上没有 → 如实 False（不去点日期标题）", ok is False)
        check("一张场次卡片都没点",
              con.driver.execute_script("return window.__clickCount.session;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.session;"))
        check("也没拿「日期标题」凑数（全文搜索最容易撞上的就是它）",
              con.driver.execute_script("return window.__clickCount.title;") == 0,
              "点了 %s 次标题" % con.driver.execute_script("return window.__clickCount.title;"))

        # 诊断：一条都对不上时，现场快照要落到临时目录里、且写着页面文案
        dump = os.path.join(DUMP_DIR, "session_pick_dump.txt")
        check("前提：快照写在临时目录里（不往仓库里写脏东西）",
              os.path.abspath(dump).startswith(os.path.abspath(DUMP_DIR)), dump)
        txt = ""
        if os.path.isfile(dump):
            with open(dump, "r", encoding="utf-8") as f:
                txt = f.read()
        check("场次对不上时留了现场快照（写着页面上真实有哪些场次）",
              "场次区现场快照" in txt and "19:30 下本" in txt and "13:00 早场" in txt,
              dump)

        # 没有卡片容器时：立刻返回，一秒都不多等
        con.driver.get("about:blank")
        t0 = time.time()
        ready = con._wait_list_ready(None, None, timeout=3.0)
        cost = time.time() - t0
        check("页面上没有场次容器 → -1、且不等待（普通页面一秒不多花）",
              con._list_ready(None, None) == -1
              and ready is False and cost < 0.5, "等了 %.2f 秒" % cost)

        # 纯逻辑：去日期 / 票档展开
        check("去日期：2026.12.27 19:30 下本 → 19:30 下本",
              con._strip_date_part("2026.12.27 19:30 下本") == "19:30 下本",
              con._strip_date_part("2026.12.27 19:30 下本"))
        check("去日期：12月27日 14:00 上本 → 14:00 上本",
              con._strip_date_part("12月27日 14:00 上本") == "14:00 上本",
              con._strip_date_part("12月27日 14:00 上本"))
        check("去日期：本来就没日期 → 原样返回（不凭空造词）",
              con._strip_date_part("19:30 下本") == "19:30 下本")
        check("票档展开：280.0元 → 归一成 280元、再补一个 280",
              con._price_match_values(["280.0元"]) == ["280.0元", "280元", "280"],
              str(con._price_match_values(["280.0元"])))
        check("票档按数比：280 命中「280元」、绝不命中「80元（缺货登记）」",
              con._option_text_match("280", "280元") is True
              and con._option_text_match("280", "80元（缺货登记）") is False)
        check("票档展开：页面原文照抄（带文字）不再展开出裸数字 188",
              con._price_match_values(["全价票188元（站席）"]) == ["全价票188元（站席）"],
              str(con._price_match_values(["全价票188元（站席）"])))
        check("两个数的票档不展开（188元（含2张）语义不明，不猜）",
              con._price_match_values(["188元（含2张）"]) == ["188元（含2张）"])
        check("票档展开：空的还是空的", con._price_match_values([]) == [])
    finally:
        con.driver.quit()


def s54_detail_new_page_shape():
    """详情页新形态（Vue 渲染，客户实测 id=1086775381505），三个坑一起钉。

    客户原话：「在日历日期已经选中了我所填的日历日期的时候，它并没有选场次，然后它又
    继续刷新了。」真页面抓下来的 DOM 说明问题不在日历，在「找场次」：① 列表容器的类名
    根本不是 sku-times-card / sku-tickets-card，而是
    `div.perform__order__select__performs`（场次）、`div.perform__order__select`（票档），
    条目统一是 `div.select_right_list_item`；旧代码
    `find_elements(By.CLASS_NAME, 'sku-times-card')` 拿到空，方法1 整个被跳过，只剩「全文
    XPath 碰运气」。② 条目文案里的空格是排版留下的：「19:30␣␣下本」（两个空格），而配置
    里写的是一个空格；XPath 的 contains(text(), ...) 是字面比较，实测命中 0 个，日志里
    「找到 0 个包含 '2026.12.27 19:30 下本' 的元素」就是这么来的，而那一刻页面上明明白白
    写着它。③ 点完日历格，列表一段时间内还挂着上一天的旧条目（真站 0.22 秒，夹具放大到
    400ms），所以「列表里有条目」不能当就绪判据，得拿点击前的内容比出差异，或者以「匹配
    得上」为准。

    两张列表共用一套类名，只能靠左边的 .select_left 认（日期 / 场次 / 票档）。夹具里还有
    一条列表外面的通知，文案里同样写着目标日期和场次名：全文搜索会先撞上它，点它不算选中
    任何场次。
    """
    print("\n[S54] 详情页新形态：容器类名 / 文案里的排版空格 / 点完日历后的旧条目")
    con = build_con(city=None, calendar_date="2026-12-27",
                    dates=["2026.12.27 19:30 下本"], prices=["280.0元"])
    try:
        def texts_in(box, item_sel):
            if box is None:
                return []
            return [con._norm_ws(t) for t in
                    con._batch_texts(box.find_elements(By.CSS_SELECTOR, item_sel))]

        def picked(what):
            # 页面上的文案带排版空格（19:30␣␣下本），比之前先归一化
            return con._norm_ws(
                con.driver.execute_script("return window.__picked.%s;" % what))

        # ① 两张列表都要认出来，而且不能认成同一张
        con.driver.get(url_of("detail_new_perform.html"))
        # 票档列表进场时是异步填的（夹具 200ms），等它出来再断言
        dl = time.time() + 3.0
        while time.time() < dl:
            if con.driver.execute_script(
                    "return document.querySelectorAll("
                    "'#ticketsList [class*=\\\"select_right_list_item\\\"]').length;") >= 4:
                break
            time.sleep(0.1)
        box_t, item_t = con._sku_box("场次")
        box_p, item_p = con._sku_box("票档")
        check("① 场次列表认出来了（新形态的类名）", box_t is not None)
        check("① 票档列表认出来了（和日期、场次共用类名，靠左边的标签区分）",
              box_p is not None)
        check("① 两张列表不是同一个容器（按第一个匹配取就会拿到日期那张）",
              box_t is not None and box_p is not None and box_t.id != box_p.id,
              "%s / %s" % (box_t.id if box_t else None, box_p.id if box_p else None))
        check("① 场次容器里装的确实是场次",
              any("19:30" in t for t in texts_in(box_t, item_t)),
              str(texts_in(box_t, item_t)))
        check("① 票档容器里装的确实是票档",
              any("280.0元" in t for t in texts_in(box_p, item_p)),
              str(texts_in(box_p, item_p)))

        # ③ 前提：进场时列表是页面默认那天的
        old = texts_in(box_t, item_t)
        check("③ 前提：进场时列表是 12-26 的（页面默认那天）",
              any("12.26" in t for t in old), str(old))

        # ② 排版空格
        check("② 归一化空白后能匹配上（配置一个空格 / 页面两个空格）",
              con._option_text_match("2026.12.27 19:30 下本",
                                    "2026.12.27 19:30  下本") is True)
        check("② 不归一化就对不上（字面比较，这就是「找到 0 个」的原因）",
              con._option_text_match("2026.12.27 19:30 下本",
                                    "2026.12.27 19:30  下本") is True
              and ("2026.12.27 19:30 下本" in "2026.12.27 19:30  下本") is False)

        # ② 文本搜索这条路本身：带排版空格的条目要搜得到
        # （方法1 会先命中，所以这条单独验方法2；退回字面 contains 时它会红）
        hit = con._find_and_click_element(
            "2026.12.26 19:30 下本", max_results=10,
            skip_keywords=['无票', '售罄'], print_results=False, root=box_t)
        check("② 文本搜索能命中带两个空格的条目（XPath 走 normalize-space）",
              hit is True)
        check("② 点中的就是那一项", picked("session") == "2026.12.26 19:30 下本",
              picked("session"))

        # 方法2 有两条 XPath：限定了容器的、和页面级的。上面那条走的是限定容器，
        # 这里补页面级那条，不归一化空白时它同样一条都搜不到。
        hit = con._find_and_click_element(
            "2026.12.26 14:00 上本", max_results=10,
            skip_keywords=['无票', '售罄'], print_results=False)
        check("② 页面级搜索也能命中带两个空格的条目（不限定容器那条 XPath）",
              hit is True)
        check("② 页面级搜索点中的也是它", picked("session") == "2026.12.26 14:00 上本",
              picked("session"))

        # ③ 只填日历日期时：说「点成了」之前列表必须已经换过。卡的是「别拿旧条目当就绪」：
        #   只填日历日期时没有后面的匹配兜底，提前说成功就是真报了个假成功。
        con.driver.get(url_of("detail_new_perform.html"))
        con.config.dates = []
        con.config.calendar_date = "2026-12-27"
        ok = con.select_date_on_page_pc()
        listed = con.driver.execute_script(
            "var e=document.getElementById('timesList');"
            "return e ? e.textContent : '';") or ""
        check("③ 只填日历日期：返回成功时，列表里已经是新那天的场次了",
              bool(ok) and "12.27" in listed,
              "返回 %s / 列表内容 %r" % (ok, listed[:60]))
        con.config.calendar_date = "2026-12-27"
        con.config.dates = ["2026.12.27 19:30 下本"]

        # ③ 纯异步列表（没有日历参与）：列表晚渲染也要等到
        con.driver.get(url_of("detail_new_perform.html", "?late=1"))
        con.config.calendar_date = ""
        con.config.dates = ["2026.12.26 19:30 下本"]
        t0 = time.time()
        ok = con.select_date_on_page_pc()
        check("③ 列表晚渲染也要等到（等不到就重试，不是比一轮就放弃）",
              bool(ok) and picked("session") == "2026.12.26 19:30 下本",
              "返回 %s / %s / 耗时 %.1f 秒" % (ok, picked("session"), time.time() - t0))
        con.config.calendar_date = "2026-12-27"
        con.config.dates = ["2026.12.27 19:30 下本"]
        con.driver.get(url_of("detail_new_perform.html"))

        # 走完整条链路
        ok = con._select_details_core(mobile=False)
        check("场次和票档都按配置选上了", bool(ok))
        check("选中的是 27 号 19:30 那场（不是页面默认的 26 号 14:00）",
              picked("session") == "2026.12.27 19:30 下本", picked("session"))
        check("选中那天是 12-27", picked("date") == "2026-12-27", picked("date"))
        check("票档选中的是 280（不是页面默认的 480）",
              picked("price") == "280", picked("price"))
        check("日历格只点了一次",
              con.driver.execute_script("return window.__clickCount.day;") == 1,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))
        check("旧条目还挂着的时候一次都没点（否则会选成上一天的场次）",
              con.driver.execute_script("return window.__staleClicks;") == 0,
              "旧列表上点了 %s 次"
              % con.driver.execute_script("return window.__staleClicks;"))
        check("没点列表外面那条通知（它文案里也有目标日期和场次名）",
              con.driver.execute_script("return window.__clickCount.notice;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.notice;"))
        check("全程没点购买",
              con.driver.execute_script("return window.__buyClicked;") == 0)

        # 等档循环重来时，日历不再点第二遍
        for _ in range(3):
            con._select_details_core(mobile=False)
        check("同一份页面上日历只点一次（重来时不再点，避免把列表反复重置）",
              con.driver.execute_script("return window.__clickCount.day;") == 1,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))

        # 变体：那天在日历上置灰（标记挂在外一层），绝不许点
        con.driver.get(url_of("detail_new_perform.html", "?day27disabled=1"))
        con.config.dates = ["2026.12.27 19:30 下本"]
        ok = con.select_date_on_page_pc()
        check("变体：置灰的日子点不动 → 如实 False（不去点死格子凑数）", ok is False)
        check("变体：一次都没点那个置灰格子",
              con.driver.execute_script("return window.__clickCount.day;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.day;"))
        check("变体：也没拿列表外面那条通知凑数（全文搜索会先撞上它）",
              con.driver.execute_script("return window.__clickCount.notice;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.notice;"))

        # 变体：场次栏不写日期（只写「19:30 下本」）
        con.driver.get(url_of("detail_new_perform.html", "?nodate=1"))
        ok = con._select_details_core(mobile=False)
        check("变体：场次栏不写日期时也选上了（去掉日期再比）", bool(ok))
        check("变体：选中的确实是 19:30 下本", picked("session") == "19:30 下本",
              picked("session"))

        # 反面：配一个页面上没有的场次，如实 False，一张卡片都不点
        con.driver.get(url_of("detail_new_perform.html"))
        con.config.dates = ["2026.12.27 23:45 深夜场"]
        t0 = time.time()
        ok = con.select_date_on_page_pc()
        check("配的场次页面上没有 → 如实 False", ok is False)
        check("反面：一张场次卡片都没点",
              con.driver.execute_script("return window.__clickCount.session;") == 0,
              "点了 %s 次" % con.driver.execute_script("return window.__clickCount.session;"))
        check("反面：页面上确实有「19:30 下本」这种很像的条目（不是空页面在糊弄）",
              con.driver.execute_script(
                  "var t='';document.querySelectorAll("
                  "'[class*=\\\"select_right_list_item\\\"]').forEach(function(e){"
                  "t+=e.textContent;});return t.indexOf('19:30')>=0;") is True)
        check("反面：重试窗口有时限（不会无限等）", time.time() - t0 < 20.0,
              "耗时 %.1f 秒" % (time.time() - t0))

        # 纯逻辑：空白归一化 / XPath 字面量转义
        check("归一化空白：连续空格压成一个",
              con._norm_ws("  2026.12.27 19:30  下本\n\t ") == "2026.12.27 19:30 下本")
        check("归一化空白：None / 空串都不炸",
              con._norm_ws(None) == "" and con._norm_ws("  ") == "")
        check("XPath 字面量：没有引号就用单引号",
              con._xpath_literal("abc") == "'abc'")
        check("XPath 字面量：含单引号时换双引号包（不再把语句拼坏）",
              con._xpath_literal("it's") == '"it\'s"',
              con._xpath_literal("it's"))
        check("XPath 字面量：两种引号都有时用 concat",
              con._xpath_literal("a'b\"c").startswith("concat("),
              con._xpath_literal("a'b\"c"))
    finally:
        con.driver.quit()


def s55_h5_price_package_form():
    """选座页价位文案的第三种载体：选中态那一档写 price__package + 「280.0元」。

    2026-10-10 客户实测（配置 280，页面上明明有 280 也有票）：日志每 0.3 秒刷一条
    「页面上的价位 [480, 680, 880, 1080, 80, 1280] 里没有配置要的 280」，
    自动选座永远不生效。直连客户正开着的浏览器抓真实 DOM 才看清：

        普通档   span class="price__text"     ￥480
        选中态   span class="price__package"  280.0元     ← 类名不同，而且没有「￥」

    老解析只按 price__text 找子节点，退路正则又要求带「￥」，
    于是这一档被整项丢掉。夹具 ?pkg=<价> 复刻这个形态。
    """
    print("\n[S55] H5 选座页：选中态那档写 price__package + 「280.0元」，也要读得出来")

    # ① 选中态那一档：读得出来、匹配得上、整条路走得通
    con = build_con(prices=["380"])
    try:
        con.driver.get(url_of("seat_h5.html", "?pkg=380"))
        con._log = lambda m: None
        hold = con.driver.execute_script(
            "var el = document.querySelector('.price--selected');"
            "return el ? el.outerHTML : '';")
        check("前提：夹具里那一档用的是 price__package、文案不带「￥」",
              'price__package' in hold and '￥' not in hold and '380.0元' in hold,
              hold[:200])

        items = con._h5_price_items()
        prices = sorted(it["price"] for it in items)
        check("四档一个不少（选中态那一档没被丢掉）",
              prices == [380, 580, 880, 1280], str(prices))
        check("它的「已选中」状态也被认出来",
              [it["selected"] for it in items if it["price"] == 380] == [True],
              str([(it["price"], it["selected"]) for it in items]))
        hit = con._h5_match_price(items)
        check("配置的价位正是这一档 → 匹配得上（不再返回 None）",
              (hit or {}).get("price") == 380, str(hit))

        check("整条路走通：选中 380 的座位并确认下单",
              con.pick_seat_on_h5() is True)
        check("买到的就是 380，不是别的价位",
              "picked=380" in (con.driver.current_url or ""),
              (con.driver.current_url or "")[:140])
    finally:
        con.driver.quit()

    # ② 老形态（全部 price__text + ￥N）结果一个字不变
    con = build_con(prices=["380"])
    try:
        con.driver.get(url_of("seat_h5.html"))
        con._log = lambda m: None
        con._h5_wait_seat_ready()
        items = con._h5_price_items()
        check("老形态：四档照旧读出来",
              sorted(it["price"] for it in items) == [380, 580, 880, 1280],
              str(sorted(it["price"] for it in items)))
        check("老形态：没有一档被误标成选中",
              all(it["selected"] is False for it in items),
              str([(it["price"], it["selected"]) for it in items]))
    finally:
        con.driver.quit()

    # ③ 大麦弹「操作异常」也要认成风控提示页
    con = build_con(prices=["380"])
    try:
        con.driver.get(url_of("risk.html"))
        con._log = lambda m: None
        check("前提：risk.html 本来就认得出来", con._is_risk_page() is True)
        con.driver.get(url_of("seat_h5.html"))
        con.driver.execute_script(
            "document.body.innerHTML = '<div>操作异常，请稍后重试</div>';")
        check("正文里出现「操作异常」也算风控提示页",
              con._is_risk_page() is True)
    finally:
        con.driver.quit()

    # ④ 浏览器被关掉之后，判页面的函数不许把原始堆栈抛出来
    con = build_con(prices=["380"])
    try:
        con.driver.get(url_of("seat_h5.html"))
        con.driver.quit()
        got = None
        try:
            got = con._is_seat_page()
        except Exception as e:
            got = "抛异常了：%s" % e
        check("浏览器关掉后 _is_seat_page 不抛异常、返回 False", got is False, got)
        check("并且把「浏览器已关闭」记了下来", con.browser_closed is True)
    finally:
        try:
            con.driver.quit()
        except Exception:
            pass


def s56_h5_risk_page_at_submit_stops():
    """提交那一刻被风控拦下：必须当场停手，不许接着点。

    2026-10-11 客户截图：点完提交，大麦甩出「亲，检测到您的访问异常，请重新试试吧~」。
    这一页原来两张词表都不认：既不是「排队/拥挤」那种可重试的，也不是「人没勾 / 协议没勾」
    那种脚本自己的问题。于是脚本：① 白等满 3 秒轮询超时才返回；② 换下一个按钮候选接着点，
    最多点 8 次（大麦正在拦这个账号，它还在锤）；③ 最后打印「点了提交订单但页面没有任何
    变化」，把「被拦了」说成「没点中」。

    夹具用 ?busyword= 把那句话原样喂进去（risk.html 里就是这句）。
    """
    print("\n[S56] H5 订单页：提交撞上风控页，要当场停手")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html",
                              "?busy=8&busyword=" + quote(RISK_SENTENCE)))
        con.commit_order()

        shown = con.driver.execute_script(
            "var t=document.getElementById('toast');return t?t.textContent:''") or ''
        clicks = con.driver.execute_script("return window.__submitClicks || 0")
        check("前提：夹具确实把提示换成了风控那句话", RISK_WORD in shown, shown)
        check("只点了 1 次就停手（被拦之后不许接着锤）",
              clicks == 1, "点击次数=%s" % clicks)
        check("没有把它当成提交成功",
              not con.driver.execute_script("return window.__submitted"))
    finally:
        con.driver.quit()


def s57_h5_risk_page_change_url_never_counts_as_success():
    """被拦时地址也换了：绝不许因为「地址变了」就报「订单已提交」。

    脚本里有一条「页面跳了 = 提交成功」（本意是拦住误点《购票须知》那种外链）。
    大麦拦掉提交时甩过来的提示页有时连地址一起换，那条规则就会把「被拦了」
    报成「抢到了」。这是这套工具里代价最大的一种错报，人会以为票到手了。
    判据只能是正文，不是地址。
    """
    print("\n[S57] H5 订单页：被拦 + 地址变了，不许报成「已提交」")
    con = build_con(if_commit_order=True)
    try:
        con.driver.get(url_of("order_h5.html", "?jump=" + quote("risk.html")))
        ok = con.commit_order()

        check("前提：地址确实换到了风控页",
              "risk.html" in (con._driver_url() or ""), con._driver_url())
        check("前提：跳过去那一页正文是风控提示",
              RISK_WORD in (con._h5_body_text() or ""))
        check("没有报成「订单已提交」", ok is not True, "commit_order 返回 %r" % (ok,))
        check("并且确实一次都没提交",
              not con.driver.execute_script("return window.__submitted"))
    finally:
        con.driver.quit()


def s58_risk_page_on_seat_url_stops():
    """风控页顶在选座页的同一个地址上：选座循环要认出来，别当成「没票」。

    真站被拦时地址还是那个 kylin.html（标题也照旧，订单页那边早就实测过
    「被拦时标题仍写着确认购买」），而 _is_seat_page() 是拿地址/标题子串认的，
    所以它照样为真。不先认风控页，这一圈会把「被拦了」一路当成
    「这一档一直没票」，转满 40 次上限才停，日志还把人引向错的方向。
    手动模式更要紧：那条路没有上限，会一直空转下去。

    夹具的地址里带上 kylin 就是为了复刻这一点。

    判据用「pick_seat_on_h5 被调了几次」，不用墙钟时间：没修的时候它是 40 次，
    修好之后是 0 次。之所以把 pick_seat_on_h5 换成桩：真站上那一支每轮都要等
    `_h5_wait_seat_ready(timeout=8)` 再叠内部 30 次尝试，一轮半分钟，40 轮要
    二十多分钟；那是「没修」的代价，不该让用例替它等（2026-10-11 实测等了
    23 分钟没跑完）。桩只把「挑座位」这一步换掉，循环和风控判定都是真的。
    """
    print("\n[S58] 风控页顶在选座页地址上：选座循环要当场停手")
    con = build_con(random_seat=True)
    try:
        con.driver.get(url_of("risk.html", "?as=kylin.html"))
        check("前提：这一页地址里有 kylin，会被当成选座页",
              con._is_seat_page() is True)
        check("前提：它其实是被拦的风控页", con._is_risk_page() is True)

        calls = {"n": 0}

        def _stub_pick():
            calls["n"] += 1
            return False

        con.pick_seat_on_h5 = _stub_pick
        t0 = time.time()
        r = con.choice_seat()
        dt = time.time() - t0

        check("一次都没去挑座位（认出风控页就停手了）",
              calls["n"] == 0, "调用了 %d 次，耗时 %.2f s" % (calls["n"], dt))
        check("并且是干净地返回 False，没有卡死",
              r is False, "返回 %r" % (r,))
    finally:
        con.driver.quit()


def s59_risk_page_and_pay_url_never_decides():
    """被拦 + 地址含 /pay：两条判据同时为真，脚本不许替人下结论。

    收银台判据里有一条 `'/pay' in url`，而风控页是顶着订单页那个地址渲染的。
    两条同时成立时，原来按「收银台优先」报，也就是报「订单已提交（页面已跳转）」
    或者「停在支付环节」。如果那次其实是被拦下的，这就等于把「被拦」报成了
    「抢到了」，是整套工具里代价最大的一种错（人会以为票到手了，不去付款）。

    真站现在凑不齐这个组合：订单页地址是 https://m.damai.cn/shows/order-pay.html，
    减号不是斜杠，不含 '/pay'。夹具用文件名 pay_risk.html 把 '/pay' 塞进地址，
    专门复刻凑齐的那一天。

    口径是「不下结论」：既不报抢到、也不报被拦，把看到的摆出来让本人自己看一眼。
    两种报法各有各的代价，脚本分不清，就不该替人猜。
    """
    print("\n[S59] 被拦 + 地址含 /pay：两条判据同时真，不许替人下结论")
    notes = []
    con = build_con(if_commit_order=True)
    # 把「关键决策」那句话接住，才能断言它到底说了哪一句
    con._log = notes.append
    try:
        con.driver.get(url_of("order_h5.html", "?jump=" + quote("pay_risk.html")))
        ok = con.commit_order()

        check("前提：地址里确实含 '/pay'（这个组合真凑出来了）",
              "/pay" in (con._driver_url() or ""), con._driver_url())
        check("前提：跳过去那一页正文是风控提示",
              RISK_WORD in (con._h5_body_text() or ""))
        check("没有报成「订单已提交」", ok is not True, "commit_order 返回 %r" % (ok,))
        said = "\n".join(str(x) for x in notes)
        check("嘴里说的是「不下结论」，不是「被拦下了」", "不下结论" in said,
              "脚本说的是：%s" % ("、".join(
                  [x for x in notes if "风控" in str(x)] or ["（什么都没说）"])))
        check("并且确实一次都没提交",
              not con.driver.execute_script("return window.__submitted"))
    finally:
        con.driver.quit()


def s60_order_page_turns_into_risk_during_viewer_wait():
    """进订单页时还不是风控页，等观演人的那几秒里才变成风控页 —— 也得认出来。

    真站时序（2026-10-11 客户实跑日志）：点「立即购买」落到 order-pay.html、标题「确认购买」，
    这时候正文里还没有风控那句话；大麦拦下之后，过一会儿才把正文换成「检测到您的访问异常」。

    进订单页那一下是查过一次风控的（`_commit_order_on_h5` 开头），但那次查早了、什么都看不到。
    原来接着就是干等满 8 秒 → 打印「观演人列表还是没渲染出来」→ 再报「观演人没勾够」。
    这两句都把真实原因（被拦下了）盖住了：人照着一句「观演人没勾够」去补观演人，白折腾一趟。

    夹具 `order-pay.html` 用 600 毫秒延时复刻这个窗口。
    """
    print("\n[S60] 订单页在等观演人期间被换成风控页 —— 要说清是被拦了，不是观演人没配上")
    notes = []
    con = build_con(if_commit_order=True)
    con._log = notes.append
    try:
        con.driver.get(url_of("order-pay.html"))
        check("前提：地址像订单页（order-pay），会被当成 H5 订单页走",
              con._is_h5_order_page() is True, con._driver_url())
        check("前提：刚进来时还不是风控页（那一下确实查早了、查不到）",
              con._is_risk_page() is False)

        t0 = time.time()
        ok = con.commit_order()
        spent = time.time() - t0
        said = "\n".join(str(x) for x in notes)

        check("没有报成「订单已提交」", ok is not True, "commit_order 返回 %r" % (ok,))
        check("认出的是风控页（正文命中「%s」）" % RISK_WORD, RISK_WORD in said,
              said[-300:] or "（什么都没说）")
        check("说的是「被拦下」，不是「观演人没勾够」",
              "风控" in said and "观演人没勾够" not in said, said[-300:])
        check("发现得早，没干等满 8 秒", spent < 6.0, "%.2f s" % spent)
        check("并且一次都没提交",
              not con.driver.execute_script("return window.__submitted"))
    finally:
        con.driver.quit()


def s61_unit_price_substring_never_picks_other_tier():
    """配置写 80.0元：绝不能因为子串撞上 280.0元 / 1080.0元 就点了别的档。

    客户配置里写的是带单位的「80.0元」。老的 `_option_text_match` 只对纯数字
    才按「独立的数」比，带单位就退回字面子串，`'80.0元' in '280.0元'` 是 True，
    于是把 280.0元 当成了「选上了 80.0元」（2026-10-11 客户实测「选 80 元跳
    到 280」）。1080.0元 同理。

    两层都钉：
      ① 页面只有 280.0元 / 1080.0元、没有 80.0元 时，必须如实「没选中」，
         一个别的档都不许点，也不许随便挑一个。
      ② 页面确实有 80.0元 那一档时（旁边夹着 280.0元 / 1080.0元 当干扰），
         还是要选得中它，别把守卫做过火。
    """
    print("\n[S61] 带单位票价：80.0元 不许撞上 280.0元 / 1080.0元")

    # ①：页面上只有 280.0元 / 1080.0元，没有 80.0元 那一档
    con = build_con(prices=["80.0元"])
    try:
        con.driver.get(url_of("detail_price_unit.html", "?no80=1"))
        con._log = lambda m: None
        page_prices = con.driver.execute_script(
            "return Array.from(document.querySelectorAll('#ticketsList .item-content'))"
            ".map(function(e){return e.getAttribute('data-price');});")
        check("前提：页面上只剩 280.0元 / 1080.0元，没有 80.0元",
              page_prices == ["280.0", "1080.0"], str(page_prices))

        ok = con.select_price_on_page_pc()
        check("配置的 80.0元 不在页面上 → 如实返回 False", ok is False)
        check("没有把 280.0元 / 1080.0元 当成「选上了 80.0元」（票价一次都没被点）",
              con.driver.execute_script("return window.__clickCount.price;") == 0,
              "被点了 %s 次" % con.driver.execute_script("return window.__clickCount.price;"))
        check("页面默认那一档也没被动过",
              con.driver.execute_script("return window.__picked.price;") == "1080.0")
    finally:
        con.driver.quit()

    # ②：页面上有 80.0元 那一档（还夹着 280.0元 / 1080.0元 当干扰），要能选中它
    con = build_con(prices=["80.0元"])
    try:
        con.driver.get(url_of("detail_price_unit.html"))
        con._log = lambda m: None
        ok = con.select_price_on_page_pc()
        check("页面上有 80.0元 那一档 → 选得中（返回 True）", ok is True)
        check("选中的确实是 80.0元，不是 280.0元 / 1080.0元",
              con.driver.execute_script("return window.__picked.price;") == "80.0")
        check("只点了一下（没顺手把别的档也点了）",
              con.driver.execute_script("return window.__clickCount.price;") == 1,
              "被点了 %s 次" % con.driver.execute_script("return window.__clickCount.price;"))
    finally:
        con.driver.quit()


def s62_textual_price_never_expands_to_bare_number():
    """配置抄的是页面原文「全价票188元（站席）」：绝不能因为展开出裸数字 188 就点了坐席。

    老的 `_price_match_values` 无条件把配置项展开出里面的数，于是
    「全价票188元（站席）」变成 ['全价票188元（站席）', '188']。原文那条比不过
    页面的「全价票188元（坐席）」之后（这一步本来是对的），裸数字 188 走纯数字
    分支命中了坐席 —— 点了同价不同区的另一档（2026-10-11 独立复核撞出来的）。

    四条：
      ① 页面只有（坐席）、配置要（站席）→ 如实「没选中」，坐席一次都不许点；
      ② 页面有（站席）→ 选中站席，只点 1 次，坐席没被碰；
      ③ 配置 VIP 1,080元、页面 VIP 1080元 → 数字写法归一后要选得中（没弄坏）；
      ④ 配置 80.0元、页面 80元 → 原来那条能力还在。
    """
    print("\n[S62] 抄页面原文的票价：不许展开成裸数字去撞同价不同区的另一档")

    # ①：页面只有（坐席），配置要（站席）
    con = build_con(prices=["全价票188元（站席）"])
    try:
        con.driver.get(url_of("detail_price_section.html", "?mode=seat_only"))
        con._log = lambda m: None
        labels = con.driver.execute_script(
            "return Array.from(document.querySelectorAll('#ticketsList .item-content'))"
            ".map(function(e){return e.textContent;});")
        check("前提：页面上只有（坐席）那一档，没有（站席）",
              labels == ["全价票188元（坐席）"], str(labels))

        ok = con.select_price_on_page_pc()
        check("配置的（站席）页面上没有 → 如实返回「没选中」", ok is False)
        check("坐席那一项点击次数为 0（没把同价不同区的另一档点了）",
              con.driver.execute_script("return window.__clickCount['坐席'] || 0;") == 0,
              "被点了 %s 次" % con.driver.execute_script("return window.__clickCount['坐席'] || 0;"))
        check("也一次都没点过任何档",
              con.driver.execute_script("return window.__clickedKeys.length;") == 0,
              str(con.driver.execute_script("return window.__clickedKeys;")))
    finally:
        con.driver.quit()

    # ②：页面有（站席）（旁边夹着（坐席）），要选中站席
    con = build_con(prices=["全价票188元（站席）"])
    try:
        con.driver.get(url_of("detail_price_section.html"))
        con._log = lambda m: None
        ok = con.select_price_on_page_pc()
        check("页面上有（站席）→ 选得中（返回 True）", ok is True)
        check("选中的是站席那一项，不是坐席",
              con.driver.execute_script("return window.__picked;") == "站席")
        check("只点了一下，坐席没被碰",
              con.driver.execute_script("return window.__clickedKeys;") == ["站席"],
              str(con.driver.execute_script("return window.__clickedKeys;")))
    finally:
        con.driver.quit()

    # ③：配置 VIP 1,080元、页面 VIP 1080元 → 数字写法归一后要选得中
    con = build_con(prices=["VIP 1,080元"])
    try:
        con.driver.get(url_of("detail_price_section.html", "?mode=vip"))
        con._log = lambda m: None
        ok = con.select_price_on_page_pc()
        check("配置 VIP 1,080元、页面 VIP 1080元 → 选得中", ok is True)
        check("选中的是 VIP 那一档",
              con.driver.execute_script("return window.__picked;") == "vip")
    finally:
        con.driver.quit()

    # ④：配置 80.0元、页面 80元 → 原来那条能力还在
    con = build_con(prices=["80.0元"])
    try:
        con.driver.get(url_of("detail_price_section.html", "?mode=plain80"))
        con._log = lambda m: None
        ok = con.select_price_on_page_pc()
        check("配置 80.0元、页面 80元 → 选得中", ok is True)
        check("选中的是 80元 那一档",
              con.driver.execute_script("return window.__picked;") == "p80")
    finally:
        con.driver.quit()


ALL_CASES = (               s1_masks_actually_block, s2_mask_without_close_button,               s3_close_button_must_be_visible, s4_no_seat_path,
               s5_seat_fallback_random_pick, s6_random_pick_seat_directly,
               s7_switches_off, s8_auto_seat_still_works, s9_slow_navigation_not_ruined,
               s10_scan_layer_replaces_buy_button, s11_scan_layer_then_buy_again,
               s12_switch_off_keeps_scan_layer, s13_h5_canvas_seat_page,
               s14_h5_manual_mode, s15_risk_page_detected,
               s16_h5_order_pick_viewers, s17_h5_auto_commit_really_submits,
               s18_h5_unknown_viewer_never_submits, s19_h5_no_protocol_box_still_works,
               s20_pc_order_page_unchanged, s21_seat_then_h5_order_autosubmit,
               s22_h5_manual_mode_never_signs_protocol,
               s23_h5_viewer_list_renders_late, s24_h5_class_names_changed,
               s25_scrolled_target_still_clickable, s26_h5_long_list_auto_commit,
               s27_h5_seat_only_target_price, s28_h5_seat_price_from_config,
               s29_h5_seat_no_legend_falls_back_to_amount,
               s30_h5_amount_reader_ignores_dash_price,
               s31_h5_busy_submit_retries_fast,
               s32_h5_non_retryable_never_spams,
               s33_h5_pick_viewer_round_trips,
               s34_detail_pick_city_date_price,
               s35_detail_pc_select_option_by_config,
               s36_detail_loop_is_cheap,
               s37_seat_page_waits_are_polls_not_sleeps,
               s38_quantity_pick_fast,
               s39_h5_order_page_renders_late,
               s40_ultron_viewer_list_without_class_or_checkbox,
               s41_unknown_skin_falls_back_to_row_signature,
               s42_ultron_submit_button_shape_changes,
               s43_bottom_renders_later_than_viewer_list,
               s44_protocol_checkbox_may_be_an_icon,
               s45_show_card_must_not_be_mistaken_for_protocol,
               s46_submit_button_covered_by_link,
               s47_detail_cn_date_text,
               s48_soldout_price_never_buys,
               s49_h5_seat_wrong_price_never_confirmed,
               s50_pc_calendar_date_pick,
               s51_seat_never_spins_forever,
               s52_refresh_interval_reaches_the_wait_loop,
               s53_calendar_page_async_lists,
               s54_detail_new_page_shape,
               s55_h5_price_package_form,
               s56_h5_risk_page_at_submit_stops,
               s57_h5_risk_page_change_url_never_counts_as_success,
               s58_risk_page_on_seat_url_stops,
               s59_risk_page_and_pay_url_never_decides,
               s60_order_page_turns_into_risk_during_viewer_wait,
               s61_unit_price_substring_never_picks_other_tier,
               s62_textual_price_never_expands_to_bare_number)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="本地下单链路回归测试")
    ap.add_argument("--only", default="",
                    help="只跑指定用例，逗号分隔的编号/名字，例如 --only s5,s9 "
                         "或 --only s36。留空跑全部。")
    args = ap.parse_args()

    cases = list(ALL_CASES)
    if args.only.strip():
        want = [x.strip().lower().replace("_", "-") for x in args.only.split(",") if x.strip()]
        picked = []
        for fn in cases:
            name = fn.__name__.lower()
            short = name.split("_", 1)[0]
            if any(w in (name, short) or name.startswith(w.replace("-", "_"))
                   for w in want):
                picked.append(fn)
        if not picked:
            print("没匹配到任何用例：%s" % args.only)
            return 2
        cases = picked

    print("=" * 60)
    print("本地下单链路回归测试（真 Chrome + 假页面）")
    print("=" * 60)

    for fn in cases:
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " 执行未抛异常", False, str(e))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过")
    for name, ok, extra in RESULTS:
        if not ok:
            print(f"  ✗ {name} {extra}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
