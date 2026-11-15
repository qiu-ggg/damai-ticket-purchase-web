# -*- coding: utf-8 -*-
"""多账号登录状态管理，调度器

用法（在项目根目录执行）：

    python account_runner.py status                # 检查每个账号的登录状态
    python account_runner.py login                 # 首次逐个账号手动登录
    python account_runner.py login "Account 01"    # 只登录指定账号
    python account_runner.py login --auto          # 用本机保存的密码自动填表
    python account_runner.py run                   # 按配置抢票（只跑启用的）
    python account_runner.py run "Account 01"      # 只跑指定账号
    python account_runner.py enable  "Account 01"
    python account_runner.py disable "Account 02"
    python account_runner.py forget "Account 01"   # 删除该账号的登录状态

设计边界（重要）：
  * accounts.json 里没有任何密码字段。密码只存在 credentials.json（见 cred_store.py）。
  * --auto 只是"替你填表 + 点一下登录"。出现验证码 / 滑块 / 短信验证时立刻停手，
    不代替用户操作，更不会去绕过大麦的验证码、风控、设备指纹或请求签名。
  * 每个账号使用独立的 Chrome Profile 目录，账号之间 Cookie / localStorage 完全隔离。
  * 绝不自动付款。 订单提交成功后就停手，把浏览器留在支付环节，提醒用户自己付。
  * 抢票逻辑完全复用原仓库的 Concert，本文件只做「账号 → 独立浏览器 → 登录态 → 复用它」。
"""

import argparse
import os
import subprocess
import sys
import time
import traceback

# 路径口径见 accounts.py：源码形态两个目录是同一个（跟改造前一字不差），
# exe 形态则分开，数据落在 exe 同级，代码/资源留在解压目录里。
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
    PKG_DIR = app_paths.resource_dir()
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PKG_DIR = BASE_DIR

# 这个 `damai/` 是「包目录」，属于随包分发的资源，所以用 PKG_DIR 而不是 BASE_DIR。
DAMAI_DIR = os.path.join(PKG_DIR, "damai")

# 复用原仓库的模块（它们的 import 是相对 damai/ 目录的）
sys.path.insert(0, DAMAI_DIR)
sys.path.insert(0, PKG_DIR)

import accounts as acc_store                                    # noqa: E402
import cred_store                                               # noqa: E402
import run_settings                                             # noqa: E402
import chrome_flags                                             # noqa: E402
from chromedriver_locator import ensure_chromedriver_env, relax_local_proxy   # noqa: E402
from check_environment import get_chromedriver_path             # noqa: E402
from config import Config                                       # noqa: E402
from concert import Concert                                     # noqa: E402

from selenium import webdriver                                  # noqa: E402
from selenium.webdriver.chrome.service import Service           # noqa: E402
from selenium.webdriver.common.by import By                     # noqa: E402
from selenium.webdriver.common.keys import Keys                  # noqa: E402

# 短信通知（旁路）。纯标准库，import 它不联网、不读配置，本身永远成功。
# 用 try/except 兜一下：通知是我们自己加的附属功能，
#   万一某个模块没随包打进去，也不该让抢票进程起不来。
try:
    import notify as notifier
except Exception:                                               # pragma: no cover
    notifier = None

# 实时状态出口（2026-10-09）：把「现在跑到哪一步」写成 logs/live/<账号>.json，
# 面板/画面墙不管任务是从哪条路起的，都从这里读。同样容错，它坏了大不了
# 面板看不到细状态，绝不该影响抢票。
try:
    import live_status
except Exception:                                               # pragma: no cover
    live_status = None

# 事件名常量在这儿再包一层，调用点就不用记名字符串
EV_GRABBED = "grabbed"
EV_FAILED = "failed"
EV_NEED_LOGIN = "need_login"
EV_STARTED = "started"


def _notify(event, account=None, wait=False, **fields):
    """发一条通知。旁路：出任何问题都只写日志，绝不影响抢票。

    `wait=True` 用在「进程马上要退」的位置（抢到票之后、出错退出前），
    后台线程会随进程一起消失，必须当场发完。
    """
    if notifier is None:
        return None
    try:
        return notifier.notify(event, account=account, wait=wait, **fields)
    except Exception:
        try:
            traceback.print_exc()
        except Exception:
            pass
        return None


INDEX_URL = "https://www.damai.cn/"
LOGIN_URL = "https://passport.damai.cn/login?ru=https%3A%2F%2Fwww.damai.cn%2F"

# 登录态判断用的 Cookie 名（大麦/淘宝系登录后会带上的票据类 Cookie）。
# 只用来"读"，不做任何写入或伪造。
LOGIN_COOKIE_HINTS = ('unb', '_nk_', 'cookie2', 'sgcookie', '_l_g_', 'munb', 'csg')

# 出现这些字样说明页面在要求人工验证，程序只提示，绝不代操作
MANUAL_CHECK_HINTS = ('验证码', '滑块', '请完成验证', '安全验证', '短信验证', '拖动滑块', '点击完成验证')

# 登录页上出现这些字样 = 账号/密码不对（2026-10-09 加）。
# 只用来如实报状态，不重试、不改写任何东西。词都取整句，
# 避免「密码」两个字被别处的提示撞上。
LOGIN_ERROR_HINTS = ('账号或密码错误', '账号名或密码不正确', '密码错误', '密码不正确',
                     '账号不存在', '该账号不存在', '用户名或密码错误')

LOGIN_ENTRY_XPATH = "//a[normalize-space(text())='登录']"

# 大麦首页的登录标识（2026-10 实测）：未登录时 <div class="login-user show"> 里有个
# <span>登录</span>，同级 <div class="span-box-header name-user"> 空且不可见；已登录时 name-user
# 填上昵称且可见，login-user 不再带 show。未登录时那个「登录」是 <span> 不是 <a>，早先按 //a 找所以找不到。
SIGNED_OUT_XPATHS = (
    "//div[contains(@class,'login-user')]",
    "//span[contains(@class,'span-user')]",
    LOGIN_ENTRY_XPATH,
)
SIGNED_IN_XPATHS = (
    "//div[contains(@class,'name-user')]",
    "//*[contains(@class,'nickname')]",
    "//*[contains(@class,'userName')]",
)

# 这个页面必须登录才能看。未登录访问会被重定向到 passport.damai.cn/login，
# 这是判断登录态最可靠的方式（比猜 Cookie 名和 DOM 结构都稳）。
LOGIN_REQUIRED_URL = "https://passport.damai.cn/accountinfo/myinfo"



def beep(times=3):
    """提醒用户注意。Windows 用蜂鸣器，其它平台退回终端响铃。"""
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(880, 180)
            time.sleep(0.12)
    except Exception:
        print("\a", end="", flush=True)


def rule(char="=", n=52):
    print(char * n, flush=True)


def _cookie_names(driver):
    try:
        return {c.get("name"): c.get("value") for c in driver.get_cookies()}
    except Exception:
        return {}


def _find(driver, xpath):
    try:
        return driver.find_elements(By.XPATH, xpath)
    except Exception:
        return []


# 在页面里搜人工验证字样。写成 JS 是为了让「找关键词」发生在页面那一侧。
_MANUAL_CHECK_JS = (
    "const t = (document.body && document.body.innerText) || '';"
    "const out = [];"
    "for (let i = 0; i < arguments[0].length; i++) {"
    "  if (t.indexOf(arguments[0][i]) >= 0) { out.push(arguments[0][i]); }"
    "}"
    "return out;"
)


def detect_manual_check(driver):
    """检测当前页面是否出现了人工验证。只读文本，不做任何操作。

    用一次 JS 在页面里搜，不要 `driver.find_element(TAG_NAME,'body').text`。
      后者会把整页可见文本序列化回 Python（大麦首页那种 DOM 动辄几十万字符），
      然后我们只在里面找这么几个词，传回来的是整页，用掉的只有几个词。
      JS 侧找完只把命中的词回传，传输量从「整页」降到「几个词」。

    用 innerText 不用 textContent：innerText 取「可见文本」，和 Selenium 的 .text
      语义一致；textContent 会把 display:none 里的隐藏文案也算进来，会误报。
      这一条很重要，登录页的滑块提示经常藏在被隐藏的容器里，
      用 textContent 会出现「明明没验证，却报要求人工验证」。

    这个函数现在会被轮询反复调用（见 _wait_login_outcome），所以它得便宜。
    """
    try:
        hits = driver.execute_script(_MANUAL_CHECK_JS, list(MANUAL_CHECK_HINTS))
    except Exception:
        return []
    return [h for h in (hits or []) if h in MANUAL_CHECK_HINTS]


def _dedupe_sub(hits):
    """一个命中词是另一个的真子串时，只留长的。

    例：页面写着「账号或密码错误」，会同时命中「账号或密码错误」和「密码错误」，
    同一处提示给用户报两遍没有意义，留最完整的那个。
    """
    out = []
    for h in hits:
        if any(h != o and h in o for o in hits):
            continue
        out.append(h)
    return out


def detect_login_error(driver):
    """页面上有没有「账号 / 密码不对」的提示。只读文本，不做任何操作。

    2026-10-09 加：任务面板要求把「登录输错」如实显示出来。
    机制和 detect_manual_check 一模一样（同一个 JS，换一份词表），
    词表见 LOGIN_ERROR_HINTS，全部是整句，不会跟别的提示撞车。
    """
    try:
        hits = driver.execute_script(_MANUAL_CHECK_JS, list(LOGIN_ERROR_HINTS))
    except Exception:
        return []
    return _dedupe_sub([h for h in (hits or []) if h in LOGIN_ERROR_HINTS])


