# -*- coding: UTF-8 -*-
"""登录/启动耗时探针（夹具驱动，不联网、不写真实日志）。

量的是「打开浏览器到能开始抢票」这一段和「自动填账号密码」这一段。
这两段原来被四五个固定 sleep 撑着，探针的价值就是把它们各自的真实开销摊开：

    _switch_to_password_tab       点完页签固定睡 1.5 秒
    auto_fill_login               点完登录固定睡 2.0 秒
    _probe_login_required_page    跳完页固定睡 2.5 秒
    check_login_state 兜底        固定睡 3.0 秒

用法（在 web/ 目录下）：
    python tools/probe_login_speed.py

用户真实数据由 `_probe_env.probe_env()` 挡在外面（重定向 + 收尾逐字节自查）。
  这一项目里「跑探针把真实 accounts.log 写脏」已经发生过 6 次，
  所以这一步不是可选项，而且它被做成了一行 import，就是为了不会再漏。

要做「改前 / 改后」对照时，不要另建一个一次性脚本：
  另建就一定会漏掉上面那行重定向（第 6 次就是这么来的）。
  正确的做法是在本文件里加一个开关（比如把「给旧实现补回 sleep」的那几十行
  挂在 `--compare-old` 下面），复用同一个环境。
"""
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）
FIX = os.path.join(HERE, "fixtures", "accounts")

# 必须在 import accounts / account_runner 之前：重定向要赶在任何一次 say() 前生效。
from _probe_env import probe_env                                   # noqa: E402

guard = probe_env()

import accounts as acc_store                                       # noqa: E402
from selenium import webdriver                                    # noqa: E402
from check_environment import get_chromedriver_path               # noqa: E402
import account_runner as ar                                        # noqa: E402

# 两个夹具各管一层（真站两层叠在一起，本地拆成两个文件）：login_iframe.html + login_inner.html
#   测跨域 iframe 里切 frame 的开销（主文档没有任何 input）；login.html 测主文档里切页签 +
#   填表 + 确认结果（默认停在「扫码登录」，密码框 display:none，要点页签才露，提交后弹滑块）。
def _page(name):
    return "file:///" + os.path.join(FIX, name).replace("\\", "/")


IFRAME_PAGE = _page("login_iframe.html")
LOGIN_PAGE = _page("login.html")

ACC = {"account_id": "probe_login_speed"}
CRED = {"phone": "13800000000", "password": "Probe-Password-123"}

# 本地 HTTP 服务：复刻「未登录被 302 踢回登录页」

REDIRECT_DELAY = [0.0]      # 秒；模拟服务端慢一点才跳


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        # 未登录：访问 myinfo 被 302 踢到登录页；跳转目标里必须留着 "accountinfo/myinfo"
        #   和 "/login?"，否则测的就不是真站那两条判据了。
        if path == "/accountinfo/myinfo_out":
            if REDIRECT_DELAY[0]:
                time.sleep(REDIRECT_DELAY[0])
            self.send_response(302)
            self.send_header("Location", "/login?ru=%2Faccountinfo%2Fmyinfo")
            self.end_headers()
            return
        # 已登录：正常停在 accountinfo/myinfo 上
        if path == "/accountinfo/myinfo":
            self._html("<title>账号信息</title><div class='name-user'>测试昵称</div>")
            return
        if path == "/login":
            self._html("<title>登录 - 大麦网</title>"
                       "<div class='login-user show'><span>登录</span></div>")
            return
        self._html("<title>大麦网-全球演出赛事官方购票平台</title>"
                   "<div class='name-user'>测试昵称</div>")

    def _html(self, body):
        raw = ("<!doctype html><html><head><meta charset='utf-8'></head>"
               "<body>" + body + "</body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def build_driver():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-data-dir=" + tempfile.mkdtemp(prefix="dmp_probe_"))
    return webdriver.Chrome(service=webdriver.ChromeService(get_chromedriver_path()),
                            options=opts)