def _looks_signed_in(driver):
    """DOM 上有没有"已登录"的迹象。"""
    for xp in SIGNED_IN_XPATHS:
        for el in _find(driver, xp):
            try:
                if (el.text or "").strip() and el.is_displayed():
                    return True
            except Exception:
                continue
    return False


def _looks_signed_out(driver):
    """DOM 上有没有"未登录"的迹象（页面上还挂着可见的登录入口）。"""
    for xp in SIGNED_OUT_XPATHS:
        for el in _find(driver, xp):
            try:
                if "登录" in (el.text or "") and el.is_displayed():
                    return True
            except Exception:
                continue
    return False



def quick_login_probe(driver):
    """不跳转页面判断登录态。返回 True / False / None。

    用于「浏览器正停在某个页面上等着」的场景。
    """
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        return None

    # 还停在登录页上，说明没登完
    if "passport.damai.cn/login" in url or url.rstrip("/").endswith("/login"):
        return False

    if _looks_signed_in(driver):
        return True
    if _looks_signed_out(driver):
        return False

    cookies = _cookie_names(driver)
    if any(cookies.get(n) for n in LOGIN_COOKIE_HINTS):
        return True
    return None


def _probe_login_required_page(driver):
    """访问"个人信息"页：被踢回登录页 = 未登录；正常打开 = 已登录；其它 = None。

    只做导航和读地址栏，不点击、不填表。

    地址栏是轮询等出来的，不是睡 2.5 秒睡出来的。
      driver.get() 在「页面加载完」时就返回了，而服务端 302 是加载过程的一部分，
      也就是说 get() 回来的时候，跳没跳已经定下来了（实测这一跳 0.75 秒完成），
      却还要再干等 2.5 秒才去看地址。
      轮询两头都对：已经定下来就立刻走；遇到慢跳转或者前端 JS 跳转就继续等到上界。
      这个函数在 run / status 里每个账号都要跑一次，所以它省的是乘以账号数的钱。

    2.5 秒这个上界保留不变，换成轮询是为了不再白等，不是为了少等，
      真慢的时候该等满还是等满。
    """
    try:
        driver.get(LOGIN_REQUIRED_URL)
    except Exception:
        return None

    def verdict():
        try:
            url = (driver.current_url or "").lower()
        except Exception:
            return None
        if not url:
            return None
        if "passport.damai.cn/login" in url or "/login?" in url or url.rstrip("/").endswith("/login"):
            return False
        if "accountinfo/myinfo" in url:
            return True
        return None

    return _wait_until_decided(verdict, timeout=2.5, interval=0.1)


def check_login_state(driver, retries=2):
    """判断登录态。返回 True(有效) / False(失效) / None(判断不了)。

    判断顺序（可靠性从高到低）：
      1. 访问需要登录的页面，看会不会被踢回登录页；
      2. 首页上的昵称 / 登录入口；
      3. 登录态 Cookie。
    拿不准就返回 None，交给调用方让用户人工确认。
    """
    for _ in range(max(1, retries)):
        verdict = _probe_login_required_page(driver)
        if verdict is not None:
            return verdict

    # 兜底：回首页看 DOM / Cookie
    try:
        driver.get(INDEX_URL)
    except Exception:
        return None

    # 同样是轮询：首页一渲染出昵称（或登录入口）就定了，不必死等 3 秒。
    #   两个信号都是三态来源，所以用 _wait_until_decided，
    #   「明确判成未登录」也是结果，不能当「还没好」继续等。
    def dom_verdict():
        if _looks_signed_in(driver):
            return True
        if _looks_signed_out(driver):
            return False
        return None

    verdict = _wait_until_decided(dom_verdict, timeout=3.0, interval=0.2)
    if verdict is not None:
        return verdict

    cookies = _cookie_names(driver)
    if any(cookies.get(n) for n in LOGIN_COOKIE_HINTS):
        return True
    return None


# 自动填表登录。用本机保存的凭据（cred_store）把登录表单填上、点一下登录。
# 只填表和点击，不做任何绕过：不碰验证码 / 滑块 / 短信验证，出现就如实提示并停手；不改设备指纹、
# 不改请求签名、不走代理；页面要求人工验证时连填表都不做。选择器覆盖阿里 passport 常见结构，找不到表单就安静退回手动登录。

PASSWORD_TAB_TEXTS = ("密码登录", "账号密码登录", "密码/短信登录", "密码 / 短信登录")

LOGIN_ID_SELECTORS = (
    (By.ID, "fm-login-id"),
    (By.CSS_SELECTOR, "input[name='fm-login-id']"),
    (By.CSS_SELECTOR, "input[name='loginId']"),
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.CSS_SELECTOR, ".login-blocks input[type='text']"),
    (By.CSS_SELECTOR, "input[placeholder*='账号']"),
    (By.CSS_SELECTOR, "input[placeholder*='手机']"),
    (By.CSS_SELECTOR, "input[placeholder*='邮箱']"),
)

LOGIN_PWD_SELECTORS = (
    (By.ID, "fm-login-password"),
    (By.CSS_SELECTOR, "input[name='fm-login-password']"),
    (By.CSS_SELECTOR, "input[name='password']"),
    (By.CSS_SELECTOR, "input[type='password']"),
)

LOGIN_SUBMIT_SELECTORS = (
    (By.CSS_SELECTOR, ".fm-button.fm-submit"),
    (By.CSS_SELECTOR, "#login-form .password-login"),
    (By.CSS_SELECTOR, "#login-form button[type='submit']"),
    (By.CSS_SELECTOR, ".login-submit"),
    (By.CSS_SELECTOR, "button[type='submit']"),
)


def _find_all(driver, how, sel):
    try:
        return driver.find_elements(how, sel)
    except Exception:
        return []


# 等待一律用轮询，不用固定 sleep。原来到处是「固定睡一段再假设已经好了」：点完页签睡 1.5 秒、点完
# 登录睡 2.0 秒、跳完页睡 2.5 秒、兜底再睡 3.0 秒（本地夹具实测加起来 9 秒），而它们等的（密码框可见
# / 地址变了）大多 100~300 毫秒就好，九成以上白等。固定 sleep 两头都不对：网快时浪费、网慢时不够；轮询两头都对。


def _wait_until(pred, timeout=2.0, interval=0.1):
    """等 pred() 变成真值。命中立刻返回；超时返回最后一次的结果。

    pred 必须便宜（一次 DOM 查询或一次读地址栏），因为它是按 interval 反复调的。
    """
    deadline = time.time() + max(0.0, timeout)
    result = pred()
    while not result and time.time() < deadline:
        time.sleep(interval)
        result = pred()
    return result


def _wait_until_decided(pred, timeout=2.5, interval=0.1):
    """等一个三态判定出结果：None = 还没定，True/False = 定了。

    和 _wait_until 的区别：这里的「假」也是结果（比如明确判成未登录），
    不能拿它当「还没好」继续等下去。所以用 None 单独表示「还没定」。
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        verdict = pred()
        if verdict is not None:
            return verdict
        if time.time() >= deadline:
            return None
        time.sleep(interval)


# 定位：一次问完，而不是一条一条问

def _combined_css(selectors):
    """把若干 (By.ID, x) / (By.CSS_SELECTOR, y) 合成一条 CSS 选择器。

    目的只有一个：省往返。原来「这条选择器存不存在」要一条一条问，
    LOGIN_ID_SELECTORS 5 条就是 5 次往返，每次都是一个到 ChromeDriver 的 HTTP 请求。
    合成一条后一次问完。本机往返约 1~4 毫秒，页面越重越贵（主线程忙时更明显）。

    组里出现非 CSS 的定位方式（By.XPATH / By.NAME 之类）时返回 None，
    让调用方走老路，宁可慢一点，也不要改坏定位语义。
    """
    parts = []
    for how, sel in selectors:
        if how == By.ID:
            parts.append("#" + sel)
        elif how == By.CSS_SELECTOR:
            parts.append(sel)
        else:
            return None
    return ", ".join(parts) if parts else None


def _find_any(driver, selectors):
    """一次往返取回这组选择器命中的全部元素；组里有非 CSS 定位时退回逐条问。

    注意顺序语义：合并查询的结果是文档顺序，而原来逐条问得到的是
      选择器顺序（即写代码的人排的优先级）。两者通常一致（登录表单上就一个框），
      但为了不把优先级弄丢，调用方按「先 ID 组、后其它组」分两轮来查。
    """
    css = _combined_css(selectors)
    if css:
        return _find_all(driver, By.CSS_SELECTOR, css)
    out = []
    for how, sel in selectors:
        out.extend(_find_all(driver, how, sel))
    return out


def _visible_field(driver, selectors):
    """这组选择器里第一个「可见且可用」的输入框；没有返回 None。"""
    for el in _find_any(driver, selectors):
        try:
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            continue
    return None


# 登录表单所在的 iframe。2026-10 实测：大麦 PC 登录页主文档里一个 input 都没有，表单全在
# https://ipassport.damai.cn/mini_login.htm 这个跨域 iframe 里（id='fm-login-id' / id='fm-login-password' /
# button.fm-button.fm-submit）。LOGIN_ID_SELECTORS / LOGIN_SUBMIT_SELECTORS 一直是对的，之前失败是没切进这个 iframe。
LOGIN_FRAME_SRC_HINTS = ("mini_login", "passport", "login")


def _login_field_present(driver):
    """当前上下文里有没有账号输入框。命中返回选择器字符串。

    这里故意只看"存不存在"，不要求可见：登录页默认可能停在扫码页，
    密码表单在 DOM 里但 display:none，必须先找到它，才能去点「密码登录」页签。
    要求可见的话会在这种页面上一直找不到，白等到超时（踩过）。
    真正要不要往里写，交给 _fill_field，它自己会挑可见、可用的框。

    5 条选择器合成一条 CSS，一次往返问完（原来 5 次）。
      这个函数在 enter_login_frame 的轮询里会被反复调用，往返次数要乘上轮询次数，
      所以这里省一次等于轮询里省很多次。
    """
    if _find_any(driver, LOGIN_ID_SELECTORS):
        return _combined_css(LOGIN_ID_SELECTORS) or LOGIN_ID_SELECTORS[0][1]
    return None


def enter_login_frame(driver, timeout=25.0, poll=0.25):
    """等登录表单出现，并把驱动切进它所在的那个 frame。

    返回 (ok, 说明)。ok=True 时上下文停在表单所在 frame；
    False 时一定已经切回主文档，不会把调用方留在某个奇怪的 iframe 里。

    poll 从 0.8 秒收到 0.25 秒。原来那一轮空转要 0.8 秒才复查一次，
      而一轮复查本身只要几十毫秒，表单比这更早出现的话就白等了。
      调小 poll 只会让「还没出现」那种情况下的复查更勤（每次几毫秒，无所谓），
      却让「刚出现」那种情况最多早 0.55 秒开始填表。
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if _login_field_present(driver):
            return True, "主文档"

        frames = _find_all(driver, By.CSS_SELECTOR, "iframe, frame")

        def rank(fr):
            # src 像登录页的排前面：命中更快，也不会先停进广告之类的 iframe
            try:
                src = (fr.get_attribute("src") or "").lower()
            except Exception:
                src = ""
            return 0 if any(h in src for h in LOGIN_FRAME_SRC_HINTS) else 1

        for fr in sorted(frames, key=rank):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(fr)
            except Exception:
                continue
            if _login_field_present(driver):
                return True, "iframe"

        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if time.time() >= deadline:
            return False, "没找到登录表单"
        time.sleep(poll)


def _switch_to_password_tab(driver):
    """登录页默认多半是扫码。找到「密码登录」那个页签并点它。

    返回点中的文案，没找到返回 None（没找到也不算错，可能本来就在密码页）。

    原来点完是 `time.sleep(1.5)`，固定睡一秒半，等的是「密码框露出来」这件事。
      本地夹具实测这一步是整条填表流程里最贵的一项（1.578 秒，占了四成），
      而密码框实际点完不到 200 毫秒就可见了。
      改成轮询等「密码框真的可见」：等你真正想要的那个状态，而不是等一个猜出来的时长。
      超时也照样返回（和原来一样），能不能写进去由 _fill_field 再判一次。
    """
    for txt in PASSWORD_TAB_TEXTS:
        xp = ("//*[self::a or self::span or self::div or self::button or self::li]"
              "[normalize-space(text())='{}']".format(txt))
        for el in _find(driver, xp):
            try:
                if not el.is_displayed():
                    continue
                r = el.rect
                # 尺寸太大的一定是整块容器而不是页签，点了会误伤
                if (r.get("width") or 0) > 360 or (r.get("height") or 0) > 90:
                    continue
                el.click()
                _wait_until(
                    lambda: _visible_field(driver, LOGIN_PWD_SELECTORS) is not None,
                    timeout=1.5, interval=0.05)
                return txt
            except Exception:
                continue
    return None


def _try_fill(el, value):
    """对一个输入框尝试：聚焦、清空、输入、回读确认。成功返回 True。

    回读这一步不能省。受控组件（前端框架接管了输入）会把不接受的输入吞掉，
      看起来 send_keys 成功了，其实框里是空的，要到提交才暴露。
      所以判据是「框里真的有值」，不是「我发过键」。
    """
    try:
        if not el.is_displayed() or not el.is_enabled():
            return False
        el.click()                      # 有些框要拿到焦点才允许输入
        try:
            el.clear()
        except Exception:
            el.send_keys(Keys.CONTROL, "a")
        el.send_keys(value)
        return bool((el.get_attribute("value") or "").strip())
    except Exception:
        return False


def _fill_field(driver, selectors, value):
    """往第一个「可见、可用、能写进去」的框里填值。

    返回命中的（合并后的）选择器字符串；一个都没成返回 None。
    只用 send_keys 模拟真人输入，不用 JS 直接改 value，那样不触发前端校验，
    看起来填上了，提交时还是空。

    定位分两轮问，一轮一条合并 CSS：
        第一轮只问 By.ID 那几条（真站上 fm-login-id / fm-login-password 就是它们，
        优先级最高），第二轮再问剩下的。
      拆成两轮而不是直接合成一条，是因为合并查询返回的是文档顺序，
      而原实现返回的是选择器顺序（写代码的人排的优先级）。
      拆两轮把优先级保住，同时把往返从「每条选择器一次」压到「每组一次」。
    """
    id_group = [s for s in selectors if s[0] == By.ID]
    rest_group = [s for s in selectors if s[0] != By.ID]
    for group in (id_group, rest_group):
        if not group:
            continue
        css = _combined_css(group)
        if css is None:
            # 组里有非 CSS 的定位方式，退回逐条问。宁可多花几次往返，
            # 也不要为了省时间把定位语义改坏。
            for how, sel in group:
                for el in _find_all(driver, how, sel):
                    if _try_fill(el, value):
                        return sel
            continue
        for el in _find_all(driver, By.CSS_SELECTOR, css):
            if _try_fill(el, value):
                return css
    return None


def _find_submit_button(driver):
    for how, sel in LOGIN_SUBMIT_SELECTORS:
        for el in _find_all(driver, how, sel):
            try:
                if el.is_displayed() and el.is_enabled():
                    return el
            except Exception:
                continue
    return None


def _wait_login_outcome(driver, before_url, budget=2.0, interval=0.1):
    """点完登录后等「有结果」，返回此刻的人工验证提示（没有就是 []）。

    原来这里是 `time.sleep(2.0)` 固定等 2 秒。它等的是「页面开始变」这件事，
      而页面通常 100~300 毫秒内就变了，那 1.7 秒全是白等。
      而且这 2 秒卡在最不该卡的位置：后面紧接着就是 wait_for_manual_login 的轮询，
      本来就要继续盯着，这 2 秒只是白白推迟了「开始盯结果」的时刻。

    「有结果」判两件事，谁先出现算谁：
        · 页面上冒出人工验证字样 → 立刻停手如实提示（这个要抢着报）；
        · 地址栏离开登录页，或者登录表单已经消失 → 说明提交被受理了。
      判定「变了」之后再给 0.3 秒「落定窗口」复核一次验证字样，
      有的站是先跳转、再在结果页弹滑块，不留这 0.3 秒就会漏报。
      （就算真漏了也不会误事：调用方 wait_for_manual_login 仍然在按秒复查。）
    """
    deadline = time.time() + max(0.0, budget)
    while True:
        hints = detect_manual_check(driver)
        if hints:
            return hints

        try:
            url = driver.current_url or ""
        except Exception:
            url = before_url

        # 地址变了，或者登录表单不见了，两种都说明「提交被受理了」
        if (url and url != before_url) or not _login_field_present(driver):
            settle = min(deadline, time.time() + 0.3)
            while time.time() < settle:
                time.sleep(interval)
                hints = detect_manual_check(driver)
                if hints:
                    return hints
            return []

        if time.time() >= deadline:
            return []
        time.sleep(interval)