def t(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return time.perf_counter() - t0, r


ROWS = []


def row(name, sec, extra=""):
    ROWS.append((name, sec, extra))
    print("  %-36s %7.3f s  %s" % (name, sec, extra))


# 一、自动填表：逐步拆解

def probe_enter_frame(d):
    print("\n=== 一、找并切入表单所在上下文 ===")
    # 表单在跨域 iframe 里：主文档一个 input 都没有，必须切进去
    d.get(IFRAME_PAGE)
    dt, (ok, where) = t(ar.enter_login_frame, d)
    row("enter_login_frame（表单在 iframe 里）", dt, "ok=%s where=%s" % (ok, where))
    assert ok and where == "iframe", "没切进登录 iframe（ok=%s where=%s）" % (ok, where)

    # 表单就在主文档里：这一步应该立刻命中，不该白等
    # （上一步把上下文留在 iframe 里了，导航前先回主文档，别带着 frame 上下文跑）
    try:
        d.switch_to.default_content()
    except Exception:
        pass
    d.get(LOGIN_PAGE)
    dt, (ok, where) = t(ar.enter_login_frame, d)
    row("enter_login_frame（表单在主文档）", dt, "ok=%s where=%s" % (ok, where))
    assert ok and where == "主文档", "主文档里就该找到表单（ok=%s where=%s）" % (ok, where)


def probe_autofill(d):
    print("\n=== 二、自动填表：逐步拆解（夹具：默认停在扫码页）===")
    d.get(LOGIN_PAGE)

    # 夹具前提自查：默认必须看不到密码框，否则这条探针测不到「点页签」那一步
    assert ar._visible_field(d, ar.LOGIN_PWD_SELECTORS) is None, \
        "夹具错了：密码框一开始就可见，就测不出切页签这一步"

    dt, hints = t(ar.detect_manual_check, d)
    row("detect_manual_check", dt, "hints=%s" % hints)
    assert hints == [], "还没提交就报验证，判据有问题"

    dt, tab = t(ar._switch_to_password_tab, d)
    row("_switch_to_password_tab", dt, "tab=%s" % tab)
    assert tab == "密码登录", "没点中「密码登录」页签"
    assert ar._visible_field(d, ar.LOGIN_PWD_SELECTORS) is not None, \
        "点了页签密码框仍不可见"

    dt, sel = t(ar._fill_field, d, ar.LOGIN_ID_SELECTORS, CRED["phone"])
    row("_fill_field（账号）", dt, "sel=%s" % sel)
    assert sel, "账号没填进去"

    dt, sel2 = t(ar._fill_field, d, ar.LOGIN_PWD_SELECTORS, CRED["password"])
    row("_fill_field（密码）", dt, "sel=%s" % sel2)
    assert sel2, "密码没填进去"

    dt, btn = t(ar._find_submit_button, d)
    row("_find_submit_button", dt, "found=%s" % (btn is not None))
    assert btn is not None


def probe_autofill_e2e(d, repeat=3):
    print("\n=== 三、auto_fill_login 端到端（含提交后确认结果）===")
    times = []
    for i in range(repeat):
        d.get(LOGIN_PAGE)
        try:
            d.switch_to.default_content()
        except Exception:
            pass
        dt, ok = t(ar.auto_fill_login, d, ACC, CRED)
        times.append(dt)
        row("auto_fill_login #%d" % (i + 1), dt, "returned=%s" % ok)
        assert ok is True, "auto_fill_login 应返回 True"
        # 真值校验：填进去的内容必须是保存的那个，不能只是「没报错」
        filled = d.execute_script("return window.__filled") or {}
        assert filled.get("id") == CRED["phone"], "手机号没填对：%r" % filled.get("id")
        assert filled.get("pwd") == CRED["password"], "密码没填对"
        assert d.execute_script("return window.__submitted") is True, "没点提交"
    row("auto_fill_login 平均", sum(times) / len(times))


# 三、登录态判定

def probe_login_state(d, base):
    print("\n=== 四、登录态判定（本地 302 复刻「被踢回登录页」）===")
    ar.INDEX_URL = base + "/index"
    ar.LOGIN_URL = base + "/login"

    ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo_out"
    dt, v = t(ar._probe_login_required_page, d)
    row("_probe_login_required_page（未登录）", dt, "verdict=%s" % v)
    assert v is False, "被 302 踢回登录页，应判定为未登录"

    dt, v = t(ar.check_login_state, d)
    row("check_login_state（未登录）", dt, "verdict=%s" % v)
    assert v is False, "check_login_state 也应判定为未登录"

    ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo"
    dt, v = t(ar._probe_login_required_page, d)
    row("_probe_login_required_page（已登录）", dt, "verdict=%s" % v)
    assert v is True, "停在 myinfo 应判定为已登录"

    dt, v = t(ar.check_login_state, d)
    row("check_login_state（已登录）", dt, "verdict=%s" % v)
    assert v is True, "check_login_state 也应判定为已登录"

    # 服务端慢 0.6 秒才跳，轮询必须照样判对，而且只花它该花的 0.6 秒
    REDIRECT_DELAY[0] = 0.6
    ar.LOGIN_REQUIRED_URL = base + "/accountinfo/myinfo_out"
    dt, v = t(ar._probe_login_required_page, d)
    row("_probe（慢跳转 0.6s）", dt, "verdict=%s" % v)
    assert v is False, "慢跳转也必须判成未登录"
    REDIRECT_DELAY[0] = 0.0


def main():
    srv, base = start_server()
    d = build_driver()
    try:
        probe_enter_frame(d)
        probe_autofill(d)
        probe_autofill_e2e(d)
        probe_login_state(d, base)

        print("\n=== 汇总 ===")
        print("  共 %d 项，合计 %.3f s" % (len(ROWS), sum(s for _n, s, _e in ROWS)))
        print("  最贵的前 5 项：")
        for n, s, _e in sorted(ROWS, key=lambda r: -r[1])[:5]:
            print("    %-36s %7.3f s" % (n, s))
    finally:
        try:
            d.quit()
        except Exception:
            pass
        srv.shutdown()
    return guard.verify()


if __name__ == "__main__":
    sys.exit(main())