def auto_fill_login(driver, account, cred=None):
    """用保存的凭据填表并点登录。返回 True=已经点了登录。

    失败 / 拿不准一律返回 False，交给调用方走「等用户手动登录」那条路，
    绝不因为自动填表出错就把登录流程卡死。
    """
    cred = cred if cred is not None else cred_store.get(account.get("account_id"))
    if not cred:
        acc_store.say("没有保存这个账号的密码，跳过自动填表（请手动登录）", account)
        return False

    # 先等表单加载出来、并切进它所在的 iframe。这步不可省：表单在跨域 iframe 里，主文档连一个 input
    # 都没有，不切进去，下面的选择器再准也命中不了（也顺手解决了「driver.get 完立刻找元素、页面还在加载」的时间差）。
    ok, where = enter_login_frame(driver)
    if not ok:
        acc_store.say("等不到登录表单（{}），请手动登录".format(where), account)
        return False
    if where != "主文档":
        acc_store.say("登录表单在 {} 里，已切进去".format(where), account)

    # 页面已经弹了人工验证，连填表都不做
    hints = detect_manual_check(driver)
    if hints:
        acc_store.say("登录页当前就在要求人工验证（{}），不自动填表".format("、".join(hints)), account)
        return False

    tab = _switch_to_password_tab(driver)
    if tab:
        acc_store.say("已切到「{}」页签".format(tab), account)

    id_sel = _fill_field(driver, LOGIN_ID_SELECTORS, cred["phone"])
    if not id_sel:
        acc_store.say("没认出账号输入框，请手动登录", account)
        return False

    pwd_sel = _fill_field(driver, LOGIN_PWD_SELECTORS, cred["password"])
    if not pwd_sel:
        acc_store.say("账号填上了，但没认出密码框（请手动补密码）", account)
        return False

    # 这句是唯一会提到密码的地方：只说"填了"，绝不打内容
    acc_store.say("已自动填入账号密码（密码不打印、不写进任何配置文件）", account)

    btn = _find_submit_button(driver)
    if btn is None:
        acc_store.say("没找到登录按钮，请手动点一下", account)
        return False

    # 点之前先记下地址，点完靠「地址变了 / 表单没了」判断提交有没有被受理
    try:
        before_url = driver.current_url or ""
    except Exception:
        before_url = ""

    try:
        btn.click()
    except Exception as e:
        acc_store.say("点登录按钮失败（{}），请手动点".format(e), account)
        return False

    hints = _wait_login_outcome(driver, before_url)
    if hints:
        acc_store.say(
            "页面要求人工验证（{}）—— 请你自己在浏览器里完成，程序不会代替你操作".format("、".join(hints)),
            account)
    else:
        acc_store.say("已提交登录请求，等待结果…", account)
    return True


def _try_auto_login(driver, account):
    """自动填表是加分项，失败绝不能影响后面的手动登录。"""
    try:
        return auto_fill_login(driver, account)
    except Exception as e:
        acc_store.say("自动填表出错（不影响手动登录）：{}".format(e), account)
        return False


def wait_for_manual_login(driver, account, poll=0.5, timeout=None):
    """等待用户在浏览器里完成登录。

    出现验证码 / 滑块 / 短信验证时不干预，只周期性地提示并继续等待。

    poll 从 2.0 秒收到 0.5 秒，缩的是「你已经登好了、脚本多久才发现」这段延迟。
      原来是每 2 秒看一眼，最坏要等 2 秒才反应过来；现在是 0.5 秒。

    为什么调快这里不涉及风控：这一轮循环里做的事（读地址栏、find_elements
      查昵称、get_cookies、在页面里搜验证字样）全是本地 ChromeDriver 调用，
      一个网络请求都不发。大麦那边看不到这里跑得多勤。
      真正会发请求、会进风控视野的只有整页刷新那一个动作，而它一直没被调快。
    """
    acc_store.say("请在浏览器中手动完成登录（扫码 / 账号密码 / 短信验证码均可）。", account)
    acc_store.say("若出现验证码、滑块或短信验证，请自行完成 —— 程序不会代替你操作，只在这里等待。", account)

    # 自动填表可能把上下文留在了登录 iframe 里。统一先回主文档，
    # 让下面的登录态判断按原来（已在真站验证过）的方式在整个页面上做。
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    # 实时状态（2026-10-09，用户要求）：等登录这段时间，面板上要能看出
    # 「需要验证码」还是「登录输错」。状态只在变化时上报一次。
    def _live(state, detail=""):
        if live_status is not None:
            live_status.update(state, detail)

    _live("等待登录")                       # 盖掉 begin() 的「启动中」

    t0 = time.time()
    last_hint = 0.0
    last_err = 0.0
    shown = "等待登录"
    while True:
        hints = detect_manual_check(driver)
        errs = detect_login_error(driver)
        if not hints or not errs:
            # 验证码 / 滑块 / 「账号或密码错误」都可能藏在登录 iframe 里，
            # 主文档里看不到，缺哪样就进去补看哪样。
            try:
                if enter_login_frame(driver, timeout=0)[0]:
                    if not hints:
                        hints = detect_manual_check(driver)
                    if not errs:
                        errs = detect_login_error(driver)
                    driver.switch_to.default_content()
            except Exception:
                pass
        now = time.time()
        if hints and now - last_hint > 20:
            acc_store.say("检测到人工验证（{}），等待你在浏览器里完成…".format("、".join(hints)), account)
            last_hint = now
        if errs and now - last_err > 20:
            acc_store.say("登录没有通过（页面提示：{}）—— 请检查账号和密码后重试。".format(
                "、".join(errs)), account)
            last_err = now

        # 状态优先级：人工验证 > 输错 > 单纯等待。变化才报。
        if hints:
            if shown != "需要验证码":
                shown = "需要验证码"
                _live(shown, "页面要求：{}".format("、".join(hints)))
        elif errs:
            if shown != "登录输错":
                shown = "登录输错"
                _live(shown, "页面提示：{}".format("、".join(errs)))
        elif shown != "等待登录":
            shown = "等待登录"
            _live(shown)

        st = quick_login_probe(driver)
        if st is True:
            acc_store.say("登录成功。登录状态已保存在：{}".format(acc_store.profile_path(account)), account)
            return True

        if timeout and now - t0 > timeout:
            acc_store.say("等待登录超时（{} 秒），本次跳过该账号。".format(timeout), account)
            return False

        time.sleep(poll)


def ensure_logged_in(driver, account, assume_yes=False, timeout=None, auto=False):
    """确保当前浏览器处于登录态。有效则直接复用，失效则等用户手动登录。

    auto=True 时，如果本机存过这个账号的凭据，会先自动填表试一次；
    出现验证码 / 滑块 / 短信仍然停下来交给用户。
    """
    acc_store.say("正在检查登录状态…", account)
    st = check_login_state(driver)

    if st is True:
        acc_store.say("登录状态有效", account)
        return True

    if st is None:
        if assume_yes:
            acc_store.say("无法自动判断登录状态（按参数假定为已登录）", account)
            return True
        try:
            ans = input("{} 无法自动判断登录状态。请看浏览器窗口：现在是否已登录？(y/n) ".format(acc_store.tag(account)))
        except EOFError:
            ans = "n"
        if ans.strip().lower().startswith("y"):
            acc_store.say("登录状态有效（人工确认）", account)
            return True

    acc_store.say("登录状态失效，需要重新登录", account)
    try:
        driver.get(LOGIN_URL)
    except Exception as e:
        acc_store.say("打开登录页失败：{}".format(e), account)
        return False
    if auto:
        _try_auto_login(driver, account)
    return wait_for_manual_login(driver, account, timeout=timeout)



def chrome_profile_in_use(profile_dir):
    """这个 Profile 是不是正被一个活着的 Chrome 占着。

    为什么需要它：这套脚本的设计是「跑完把浏览器留给你手动支付」。你支付完
    如果没关那个窗口，它就一直占着这个 Profile 目录；下次再跑时，新起的 Chrome
    发现 Profile 已被占用，会把请求转交给已有的那个实例然后自己退出，
    chromedriver 只看到「进程没了」，于是抛出：

        session not created: Chrome instance exited

    这句话完全看不出真正的原因，用户只会以为程序坏了。

    判据：Chrome 运行时会独占锁住 Profile 里的库文件。实测
    `Default/Network/Cookies` 被锁时，连复制一份都会报 `WinError 32`。
    所以只要能以独占方式打开它，就说明有 Chrome 在跑。
    纯标准库，不需要 psutil。
    """
    if not os.path.isdir(profile_dir):
        return False
    for rel in ("Default/Network/Cookies", "Default/Cookies", "lockfile"):
        p = os.path.join(profile_dir, *rel.split("/"))
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb"):
                pass
        except PermissionError:
            return True          # ← 被别的进程独占，就是它
        except OSError:
            return True
    return False


def _ps_regex_escape(s):
    """.NET 正则的字面量转义（PowerShell 的 -match 用的是 .NET 正则）。

    不能直接用 Python 的 `re.escape`：它会给 '-' ':' '/' 之类也加反斜杠，
    而 .NET 对 `\\-` `\\:` 这类未定义转义会直接报错，而账号目录名里
    出现 '-' 太正常了。所以只转义真正有特殊含义的那几个字符。
    """
    out = []
    for ch in s:
        if ch in "\\^$.*+?()[]{}|":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _own_chrome_processes(profile_dir):
    """列出「用本账号 Profile 起的 chrome 进程」的 PID。

    只认 `--user-data-dir` 指向本账号 Profile 的进程。
      绝不按进程名无差别结束所有 chrome 进程，那会把你手动开的浏览器
      一起杀掉（而且 Chrome 的 gpu/renderer 子进程也会跟着主进程走，
      按名字杀等于把整个桌面上的浏览器全端了）。
      查进程走 PowerShell 的 Win32_Process，不要用 `tasklist /FI`：
      在 Git Bash 下 `/FI` 会被当成路径转换掉，参数直接失效。

    匹配要卡在路径边界上，不能只做子串包含。
      否则处理 `profiles/Account_01` 时会连带认领 `profiles/Account_01_backup`
，那是另一个账号的浏览器，关掉就是真事故。
      Chrome 的命令行有两种写法，都要认：
        · 主进程：          --user-data-dir="D:\\...\\Account_01"        （带引号）
        · crashpad 等子进程：--user-data-dir=D:\\...\\Account_01 /prefetch:4 （不带引号）
      所以路径后面必须跟「空白」或「字符串结束」。
    """
    if os.name != "nt":
        return []
    target = os.path.abspath(profile_dir)
    esc = _ps_regex_escape(target)
    pattern = ('--user-data-dir="' + esc + '"(?:\\s|$)'
               '|--user-data-dir=' + esc + '(?:\\s|$)')
    # 单引号在 PowerShell 单引号串里要写成两个
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match '%s' } | "
        "ForEach-Object { $_.ProcessId }"
    ) % pattern.replace("'", "''")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            # errors="replace"：本机 PYTHONUTF8=1 时 text=True 会拿 UTF-8 解
            # PowerShell 的 GBK 报错文本，读线程会崩、输出变空，那会让
            # 「认领残留浏览器」静默失效（什么都认不到，却不报错）。
            capture_output=True, text=True, errors="replace", timeout=25)
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return out


def close_own_chrome(profile_dir):
    """关掉「用本账号 Profile 起的自动化 Chrome」，返回关掉几个。

    只结束上面认领到的那一组进程；主进程一结束，它的 gpu / renderer 子进程
    也会跟着退。用户自己开的浏览器不在认领范围内，一个都不会碰。
    """
    pids = _own_chrome_processes(profile_dir)
    if not pids:
        return 0
    ps = "; ".join("Stop-Process -Id %d -Force -ErrorAction SilentlyContinue" % p
                   for p in pids)
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, errors="replace", timeout=30)
    except Exception:
        return 0
    return len(pids)


def ensure_profile_free(profile_dir, account=None):
    """确保这个 Profile 没被上一轮遗留的浏览器占着；被占就先关掉它。

    为什么必须做成公用函数：
      这套脚本有两条起浏览器的路：
        · `status` / `login` → 本文件的 build_driver()
        · `run`             → Concert.__init__()（在 damai/concert.py 里）
      上一轮我只在 build_driver 里加了守卫，结果用户实际用的 `run` 那条路没护住，
      他报的错里那句 `✓ 已启用独立浏览器 Profile:` 正是 Concert.__init__ 打出来的，
      而 `build_driver` 打的是「浏览器 Profile：」，两者不是同一句话。
      所以守卫要能同时被两条路调用，且要在启动之前调。

    没被占用时几乎不花钱（只试打开一个文件），所以放在热路径上也没关系。
    """
    if not chrome_profile_in_use(profile_dir):
        return True

    acc_store.say(
        "⚠ 这个账号的浏览器还开着 —— 上一轮跑完把它留给你支付，你没关。", account)
    acc_store.say(
        "  Chrome 不允许在同一个 Profile 上开第二个窗口，所以先把它关掉再启动。", account)
    acc_store.say(
        "  （如果那个窗口里还有没付的订单：订单在大麦「我的订单」里也能继续支付，"
        "不会因为关掉浏览器而消失。）", account)
    n = close_own_chrome(profile_dir)
    if n:
        acc_store.say("  · 已关掉上一轮的浏览器（{} 个进程）".format(n), account)
    else:
        acc_store.say("  · 没找到可关的进程。请手动关掉那个 Chrome 窗口再跑一次。", account)
    # 给它一点时间释放文件锁，然后复核一次
    for _ in range(10):
        time.sleep(0.4)
        if not chrome_profile_in_use(profile_dir):
            return True
    if chrome_profile_in_use(profile_dir):
        raise RuntimeError(
            "账号「{}」的浏览器 Profile 仍被占用：{}\n"
            "  · 请手动关掉用这个 Profile 打开的 Chrome 窗口，然后重新运行。\n"
            "  · 查看占用者：任务管理器里找命令行带 --user-data-dir=...\\profiles\\{} 的 chrome.exe"
            .format((account or {}).get("account_id"), profile_dir,
                    os.path.basename(profile_dir)))
    return True


def build_driver(account):
    """为该账号启动一个使用独立 Profile 的 Chrome。

    只用账号自己的 profile_dir；账号之间不共享任何 Cookie 或缓存。
    """
    profile_dir = acc_store.ensure_profile_dir(account)

    # 先看上一轮留下的浏览器是不是还开着（放在解析驱动之前：被占用时没必要白跑一次驱动查找）。
    # 不处理的话会抛 `session not created: Chrome instance exited`，那报错完全指不到真正的原因（Profile 被占用）。
    ensure_profile_free(profile_dir, account)

    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-data-dir=" + profile_dir)

    # 多开减负参数。跟上面的顺序一样：`--user-data-dir` 自己加，
    # 其它策略性参数交给 chrome_flags 统一决定（设置里关掉就是空列表）。
    chrome_flags.add_to(options, run_settings.chrome_args())

    driver_path = get_chromedriver_path()
    acc_store.say("浏览器 Profile：{}".format(profile_dir), account)
    return webdriver.Chrome(service=Service(driver_path), options=options)


class AccountConcert(Concert):
    """复用原仓库 Concert 的全部抢票逻辑，只替换「登录」这一环。

    原实现认为"还没登录"，会一直等页面标题变化；复用已有登录态时那个循环
    永远不会结束，所以这里把 login() 换成：登录态已经由 Profile 携带，
    直接跳到目标页即可。
    """

    def __init__(self, config, account):
        self.account = account
        super().__init__(config)

        # 关键决策双写：accounts.log + 控制台（有的话）。多账号起的进程没有控制台窗口，输出被重定向进
        # runs/ 下的日志文件；把「做没做 / 为什么没做」同时写进 accounts.log，出问题才有现场可查。
        # 必须在 super().__init__() 之后赋值，那边会把 _log 初始化成 print。
        def _log_to_file(msg):
            acc_store.say(str(msg).strip(), self.account)

        self._log = _log_to_file

    def login(self):
        acc_store.say("复用已有登录状态，进入目标演出", self.account)
        self.driver.get(self.config.target_url)


def build_config(account):
    """把账号配置翻译成原仓库的 Config 对象。"""
    users = [str(u).strip() for u in (account.get("users") or []) if str(u).strip()]
    qty = int(account.get("quantity") or 1)
    # 购票数量由 quantity 决定，观演人按顺序取前 qty 个
    users = users[:qty]

    cfg = Config(
        INDEX_URL,
        LOGIN_URL,
        str(account.get("target_url") or "").strip(),
        users,
        account.get("city") or None,
        [str(d) for d in (account.get("dates") or [])],
        [str(p) for p in (account.get("prices") or [])],
        bool(account.get("if_listen", True)),
        bool(account.get("if_commit_order", False)),
        int(account.get("max_retries") or 1000),
        quantity=qty,
    )

    profile = acc_store.profile_path(account)
    # Concert.__init__ 认这两个属性：Profile 隔离 + Cookie 文件隔离
    cfg.user_data_dir = profile
    cfg.cookie_file = os.path.join(profile, "damai_cookies.pkl")

    # 日历日期（选填）。它和 dates（场次）是两件事，见 config.Config 里的说明。
    # 空 = 这一页没有月历格子，日历那一步整个跳过。
    cfg.calendar_date = str(account.get("calendar_date") or "").strip() or None

    # PC 端下单相关的三个开关（老配置里没有这两项时按默认「开」处理）
    cfg.no_seat = bool(account.get("no_seat", True))
    cfg.random_seat = bool(account.get("random_seat", True))
    cfg.dismiss_mask = bool(account.get("dismiss_mask", True))

    # 「还没开票」时整页刷新一次等多久（秒）。不填就按 fast_mode 走（快速 0.3）。建议往稳里设：整页刷新是整套流程里
    # 唯一会把整个页面重拉一遍的动作，也是风控最在意的（社区实测「开票前反复刷新」是被盾的头号原因，共识区间 1~3 秒）。
    # 刷新快不等于更抢得到，决定成败的是「票放出来后多久能勾完人并提交」（那段提速在 concert.py 的 _h5_* 里）。实际等待还会乘一个 0.8~1.25 的随机抖动，见 Concert._refresh_wait()。
    raw_refresh = account.get("refresh_interval")
    if raw_refresh not in (None, ""):
        try:
            cfg.refresh_interval = float(raw_refresh)
        except (TypeError, ValueError):
            pass
    # 多开减负：Chrome 的启动参数（关掉与抢票无关的后台杂活）。
    # 设置里关掉或没配就是空列表，行为与改造前一致。
    cfg.chrome_args = run_settings.chrome_args()

    return cfg



def notify_payment(con, account):
    """提交订单后停手并提醒用户手动支付。绝不代付。"""
    try:
        title = con.driver.title
        url = con.driver.current_url
    except Exception:
        title, url = "?", "?"

    at_pay = False
    try:
        at_pay = con._is_payment_page()
    except Exception:
        pass

    rule("*")
    if at_pay:
        acc_store.say("***订单已提交，当前停在支付环节***", account)
    else:
        acc_store.say("***订单流程已结束，请到浏览器里确认抢票结果***", account)
    acc_store.say("当前页面：{}".format(title), account)
    acc_store.say("页面地址：{}".format(url), account)
    acc_store.say("请尽快手动完成支付 —— 程序不会代你付款。", account)
    rule("*")

    # 短信通知（旁路）。必须 wait=True：抢到票后马上走到 hold_browsers() 等用户回车，万一用户不敲、
    # 或任务是被面板起的（stdin=DEVNULL，input() 立刻 EOF），进程会很快退出，扔给后台线程的短信会跟着
    # 一起消失，而这是唯一一条「丢了就白抢」的通知。带上演出名和停在哪一页，是为了让收到的短信本身就能判断要不要马上处理。
    acc = account if isinstance(account, dict) else {}
    _notify(EV_GRABBED, account=acc.get("account_id"), wait=True,
            show=title,
            status=("已停在支付环节" if at_pay else "流程已结束，请到浏览器确认"),
            price=(acc.get("prices") or [None])[0],
            city=acc.get("city"))

    beep()


def _hold_until_all_closed(held, poll=1.0):
    """守着浏览器，直到用户把它们全部手动关闭才返回。

    为什么必须「守着」而不是说完一句就退出（2026-10-09 用户要求：
      「跑完后不要自动关闭浏览器，停在最后那个页面，用户手动关闭」）：
      Chrome 是 chromedriver 起来的，我们（客户端）一退出，chromedriver
      跟着退出时会把它管理的浏览器一并收走，「停在最后那一页」就没了。
      守着 = 驱动连接一直活着 = 浏览器一定保持打开。
      用户手动关掉浏览器 → 本进程才收尾退出（面板上的状态随之变「已结束」）。

    检测「关了没有」用 `driver.window_handles`：活着的浏览器至少有窗口
      句柄；关掉最后一个窗口后是空列表，或者驱动连接直接断（抛异常），
      两种都算已关闭。
    """
    while True:
        alive = 0
        for con, _acc in held:
            try:
                if con.driver.window_handles:
                    alive += 1
            except Exception:
                pass                      # 驱动断了 = 那个浏览器已经关了
        if not alive:
            return
        time.sleep(poll)


def hold_browsers(held):
    """把所有浏览器留着，等用户处理完支付再关闭。"""
    if not held:
        return
    print()
    acc_store.say("以下账号的浏览器会保持打开，方便你完成支付：")
    for con, acc in held:
        try:
            where = con.driver.current_url
        except Exception:
            where = "?"
        acc_store.say("浏览器保持打开：{}".format(where), acc)

    print()
    try:
        input("处理完后按回车，关闭所有浏览器并退出…")
    except (EOFError, KeyboardInterrupt):
        # 面板路径走这里（stdin=DEVNULL，input() 立刻 EOF）。2026-10-09 用户要求「跑完后不要自动关闭浏览器、
        # 停在最后那个页面」。老写法说完一句「浏览器保持打开」就 return，进程立刻退出、chromedriver 跟着退、
        # 把浏览器一起收走，用户看到的就是「跑完浏览器没了」。现在改成原地守着。
        acc_store.say("浏览器会保持打开、停在最后那一页；"
                      "你处理完后**手动关掉浏览器窗口**，本任务就会自动收尾退出。"
                      "（不会自动付款，也不会自动关闭浏览器。）")
        _hold_until_all_closed(held)
        for con, acc in held:
            try:
                con.finish()              # 浏览器已经关了；这一步收掉 chromedriver
            except Exception:
                pass
        return

    for con, acc in held:
        try:
            con.finish()
            acc_store.say("浏览器已关闭", acc)
        except Exception:
            pass



def _load_and_pick(args, only_enabled=False):
    data = acc_store.load()
    accounts = acc_store.pick(data, getattr(args, "ids", None), only_enabled=only_enabled)
    return data, accounts


def cmd_status(args):
    data, accounts = _load_and_pick(args)
    if not accounts:
        acc_store.say("accounts.json 里还没有账号，请先编辑它。")
        return 1

    # 只报启用账号的配置问题；禁用的本来就不会跑，不用打扰
    all_ids = [str(a.get("account_id")) for a in acc_store.all_accounts(data)]
    for acc in acc_store.enabled_accounts(data):
        others = [i for i in all_ids if i != str(acc.get("account_id"))]
        errs = acc_store.validate(acc, other_ids=others)
        if errs:
            acc_store.say("配置有问题：{}".format("；".join(errs)), acc)
    print()

    for acc in accounts:
        if not acc.get("enabled", True):
            acc_store.say("已禁用，跳过", acc)
            continue
        if not acc_store.login_state_exists(acc):
            acc_store.say("登录状态失效，需要重新登录（尚未保存过登录状态）", acc)
            continue

        driver = None
        try:
            driver = build_driver(acc)
            st = check_login_state(driver)
            if st is True:
                acc_store.say("登录状态有效", acc)
            elif st is False:
                acc_store.say("登录状态失效，需要重新登录", acc)
            else:
                acc_store.say("无法自动判断登录状态（可运行 login 重新登录确认）", acc)
        except Exception as e:
            acc_store.say("检查失败：{}".format(e), acc)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
    return 0


def cmd_login(args):
    data, accounts = _load_and_pick(args)
    if not accounts:
        acc_store.say("accounts.json 里还没有账号，请先编辑它。")
        return 1

    ok = 0
    for acc in accounts:
        rule("-")
        if not acc.get("enabled", True) and not args.force:
            acc_store.say("已禁用，跳过（要强制登录请加 --force）", acc)
            continue
        errs = acc_store.validate(acc, for_login=True)
        if errs:
            acc_store.say("账号信息不完整，跳过：{}".format("；".join(errs)), acc)
            continue

        driver = None
        try:
            driver = build_driver(acc)
            st = check_login_state(driver)
            if st is True and not args.relogin:
                acc_store.say("登录状态有效，无需重复登录", acc)
                ok += 1
                continue
            driver.get(LOGIN_URL)
            if args.auto:
                _try_auto_login(driver, acc)
            if wait_for_manual_login(driver, acc, timeout=args.timeout):
                ok += 1
        except Exception as e:
            acc_store.say("登录过程出错：{}".format(e), acc)
            traceback.print_exc()
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            time.sleep(1)

    print()
    acc_store.say("登录流程结束，成功 {} / {} 个账号。".format(ok, len(accounts)))
    acc_store.say("登录状态已保存在各自账号的 profiles 目录里，下次运行会自动复用。")
    return 0


def cmd_run(args):
    # 让 print 也进日志 + 记「现在到哪一步」（2026-10-09）。
    #   只装在 run 这条路上，status / login 跑完就退，没有「实时状态」这回事。
    tee = _install_runner_tee()
    data, accounts = _load_and_pick(args, only_enabled=True)
    if not accounts:
        acc_store.say("没有可运行的账号（可能所有账号都被禁用了）。")
        return 1

    ids = [str(a.get("account_id")) for a in acc_store.all_accounts(data)]
    runnable = []
    for acc in accounts:
        others = [i for i in ids if i != str(acc.get("account_id"))]
        errs = acc_store.validate(acc, other_ids=others)
        if errs:
            acc_store.say("配置不完整，跳过该账号：{}".format("；".join(errs)), acc)
            continue
        runnable.append(acc)

    if not runnable:
        acc_store.say("没有配置完整、可直接运行的账号。")
        return 1

    # 「开抢了」这条通知（默认关，见 notify_settings.DEFAULT_EVENTS）。
    #   整轮只发一条、不按账号发：5 个账号就是 5 条短信，纯粹是刷屏。
    #   不等它，抢票才是正事，没理由为一条短信晚开哪怕 0.2 秒。
    _notify(EV_STARTED, account=None, wait=False,
            status="这轮要跑 {} 个账号".format(len(runnable)),
            show="、".join(str(a.get("account_id")) for a in runnable[:3]))

    held = []
    for acc in runnable:
        rule("=")
        # 每处理一个账号：① 输出归属切到这个账号（日志带对前缀）
        #   ② 实时状态文件立起来（面板/画面墙从这里读，谁起的都能读到）。
        if tee is not None:
            tee.account = acc
        if live_status is not None:
            live_status.begin(acc)
        acc_store.say("开始处理", acc)
        acc_store.say(acc_store.summary_line(acc), acc)
        con = None
        keep_live = False          # 走进下单流程=True：浏览器要留给你，状态也留着
        try:
            config = build_config(acc)
            # 起浏览器之前先确认这个账号的 Profile 没被上一轮遗留的窗口占着。放这里而不是 build_driver 里：`run` 这条路
            # 根本不是 build_driver 起的（AccountConcert 走 Concert.__init__ 自己建 driver），只在 build_driver 加守卫护不住
            # 用户实际用的那条。被占用时不处理会白等七八秒，再抛一句看不出原因的 session not created: Chrome instance exited。
            ensure_profile_free(acc_store.ensure_profile_dir(acc), acc)

            con = AccountConcert(config, acc)

            if not ensure_logged_in(con.driver, acc, assume_yes=args.yes,
                                    timeout=args.timeout, auto=args.auto):
                acc_store.say("未完成登录，跳过该账号", acc)
                # 登录态失效 = 用户必须亲自到场，是「值得吵醒你」的事件之一。
                #   这里是后台发（不等），因为后面可能还有别的账号要处理，
                #   不该为一条短信卡住整个流程。
                _notify(EV_NEED_LOGIN, account=acc.get("account_id"),
                        status="登录态失效，需要人工重新登录")
                con.finish()
                continue

            con.enter_concert()
            acc_store.say("已进入目标演出", acc)

            con.choose_ticket()

            # choose_ticket 是「干净收尾」返回的，不等于「已经走到下单环节」：
            #   浏览器中途被关掉时它就是这么返回的。不区分的话，下面照样会发一条
            #   「抢到了，去付款」的短信，还把这个账号留在 held 里等一个不会来的付款。
            if getattr(con, 'browser_closed', False):
                acc_store.say("浏览器窗口已被关掉，这个账号到此为止", acc)
                try:
                    con.finish()
                except Exception:
                    pass
                continue

            notify_payment(con, acc)
            held.append((con, acc))
            keep_live = True       # 浏览器留着等你付款，实时状态也留着

        except KeyboardInterrupt:
            acc_store.say("收到中断，停止后续账号", acc)
            if con is not None:
                try:
                    con.finish()
                except Exception:
                    pass
            break
        except Exception as e:
            acc_store.say("出错：{}".format(e), acc)
            traceback.print_exc()
            if con is not None:
                try:
                    con.finish()
                except Exception:
                    pass
            # 出错通知（旁路）。也走 wait=True，这个账号处理完就轮到下一个，而整个 run 可能没几秒就结束了。
            # 冷却期（默认 10 分钟）在这里是必须的：抢票会死循环重试，同一个账号一晚上能「出错」几千次，不能每错一次发一条短信。
            _notify(EV_FAILED, account=acc.get("account_id"), wait=True,
                    reason=str(e),
                    show=(acc.get("target_url") or ""),
                    city=acc.get("city"))
        finally:
            # 账号处理完（跳过 / 出错）= 这个账号的实时状态收掉；
            # 走进下单流程的不收，浏览器还开着等你付款，面板要一直看得见它。
            if not keep_live and live_status is not None:
                live_status.end(acc)
            if tee is not None:
                tee.account = None

    # 整轮结束时的汇总。只有「一个都没落到 held 里」才发，
    #   那种情况下用户什么都不知道，最需要被告知。
    #   单个账号失败上面已经报过了，这里不重复（同一账号同一事件有冷却）。
    if runnable and not held:
        _notify(EV_NEED_LOGIN, account=None, wait=False,
                status="本轮没有任何账号走到下单环节，请检查登录态和配置",
                reason="全部账号都没成功进入下单流程")

    if args.no_hold:
        for con, acc in held:
            try:
                con.finish()
                acc_store.say("浏览器已关闭", acc)
            except Exception:
                pass
    else:
        hold_browsers(held)

    # 到这儿这一轮就真的结束了（浏览器关了 / 你按了回车），
    # 把留在盘上的实时状态收干净，别让下一轮的自己误读成「还在跑」。
    if live_status is not None:
        for _con, acc in held:
            live_status.end(acc)

    return 0


def cmd_notify(args):
    """短信通知：看配置 / 存密钥 / 发一条试试。

    这条命令不影响抢票，先单独试一下短信通不通是很合理的排查顺序。
    """
    try:
        import notify as nf
        import notify_settings as nset
    except Exception:
        acc_store.say("通知模块没装全（notify.py / notify_settings.py 至少要有一个）。")
        traceback.print_exc()
        return 1

    if args.check:
        problems = nf.self_check()
        if problems:
            acc_store.say("签名自检没过：")
            for p in problems:
                acc_store.say("  - " + p)
            return 1
        acc_store.say("签名自检通过：编码规则、参数排序、确定性都对（没联网）。")
        return 0

    if args.set_secret:
        try:
            nset.set_secret(args.set_secret)
        except Exception as exc:
            acc_store.say("存密钥失败：{}".format(exc))
            return 1
        acc_store.say("AccessKeySecret 已保存（{}）。".format(
            nset.mask_secret(args.set_secret)))
        acc_store.say("提醒：建议用只授予「短信发送」权限的 RAM 子账号，不要用主账号的 AccessKey。")
        return 0

    if args.clear_secret:
        nset.set_secret("")
        acc_store.say("已删掉本机保存的 AccessKeySecret。")
        return 0

    if args.init:
        created = nset.touch()
        acc_store.say("{}：{}".format(
            "已写出默认配置" if created else "配置文件本来就在，没动它",
            nset.CONFIG_PATH))
        acc_store.say("默认是**关闭**状态。改完 enabled=true 再 `notify --test`。")
        return 0

    if args.test:
        # --test 刻意绕开总开关和冷却：它就是拿来「确认能不能发出去」的。但不绕开配置校验，缺什么直接说缺什么，
        # 比报一句 network error 强。冷却要逐个事件显式写成 0，写成 {} 没用：cooldown_left() 查不到这个 key
        # 时会退回默认的 600 秒，于是「发过一次测试，十分钟内再也发不出第二条」。
        cfg = nset.load()
        cfg["enabled"] = True
        cfg["events"] = dict(cfg.get("events") or {})
        cfg["events"][args.event] = True
        cfg["cooldown_seconds"] = {ev: 0 for ev in nset.EVENTS}
        acc_store.say("正在发一条测试通知（事件：{}，方式：{}）…".format(
            nset.name_cn(args.event), cfg.get("provider")))
        ok, msg = nf.send_sync(
            args.event, account="测试", cfg=cfg,
            show="这是一条测试通知", city="测试", price="测试",
            status="测试", reason="测试")
        acc_store.say("{}：{}".format("发送成功" if ok else "没发出去", msg))
        return 0 if ok else 1

    # 默认（含 --show）：打印配置
    nset.touch()
    acc_store.say("通知配置：{}".format(nset.CONFIG_PATH))
    acc_store.say("密钥文件：{}（{}）".format(
        nset.SECRET_PATH, "已存" if nset.has_secret() else "没存"))
    for line in nset.describe().splitlines():
        acc_store.say(line)
    acc_store.say("")
    acc_store.say("最近发过的：")
    for line in nf.describe_state().splitlines():
        acc_store.say(line)
    acc_store.say("")
    acc_store.say("下一步：改 notify.json 里的 enabled / phone_numbers / aliyun，"
                  "然后 `notify --set-secret <AccessKeySecret>`、`notify --test`。")
    return 0


def cmd_toggle(args, enabled):
    data = acc_store.load()
    changed = 0
    for aid in args.ids:
        acc = acc_store.find(data, aid)
        if acc is None:
            acc_store.say("找不到账号：{}".format(aid))
            continue
        acc["enabled"] = enabled
        acc_store.say("已{}".format("启用" if enabled else "禁用"), acc)
        changed += 1
    if changed:
        acc_store.save(data)
    return 0 if changed else 1


def cmd_forget(args):
    data = acc_store.load()
    accounts = acc_store.pick(data, args.ids)
    if not accounts:
        acc_store.say("没有匹配的账号。")
        return 1

    for acc in accounts:
        p = acc_store.profile_path(acc)
        if not acc_store.login_state_exists(acc):
            acc_store.say("本来就没有保存过登录状态，无需删除（{}）".format(p), acc)
            continue
        if not args.yes:
            try:
                ans = input("{} 确认删除该账号的登录状态？\n    目录：{}\n    输入 yes 确认：".format(acc_store.tag(acc), p))
            except EOFError:
                ans = ""
            if ans.strip().lower() not in ("y", "yes"):
                acc_store.say("已取消", acc)
                continue
        try:
            acc_store.forget_login_state(acc)
            acc_store.say("已删除登录状态：{}".format(p), acc)
        except Exception as e:
            acc_store.say("删除失败：{}".format(e), acc)
    return 0


def cmd_add(args):
    data = acc_store.load()
    copy_from = acc_store.find(data, args.copy_from) if args.copy_from else None
    try:
        acc = acc_store.add_account(data, args.account_id, note=args.note or "", copy_from=copy_from)
    except ValueError as e:
        acc_store.say(str(e))
        return 1
    acc_store.say("已新增账号", acc)
    acc_store.say("请编辑 accounts.json 补全 target_url / users / prices 等字段（不要填密码，登录靠手动）。", acc)
    return 0


def cmd_close(args):
    """关掉「跑完留给你的那个浏览器」，把账号的 Profile 释放出来。

    为什么需要这个命令：脚本的设计是「提交成功后把浏览器留在支付环节，让你自己付」。
    你支付完要是没关那个窗口，它会一直占着这个账号的 Profile 目录，
    下次再跑就会报 `session not created: Chrome instance exited`。
    这里给你一个明确的手动出口（`run` 自己也会在启动前自动处理一次）。
    """
    data = acc_store.load()
    accounts = acc_store.pick(data, args.ids)
    if not accounts:
        acc_store.say("没有匹配的账号。")
        return 1

    total = 0
    for acc in accounts:
        p = acc_store.profile_path(acc)
        if not chrome_profile_in_use(p):
            acc_store.say("这个账号的 Profile 没被占用，没有要关的浏览器", acc)
            acc_store.say("  （{}）".format(p), acc)
            continue
        n = close_own_chrome(p)
        if n:
            acc_store.say("已关掉该账号的浏览器（{} 个进程），Profile 已释放".format(n), acc)
            total += n
        else:
            acc_store.say("没能关掉（可能是权限不够）。请手动关掉那个 Chrome 窗口：", acc)
            acc_store.say("  任务管理器里找命令行含 profiles\\{} 的 chrome.exe"
                          .format(os.path.basename(p)), acc)
    if total:
        acc_store.say("共关掉 {} 个进程。你手动开的浏览器不受影响。".format(total))
    return 0



def build_parser():
    p = argparse.ArgumentParser(
        prog="account_runner.py",
        description="大麦抢票 —— 多账号登录状态管理（不保存密码；出现验证码/滑块只等待，不绕过；绝不自动付款）",
    )
    sub = p.add_subparsers(dest="cmd")

    def add_common(sp, with_ids=True):
        if with_ids:
            sp.add_argument("ids", nargs="*", help="指定 account_id；不填则处理全部账号")
        sp.add_argument("--timeout", type=float, default=None, help="等待手动登录的秒数上限，默认一直等")

    sp = sub.add_parser("status", help="检查每个账号的登录状态")
    add_common(sp)

    sp = sub.add_parser("login", help="逐个账号登录（首次使用）")
    add_common(sp)
    sp.add_argument("--force", action="store_true", help="已禁用的账号也登录")
    sp.add_argument("--relogin", action="store_true", help="即使登录态有效也重新登录")
    sp.add_argument("--auto", action="store_true",
                    help="先用本机保存的账号密码自动填表（出现验证码/滑块仍由你手动完成）")

    sp = sub.add_parser("run", help="按配置抢票（只处理启用的账号）")
    add_common(sp)
    sp.add_argument("--yes", action="store_true", help="无法自动判断登录态时，假定为已登录")
    sp.add_argument("--no-hold", action="store_true", help="结束后直接关掉浏览器（默认留着让你付款）")
    sp.add_argument("--auto", action="store_true",
                    help="登录态失效时用本机保存的账号密码自动填表重登")

    # 短信通知（旁路功能，不影响上面任何一个子命令）
    sp = sub.add_parser("notify", help="短信通知：看配置 / 存密钥 / 发一条试试")
    sp.add_argument("--show", action="store_true",
                    help="打印当前配置（手机号、密钥都按脱敏显示）")
    sp.add_argument("--init", action="store_true",
                    help="把默认配置文件写出来（默认是关的），然后自己去改")
    sp.add_argument("--set-secret", default="", metavar="AccessKeySecret",
                    help="存阿里云 AccessKeySecret")
    sp.add_argument("--clear-secret", action="store_true", help="删掉已存的密钥")
    sp.add_argument("--test", action="store_true",
                    help="立刻发一条测试通知（会忽略总开关和冷却）")
    sp.add_argument("--event", default="grabbed",
                    help="配合 --test：发哪个事件的样子（默认 grabbed）")
    sp.add_argument("--check", action="store_true",
                    help="自检：签名算法对不对（不联网、不需要配置）")

    sp = sub.add_parser("enable", help="启用账号")
    sp.add_argument("ids", nargs="+")

    sp = sub.add_parser("disable", help="禁用账号")
    sp.add_argument("ids", nargs="+")

    sp = sub.add_parser("forget", help="删除某个账号的登录状态")
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--yes", action="store_true", help="跳过确认")

    sp = sub.add_parser("close",
                        help="关掉跑完留给你的那个浏览器，释放账号 Profile"
                             "（下次 run 报 Chrome instance exited 时用这个）")
    sp.add_argument("ids", nargs="*", help="留空 = 所有账号")

    sp = sub.add_parser("add", help="新增一个账号")
    sp.add_argument("account_id")
    sp.add_argument("--note", default="")
    sp.add_argument("--copy-from", default=None, help="以某个已有账号为模板")

    return p


# 「现在这一步在干什么」的识别表：输出里出现左边的话，就记下右边的状态。顺序 = 优先级，从上往下第一个
# 命中的算。左边全部取自本文件 / concert.py 真实会打出来的整句，不许放「提交」「订单」这类光杆词：
# 按包含匹配的清单放短词就会撞车，而撞车（把演出信息卡当协议行）比漏报糟得多，这条踩过坑。
_STATE_BY_OUTPUT = (
    ("抢票未开始，刷新等待开始", "刷新中"),
    ("开始轮询检测预订按钮", "轮询中"),
    ("已进入选座页", "选座中"),
    ("准备提交订单", "待提交订单"),
    ("开始确认订单", "待提交订单"),
    ("订单已提交", "已提交，待支付"),
    ("当前已在支付环节", "已提交，待支付"),
    ("检测到人工验证", "需要验证码"),
    ("请在浏览器中手动完成登录", "等待登录"),
    ("请点击登录", "等待登录"),
    ("请扫码登录", "等待登录"),
    ("登录成功", "已登录"),
    ("登录状态有效", "已登录"),
    ("已进入目标演出", "已进演出页"),
    ("开始在详情页选择", "正在选票档"),
)


def _looks_like_say(line):
    """行首形如 `[xxx] ` 的行 = acc_store.say 的输出（它自己已经写过日志）。"""
    if not line.startswith("["):
        return False
    end = line.find("]")
    return 0 < end <= 40


class _RunnerTee(object):
    """把子进程的 print 也写进 accounts.log，并顺带记「现在到哪一步了」。

    为什么要替换 sys.stdout（2026-10-09 用户要求）：
      · 抢票主流程大量用 `print('*xxx*')` 报进度，这些只进控制台、
        不进日志，关掉窗口就什么都不剩；
      · 面板要求「不管哪条路起的任务，都算到对应账号的状态里，还要细到
        刷新中 / 轮询中 / 待提交订单」，而 print 是唯一说这些话的地方。
      本类只多「写一份日志 + 更新实时状态」，原输出一个字符不改
      （照常转发给真正的控制台）。

    为什么不重复写日志：`acc_store.say()` 的行自己已经写过日志了，
      这里靠行首形态（`[账号] `）认出来跳过，不然每条 say 出现两次。
    """

    def __init__(self, stream):
        self._stream = stream
        self.account = None       # 当前在跑哪个账号（cmd_run 每处理一个设一次）
        self._buf = ""

    def write(self, s):
        # ① 原样转发：控制台看到的和以前一模一样
        try:
            self._stream.write(s)
            self._stream.flush()
        except Exception:
            pass
        # ② 按整行处理：补日志 + 认状态
        try:
            self._feed(s)
        except Exception:
            pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def _feed(self, s):
        self._buf += str(s)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.strip() and self.account is not None:
                self._on_line(line)

    def _on_line(self, line):
        # ① 补一份日志（say 的行跳过，它已经写过）
        if not _looks_like_say(line):
            try:
                acc_store._write_log("{} {}".format(
                    acc_store.tag(self.account), line))
            except Exception:
                pass
        if live_status is None:
            return
        t = line.strip()
        for mark, state in _STATE_BY_OUTPUT:
            if mark in t:
                live_status.update(state)
                break


def _install_runner_tee():
    """给 `run` 命令装输出转发（幂等：装过就把同一个复用）。

    只装给 `run`，status / login 那些命令不改动（它们跑完就退，
    没有「实时状态」这回事，少动一处少一分风险）。
    """
    if isinstance(sys.stdout, _RunnerTee):
        return sys.stdout
    tee = _RunnerTee(sys.stdout)
    sys.stdout = tee
    # stderr 也走同一个 tee：出错时 `traceback.print_exc()` 的现场同样该进日志。
    # 共用同一个对象（不是再包一层），这样「当前账号」只有一个来源。
    try:
        sys.stderr = tee
    except Exception:
        pass
    return tee


def _setup_console_encoding():
    """把本进程的 stdout / stderr 钉成 UTF-8。

    为什么非做不可：多账号进程的输出目的地不止一种，从向导起任务是
    重定向进 runs/ 下的日志文件（`damai_wizard.spawn_runner`），
    而被人在 cmd 里手动跑（`大麦抢票.exe --runner run <账号>`）或双击
    accounts.bat 时，输出去的是中文 Windows 的控制台，代码页 936。
    源码形态下调用方还顺手带了 `-X utf8` / `PYTHONUTF8=1`，打包成 exe 之后
    `--runner` 这个角色未必带，于是按 GBK 编码时，第一条
    `print("⏳ 正在检查 Chrome 环境...")` 就直接抛 UnicodeEncodeError，
    把整个账号打断。用户看到的是「开始处理」之后立刻「出错」，
    一个字都抢不到，而日志里只有一句 codec 报错。

    这里自己兜住：不管谁起的进程、输出去哪儿，编码都按 UTF-8 走；
    顺手把控制台代码页切到 65001，不然输出到屏幕的中文会是乱码。
    stdout 是文件、或压根没有的时候，这两步都无害。
    `errors="replace"` 是最后一道保险，编码这点事再怎么样也不该让抢票挂掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def main(argv=None):
    _setup_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    # 需要浏览器的子命令：先把驱动备好，并让本地地址绕过代理
    if args.cmd in ("status", "login", "run"):
        relax_local_proxy()
        driver_path = ensure_chromedriver_env(venv_python=sys.executable)
        if driver_path:
            acc_store.say("ChromeDriver 就绪：{}".format(driver_path))
        else:
            acc_store.say("警告：没找到匹配的 ChromeDriver，浏览器可能起不来。")
            acc_store.say("可以设置环境变量 CHROMEDRIVER_PATH 指向本机驱动后重试。")

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "notify":
        return cmd_notify(args)
    if args.cmd == "login":
        return cmd_login(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "enable":
        return cmd_toggle(args, True)
    if args.cmd == "disable":
        return cmd_toggle(args, False)
    if args.cmd == "forget":
        return cmd_forget(args)
    if args.cmd == "close":
        # 故意不放进上面「需要浏览器」那一组：这个命令恰恰是不需要启动浏览器、
        # 也不用备 ChromeDriver 的，它只是把上一个浏览器关掉。
        return cmd_close(args)
    if args.cmd == "add":
        return cmd_add(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
