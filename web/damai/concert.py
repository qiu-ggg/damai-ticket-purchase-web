# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = ""
__Created__ = 2023/10/10 17:00
"""

import io
import json
import os.path
import pickle
import random
import re
import time
from time import sleep

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from check_environment import get_chromedriver_path

# 项目根目录（damai/ 的上一层）。源码形态下诊断产物写在这里，用户一眼能找到。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_dump_dir():
    """结构快照往哪个目录写。

    源码形态 = `web/`（和以前一字不差，测试和文档都按这个找）；
    打包形态 = exe 同级目录，客户一眼能找到，也好直接发出来定位。

    不能再用 `_PROJECT_ROOT`：打包后它指向 `_internal/`（PyInstaller 每次
      启动解压出来的临时目录），进程一退出就被删，写进去等于没写，
      而那正是「认不出页面时留给下一次的唯一线索」。
    """
    try:
        import app_paths
        if app_paths.is_frozen():
            return app_paths.data_dir()
    except Exception:
        pass
    return _PROJECT_ROOT


class Concert:
    def __init__(self, config):
        self.config = config
        self.status = 0  # 状态,表示如今进行到何种程度
        self.login_method = 1  # {0:模拟登录,1:Cookie登录}自行选择登录方式

        # 「关键决策」的输出口。默认 print；多账号模式下 AccountConcert 换成 accounts.py 的 say()
        # （落日志 + 有控制台就一起打）。多账号进程没有控制台，print 用户看不见，所以下单链路统一走它。
        self._log = print

        # 环境检查：自动安装/验证 ChromeDriver
        print("⏳ 正在检查 Chrome 环境...")
        try:
            chromedriver_path = get_chromedriver_path()
            print(f"✓ ChromeDriver 就绪: {chromedriver_path}\n")
        except RuntimeError as e:
            print(f"✗ 环境检查失败: {e}")
            print("\n建议运行: python damai/check_environment.py")
            exit(1)

        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_experimental_option("excludeSwitches", ['enable-automation'])
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')

        # 多账号：每个账号一个独立 Chrome Profile 目录，Cookie / localStorage / 登录状态都落在
        # 里面，账号之间互不影响。不设置时用 Chrome 默认目录，行为与改造前一致。
        user_data_dir = getattr(self.config, 'user_data_dir', None)
        if user_data_dir:
            profile_dir = os.path.abspath(user_data_dir)
            os.makedirs(profile_dir, exist_ok=True)
            chrome_options.add_argument('--user-data-dir=' + profile_dir)
            print(f"✓ 已启用独立浏览器 Profile: {profile_dir}")

        # 多开减负参数。config.chrome_args 没设/为空时一个都不加，行为与改造前一致。
        for _extra in (getattr(self.config, 'chrome_args', None) or []):
            if _extra:
                chrome_options.add_argument(_extra)

        from selenium.webdriver.chrome.service import Service
        service = Service(chromedriver_path)
        self.driver = webdriver.Chrome(service=service, options=chrome_options)  # 默认Chrome浏览器

    def _cookie_path(self):
        """Cookie 文件路径。

        多账号场景下按账号隔离，避免 A 账号的登录票据被 B 账号拿去用。
        不设置 config.cookie_file 时与改造前一致，仍是 damai_cookies.pkl。
        """
        return getattr(self.config, 'cookie_file', None) or 'damai_cookies.pkl'

    def set_cookie(self):
        """
        :return: 写入cookie
        """
        self.driver.get(self.config.index_url)
        print("***请点击登录***\n")
        while self.driver.title.find('大麦网-全球演出赛事官方购票平台') != -1:
            sleep(1)
        print("***请扫码登录***\n")
        while self.driver.title != '大麦网-全球演出赛事官方购票平台-100%正品、先付先抢、在线选座！':
            sleep(1)
        print("***扫码成功***\n")

        pickle.dump(self.driver.get_cookies(), open(self._cookie_path(), "wb"))
        print("***Cookie保存成功***")
        self.driver.get(self.config.target_url)

    def get_cookie(self):
        """
        :return: 读取cookie
        """
        try:
            cookies = pickle.load(open(self._cookie_path(), "rb"))
            for cookie in cookies:
                cookie_dict = {
                    'domain': '.damai.cn',  # 域为大麦网的才为有效cookie
                    'name': cookie.get('name'),
                    'value': cookie.get('value'),
                }
                self.driver.add_cookie(cookie_dict)
            print('***完成cookie加载***\n')
        except Exception as e:
            print(e)

    def login(self):
        """
        :return: 登录
        """
        if self.login_method == 0:
            self.driver.get(self.config.login_url)
            print('***开始登录***\n')
        elif self.login_method == 1:
            if not os.path.exists(self._cookie_path()):
                self.set_cookie()
            else:
                self.driver.get(self.config.target_url)
                self.get_cookie()

    def enter_concert(self):
        """
        :return: 打开浏览器
        """
        print('***打开浏览器，进入大麦网***\n')
        self.login()
        # 登录后直接跳到 target_url，不再 refresh（浪费时间、可能丢状态）
        self.status = 2
        print('***登录成功***')
        if self.is_element_exist('/html/body/div[2]/div[2]/div/div/div[3]/div[2]'):
            self.driver.find_element(value='/html/body/div[2]/div[2]/div/div/div[3]/div[2]', by=By.XPATH).click()

    def is_element_exist(self, element):
        """
        :param element: 判断元素是否存在
        :return:
        """
        flag = True
        browser = self.driver
        try:
            browser.find_element(value=element, by=By.XPATH)
            return flag
        except Exception:
            flag = False
            return flag

    def _get_element_text_safe(self, locator, by=By.CLASS_NAME):
        """安全地获取元素文本"""
        try:
            elements = self.driver.find_elements(value=locator, by=by)
            return elements[0].text if elements else None
        except Exception:
            return None

    def _click_element_safe(self, locator, by=By.CLASS_NAME):
        """安全地点击元素"""
        try:
            element = self.driver.find_element(value=locator, by=by)
            element.click()
            return True
        except Exception:
            return False


    def _visible(self, el):
        """元素是不是真的能被点到（显示中 + 没被禁用）。"""
        try:
            return bool(el.is_displayed()) and bool(el.is_enabled())
        except Exception:
            return False

    def _safe_text(self, el):
        try:
            return (el.text or '').strip()
        except Exception:
            return ''

    def _js_click(self, el):
        """先滚动到视野中间再点；普通 click 被遮住时退回 JS click。

        弹窗遮挡、元素在折叠区外面，普通 click 都会抛
        ElementClickInterceptedException，所以这里必须留后路。
        """
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        except Exception:
            pass
        try:
            el.click()
            return True
        except Exception:
            pass
        try:
            self.driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False

    def _short_url(self):
        """当前地址的「页名」那一小段，只用来写日志。

        真站的订单页地址是一个几百字符的 query 串（里面还带着 umidToken），
        整条打进日志既读不出重点、又会把 token 写进日志文件。这里只取路径。
        """
        try:
            url = self.driver.current_url or ''
        except Exception:
            return '（读不到地址）'
        path = url.split('?', 1)[0]
        return path.rsplit('/', 1)[-1] or path

    def _note(self, msg):
        """输出一条「关键决策」。默认打到控制台，多账号模式下同时落 accounts.log。

        规矩：凡是「提前返回」的地方，前面都得先 _note() 一句为什么。
          静默跳过是这套脚本最难查的一类 bug，它不报错、不抛异常，
          只是什么都不做，看起来和「页面没反应」一模一样。
        """
        try:
            self._log(str(msg))
        except Exception:
            try:
                print(str(msg))
            except Exception:
                pass

    def _refresh_wait(self):
        """「还没开票」时，下一次整页刷新之前等多久。

        两个决定，都在回答「多短的刷新不会被封」这个问题：

        1) 随机抖动。固定节奏本身就是机器特征，严格 0.3 秒一次、
           误差不到 1 毫秒，真人是不会那么准的。乘一个 0.8~1.25 的随机数，
           节奏看起来才像「有人在等」。这一步零代价，只赚不亏。

        2) 下限。整页刷新是这套流程里唯一会真的重新拉一遍整个页面的动作，
           也是风控最在意的那一类。大麦从未公开过风控规则，但社区实测里
           「开票前反复刷新」是被「盾」的头号原因，共识区间是 1~3 秒；
           宁可按 1 秒来。而刷新更快也不等于更抢得到，开票那一刻
           真正决定成败的是「点开购买之后，多快能勾完人并提交」，
           那一段的提速在 _h5_* 那一组里做（见 _h5_rows_info 上面的说明）。
        """
        base = getattr(self.config, 'refresh_interval', None)
        if base is None:
            base = 0.3 if self.config.fast_mode else 1.0
        try:
            base = float(base)
        except (TypeError, ValueError):
            base = 1.0
        return max(0.2, base) * random.uniform(0.8, 1.25)

    # 节奏抖动的上下限。固定间隔本身就是机器特征：严格 3.0 秒一次、误差不到
    # 1 毫秒，真人是不会那么准的。会长时间重复的等待都乘一个 0.8~1.25 的
    # 随机数，平均等待基本不变，只是不再准点。
    PACE_LO = 0.8
    PACE_HI = 1.25

    def _pace(self, sec):
        """给基准秒数乘一个 0.8~1.25 的随机数。

        固定间隔本身就是机器特征：严格 3.0 秒一次、误差不到 1 毫秒，
        真人是不会那么准的。乘上随机数之后节奏才像「有人在等」。
        平均等待基本没变，只是不再准点。
        """
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            sec = 0.5
        return max(0.05, sec) * random.uniform(self.PACE_LO, self.PACE_HI)

    def _get_wait_time(self, short=False):
        """点击之间的等待间隔，包一层抖动（固定间隔是机器特征）。

        short=True 用在关浮层、选城市这种「点完马上要再点一下」的地方。
        """
        if short:
            return self._pace(0.1 if self.config.fast_mode else 0.2)
        return self._pace(0.2 if self.config.fast_mode else 0.3)

    def _poll_until(self, pred, timeout=0.6, interval=0.04):
        """轮询等一个条件成立，成立就立刻返回，等不到才耗到 timeout。

        这套脚本里「等一件事发生」只走这一个函数，不再有「点完先睡一段、
          再假设已经好了」的写法。

        关键约定：timeout 一律取原来那个固定 sleep 的秒数。
          上界一点没缩，所以页面真慢的时候行为跟改之前完全一样；
          赚到的只是「本来 40 毫秒就好了」的那些情况，
          它们以前是按最坏情况付钱的。

        interval 的取值经验（都是实测出来的量级，不是拍的）：
          · 0.04，受控组件点完在下一帧就改好 checked，40ms 内可见
          · 0.05，读纯 DOM 状态（class / 金额 / 是否还在选座页）
          · 0.08，等「整页导航完成」这类要一个渲染周期的信号
          条件成立就返回，所以绝大多情况 1~2 轮命中，不会真的空转几百次。
        """
        deadline = time.time() + max(0.0, timeout)
        while True:
            try:
                if pred():
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(max(0.0, interval))

    # 一次往返把「地址 + 标题」拿回来。主循环原来分两次独立往返读 current_url / title，
    # 它们只是「这一页是不是订单页 / 选座页」的廉价初筛，贵的结构判定只在初筛命中时做。
    PAGE_LITE_JS = ("return {url: location.href, "
                    "title: (document.title || '')};")

    def _page_lite(self):
        """一次往返读回 {url, title}；取不到返回 {}。"""
        try:
            r = self.driver.execute_script(self.PAGE_LITE_JS)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    # 详情页「等待开票」每轮要一次问完的三件事：有没有扫码浮层、购买按钮文案、选座链接文案。
    # 老写法逐个 find_elements 各读一次 el.text，而这里 0.2 秒一轮。
    DETAIL_STATE_JS = r"""
    function _vis(el) {
      if (!el) return false;
      try {
        var cs = getComputedStyle(el), r = el.getBoundingClientRect();
        return !!(cs && cs.display !== 'none' && cs.visibility !== 'hidden'
                  && r.width > 0 && r.height > 0);
      } catch (e) { return false; }
    }
    function _txt(el) {
      if (!el) return null;
      // 只认 innerText，它就是「渲染出来的文本」，和 Selenium 的 el.text
      //   语义一致。用 `(innerText || textContent)` 那个写法会把 display:none
      //   的元素内容也读出来，于是「按钮藏起来了」会被当成「按钮还在」。
      var t = el.innerText;
      return t == null ? null : t.replace(/\s+/g, ' ').trim();
    }
    var out = {scan: false, buy: null, link: null};
    var scanSels = %s;
    for (var i = 0; i < scanSels.length; i++) {
      var els = document.querySelectorAll(scanSels[i]);
      for (var j = 0; j < els.length; j++) {
        if (_vis(els[j])) { out.scan = true; break; }
      }
      if (out.scan) break;
    }
    var b = document.querySelector('.buy__button__text');
    if (b) out.buy = _txt(b);
    var l = document.querySelector('.buy-link');
    if (l) out.link = _txt(l);
    return out;
    """

    def _detail_state(self):
        """详情页每轮状态的一次性快照；取不到返回 {}。"""
        try:
            js = self.DETAIL_STATE_JS % (list(self.SCAN_LAYER_SELECTORS),)
            r = self.driver.execute_script(js)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    # 「作废缓存」用的哨兵，任何真实 (url, title) 都不等于它。不能用 None：主循环拿它喂
    # _nav_changed，None 恒不等于快照，会让轮询空转成忙等。
    STALE_SIG = ('\x00', '\x00')

    def _nav_changed(self, sig):
        """地址或标题跟 sig 不一样了，说明页面在导航。

        用它当 `_poll_until` 的条件，就能「一跳转就立刻走」，
        而不是把那段等待睡满。
        """
        s = self._page_lite()
        return (s.get('url') or '', s.get('title') or '') != sig

    def _is_order_confirmation_page(self):
        """检查是否为订单确认页"""
        if self._is_risk_page():
            return False
        title = self._driver_title()
        if '订单确认页' in title or '确认购买' in title:
            return True
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            return '支付方式' in page_text
        except Exception:
            return False

    # 大麦风控拦请求时吐的页面，标题仍是「确认购买」，必须单独认，否则混成正常订单页。
    RISK_PAGE_WORDS = ('访问异常', '请重新试试', '安全验证', '异常流量',
                       '操作过于频繁', '请求过于频繁', '操作异常')

    # 风控判定走一次 JS 谓词。老写法 find_element(body).text 会把整页渲染文本
    # （几十上百 KB）搬回来本地 in 判断，而它在「等开票」那圈里每轮都跑。
    # 命中时把命中的那个词带回来：真站排查时「拦的是哪一类」比单纯真/假有用得多。
    RISK_WORD_JS = r"""
    var words = %s, t = '';
    try { t = (document.body && (document.body.innerText || '')) || ''; } catch (e) {}
    for (var i = 0; i < words.length; i++) {
      if (t.indexOf(words[i]) !== -1) return words[i];
    }
    return '';
    """

    # 诊断用：正文可见文本多长、提没提「观演人 / 提交订单」。真订单页必然提到这两样，
    # 风控页正文只有那两句话、两样都不提。只回长度和两个真/假，不把正文搬回来。
    PAGE_SHAPE_JS = r"""
    var t = '';
    try { t = (document.body && (document.body.innerText || '')) || ''; } catch (e) {}
    return {len: t.length,
            viewer: t.indexOf('观演人') !== -1,
            submit: t.indexOf('提交订单') !== -1};
    """

    def _risk_word_now(self):
        """当前页正文命中的风控词，没命中返回空串。"""
        try:
            words = json.dumps(list(self.RISK_PAGE_WORDS), ensure_ascii=False)
            return self.driver.execute_script(self.RISK_WORD_JS % words) or ''
        except Exception:
            return ''

    def _is_risk_page(self):
        """当前页是不是大麦的「访问异常」风控提示页。"""
        return bool(self._risk_word_now())

    # 遮挡浮层 / 不选座购票 / 随机选座。PC 端从点购买到下单会弹「手机扫码购买更便捷」二维码
    # 浮层，遮罩盖住按钮，关不掉就隐藏让点击穿过去。只点用户本来要点的那几下，不碰支付。

    # 「不，xxx」型：拒绝扫码 / 拒绝手机端，继续在电脑上买。扫码浮层上唯一能点的就是
    # 「不，选座购票」，它没有关闭叉也不是全屏遮罩，不点就没法往下走。
    REJECT_SCAN_TEXTS = (
        '不，选座购票', '不,选座购票', '不，选座购买', '不,选座购买',
        '不，选座', '不,选座',
        '不，立即预订', '不,立即预订', '不，立即购买', '不,立即购买',
        '不，谢谢', '不,谢谢',
    )

    # 浮层里「继续在电脑上购买」这类按钮文案
    MASK_CONTINUE_TEXTS = (
        '继续在电脑上购买', '继续在电脑购买', '继续电脑购买', '仍在电脑上购买',
        '继续网页购买', '网页购买', '在电脑上购买',
        '暂不需要', '不用了', '继续购买', '继续购票', '仍然购买', '坚持购买',
        '取消扫码', '关闭弹窗',
    ) + REJECT_SCAN_TEXTS

    # 详情页「手机扫码购买更便捷」浮层（2026-10-06 实测）：点购买不跳转，购买按钮整块换成
    # .scan-buy（内含二维码和 .buy-link「不，选座购票」）。浮层出现时 buy__button__text 从
    # DOM 整个消失（不是被盖住），所以「找不到购买按钮」就是浮层弹起的信号，不点掉永远死循环。
    SCAN_LAYER_SELECTORS = (
        '[class*="scan-buy"]', '[class*="scanBuy"]', '[class*="scan_buy"]',
    )
    # 出现这些词，基本可以确定是「扫码 / 推 App」类浮层
    MASK_HINT_WORDS = ('扫码', '二维码', '下载大麦', '客户端', 'APP', 'app', '手机购票')

    # 浮层上的关闭控件
    MASK_CLOSE_SELECTORS = (
        '.next-dialog-close', '.bui-dialog-close', '.next-dialog-close-btn',
        '.bui-dialog-close-icon',
        '[class*="dialog"] [class*="close"]',
        '[class*="modal"] [class*="close"]',
        '[class*="popup"] [class*="close"]',
        '[class*="close-icon"]', '[class*="closeIcon"]', '[class*="close-btn"]',
        '[aria-label="关闭"]', '[title="关闭"]',
    )

    # 「页面上有浮层」用的选择器。.scan-buy 故意不列在这里：它是内容区不是遮罩，正确做法是
    # 点它里面的「不，选座购票」；一旦算进要隐藏的遮罩，兜底逻辑会连同那颗按钮一起隐藏，就永远点不到了。
    OVERLAY_SELECTORS = (
        '.next-overlay-backdrop', '.bui-overlay', '.next-dialog', '.bui-dialog',
        '[class*="overlay"]', '[class*="dialog"]', '[class*="modal"]',
        '[class*="popup"]', '[class*="mask"]', '[class*="qrcode"]', '[class*="qr-code"]',
    )

    # 「不选座购票」的各种叫法
    NO_SEAT_TEXTS = (
        '不选座购票', '不选座购买', '不选座买', '不选座',
        '无需选座', '不需要选座', '系统自动分配', '自动分配座位', '随机分配座位',
    )

    # 选座页：确认按钮的文案
    SEAT_CONFIRM_TEXTS = ('确认选座', '确定选座', '确认座位', '选好了', '确认', '确定')
    # 选座页：提示「这个位置不能选」的文案
    SEAT_TAKEN_WORDS = ('已被选', '已被占用', '已被预订', '已经被选', '请重新选择',
                        '不可选', '已售', '无法选择')


    def dismiss_obstructions(self, rounds=3, verbose=True):
        """关掉挡在按钮前面的浮层。

        两条路一起走：
          1) 按常见类名找弹窗上的关闭按钮 / 「继续在电脑上购买」
          2) 命中测试：谁盖着提交按钮就清谁（不依赖类名）

        返回关掉的个数，0 表示本来就没有遮挡。
        """
        if not getattr(self.config, 'dismiss_mask', True):
            return 0

        had_overlay = bool(self._overlay_elements())
        closed = 0
        for _ in range(max(1, rounds)):
            if not self._close_one_obstruction():
                break
            closed += 1
            time.sleep(self._get_wait_time(short=True))

        # 有浮层说明屏幕被盖了：再按「谁挡着按钮」清一遍，把类名不标准的遮罩一并处理掉。
        if had_overlay or closed:
            self._unblock_submit_buttons()

        if closed and verbose:
            print(f"  ✓ 已关闭 {closed} 个遮挡浮层（扫码弹窗类）\n")
        return closed

    def _scan_layer_elements(self):
        """当前可见的「手机扫码购买更便捷」浮层元素。"""
        out = []
        for sel in self.SCAN_LAYER_SELECTORS:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if el.is_displayed():
                        out.append(el)
                except Exception:
                    continue
        return out

    def scan_layer_present(self):
        """详情页上是不是弹着「手机扫码购买更便捷」浮层。"""
        return bool(self._scan_layer_elements())

    def _overlay_elements(self):
        """返回当前可见的浮层元素（弹窗 / 遮罩 / 二维码层），已去重。

        扫码浮层及其内部一律排除：它在详情页里是内容区，正确做法是点它的
        「不，选座购票」，而不是把它 display:none 掉，真把它隐藏了，
        那颗按钮也没了，就再也点不到（这个坑当场踩过一次）。
        """
        scan_layers = self._scan_layer_elements()
        found = []
        seen = set()
        for sel in self.OVERLAY_SELECTORS:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    if any(sl is el or sl.contains(el) for sl in scan_layers):
                        continue
                    r = el.rect      # 只用宽高做「是不是浮层」的判断，不涉及坐标点击
                    w = r.get('width') or 0
                    h = r.get('height') or 0
                    # 太小的是关闭图标之类，不算浮层
                    if w < 40 or h < 40:
                        continue
                    key = (r.get('x'), r.get('y'), w, h)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(el)
                except Exception:
                    continue
        return found

    def _strip_overlays_js(self):
        """兜底：把可见浮层直接隐藏掉（只动样式，不删 DOM）。

        有些扫码浮层没有标准的关闭按钮，ESC 也关不掉，
        这时候把遮罩藏起来，点击就能穿到下面的按钮上。
        """
        sel_js = '[' + ','.join("'%s'" % s for s in self.OVERLAY_SELECTORS) + ']'
        js = """
        var n = 0;
        %s.forEach(function (s) {
          document.querySelectorAll(s).forEach(function (e) {
            var cs = window.getComputedStyle(e);
            if (cs.display === 'none' || cs.visibility === 'hidden') { return; }
            var r = e.getBoundingClientRect();
            if (r.width < 40 || r.height < 40) { return; }
            e.style.setProperty('display', 'none', 'important');
            n++;
          });
        });
        return n;
        """ % sel_js
        try:
            return int(self.driver.execute_script(js) or 0) > 0
        except Exception:
            return False

    # 提交类按钮的文案：用来判断"哪个按钮正被盖住"
    SUBMIT_BUTTON_TEXTS = ('提交订单', '立即提交', '提交', '确认购买', '确认下单',
                           '确认选座', '确定选座')

    def _element_covered(self, el):
        """按钮中心点上是不是别的元素？是的话说明它被盖住了。"""
        js = """
        var el = arguments[0];
        var r = el.getBoundingClientRect();
        var x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) { return false; }
        var hit = document.elementFromPoint(x, y);
        if (!hit) { return false; }
        if (hit === el || el.contains(hit)) { return false; }
        return true;
        """
        try:
            return bool(self.driver.execute_script(js, el))
        except Exception:
            return False

    def _remove_cover_js(self, el):
        """把盖在 el 上面的那层浮层隐藏掉。返回清掉的层数（0 = 本来没被盖）。

        刻意不猜类名：直接做命中测试，谁挡着就清谁。
        大麦换弹窗类名也不会失效。
        """
        js = """
        var el = arguments[0];
        var vw = innerWidth, vh = innerHeight, area = vw * vh;
        var r = el.getBoundingClientRect();
        var x = r.left + r.width / 2, y = r.top + r.height / 2;
        var hit = document.elementFromPoint(x, y);
        if (!hit || hit === el || el.contains(hit)) { return 0; }

        var n = 0;
        // 从挡路的元素往上找，找到第一层"铺满屏幕 + 非 static 定位"的就是遮罩
        var node = hit, cover = null;
        while (node && node !== document.body && node !== document.documentElement) {
          var cs = getComputedStyle(node);
          var nr = node.getBoundingClientRect();
          if (cs.position !== 'static' && (nr.width * nr.height) >= 0.3 * area) {
            cover = node;
            break;
          }
          node = node.parentElement;
        }
        if (!cover) { cover = hit; }
        if (!cover.contains(el) && !el.contains(cover)) {
          cover.style.setProperty('display', 'none', 'important');
          n++;
        } else {
          return 0;
        }

        // 遮罩常常一层套一层，把同量级的浮层一并摘掉
        var all = document.querySelectorAll('body *');
        for (var i = 0; i < all.length; i++) {
          var e = all[i];
          if (e === cover || e.contains(el) || el.contains(e)) { continue; }
          var cs2 = getComputedStyle(e);
          if (cs2.display === 'none' || cs2.visibility === 'hidden' || cs2.opacity === '0') { continue; }
          if (cs2.position !== 'fixed' && cs2.position !== 'absolute') { continue; }
          if (!(parseInt(cs2.zIndex) >= 1)) { continue; }
          var er = e.getBoundingClientRect();
          if ((er.width * er.height) < 0.3 * area) { continue; }
          e.style.setProperty('display', 'none', 'important');
          n++;
        }
        return n;
        """
        try:
            return int(self.driver.execute_script(js, el) or 0)
        except Exception:
            return 0

    def _has_submit_button(self):
        """便宜的预检：页面上有没有提交类按钮（避免无谓的全量扫描）。"""
        ors = ' or '.join(
            f"contains(normalize-space(text()), '{t}')" for t in self.SUBMIT_BUTTON_TEXTS)
        xp = ("//*[self::button or self::div or self::span or self::a]"
              f"[{ors}]")
        try:
            return bool(self.driver.find_elements(By.XPATH, xp))
        except Exception:
            return False

    def _unblock_submit_buttons(self):
        """提交按钮要是被浮层盖着，就把那层浮层清掉。

        比按类名找弹窗可靠：直接看按钮中心点上站的是谁。
        """
        if not getattr(self.config, 'dismiss_mask', True):
            return 0
        if not self._has_submit_button():
            return 0
        killed = 0
        for txt in self.SUBMIT_BUTTON_TEXTS:
            for tag in ('button', 'div', 'span', 'a'):
                try:
                    els = self.driver.find_elements(
                        By.XPATH, f"//{tag}[contains(normalize-space(text()), '{txt}')]")
                except Exception:
                    continue
                for el in els:
                    t = self._safe_text(el)
                    if not t or len(t) > 10:
                        continue
                    if self._is_pay_button_text(t):
                        continue
                    try:
                        if not el.is_displayed():
                            continue
                    except Exception:
                        continue
                    if self._remove_cover_js(el):
                        killed += 1
        if killed:
            print(f"  ✓ 已清掉 {killed} 层盖在按钮上的浮层\n")
        return killed

    def _close_one_obstruction(self):
        """尝试关掉一个可见浮层。关掉了返回 True。"""
        overlays = self._overlay_elements()
        if not overlays:
            return False

        for overlay in overlays:
            otext = self._safe_text(overlay)
            hinted = any(w in otext for w in self.MASK_HINT_WORDS)

            # ① 浮层自带的关闭控件（必须可见，否则 JS click 能"点"到隐藏元素，白高兴一场）
            for sel in self.MASK_CLOSE_SELECTORS:
                try:
                    btns = overlay.find_elements(By.CSS_SELECTOR, sel)
                except Exception:
                    continue
                for btn in btns:
                    if not self._visible(btn):
                        continue
                    if self._js_click(btn):
                        return True

            # ② 浮层里的「继续在电脑上购买」这类按钮
            for txt in self.MASK_CONTINUE_TEXTS:
                for tag in ('button', 'div', 'span', 'a'):
                    xp = f".//{tag}[contains(normalize-space(text()), '{txt}')]"
                    try:
                        els = overlay.find_elements(By.XPATH, xp)
                    except Exception:
                        continue
                    for el in els:
                        t = self._safe_text(el)
                        if not t or len(t) > 20:
                            continue
                        if self._is_pay_button_text(t):
                            continue
                        if not self._visible(el):
                            continue
                        if self._js_click(el):
                            return True

            # ③ 确认是扫码类浮层：ESC 关不掉的直接摘掉
            if hinted and self._strip_overlays_js():
                return True

            # ④ 最后试一下 ESC
            try:
                self.driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                time.sleep(0.1)
                if not self._overlay_elements():
                    return True
            except Exception:
                pass

        return False


    # 详情页上「可点的主购买按钮」的文案
    BUY_BUTTON_TEXTS = ('选座购买', '不选座购买', '不选座购票', '立即购买', '立即预订')

    def _is_buy_button_text(self, text):
        """这个文案是不是一颗可点的主购买按钮。"""
        return (text or '').strip() in self.BUY_BUTTON_TEXTS

    def _should_try_no_seat(self, buy_button_text):
        """只有购买按钮真的可点时才去找「不选座」入口，免得乱点。"""
        if not getattr(self.config, 'no_seat', True):
            return False
        return self._is_buy_button_text(buy_button_text)

    def click_no_seat_buy(self):
        """优先点「不选座购票」，跳过选座直接进订单确认页。

        找不到这样的入口就返回 False，由调用方退回原来的「选座购买」流程。
        """
        if not getattr(self.config, 'no_seat', True):
            return False

        self.dismiss_obstructions(rounds=2, verbose=False)

        for txt in self.NO_SEAT_TEXTS:
            for tag in ('button', 'a', 'div', 'span', 'li', 'label'):
                try:
                    els = self.driver.find_elements(
                        By.XPATH, f"//{tag}[contains(normalize-space(text()), '{txt}')]")
                except Exception:
                    continue
                for el in els:
                    t = self._safe_text(el)
                    # 文本太长的往往是包着整块区域的外层 div，点它没用
                    if not t or len(t) > 20:
                        continue
                    if self._is_pay_button_text(t):
                        continue
                    if not self._visible(el):
                        continue
                    if self._js_click(el):
                        print(f"  ✓ 已点「{t}」（跳过选座）\n")
                        return True
        return False


    def _is_reject_scan_text(self, text):
        """判断一个文案是不是「拒绝扫码、继续在电脑上买」那颗按钮。"""
        t = (text or '').strip()
        if not t:
            return False
        if t in self.REJECT_SCAN_TEXTS:
            return True
        # 「不，选座购票」这类永远以「不，」开头，兜住以后改文案
        return t.startswith('不，') or t.startswith('不,')

    def dismiss_scan_layer(self):
        """点掉扫码浮层上的「不，选座购票」，继续在电脑上买。

        为什么单独写、不走 dismiss_obstructions：
        这层浮层没有关闭叉、也不是铺满屏幕的遮罩，唯一出路就是点它自己那颗
        「不，选座购票」。点完大麦才会恢复购买按钮（或直接进选座页）。
        """
        if not getattr(self.config, 'dismiss_mask', True):
            return False
        if not self.scan_layer_present():
            return False

        for txt in self.REJECT_SCAN_TEXTS:
            for tag in ('div', 'a', 'span', 'button', 'li'):
                try:
                    els = self.driver.find_elements(
                        By.XPATH,
                        f"//{tag}[contains(normalize-space(text()), '{txt}')]")
                except Exception:
                    continue
                for el in els:
                    t = self._safe_text(el)
                    if not t or len(t) > 20:
                        continue
                    if self._is_pay_button_text(t):
                        continue
                    if not self._visible(el):
                        continue
                    if self._js_click(el):
                        print(f"  ✓ 已点「{t}」，跳过手机扫码浮层，继续在电脑上购买\n")
                        # 告诉主循环：上一次「点了购买」作废了（按钮被换掉），该重新找按钮点，别干等 20 秒超时。
                        self._scan_layer_dismissed = True
                        return True
        return False

    # 3) 随机选座。老 PC 页 title「选座购买」、座位是 DOM（.seat-item）；新版 H5（kylin.html）
    # title「选择座位」、URL 带 select-seat、座位画在 canvas 上，没有 DOM 座位，只能按坐标点，靠底部「￥N」由 0 变正判断点没点中。

    H5_SEAT_URL_HINTS = ('select-seat', 'kylin')
    SEAT_PAGE_TITLE_HINTS = ('选座', '选择座位')
    # 座位图容器（按优先级）。用容器不用里面的 img：img 是 blob: 动态图有时拿不到，容器范围就是座位图范围。
    H5_SEAT_BOX_SELECTORS = (
        '.aseat-layer-panel',
        '.select-seat__container',
        '.select-seat',
    )
    H5_CONFIRM_TEXTS = ('立即购买', '确认选座', '选好了', '立即预订')

    # 价位和座位颜色一一对应，选座必须比色，否则点到别的价位。色块图（div.price > img.price__img）
    # 和座位图同源；点价位只是 grayPrice() 把别档降透明度，那些座位仍点得中，得按颜色筛候选点。
    # 没有价位信息的座位是纯黑 #000000，salable=false 的档不可选。
    H5_PRICE_ITEM_SELECTORS = (
        '.price-list .price',
        '.price-list__item .price',
        '[class*="price-list"] [class*="price"]',
        '.price',
    )
    # 价位文案有两套类名：普通档 `.price__text`（写「￥480」），选中态那档换成 `.price__package`
    # （写「280.0元」，没「￥」）。只认前一套的话，配置那一档恰好在选中态时会被整项丢掉。
    H5_PRICE_TEXT_SELECTOR = '.price__text, .price__package'
    H5_PRICE_IMG_SELECTOR = '.price__img'
    # 纯黑是无价位信息的座位，永远不要选
    H5_SEAT_BAD_COLOR = (0, 0, 0)
    # alpha 低于此值的像素算「被灰化的其他价位」。线上灰化 = globalAlpha 0.2 让 alpha≈51，
    # 正常/高亮是 255；取 180 是因为缩放插值会让边缘像素的 alpha 略掉一点。
    H5_SEAT_MIN_ALPHA = 180
    # 连着挑到「价位不对」的座位多少次就停手。挑错一次会真的下单再取消，在真站上等于白点两下，
    # 连着这么多次说明筛颜色那条路没生效。
    H5_SEAT_WRONG_PRICE_LIMIT = 6

    # 读一个元素（通常是色块 <img>）的「代表色」：画到离屏 canvas 取像素，取出现最多的不透明色。
    # 色块图可能有描边/高光，众数比平均值靠谱。
    ELEM_COLOR_JS = r"""
    var el = arguments[0];
    if (!el) { return null; }
    var w = el.naturalWidth || el.width || 0;
    var h = el.naturalHeight || el.height || 0;
    if (!w || !h) { return null; }
    var cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    var cx = cv.getContext('2d', {willReadFrequently: true});
    try { cx.drawImage(el, 0, 0); } catch (e) { return null; }
    var d;
    try { d = cx.getImageData(0, 0, w, h).data; } catch (e) { return null; }
    var tally = {}, best = null, bestN = 0;
    for (var i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 200) { continue; }              // 半透明边角不算
      var k = (d[i] >> 3) + ',' + (d[i + 1] >> 3) + ',' + (d[i + 2] >> 3);
      tally[k] = (tally[k] || 0) + 1;
      if (tally[k] > bestN) { bestN = tally[k]; best = [d[i], d[i + 1], d[i + 2]]; }
    }
    if (!best) { return null; }
    return {r: best[0], g: best[1], b: best[2], n: bestN};
    """

    def _elem_color(self, el):
        """元素（色块图）的代表色 (r,g,b)；读不到返回 None。"""
        try:
            c = self.driver.execute_script(self.ELEM_COLOR_JS, el)
        except Exception:
            return None
        if not c:
            return None
        return (int(c['r']), int(c['g']), int(c['b']))

    def _h5_price_items(self):
        """读出选座页上的价位项：每项 {price, color, selected, disabled, el}。

        price 是 `￥380` 里的数字；color 取自色块图，就是该价位座位的颜色。
        读不到色块的项也会返回（color=None），只是不能用来按颜色筛像素。
        """
        out = []
        seen = set()
        els = []
        for sel in self.H5_PRICE_ITEM_SELECTORS:
            try:
                found = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            if found:
                els = found
                break

        for el in els:
            try:
                if not el.is_displayed():
                    continue
                price = None
                for txt in el.find_elements(By.CSS_SELECTOR, self.H5_PRICE_TEXT_SELECTOR):
                    raw = (txt.text or '').strip()
                    m = re.search(r'(\d+(?:\.\d+)?)', raw.replace(',', ''))
                    if m:
                        price = int(float(m.group(1)))
                        break
                if price is None:
                    # 类名换了就退到「这一项的全部文字」，先找带「￥」的，再取第一个数字（选中态写 `280.0元`，没「￥」）。
                    full = (el.text or '').replace(',', '')
                    m = (re.search(r'￥\s*(\d+(?:\.\d+)?)', full)
                         or re.search(r'(\d+(?:\.\d+)?)', full))
                    if m:
                        price = int(float(m.group(1)))
                if price is None or price in seen:
                    continue

                color = None
                try:
                    imgs = el.find_elements(By.CSS_SELECTOR, self.H5_PRICE_IMG_SELECTOR)
                except Exception:
                    imgs = []
                if not imgs:
                    try:
                        imgs = el.find_elements(By.CSS_SELECTOR, 'img')
                    except Exception:
                        imgs = []
                if imgs:
                    color = self._elem_color(imgs[0])

                cls = (el.get_attribute('class') or '')
                seen.add(price)
                out.append({
                    'price': price,
                    'color': color,
                    'selected': 'selected' in cls,
                    'disabled': 'disable' in cls,
                    'el': el,
                })
            except Exception:
                continue
        return out

    def _price_int_list(self):
        """配置里要的价位（按优先级），转成整数；转不出来的丢掉。"""
        raw = getattr(self.config, 'prices', None) or []
        if isinstance(raw, (str, int)):
            raw = [raw]
        vals = []
        for p in raw:
            m = re.search(r'(\d+)', str(p))
            if m:
                vals.append(int(m.group(1)))
        return vals

    def _h5_match_price(self, items):
        """在页面价位里挑出配置要的、且可售的那一档。返回 item 或 None。

        按配置顺序试（prices 的数组顺序就是优先级），配置写 [380, 580]
        意思是「380 优先，没有再要 580」。
        """
        if not items:
            return None
        want = self._price_int_list()
        if not want:
            return None
        by_price = {}
        for it in items:
            by_price.setdefault(it['price'], it)
        for p in want:
            it = by_price.get(p)
            if it and not it['disabled']:
                return it
        return None

    def _h5_highlight_price(self, item):
        """点一下目标价位项，页面自己会调 aseat.highlightPrice() 把其他档灰掉。

        已经高亮的就别再点：onClick 里 `priceId === selectedPriceId` 是反选，
        再点一下会把高亮取消掉。
        """
        price = item.get('price')
        if item.get('selected'):
            return True
        el = item.get('el')
        if el is None:
            return False
        # 页面不认 JS click，这里同样走 CDP 真实鼠标事件
        self._h5_click(el)
        # 点完必须重新读列表，不能拿旧元素读 class：点完页面重渲染，旧节点已脱离文档，读到点击前的值，
        # 「点成功了」被误判成失败，退回「只靠金额逐个试」的慢路。改成轮询「这一档高亮了没有」，亮就返回。
        self._poll_until(lambda: self._h5_price_selected(price),
                         timeout=0.45, interval=0.05)
        return self._h5_price_selected(price)

    def _h5_price_selected(self, price):
        """某个价位现在是不是「已选中」状态。读不到返回 False。"""
        try:
            for it in self._h5_price_items():
                if it['price'] == price:
                    return bool(it['selected'])
        except Exception:
            pass
        return False

    @staticmethod
    def _color_close(c1, c2, tol=48):
        """两个颜色算不算同一个（按通道比）。容差给得宽一点：
        缩放插值、圆角抗锯齿都会让边缘像素偏色。"""
        if c1 is None or c2 is None:
            return False
        return (abs(c1[0] - c2[0]) <= tol and
                abs(c1[1] - c2[1]) <= tol and
                abs(c1[2] - c2[2]) <= tol)

    # 浏览器被关、或会话销毁时 selenium 抛这几种消息。判页面的函数每轮都在调，绝不能让异常漏到主流程：
    # 漏出去的表现是一大段英文堆栈砸客户脸上、任务直接崩（2026-10-10 实跑日志两次都这么结束）。
    BROWSER_GONE_MARKS = ('no such window', 'invalid session id',
                          'not connected to devtools', 'chrome not reachable',
                          'disconnected', 'web view not found',
                          # chromedriver 自己没了：selenium 抛 urllib3 的 MaxRetryError（连接被拒），文案里含这两个词。
                          'max retries', 'connection refused')
    browser_closed = False

    def _mark_browser_gone(self, exc):
        """这个异常算不算「浏览器已经没了」；算就记下来，让主循环收尾。

        判据取消息文本。关窗口得到 NoSuchWindowException、会话被销毁得到
        InvalidSessionIdException；而 chromedriver 进程自己没了时，selenium 抛的是
        urllib3 的 MaxRetryError（Max retries exceeded / Connection refused），
        它不在 WebDriverException 这棵树上，按类型判会把这一种漏掉。
        """
        if any(k in str(exc).lower() for k in self.BROWSER_GONE_MARKS):
            self.browser_closed = True
            return True
        return False

    def _driver_url(self):
        """当前地址（小写）。浏览器没了就返回空串，并记下这件事。"""
        try:
            return (self.driver.current_url or '').lower()
        except Exception as e:
            self._mark_browser_gone(e)
            return ''

    def _driver_title(self):
        """当前标题。浏览器没了就返回空串，并记下这件事。"""
        try:
            return self.driver.title or ''
        except Exception as e:
            self._mark_browser_gone(e)
            return ''

    def _is_seat_page(self):
        """当前页是不是选座页（新旧两种形态都算）。"""
        url = self._driver_url()
        if any(h in url for h in self.H5_SEAT_URL_HINTS):
            return True
        title = self._driver_title()
        return any(h in title for h in self.SEAT_PAGE_TITLE_HINTS)

    def _is_h5_seat_page(self):
        """是不是新版 H5 选座页（座位画在 canvas 上的那种）。"""
        url = self._driver_url()
        if any(h in url for h in self.H5_SEAT_URL_HINTS):
            return True
        try:
            return bool(self.driver.find_elements(By.CSS_SELECTOR, '.aseat-layer-panel'))
        except Exception:
            return False

    def _h5_seat_box(self):
        """H5 选座页里座位图的可视矩形（已裁进视口）；找不到返回 None。"""
        sels = list(self.H5_SEAT_BOX_SELECTORS)
        js = """
        var sels = %s;
        for (var i = 0; i < sels.length; i++) {
          var el = document.querySelector(sels[i]);
          if (!el) continue;
          var r = el.getBoundingClientRect();
          var x = Math.max(0, r.left), y = Math.max(0, r.top);
          var x2 = Math.min(innerWidth, r.right), y2 = Math.min(innerHeight, r.bottom);
          if (x2 - x < 60 || y2 - y < 60) continue;
          return {x: Math.round(x), y: Math.round(y),
                  w: Math.round(x2 - x), h: Math.round(y2 - y)};
        }
        return null;
        """ % (sels,)
        try:
            return self.driver.execute_script(js)
        except Exception:
            return None

    # 坐标只能发视口坐标，不能用 el.rect：它是文档坐标（getBoundingClientRect + pageYOffset），
    # headless 实测差出整一个 pageYOffset，按它发 CDP 点毫无反应还不抛异常。
    # 订单页第 10 行观演人得先滚动，滚动后点落到视口外，观演人永远勾不上。
    VIEW_RECT_JS = r"""
    var el = arguments[0];
    if (!el) { return null; }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) { return null; }
    return {x: r.left, y: r.top, w: r.width, h: r.height,
            vw: innerWidth, vh: innerHeight, pageY: window.pageYOffset};
    """

    # 命中测试：问浏览器「这一点上到底站着谁」
    HIT_JS = r"""
    var el = arguments[0], x = arguments[1], y = arguments[2];
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) { return null; }
    var hit = document.elementFromPoint(x, y);
    if (!hit) { return null; }
    return {inside: (hit === el || el.contains(hit)),
            tag: hit.tagName,
            cls: ('' + (hit.className || '')).slice(0, 60)};
    """

    def _viewport_rect(self, el):
        """元素的视口矩形（可以直接拿去发坐标点击）；拿不到返回 None。"""
        try:
            return self.driver.execute_script(self.VIEW_RECT_JS, el)
        except Exception:
            return None

    def _view_point_on(self, el, rect=None):
        """算出一个「真的落在 el 上」的视口坐标点；算不出来返回 None。

        不只用几何中心：中心可能被别的元素盖住（浮层、或者行里更上层的子元素），
        也可能压根在视口外面。所以每个候选点都过一遍 elementFromPoint 命中测试，
        只有命中的点才算数，点不到就老实返回 None，让调用方去报错，
        而不是发一个必然落空的坐标、再假装点成功了。
        """
        r = rect or self._viewport_rect(el)
        if not r:
            return None
        vw, vh = r.get('vw') or 0, r.get('vh') or 0
        cands = [(0.5, 0.5)]
        # 中心不中就在矩形里多撒几个点。避开四条边，边缘常是圆角/描边，容易判成没中。
        for fy in (0.30, 0.70):
            for fx in (0.25, 0.50, 0.75):
                cands.append((fx, fy))
        for fx, fy in cands:
            x = int(r['x'] + r['w'] * fx)
            y = int(r['y'] + r['h'] * fy)
            if vw and vh and (x < 1 or y < 1 or x > vw - 2 or y > vh - 2):
                continue        # 在视口外面，发了也是白发
            try:
                hit = self.driver.execute_script(self.HIT_JS, el, x, y)
            except Exception:
                hit = None
            if hit and hit.get('inside'):
                return x, y
        return None

    def _click_point_cdp(self, x, y):
        """按视口坐标点一下，走 CDP 的真实鼠标事件。

        为什么不能用 JS 的 el.click()：canvas 监听的是 mousedown/mouseup，
        JS 造出来的 click 事件它根本不理会。
        """
        try:
            for ev in (
                {"type": "mouseMoved", "x": int(x), "y": int(y),
                 "button": "none", "buttons": 0},
                {"type": "mousePressed", "x": int(x), "y": int(y),
                 "button": "left", "buttons": 1, "clickCount": 1},
                {"type": "mouseReleased", "x": int(x), "y": int(y),
                 "button": "left", "buttons": 0, "clickCount": 1},
            ):
                self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", ev)
            return True
        except Exception:
            return False

    def _h5_picked_amount(self):
        """H5 选座页底部「￥N」里的 N（已选座位合计）；读不到返回 -1。

        只认 `.footer-bar-info__price`（即 `￥380` 那一处）。
          不要退到「整个 footer-bar 里的第一个 ￥N」，底栏还有
          `.footer-bar-info__dash-price`（划线价，斜杠原价），
          先撞上它就等于拿原价当实付价，校验会全错。
          真站 JSX：
            <span class="footer-bar-info__price">
              <span class="footer-bar-info__unit">￥</span>{price}
            </span>
        """
        js = r"""
        var info = document.querySelector('[class*="footer-bar-info__price"]');
        if (!info) {
          // 类名换过：在底栏里找「不含 dash」的那个价格节点
          var all = document.querySelectorAll('[class*="footer-bar"] [class*="price"]');
          for (var i = 0; i < all.length; i++) {
            var cn = '' + (all[i].className || '');
            if (cn.indexOf('dash') !== -1) continue;
            info = all[i]; break;
          }
        }
        var footer = document.querySelector('[class*="footer-bar"]');
        if (!info && !footer) { return null; }          // 底栏都没有 → 读不到
        var txt = info ? (info.textContent || '') : (footer.innerText || '');
        txt = txt.replace(/,/g, '');
        var m = txt.match(/￥\s*(\d+(?:\.\d+)?)/);
        if (!m) { return 0; }                           // 底栏在但还没选座位 → 0
        return Math.round(parseFloat(m[1]));
        """
        try:
            v = self.driver.execute_script(js)
        except Exception:
            return -1
        return -1 if v is None else int(v)

    def _h5_confirm_button(self):
        """H5 选座页底部的「立即购买」按钮对象（不管它当前能不能点）。

        坑：没选座位时它的 class 是 `footer-bar__button--disable`、背景灰、
        opacity 0.5，但没有 disabled 属性。要是按「禁用」把它过滤掉，
        就永远找不到这颗按钮了。能不能点交给 _h5_confirm 用「￥N > 0」判断。
        """
        for txt in self.H5_CONFIRM_TEXTS:
            try:
                els = self.driver.find_elements(
                    By.XPATH,
                    "//*[self::button or self::div or self::a or self::span]"
                    f"[normalize-space(text())='{txt}']")
            except Exception:
                continue
            for el in els:
                if self._visible(el):
                    return el
        for sel in ('button.bui-btn-primary', '.bui-btn-primary',
                    '[class*="footer"] button[class*="primary"]'):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                if self._visible(el):
                    return el
        return None

    def _h5_confirm(self):
        """点 H5 选座页的「立即购买」。

        只有真选中了座位才点，没选时那颗按钮是灰的，按下去也不会往前走。
        点击走 CDP 的真实鼠标事件：这个页面是 H5 框架，canvas 那边已经证明
        它不认 JS 造出来的 click。
        """
        if not self._h5_picked():
            return False
        btn = self._h5_confirm_button()
        if btn is None:
            return False

        # 坐标走 _view_point_on（视口坐标 + 命中测试）。按钮是 position:fixed，页面滚过后 el.rect 给出的 y 远在视口外，点了等于没点。
        pt = self._view_point_on(btn)
        if pt is not None:
            self._click_point_cdp(pt[0], pt[1])
            # 盯「离开选座页了没有」，离开就返回。上界仍是原来那个固定 sleep 的 0.6 秒。
            if self._poll_until(lambda: not self._is_seat_page(),
                                timeout=0.6, interval=0.05):
                print("  ✓ 已点「立即购买」，进入订单确认\n")
                return True

        # 兜底：CDP 点不动就用 JS 点一次
        if self._js_click(btn):
            if self._poll_until(lambda: not self._is_seat_page(),
                                timeout=0.6, interval=0.05):
                print("  ✓ 已点「立即购买」（JS 兜底），进入订单确认\n")
                return True
        print("  ⚠ 点了「立即购买」但页面没走，可能是座位被占了或按钮还是灰的\n")
        return False

    # 从 H5 选座页 canvas 像素级找座位：座位图没被跨域污染，getImageData 读得到，找「非白底非浅灰」
    # 的点换算成页面坐标（实测整屏瞎点 40 次一个没中）。每点带颜色和 alpha：颜色认价位，
    # alpha 认高亮：真站灰化是 globalAlpha=0.2 重画（RGB 不变、alpha 掉到 ~51），只按颜色筛会点到灰座位。
    H5_SEAT_PIXEL_JS = r"""
    var cs = document.querySelectorAll('.aseat-layer-panel canvas');
    var pts = [];
    for (var k = 0; k < cs.length; k++) {
      var c = cs[k];
      var r = c.getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      if (r.bottom < 0 || r.top > innerHeight
          || r.right < 0 || r.left > innerWidth) continue;
      var ctx;
      try { ctx = c.getContext('2d', {willReadFrequently: true}); } catch (e) { continue; }
      var W = c.width, H = c.height, img;
      try { img = ctx.getImageData(0, 0, W, H); } catch (e) { continue; }
      var data = img.data, sx = r.width / W, sy = r.height / H, step = 16;
      for (var y = 0; y < H; y += step) {
        for (var x = 0; x < W; x += step) {
          var i = (y * W + x) * 4;
          if (data[i + 3] < 40) continue;                 // 透明
          var rr = data[i], gg = data[i + 1], bb = data[i + 2];
          var mx = Math.max(rr, gg, bb), mn = Math.min(rr, gg, bb);
          if (mx > 244 && (mx - mn) < 14) continue;       // 白底
          if ((mx - mn) < 12 && mx > 190) continue;       // 浅灰底
          var px = r.left + x * sx, py = r.top + y * sy;
          if (px < 1 || py < 1 || px > innerWidth - 1 || py > innerHeight - 1) continue;
          pts.push([Math.round(px), Math.round(py), rr, gg, bb, data[i + 3]]);
        }
      }
    }
    return pts;
    """

    def _h5_button_enabled(self):
        """底部「立即购买」是不是已经能点了（= 座位选好了）。

        没选座位时它的 class 带 `footer-bar__button--disable`。
        """
        btn = self._h5_confirm_button()
        if btn is None:
            return False
        cls = (btn.get_attribute('class') or '')
        return 'disable' not in cls.lower()

    def _h5_picked(self):
        """座位到底选上没有：￥N 变正、或按钮不再是灰的，任一成立就算选中。"""
        if self._h5_picked_amount() > 0:
            return True
        return self._h5_button_enabled()

    def _h5_seat_points(self, limit=400):
        """扫 canvas 像素，返回 (x, y, (r,g,b), alpha) 列表（已打乱）。

        颜色和 alpha 都跟着点一起返回，调用方要拿颜色认价位、
        拿 alpha 判断这个价位当前有没有被高亮。
        """
        try:
            pts = self.driver.execute_script(self.H5_SEAT_PIXEL_JS) or []
        except Exception:
            return []
        out = []
        for p in pts:
            if len(p) >= 6:
                out.append((p[0], p[1], (int(p[2]), int(p[3]), int(p[4])), int(p[5])))
            elif len(p) >= 5:
                out.append((p[0], p[1], (int(p[2]), int(p[3]), int(p[4])), None))
            elif len(p) >= 2:
                out.append((p[0], p[1], None, None))
        if len(out) > limit:
            out = random.sample(out, limit)
        random.shuffle(out)
        return out

    def _h5_ticket_count(self):
        """要买几张。quantity 优先；没传就按观演人人数。"""
        n = getattr(self.config, 'quantity', None)
        try:
            n = int(n) if n else 0
        except Exception:
            n = 0
        if n <= 0:
            n = len(getattr(self.config, 'users', None) or [])
        return max(1, n)

    def _h5_seat_probe(self):
        """一次拿回选座页的多个观测量：座位图有没有像素、价位列表有没有项。

        给「等页面准备好」用。判据里刻意不放「一定会先出现」的东西
        （比如底栏按钮），那会让等待形同虚设。
        """
        js = r"""
        var out = {pixels: 0, prices: 0, canvas: 0};
        var cs = document.querySelectorAll('.aseat-layer-panel canvas');
        out.canvas = cs.length;
        // 座位图可能很大（实测 2000x2000），每 0.2 秒全量 getImageData 太贵。
        // 先缩到 200x200 再读，只为回答「画上去没有」，不需要精度。
        var tmp = document.createElement('canvas');
        tmp.width = 200; tmp.height = 200;
        var tg = tmp.getContext('2d', {willReadFrequently: true});
        for (var k = 0; k < cs.length; k++) {
          var W = cs[k].width, H = cs[k].height;
          if (!W || !H) continue;
          var d;
          try {
            tg.clearRect(0, 0, 200, 200);
            tg.drawImage(cs[k], 0, 0, W, H, 0, 0, 200, 200);
            d = tg.getImageData(0, 0, 200, 200).data;
          } catch (e) { continue; }        // 画布被跨域污染时读不了
          for (var i = 3; i < d.length; i += 4) {
            if (d[i] > 40) { out.pixels++; }
          }
          if (out.pixels) break;
        }
        out.prices = document.querySelectorAll(
            '[class*="price-list"] [class*="price"], .price').length;
        return out;
        """
        try:
            return self.driver.execute_script(js) or {}
        except Exception:
            return {}

    def _h5_wait_seat_ready(self, timeout=8.0, price_grace=1.5):
        """等选座页「能开工」：座位图出像素（必要）+ 价位列表出现（有最好）。

        座位图和价位列表都是接口异步拉的。固定 sleep 代替不了这个：
        等短了会读到空表、误判成「页面上没有价位」；等长了每一轮都白等。
        所以等「座位图真的画出来了」就走，价位列表只再多给一小段宽限
        （这一场本来就没有价位列表时，不该把每轮都拖满超时）。
        """
        deadline = time.time() + timeout
        pixels_since = None
        while True:
            p = self._h5_seat_probe()
            if p.get('pixels') and p.get('prices'):
                return True
            if p.get('pixels'):
                if pixels_since is None:
                    pixels_since = time.time()
                if time.time() - pixels_since >= price_grace:
                    return True
            if time.time() >= deadline:
                self._note('  ⚠ 等了 %.0f 秒，选座页还没画出座位（探针=%r）' % (timeout, p))
                return False
            # 0.2 降到 0.12，不能再小：这一轮的探针要逐个 canvas 读像素（getImageData），本身不便宜。
            time.sleep(0.12)

    def pick_seat_on_h5(self, tries=30):
        """H5 选座页：按目标价位的颜色挑座位，点完再用金额复核。

        为什么不能「随便点一个非白非灰的像素」，
          真站选座页会把所有价位的座位都画在 canvas 上；你点某个价位项时，
          页面只是把其他价位 `grayPrice()`（降透明度）而已，它们还在那儿、
          也还是点得中。所以随机点就会买到别的价位的票，
          用户的原话是「选座的时候会选到其他价位的座位」。

        三道闸门，从便宜到可靠：
          1. 点一下目标价位项 → 页面自己 highlightPrice()，其他档灰化
          2. 只点「颜色 == 该价位色块颜色」的像素（色块图跟座位图同一个生成函数）
          3. 点完读底栏金额：累计必须正好等于 单价 × 已选数；
             不等就再点同一个位置取消，然后换下一个候选点
        """
        # 已经选好了（用户自己点的，或上一轮点中了）。确认前必须核金额：上一轮可能是「点了别的价位、
        # 取消没成功」的现场（2026-10-10 客户实测配置 80 元、重试几轮把 880 元的座确认了）。读不到金额保持原行为，对不上绝不提交。
        if self._h5_picked():
            want = self._price_int_list()
            price0 = want[0] if want else None
            amount0 = self._h5_picked_amount()
            count0 = self._h5_ticket_count()
            if price0 is not None and amount0 is not None and amount0 > 0 \
                    and amount0 != price0 * count0:
                self._note('  ⚠ 当前选中的座位合计 ￥%s，不是配置要的 ￥%d × %d 张'
                           ' —— 绝不替你提交别的价位。请手动取消这个座位'
                           '（再点它一下），或自己确认。'
                           % (amount0, price0, count0))
                return False
            return self._h5_confirm()

        count = self._h5_ticket_count()
        # 价位列表和座位图都是接口异步拉的，刚进页面可能还没有。读一次空表就下结论 = 「明明该有价位
        # 却当成没有」，所以等一个可观测信号（价位项出现 / canvas 出像素），不固定睡。
        self._h5_wait_seat_ready()

        items = self._h5_price_items()
        target = self._h5_match_price(items)
        price = None
        want_color = None

        if target:
            price = target['price']
            want_color = target['color']
            self._note('  · 目标价位 ￥%d（配置：%s）' % (
                price, '、'.join(str(p) for p in self._price_int_list()) or '未填'))
            if self._h5_highlight_price(target):
                self._note('  · 已点中该价位项，页面已把其它价位灰掉')
            else:
                self._note('  · 没能点中价位项（页面可能忽略这一点），'
                           '改为只按颜色和金额筛')
            if want_color:
                self._note('  · 该价位座位颜色 rgb%s' % (want_color,))
        elif items:
            # 页面有价位列表，但没有一档是配置要的。
            want = self._price_int_list()
            if not want:
                # 配置没填价位就没「要等的那一档」，必须退回老行为：不按价位筛，直接挑一个可点的座位，
                # 靠底栏金额复核把关。绝不能返回 False 让外层空转：2026-10-10 实测整条流水线卡死在选座页、订单永远提交不了还不报错（S21 钉着）。
                self._note('  · 配置里没填价位 —— 不按价位筛，直接挑一个可点的座位'
                           '（点完仍会核对底栏金额）')
            elif [it['price'] for it in items
                  if it['price'] in want and it['disabled']]:
                disabled_want = [it['price'] for it in items
                                 if it['price'] in want and it['disabled']]
                # 2026-10-10 客户实测：配置 80 元、80 缺货，老写法退到「按 80 核金额逐个点座位」，点到
                # 280/880 的座再取消；取消一失败，错的座留在页面上，下一轮入口直接把它确认了。客户要求很明确
                # 「只等这一档」，所以这里一个座位都不点，交给外层轮询去等。
                self._note('  ⚠ 配置要的 ￥%s 在页面上是缺货/不可点 —— '
                           '只等这一档，别的价位的座位一个不点；'
                           '每几秒重试，刷到就自动继续。'
                           % '、￥'.join(str(p) for p in disabled_want))
                return False
            else:
                self._note('  ⚠ 页面上的价位 %s 里没有配置要的 %s —— '
                           '只等配置的档，绝不点别的价位的座位，每几秒重试。'
                           % ([i['price'] for i in items], '、'.join(str(p) for p in want)))
                return False
        else:
            # 读不到价位列表（前端又改名之类），颜色没法筛了，但金额还能校验：配置里写着要买多少钱。
            want = self._price_int_list()
            if want:
                price = want[0]
            self._note('  · 这页读不到价位列表，只能靠金额复核（要 %s 元；'
                       '挑不到对的价位就如实报错，不会替你买别的价位）'
                       % (price if price is not None else '?'))

        pts = self._h5_seat_points()
        if not pts:
            print("  ⚠ 没从 canvas 里读出座位坐标，请自己点一个"
                  "（点完我会自动按「立即购买」）\n")
            return False

        # 先按 alpha 排掉「被灰化的其他价位」：真站灰化 = 同图 globalAlpha 0.2 重画，alpha≈51，正常/高亮是 255。不看颜色，只认「画得够实」。
        strong = [p for p in pts if (p[3] or 0) >= self.H5_SEAT_MIN_ALPHA]
        if strong and len(strong) < len(pts):
            self._note('  · %d 个候选点里有 %d 个是被灰化的（alpha 很低），已排除'
                       % (len(pts), len(pts) - len(strong)))
            pts = strong

        # 只留「颜色对得上目标价位」的候选点。颜色读不出来（want_color is None）时不筛，交给金额复核兜底。
        bad = self.H5_SEAT_BAD_COLOR
        if want_color:
            keep = [p for p in pts if self._color_close(p[2], want_color)]
            dropped = len(pts) - len(keep)
            if keep:
                self._note('  · 候选点 %d 个，其中颜色匹配目标价位的 %d 个'
                           '（滤掉 %d 个其他价位/其它颜色）'
                           % (len(pts), len(keep), dropped))
                pts = keep
            else:
                self._note('  ⚠ 没有一个候选点的颜色对得上 ￥%s（色块 rgb%s）——'
                           '改用金额复核逐个试' % (price, want_color))
        if price:
            pts = [p for p in pts if not self._color_close(p[2], bad, tol=12)] or pts

        print("  · 从 canvas 认出 %d 个座位候选点，开始试\n" % len(pts))

        picked = 0          # 已经选中的张数
        wrong = 0           # 碰到「价位不对」的次数
        for n, pt in enumerate(pts[:tries], 1):
            x, y = pt[0], pt[1]
            col = pt[2] if len(pt) > 2 else None
            if not self._is_seat_page():
                return True
            if picked >= count:
                break
            if wrong >= self.H5_SEAT_WRONG_PRICE_LIMIT:
                # 连着挑错这么多次，说明「颜色筛」没起作用（页面又改版之类）。继续点是拿真站当靶场、真的在下单，必须停。
                self._note('  ⚠ 连着 %d 次挑到的都不是 %s 元的座位，先停下'
                           '（可以自己在页面上点一个，我接着按「立即购买」）'
                           % (wrong, price))
                break

            before = self._h5_picked_amount()
            self._click_point_cdp(x, y)
            # 盯「底栏金额变了没有」，变了就说明点中了，立刻往下走。上界仍是原来那个固定 sleep 的 0.3 秒。
            self._poll_until(lambda: self._h5_picked_amount() != before,
                             timeout=0.3, interval=0.05)
            after = self._h5_picked_amount()

            if before < 0 or after < 0:
                # 底栏金额读不到，这一点的价位就核对不了，绝不能算选中：2026-10-10 客户实测里 880 元的座
                # 就是从这条路混进去的（老写法在这里 picked += 1 放行）。核对不了就不选，停下交人工。
                self._note('  ⚠ 底栏金额读不到，没法核对座位的价位 —— '
                           '不敢替你选，先停下。你可以自己在页面上点一个。')
                return False

            if after <= before:
                continue                    # 这一点没选中（空的 / 已被占 / 把自己取消了）

            delta = after - before
            if price is not None and delta != price:
                # 买错价位了，取消它，换个候选点重来
                wrong += 1
                self._note('  · 这一点落到了 %d 元的座位（要的是 %d 元），取消重选'
                           % (delta, price))
                self._click_point_cdp(x, y)
                # 盯「金额退回去了没有」（上界仍是原来的 0.25 秒）
                self._poll_until(lambda: self._h5_picked_amount() != after,
                                 timeout=0.25, interval=0.05)
                if self._h5_picked_amount() == before:
                    continue                # 成功取消
                self._note('  ⚠ 取消了但金额没退回去，不敢再乱点，交给人工')
                break

            picked += 1
            self._note('  ✓ 选中第 %d/%d 张（%s元%s）'
                       % (picked, count, delta,
                          '' if col is None else '，颜色rgb%d,%d,%d' % col))

        if picked < count:
            print("  ⚠ 只选出 %d/%d 张，请自己在页面上补一下"
                  "（选好我会自动按「立即购买」）\n" % (picked, count))

        if not self._h5_picked():
            print("  ⚠ 试了 %d 个点都没选中，请自己点一个"
                  "（点完我会自动按「立即购买」）\n" % tries)
            return False

        amount = self._h5_picked_amount()
        if price is not None and amount != price * count:
            self._note('  ⚠ 底栏合计 ￥%s 与「%d 元 × %d 张」对不上，'
                       '很可能选到了别的价位或者张数不对 —— 交给你确认'
                       % (amount, price, count))
            return False
        return self._h5_confirm()

    def _random_seat_element(self):
        """能拿到一个个座位元素时，直接随机抽一个（比点坐标准得多）。"""
        cands = []
        selectors = self.SEAT_ITEM_SELECTORS + ('[class*="seat"]',)
        skip_cls = ('seat-map', 'seatmap', 'seat-area', 'seat-container',
                    'seat-wrap', 'seat-wrapper', 'seat-layer', 'seat-box-wrap',
                    'seat-list', 'seat-legend', 'seat-zoom', 'seat-scale')
        skip_state = ('disabled', 'disable', 'sold', 'unavailable', 'taken',
                      'gray', 'grey', 'occupied', 'locked', 'none')
        for sel in selectors:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    cls = (el.get_attribute('class') or '').lower()
                    if any(k in cls for k in skip_cls):
                        continue
                    if any(k in cls for k in skip_state):
                        continue
                    r = el.rect      # 这里只用宽高，不参与坐标点击，文档/视口都一样
                    w = r.get('width') or 0
                    h = r.get('height') or 0
                    # 单个座位是个小方块，太大的是容器
                    if w < 4 or h < 4 or w > 60 or h > 60:
                        continue
                    cands.append(el)
                except Exception:
                    continue
            if cands:
                break
        return random.choice(cands) if cands else None

    # 单个座位元素可能用的选择器（顺序即优先级）
    SEAT_ITEM_SELECTORS = (
        '[data-seat-id]', '[data-seatid]',
        '[class*="seat-item"]', '[class*="seatItem"]',
        '[class*="seat"][class*="item"]', '[class*="seat"][class*="box"]',
        '[class*="seat"][class*="cell"]',
    )

    def _seat_map_box_from_seats(self):
        """用所有座位元素的外接矩形当选座区域。

        比猜容器的 class 稳得多，容器叫什么名字都无所谓。

        用 _viewport_rect（视口坐标）：这个框最后是拿去当坐标点的，
          混进文档坐标会在页面滚动后点到视口外面。
        """
        rects = []
        for sel in self.SEAT_ITEM_SELECTORS:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    r = self._viewport_rect(el)
                    if not r:
                        continue
                    w = r.get('w') or 0
                    h = r.get('h') or 0
                    if w < 4 or h < 4:
                        continue
                    rects.append((r['x'], r['y'], w, h))
                except Exception:
                    continue
            if len(rects) >= 4:
                break

        if len(rects) < 4:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        w, h = x1 - x0, y1 - y0
        if w < 120 or h < 120:
            return None
        return (x0, y0, w, h)

    def _find_seat_map_box(self):
        """找到选座区域，返回视口坐标 (x, y, w, h)；找不到返回 None。"""
        selectors = (
            ('css', 'canvas'),
            ('css', '[class*="seat-map"]'),
            ('css', '[class*="seatMap"]'),
            ('css', '[class*="seatmap"]'),
            ('css', '[id*="seat-map"]'),
            ('css', '[id*="seatMap"]'),
            ('css', '[class*="seat-area"]'),
            ('css', '[class*="seat-container"]'),
            ('css', '[class*="seat"][class*="map"]'),
            ('xpath', '//*[@id="app"]/div[2]/div[2]/div[1]/div[2]'),
        )
        for how, sel in selectors:
            try:
                if how == 'xpath':
                    els = self.driver.find_elements(By.XPATH, sel)
                else:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    r = self._viewport_rect(el)
                    if not r:
                        continue
                    w = r.get('w') or 0
                    h = r.get('h') or 0
                    if w >= 240 and h >= 180:
                        return (r['x'], r['y'], w, h)
                except Exception:
                    continue

        # 兜底：容器的类名不认识，就用座位元素自己算
        return self._seat_map_box_from_seats()

    def _click_at(self, x, y):
        """在视口坐标 (x, y) 点一下（合成完整鼠标事件序列，canvas 座位图也能收）。"""
        js = """
        var x = arguments[0], y = arguments[1];
        var el = document.elementFromPoint(x, y);
        if (!el) { return false; }
        var o = {bubbles: true, cancelable: true, view: window,
                 clientX: x, clientY: y, button: 0, buttons: 1};
        el.dispatchEvent(new MouseEvent('mousemove', o));
        el.dispatchEvent(new MouseEvent('mousedown', o));
        el.dispatchEvent(new MouseEvent('mouseup', o));
        el.dispatchEvent(new MouseEvent('click', o));
        return true;
        """
        try:
            if self.driver.execute_script(js, int(x), int(y)):
                return True
        except Exception:
            pass
        return False

    def _seat_taken_toast(self):
        """选到的位置被别人占了 / 不可选时，页面上的提示。"""
        for sel in ('[class*="toast"]', '[class*="message"]', '[class*="tips"]',
                    '[class*="tip"]', '[class*="notice"]'):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                except Exception:
                    continue
                t = self._safe_text(el)
                if t and any(w in t for w in self.SEAT_TAKEN_WORDS):
                    return True
        return False

    def _seat_confirm_button(self):
        """选座页上可点的「确认选座」按钮；没有可点的就返回 None。"""
        bad_cls = ('disabled', 'disable', 'gray', 'grey', 'forbidden', 'inactive')

        for txt in self.SEAT_CONFIRM_TEXTS:
            for tag in ('button', 'div', 'span', 'a'):
                try:
                    els = self.driver.find_elements(
                        By.XPATH, f"//{tag}[contains(normalize-space(text()), '{txt}')]")
                except Exception:
                    continue
                for el in els:
                    t = self._safe_text(el)
                    if not t or len(t) > 8:
                        continue
                    if not any(t == x or x in t for x in self.SEAT_CONFIRM_TEXTS):
                        continue
                    if self._is_pay_button_text(t):
                        continue
                    if not self._visible(el):
                        continue
                    cls = (el.get_attribute('class') or '').lower()
                    if any(k in cls for k in bad_cls):
                        continue
                    try:
                        if el.get_attribute('aria-disabled') == 'true':
                            continue
                    except Exception:
                        pass
                    return el

        # 文案抓不到时，按 class 抓
        for sel in ('button[class*="confirm"]', 'button[class*="submit"]',
                    '[class*="confirm-btn"]', '[class*="submit-btn"]',
                    '[class*="confirmBtn"]', '[class*="submitBtn"]'):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                cls = (el.get_attribute('class') or '').lower()
                if any(k in cls for k in bad_cls):
                    continue
                if not self._visible(el):
                    continue
                if self._is_pay_button_text(self._safe_text(el)):
                    continue
                return el
        return None

    def _seat_reaction(self):
        """点完座位后「页面有反应了」的判据（老 PC 选座页）。

        任一条成立就算有反应：
          · 弹了「已被选/不可选」提示   → 这一点落空了
          · 页面已经不是选座页         → 走成了
          · 确认按钮变可点             → 选中了

        用它替掉「点完固定睡 0.25 秒」：0.25 秒是按最坏情况付的钱，
          而这个页面通常在几十毫秒内就有反应。
        """
        if self._seat_taken_toast():
            return True
        if not self._is_seat_page():
            return True
        return self._seat_confirm_button() is not None

    def random_pick_seat(self, tries=25):
        """在选座页随机挑一个位置并点确认。

        成功（已确认或页面已跳走）返回 True，否则 False。
        随机只求「能按下去」，不挑好坏。
        """
        for attempt in range(tries):
            if not self._is_seat_page():
                return True

            clicked = False
            seat = self._random_seat_element()
            if seat is not None:
                clicked = self._js_click(seat)
            if not clicked:
                box = self._find_seat_map_box()
                if not box:
                    if attempt == 0:
                        print("  ⚠ 没找到选座区域，请手动点一个座位\n")
                    return False
                x, y, w, h = box
                px = random.randint(int(x + w * 0.10), int(x + w * 0.90))
                py = random.randint(int(y + h * 0.15), int(y + h * 0.85))
                clicked = self._click_at(px, py)
            if not clicked:
                time.sleep(0.2)
                continue

            # 盯「页面有反应了没有」，不固定睡 0.25 秒。上界仍是 0.25 秒，慢的时候一点都不比以前少等。
            self._poll_until(self._seat_reaction, timeout=0.25, interval=0.05)

            if self._seat_taken_toast():
                time.sleep(0.15)
                continue

            btn = self._seat_confirm_button()
            if btn is not None:
                # 确认按钮要是也被浮层盖着，先把盖它的清掉
                if self._remove_cover_js(btn):
                    btn = self._seat_confirm_button() or btn
                if self._js_click(btn):
                    return True

            if not self._is_seat_page():
                return True
            time.sleep(0.15)
        return False

    # 「只等配置的场次/票档」那一圈的节奏（秒）。整页重载是被盾的头号动作：
    # 10-10 从 10 秒压到 2 秒，10-11 客户账号被风控拦下后按 10-07 调研结论退到
    # 3 秒（社区共识的安全区间 1~3 秒）；同日用户明确要求整体再降一档，折中到 3.5 秒。
    # 重选是纯本地 DOM 操作、不发请求，可以比刷新快（2.0 秒）。
    # 这些值只是基准：实际等待会乘 _pace() 的 0.8~1.25 抖动，不再准点。
    # 账号里填了「刷新频率」时以它为准，但不低于 2.5 秒（MIN_REFRESH_SEC）。
    WAIT_RESELECT_SEC = 2.0
    WAIT_REFRESH_SEC = 3.5
    MIN_REFRESH_SEC = 2.5

    def _wait_refresh_sec(self):
        """这一圈多久整页刷新一次。

        * 账号里没填「刷新频率」→ `WAIT_REFRESH_SEC`（默认 3.5 秒）；
        * 填了 → 听它填的（下限 `MIN_REFRESH_SEC`，填 0.1 也按 2.5 秒走）。

        判据只能落在返回值上：`refresh_interval` 允许是字符串（从
          `accounts.json` 手改出来的），所以这里连同「能不能转成数」一起兜住，
          绝不把异常漏给那个要跑几个小时的循环。
        """
        base = getattr(self.config, 'refresh_interval', None)
        if base in (None, ''):
            return self.WAIT_REFRESH_SEC
        try:
            base = float(base)
        except (TypeError, ValueError):
            return self.WAIT_REFRESH_SEC
        return max(self.MIN_REFRESH_SEC, base)

    def _wait_for_configured_option(self, is_mobile):
        """详情页没按配置选中的话，只等配置的那一份，绝不买别的。

        客户的原话：「我就想等待刷 80 这个票怎么操作？」，这就是那个「等」。
        节奏（跟 if_listen 的刷新等待同一个量级）：
          · 每 `WAIT_RESELECT_SEC` 秒左右重选一次城市/场次/票价（都是本地 DOM
            操作，不向大麦发请求）
          · 每 `_wait_refresh_sec()` 秒左右整页刷新一次，库存/开票状态要刷新
            才会更新。这个秒数默认 3.5 秒；账号里填了「刷新频率」就听它的。
          · 两类等待都乘 _pace() 的 0.8~1.25 抖动：这一圈可能跑几个小时，
            准点的节奏本身就是机器特征。刷新按「下次到点时间」排程，
            不在循环里硬等。
        重选刻意不含数量：数量不依赖库存，而且反复点加号会把张数越点越多；
        等到了之后在这里补选一次数量，再回到正常购票流程。
        """
        refresh_sec = self._wait_refresh_sec()
        self._note('  ⚠ 场次/票档没能按配置选中 —— 绝不替你买页面默认选中的那一份。')
        self._note('     转入「只等配置的场次/票档」：每 %.1f 秒左右重选、'
                   '每 %.1f 秒左右刷新，刷到就自动继续；'
                   '不想等就停掉任务或关掉浏览器。'
                   % (self.WAIT_RESELECT_SEC, refresh_sec))
        next_refresh = time.time() + self._pace(refresh_sec)
        last_err_note = 0.0
        while True:
            time.sleep(self._pace(self.WAIT_RESELECT_SEC))
            try:
                if self._select_details_core(mobile=is_mobile):
                    self._note('  ✓ 等到了配置的场次/票档，继续购票流程')
                    if len(self.config.users) > 1:
                        if is_mobile:
                            self.select_quantity_on_page()
                        else:
                            self._select_quantity_on_page(platform="PC端")
                    return
            except Exception as e:
                # 出错如实说，但不能每拍一行：这段可能跑几小时，刷屏会把有用的记录挤掉。30 秒最多一条。
                now = time.time()
                if now - last_err_note >= 30.0:
                    last_err_note = now
                    self._note('  等档重选出错（继续等）：{}'.format(e))
            if time.time() >= next_refresh:
                next_refresh = time.time() + self._pace(refresh_sec)
                try:
                    self.driver.get(self.config.target_url)
                except Exception:
                    pass

    def choose_ticket(self):
        """
        :return: 选票
        """
        if self.status != 2:
            self._note('  （内部状态不是「待选票」，跳过详情页选择这一步）')
            return

        print("*******************************\n")
        print("***开始在详情页选择***\n")

        is_mobile = 'm.damai.cn' in self.driver.current_url

        # 详情页完成所有选择：城市、场次、票价、数量。「选成」是闸门：场次/票档没按配置选中时绝不点购买，
        # 页面上还停着默认选中的那份。2026-10-10 客户实测两单：要 27 号买成默认 26 号；要 80 元缺货、
        # 把默认 280 元买了。没选成就转入「只等配置的那一份」。
        if is_mobile:
            print("检测到移动端页面\n")
            selected = self.select_details_page_mobile()
        else:
            print("检测到PC端页面\n")
            selected = self.select_details_page_pc()
        if not selected:
            self._wait_for_configured_option(is_mobile)

        print("*******************************\n")
        print("***开始轮询检测预订按钮***\n")

        clicked_booking = False
        clicked_at = 0.0
        committed = False  # 防止同一个订单被提交两次
        self._scan_layer_dismissed = False

        # 这个循环的三处提速：① 页面类型判定（一轮 5~9 次往返）缓存，只在地址/标题变了或超 1.5 秒时重做；
        # ② 每轮状态一次往返问完（扫码浮层 / 购买按钮 / 链接文案）；③ 等待间隔 0.2 降到 0.08 秒。
        # ③ 全是本地 DOM 读，跟风控无关；间隔一改，按轮数计的阈值必须换成按时间计（见 idle_since），否则是刷新变勤而不是提速。
        seen_sig = self.STALE_SIG   # 上一轮的 (url, title)；哨兵值 = 强制重判
        judged_at = 0.0             # 上次做结构判定的时刻
        on_order_page = False
        on_seat_page = False
        idle_since = 0.0            # 「有按钮但点不动」是从什么时候开始的
        # 原来是「连续 20 轮」，快速模式 0.2 秒一轮、正常模式 0.5 秒一轮
        idle_limit = 4.0 if self.config.fast_mode else 10.0

        while True:
            # 浏览器被关掉（人或大麦那边都可能）就到此为止。不认出来，下面每步都拿到空值，「抢票流程结束」会一轮轮空转。
            if self.browser_closed:
                # 落地前再确认一次：偶尔一次抖动也会带上「connection refused」，误判成「浏览器没了」等于白扔一轮；真没了这次照样失败。
                try:
                    self.driver.current_url
                    self.browser_closed = False
                except Exception:
                    print('\n  ⚠ 浏览器窗口已经被关掉了，这一轮到此为止。\n')
                    return
            now = time.time()
            snap = self._page_lite()
            sig = (snap.get('url') or '', snap.get('title') or '')
            if sig != seen_sig or now - judged_at >= 1.5:
                seen_sig = sig
                judged_at = now
                # 判定里带上 _is_h5_order_page()：新版 H5 订单页 title 也是「确认购买」（_is_order_confirmation_page
                # 靠标题认），标题一改就全断；多一条按 URL / 结构的判据，多一层保险。
                on_order_page = (self._is_order_confirmation_page()
                                 or self._is_h5_order_page())
                on_seat_page = self._is_seat_page()
            if on_order_page:
                break

            # 详情页这一轮的状态：一次往返拿全
            st = self._detail_state() or {}
            buy_button = st.get('buy')
            by_link = st.get('link')

            # 每轮先看有没有「手机扫码购买更便捷」浮层。它一出现购买按钮就从 DOM 整块消失，后面的判断全走偏；
            # 点完购买后的头 20 秒循环只在 sleep，不在循环开头处理就永远处理不到。
            if st.get('scan'):
                self.dismiss_scan_layer()
                # 等浮层真的消失（按钮才会回来），不固定睡。上界仍是原来的 0.2 / 0.4 秒。
                if self._poll_until(
                        lambda: not (self._detail_state() or {}).get('scan'),
                        timeout=0.2 if self.config.fast_mode else 0.4,
                        interval=0.08):
                    st = self._detail_state() or {}
                    buy_button = st.get('buy')
                    by_link = st.get('link')
                # 浮层整块换掉 DOM，缓存的页面类型判定作废
                seen_sig = self.STALE_SIG

            # 浮层把上一次「点了购买」作废了（按钮被换掉）：重置状态让循环重新找按钮点，别干等 20 秒超时。
            if self._scan_layer_dismissed:
                self._scan_layer_dismissed = False
                clicked_booking = False
                idle_since = 0.0

            if clicked_booking:
                if on_order_page:
                    print('  ✓ 页面已跳转到订单确认页\n')
                    break
                elif on_seat_page:
                    print('  ✓ 页面已跳转到选座购买页\n')
                    break
                elif time.time() - clicked_at > 20:
                    # 点了按钮但页面迟迟不跳，多半是那一下没点中，重来
                    print('  ⚠ 点了购买但页面一直没跳转，重新尝试\n')
                    clicked_booking = False
                else:
                    # 盯「地址/标题变了没有」，变了立刻回头重判（上界仍是原来的 0.2 / 0.5 秒）
                    self._poll_until(lambda: self._nav_changed(sig),
                                     timeout=0.2 if self.config.fast_mode else 0.5,
                                     interval=0.08)
                    continue

            action_taken = False
            try:
                if buy_button == "提交缺货登记":
                    self.status = 2
                    self.driver.get(self.config.target_url)
                    print('***抢票未开始，刷新等待开始***\n')
                    seen_sig = self.STALE_SIG
                    continue

                clickable_actions = [
                    ("不选座购票", buy_button, 'buy__button__text'),
                    ("不选座购买", buy_button, 'buy__button__text'),
                    ("立即预订", buy_button, 'buy__button__text'),
                    ("立即购买", buy_button, 'buy__button__text'),
                    ("缺货登记", buy_button, 'buy__button__text', lambda: self.config.if_listen),
                    ("选座购买", buy_button, 'buy__button__text'),
                ]

                # 地址用本轮快照里那份（已经拿过了），不再单独问一次
                url_before = snap.get('url') or self.driver.current_url

                # ① 优先走「不选座购票」：跳过选座直接进订单确认页。注意有的页面这里只是切换 tab，点了不跳转，
                #    所以只有观察到页面真变了才算数，否则退到 ② 再点一次主按钮。
                if not clicked_booking and self._should_try_no_seat(buy_button):
                    if self.click_no_seat_buy():
                        # 等页面动（上界仍是原来的 0.3 / 0.7 秒）
                        self._poll_until(lambda: self._nav_changed(sig),
                                         timeout=0.3 if self.config.fast_mode else 0.7,
                                         interval=0.08)
                        # 购买按钮点完，扫码浮层通常紧跟着弹出来，先关掉
                        self.dismiss_obstructions(rounds=2)
                        if (self.driver.current_url != url_before
                                or self._is_order_confirmation_page()
                                or self._is_seat_page()):
                            self.status = 3
                            clicked_booking = True
                            clicked_at = time.time()
                            action_taken = True
                            print('  等待页面跳转...\n')

                # ② 常规购买按钮
                if not action_taken:
                    for action in clickable_actions:
                        text, current_text, locator, *condition = action
                        if current_text == text and (not condition or condition[0]()):
                            print(f'✓ 检测到按钮: {text}')
                            self._click_element_safe(locator, By.CLASS_NAME)
                            self.status = 3
                            clicked_booking = True
                            clicked_at = time.time()
                            print('  等待页面跳转...\n')
                            # 等页面动（上界仍是原来的 0.15 / 0.4 秒）
                            self._poll_until(lambda: self._nav_changed(sig),
                                             timeout=0.15 if self.config.fast_mode else 0.4,
                                             interval=0.08)
                            self.dismiss_obstructions(rounds=2)
                            action_taken = True
                            break

                if not action_taken and self._is_reject_scan_text(by_link):
                    print(f'✓ 检测到链接: {by_link}')
                    self._click_element_safe('buy-link', By.CLASS_NAME)
                    self.status = 3
                    clicked_booking = True
                    clicked_at = time.time()
                    print('  等待页面跳转...\n')
                    action_taken = True

            except Exception as e:
                print(e)

            # 风控页：脚本不做任何规避，直接把话说清楚并停手
            if self._is_risk_page():
                print('\n  ⚠ 大麦返回「访问异常」（风控提示页），这次请求被拦了。')
                print('    本脚本按约定不做任何风控规避（不换 IP、不伪造指纹、不解验证码）。')
                print('    建议：换个网络重试 / 等十几分钟再试 / 改手动操作。\n')
                return

            # 点过东西，点击可能同步就完成了导航（本地页面秒跳，真站开票瞬间也可能很快），所以立刻重判一次页面类型，别拿点之前的缓存去分派。
            if action_taken or clicked_booking:
                snap = self._page_lite()
                sig = (snap.get('url') or '', snap.get('title') or '')
                seen_sig = sig
                judged_at = time.time()
                on_order_page = (self._is_order_confirmation_page()
                                 or self._is_h5_order_page())
                on_seat_page = self._is_seat_page()

            # 检查页面类型（用上面缓存 / 刚重判过的结论，不再重复问浏览器）
            if on_seat_page:
                self.dismiss_obstructions(rounds=2, verbose=False)
                self.choice_seat()
                # 这里以前只测 _is_order_confirmation_page()：选完座落到新版 H5 订单页时它靠标题认，
                # 标题一变这步就被跳过，订单再也不提交。选座走完页面是真会变的，必须重新判定，不吃缓存。
                if not committed and (self._is_order_confirmation_page()
                                      or self._is_h5_order_page()):
                    self._note('***选座完成，进入订单确认页***')
                    committed = True
                    self.commit_order()
            elif on_order_page:
                if not committed:
                    self._note('***进入订单确认页***')
                    committed = True
                    self.commit_order()
            else:
                if clicked_booking:
                    # 刚点完购买、页面还在跳转，别急着判成「抢票未开始」把页面刷新掉，那等于白点。
                    self._poll_until(lambda: self._nav_changed(sig),
                                     timeout=0.2 if self.config.fast_mode else 0.5,
                                     interval=0.08)
                    continue

                # 页面上明明有可点的购买按钮（比如刚把扫码浮层点掉、按钮回来了），那就该我们出手，不是「还没开票」。
                # 直接刷新会把这次机会刷掉，还会被大麦断连。
                if self._is_buy_button_text(buy_button):
                    now2 = time.time()
                    if not idle_since:
                        idle_since = now2
                    if now2 - idle_since < idle_limit:
                        self._poll_until(lambda: self._nav_changed(sig),
                                         timeout=0.2 if self.config.fast_mode else 0.5,
                                         interval=0.08)
                        continue
                    idle_since = 0.0

                idle_since = 0.0
                print('***抢票未开始，刷新等待开始***\n')
                time.sleep(self._refresh_wait())
                self.driver.refresh()
                # 整页重载了：缓存的页面类型判定作废，下一轮重新判
                seen_sig = self.STALE_SIG

        # 循环退出后补齐后续动作，两条路都要管：还停在选座页（主循环看见选座页就 break，选座故意
        # 留在循环外做），或者已经在订单确认页。原来这里带了个 self.status == 3 的门槛，而走选座分支
        # 时 status 一直是 2，整段下单逻辑被静默跳过：观演人没选、订单也没提交。
        if not committed and self._is_seat_page():
            self._note('***已进入选座页，开始自动选座***')
            self.choice_seat()

        if not committed and (self._is_order_confirmation_page() or self._is_h5_order_page()):
            self._note('***进入订单确认页***')
            committed = True
            self.commit_order()
        elif not committed:
            self._note('***这一轮没走到订单确认页，抢票流程结束***')

    def choice_seat(self):
        """选座页：新旧两种形态都要能走。

        · 老版 PC 页：座位是 DOM 元素 → random_pick_seat()
        · 新版 H5 页（kylin.html）：座位画在 canvas 上 → pick_seat_on_h5()

        默认自动随机选一个并确认。config.random_seat = False 时改回手动：
        脚本停在选座页等你点，你一点好座位，它自动帮你按「立即购买」。

        自动模式有失败上限（`AUTO_FAIL_LIMIT`）。选座页自己不会刷新，
          在原地空转一小时也等不来库存；2026-10-10 实测过最坏的一种：
          「配置里没填价位」让 pick_seat_on_h5 永远返回 False，于是这里的
          while 无限空转、日志每 0.3 秒刷两条警告，订单永远提交不了、
          而且一声不响（回归用例：check_order_flow 的 S21）。
          到了上限就停下自动选座、把浏览器交给用户手动，这比闷头空转诚实。
          手动模式（等你点座位）不受这个上限约束。
        """
        AUTO_FAIL_LIMIT = 40                      # 40 × 0.3 秒 ≈ 12 秒
        auto_fails = 0
        last_complain = 0.0

        auto = getattr(self.config, 'random_seat', True)
        h5 = self._is_h5_seat_page()
        if h5:
            print("  检测到新版 H5 选座页（座位画在 canvas 上，按坐标点）\n")

        while self._is_seat_page():
            # 风控页顶着选座页的地址和标题渲染，_is_seat_page() 仍为真。不先认它，这一圈会把「被拦了」当成
            # 「这一档一直没票」空转到上限，日志把人引向错误方向。真站被拦时地址若会变，多这一问也没副作用。
            if self._is_risk_page():
                print('\n  ⚠ 大麦返回风控提示页 —— 这次请求被拦了，停止选座。')
                print('    本脚本不做风控规避（不换 IP、不伪造指纹、不解验证码）。')
                print('    建议：等十几分钟再试 / 换个网络 / 改手动操作。\n')
                return False

            self.dismiss_obstructions(rounds=2, verbose=False)

            if auto:
                ok = self.pick_seat_on_h5() if h5 else self.random_pick_seat()
                if ok:
                    print('  ✓ 已随机选中座位并提交\n')
                    auto_fails = 0
                    # 等页面跳走：一跳就立刻走，最多等 5 秒（上界一点没缩）
                    self._poll_until(lambda: not self._is_seat_page(),
                                     timeout=5.0, interval=0.1)
                    continue
                auto_fails += 1
                if auto_fails >= AUTO_FAIL_LIMIT:
                    self._note('  ⚠ 在选座页连着 %d 次没能自动选中（这一档多半一直'
                               '没票）—— 停止自动选座，浏览器留给你手动点。'
                               '想让它继续自动抢，停掉这个任务重新启动一次。'
                               % auto_fails)
                    return False
                # 日志节流：这段 0.3 秒一拍，不节流会刷爆、挤掉有用的记录（5 秒最多一条）。
                now = time.time()
                if now - last_complain >= 5.0:
                    last_complain = now
                    print('  ⚠ 这一轮自动选座没成功，继续重试…'
                          '（你也可以自己动手点一个）')
            else:
                # 手动模式：等用户选好座位，再帮按「立即购买」
                if h5 and self._h5_picked() and self._h5_confirm():
                    # 同上：一跳就走，最多等 5 秒
                    self._poll_until(lambda: not self._is_seat_page(),
                                     timeout=5.0, interval=0.1)
                    continue
                now = time.time()
                if now - last_complain >= 5.0:
                    last_complain = now
                    print('请快速选择您的座位！！！')
            # 这一圈在等用户手动点座位，可能一等很久；固定一拍也是机器特征
            time.sleep(self._pace(0.5))

    def _legacy_choice_seat_manual(self):
        """原来的手动选座逻辑，留着备查（不再被调用）。"""
        while self.driver.title == '选座购买':
            while self.is_element_exist('//*[@id="app"]/div[2]/div[2]/div[1]/div[2]/img'):
                # 座位手动选择 选中座位之后//*[@id="app"]/div[2]/div[2]/div[1]/div[2]/img 就会消失
                print('请快速选择您的座位！！！')
            # 消失之后就会出现 //*[@id="app"]/div[2]/div[2]/div[2]/div
            while self.is_element_exist('//*[@id="app"]/div[2]/div[2]/div[2]/div'):
                self.driver.find_element(value='//*[@id="app"]/div[2]/div[2]/div[2]/button', by=By.XPATH).click()

    @staticmethod
    def _norm_ws(val):
        """把连续空白压成一个空格、去掉首尾。

        真站（Vue 渲染）的文案里，空格是排版留下的：
            页面 DOM：`2026.12.27 19:30  下本`（「19:30」和「下本」之间两个空格）
            客户配置：`2026.12.27 19:30 下本`（一个空格）
          而 XPath 的 `contains()` / Python 的 `in` 都是字面比较、不归一化空白
，于是「页面上明明写着，就是匹配不上」。凡是要比文本的地方，
          两边都先过这里。（2026-10-10 客户实测：场次一个都选不上。）
        """
        return re.sub(r'\s+', ' ', str(val if val is not None else '')).strip()

    @staticmethod
    def _xpath_literal(s):
        """把任意文本塞进 XPath 字面量里（含单/双引号也不炸）。

        老写法是 `f"//*[contains(text(), '{s}')]"`，演出名里只要有一个
        单引号（`《XX's》`），整条 XPath 就是语法错误，抛出来被上层 except
        一吞，表现成「页面上有这一项却怎么都选不中」。
        """
        s = str(s)
        if "'" not in s:
            return "'" + s + "'"
        if '"' not in s:
            return '"' + s + '"'
        return "concat('" + s.replace("'", "', \"'\", '") + "')"

    @staticmethod
    def _date_tokens(val):
        """从「2026-12-27」「12-27」「12月27日」「2026.12.27 14:00」里取「月,日」。

        返回 (月, 日)；不是日期写法（比如票价 380、人名）返回 None。
        年份和开演时间刻意忽略：配置里写了年份、页面上通常没有；
        场次卡片后面跟着「周六 14:00」也不影响。数字对取「第一个 ≤12 的
        数 + 它后面那个 ≤31 的数」，2026 开头的四位年份自然被跳过。
        """
        nums = [int(x) for x in re.findall(r'\d+', str(val or ''))]
        for i in range(len(nums) - 1):
            a, b = nums[i], nums[i + 1]
            if 1 <= a <= 12 and 1 <= b <= 31:
                return (a, b)
        return None

    @staticmethod
    def _num_tokens(val):
        """取出文本里所有「独立的数」，例：「280 元」得到 [280.0]、「12月27日」得到 [12,27]。

        为什么数字不能用子串判：配置写 80、页面上是「280 元」，
          `'80' in '280 元'` 是 True，于是该跳过的「80 元 缺货」被跳过后，
          反而把 280 当成了「选上了 80」，买贵一档（2026-10-10 客户实测：
          要 80、80 缺货，成交价成了 280）。数字必须按「独立的数」比。
          千分位（1,280）先去逗号再取。
        """
        out = []
        for x in re.findall(r'\d+(?:\.\d+)?', str(val or '').replace(',', '')):
            try:
                out.append(float(x))
            except ValueError:
                pass
        return out

    @classmethod
    def _nums_covered(cls, cfg, txt):
        """cfg 里的每个数，都得在 txt 里作为「独立的数」出现。

        子串匹配只有在没有把数字切开时才算命中。配置写 80.0元、页面上是
        280.0元 时，`'80.0元' in '280.0元'` 是 True，但那个 80 是从 280 里切
        出来的半个数，和 80.0元 不是同一档。2026-10-11 客户报的「选 80 元跳
        到 280」就是这一条。
        """
        nums = cls._num_tokens(cfg)
        if not nums:
            return True
        have = cls._num_tokens(txt)
        return all(n in have for n in nums)

    # 「价钱写法」允许的字符：数字、千分位逗号、点、空白，以及各种货币符号 / 「元」。
    _BARE_PRICE_NOISE_RE = re.compile(r'[0-9.,\s\uFFE5\u00A5$\u5143]')

    @classmethod
    def _is_bare_price(cls, val):
        """这个值是不是「就是一个价」：只剩数字和价钱写法，没有别的字。

        「188元（站席）」不是：括号和「站席」是带区分度的文字，去了就丢了信息。
        """
        return not cls._BARE_PRICE_NOISE_RE.sub('', str(val or '')).strip()

    @classmethod
    def _norm_num_text(cls, val):
        """数字写法归一：去千分位逗号、去掉整数后面的 .0。

        页面写「VIP 1080元」、配置写「VIP 1,080元」，或页面写「80元」、配置写
        「80.0元」时，两边字面永远对不上，归一之后才对得上。2026-10-11 独立复核
        的这两条就是靠它。日期（2026.12.27）、时刻（19:30）里没有 .0，不受影响。
        """
        s = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', str(val or ''))
        return re.sub(r'(?<=\d)\.0+(?=\D|$)', '', s)

    @classmethod
    def _option_text_match(cls, config_value, elem_text):
        """配置值和页面文案对不对得上：数字按独立的数、日期归一化、其余子串。

        · 纯数字（票价）→ 只认「独立的数」：`80` 不命中 `280 元`（见 _num_tokens）。
        · 日期 → 归一化到「月/日」比：配置 2026-12-27、页面「12月27日」也算命中。
          不归一化的话子串永远对不上，而后果是「场次没选成却继续往下买」，
          把页面默认选中的那天买了（2026-10-10 客户实测：配 27 号买成 26 号）。
        · 日期对上之后，两边都写了时刻就以时刻为准：同一天有好几场
          （14:00 上本 / 19:30 下本），只比月/日会把 14:00 当成「要的那一场」
          （客户实测「要 19:30 买成 14:00」）。
        · 其余（城市名等）→ 子串。
        · 两边都先归一化空白（见 _norm_ws）：真站写「19:30␣␣下本」（两个空格）、
          客户写「19:30 下本」（一个空格），字面比永远对不上。
        """
        cfg = cls._norm_ws(config_value)
        txt = cls._norm_ws(elem_text)
        if not cfg or not txt:
            return False
        if re.fullmatch(r'\d+(?:\.\d+)?', cfg):
            if float(cfg) in cls._num_tokens(txt):
                return True
        elif cfg in txt and cls._nums_covered(cfg, txt):
            return True
        a = cls._date_tokens(cfg)
        if a is None or a != cls._date_tokens(txt):
            return False
        # 日期对上还不够：同一天常有多场（14:00 上本 / 19:30 下本），只比月/日会把「19:30 下本」和「14:00 上本」
        # 判成同一场（2026-10-10 客户实测「要 19:30 买成 14:00」）。两边都写了时刻就以时刻为准，缺时刻的
        # 不参与判定（宁可少卡一层，也别因为没写时间就选不上）。
        ct, et = cls._time_tokens(cfg), cls._time_tokens(txt)
        if ct and et:
            return cls._same_time(ct, et)
        return True

    # 时刻的几种写法：「19:30」「19：30」「19点30」「19点30分」「19点」。
    _TIME_RE = re.compile(r'(\d{1,2})\s*[:：]\s*(\d{2})'
                          r'|(\d{1,2})\s*点\s*(\d{1,2})?\s*分?')

    @classmethod
    def _time_tokens(cls, val):
        """文本里的时刻集合，例：「19:30 下本」得到 {(19,30)}、「19点」得到 {(19,None)}。"""
        out = set()
        for m in cls._TIME_RE.finditer(str(val or '')):
            h = m.group(1) if m.group(1) is not None else m.group(3)
            mi = m.group(2) if m.group(2) is not None else m.group(4)
            if h is None:
                continue
            hh = int(h)
            if hh > 23:
                continue
            out.add((hh, int(mi) if mi else None))
        return out

    @classmethod
    def _same_time(cls, a, b):
        """两组时刻有没有交集。分钟缺省（「19点」）按小时比。"""
        for h1, m1 in a:
            for h2, m2 in b:
                if h1 != h2:
                    continue
                if m1 is None or m2 is None or m1 == m2:
                    return True
        return False

    @classmethod
    def _date_search_candidates(cls, value):
        """把配置里的日期展开成页面上可能出现的几种写法（去重、保序）。

        给「通用文本搜索」那条路用，那条路是 XPath contains(原文)，
        只能拿字面去碰，所以先把可能的写法都列出来挨个试。
        """
        cfg = str(value or '').strip()
        out = [cfg] if cfg else []
        t = cls._date_tokens(cfg)
        if t:
            m, d = t
            for s in ('%d月%d日' % (m, d), '%d月%d' % (m, d),
                      '%d-%02d' % (m, d), '%d.%d' % (m, d)):
                if s not in out:
                    out.append(s)
        return out

    # 「2026.12.27 / 2026-12-27 / 2026年12月27日」这种带年份的写法，以及「12月27日 / 12月27」这种不带年份的。
    _DATE_IN_TEXT_RE = re.compile(
        r'\d{4}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*日?')
    _MD_IN_TEXT_RE = re.compile(r'\d{1,2}\s*月\s*\d{1,2}\s*日?')

    @classmethod
    def _strip_date_part(cls, value):
        """把「2026.12.27 19:30 下本」里的日期部分去掉，得到「19:30 下本」。

        给「日历日期」那种页面兜底用：日历点完之后场次列表不再写日期了，
          用户抄来的原文带着日期就永远匹配不上。去掉日期剩下的部分
          （时间 + 场次名）在同一天里足够精确。
        """
        s = str(value or '')
        out = cls._DATE_IN_TEXT_RE.sub(' ', s)
        if out == s:
            out = cls._MD_IN_TEXT_RE.sub(' ', out)
        return re.sub(r'\s+', ' ', out).strip()

    @classmethod
    def _price_match_values(cls, prices):
        """票档配置转成真正拿去匹配的写法列表（保序、去重）。

        每个配置项按优先级依次产候选：

        1) 原样。用户抄的就是页面原文时，这一条最准。
        2) 数字写法归一后的写法（见 _norm_num_text）。页面写「80元」、配置写
           「80.0元」，或页面写「VIP 1080元」、配置写「VIP 1,080元」时，
           字面对不上，归一之后才对得上（2026-10-11 独立复核的两条）。
        3) 只有配置项本身就是「一个价」（见 _is_bare_price）且只有一个数时，
           才再展开出裸数字。页面写「80」不带单位时，得拿数字去碰。

        第 3 条带条件是因为：原文里的文字有区分度信息，丢掉文字只剩数字，就会
           把同价不同区的另一档当成它 —— 2026-10-11 独立复核拿「全价票188元（站席）」
           对页面「全价票188元（坐席）」，展开出的裸数字 188 走纯数字分支点中了坐席。
           带文字的值不展开，只留前两条。两个数（"188元（含2张）"）语义不明，也不展开。
        """
        out = []
        for p in (prices or []):
            s = str(p or '').strip()
            if not s:
                continue
            if s not in out:
                out.append(s)
            normed = cls._norm_num_text(s)
            if normed != s and normed not in out:
                out.append(normed)
            toks = cls._num_tokens(s)
            if len(toks) == 1 and cls._is_bare_price(s):
                cand = ('%g' % toks[0]) if toks[0] != int(toks[0]) else str(int(toks[0]))
                if cand not in out:
                    out.append(cand)
        return out

    def _select_option_by_config(self, config_list, element_list, skip_keywords=None):
        """根据配置列表选择选项

        Args:
            config_list: 配置的选项列表（如日期、价格列表）
            element_list: 页面上的元素列表
            skip_keywords: 需要跳过的关键词列表（如['无票', '售罄']）

        Returns:
            bool: 是否成功选择
        """
        if not config_list or not element_list:
            return False

        skip_keywords = skip_keywords or ['无票', '缺货']

        wait_time = 0.2 if self.config.fast_mode else 0.5
        # 点完这一档要隔一会儿再点下一档；间隔带抖动，不固定拍点

        # 一次往返读回所有选项文本，再本地做「配置×选项」比对。老写法把 el.text 写在双层循环最里层，
        # 每个组合单独问一次浏览器，3 场次 × 8 选项 = 24 次往返，而它们只是拿来比字符串。
        elements = list(element_list)
        texts = self._batch_texts(elements)

        for config_value in config_list:
            for i, element in enumerate(elements):
                try:
                    elem_text = texts[i] or ''
                    if (self._option_text_match(config_value, elem_text)
                            and not any(kw in elem_text for kw in skip_keywords)):
                        element.click()
                        time.sleep(self._pace(wait_time))
                        return True
                except Exception:
                    continue
        return False

    def choice_order(self):
        """选择订单：包括场次、票档、人数

        场次或票档没按配置选中时绝不点「确定」，这个弹层里页面
          自己默认选中着一份（别的场次/票档），点确定就是把那份买走。
          2026-10-10 客户实测：要 80 元、80 缺货，老写法把页面默认的
          280 元直接提交了。没选成就放弃这一轮，返回 False（调用方重试）。
        """
        self.driver.find_element(value='buy__button__text', by=By.CLASS_NAME).click()
        time.sleep(0.2)
        print("***选定场次***\n")

        dates_ok = True
        if self.driver.find_elements(value='sku-times-card', by=By.CLASS_NAME) and self.config.dates:
            order_name_element_list = self.driver.find_element(
                value='sku-times-card', by=By.CLASS_NAME
            ).find_elements(value='bui-dm-sku-card-item', by=By.CLASS_NAME)
            dates_ok = self._select_option_by_config(self.config.dates, order_name_element_list)
            if dates_ok:
                print("  ✓ 场次选择成功")

        print("***选定票档***\n")
        prices_ok = True
        if self.driver.find_elements(value='sku-tickets-card', by=By.CLASS_NAME) and self.config.prices:
            sku_name_element_list = self.driver.find_elements(value='item-content', by=By.CLASS_NAME)
            prices_ok = self._select_option_by_config(
                self._price_match_values(self.config.prices),
                sku_name_element_list, ['缺', '售罄'])
            if prices_ok:
                print("  ✓ 票档选择成功")

        if not (dates_ok and prices_ok):
            print("  ⚠ 场次/票档没按配置选中 —— 绝不点「确定」去买页面默认的那一份，本轮放弃（会自动重试）")
            return False

        print("***选定人数***\n")
        if self.driver.find_elements(value='bui-dm-sku-counter', by=By.CLASS_NAME):
            for i in range(len(self.config.users) - 1):
                self.driver.execute_script(
                    'document.getElementsByClassName("number-edit-bg")[1].click();')
            print(f"  ✓ 已选择 {len(self.config.users)} 张票")

        self.driver.find_element(value='bui-btn-contained', by=By.CLASS_NAME).click()

    def _scan_page_info(self):
        """扫描页面基本信息用于调试"""
        print("  📄 页面信息:")
        print(f"    URL: {self.driver.current_url}")
        print(f"    标题: {self.driver.title}\n")

    def _scan_page_text(self):
        """扫描页面文本内容"""
        print("  🔍 扫描页面文本内容...")
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            if body_text:
                lines = body_text.split('\n')[:20]
                print(f"    页面文本内容（前20行）:")
                for line in lines:
                    line = line.strip()
                    if line:
                        print(f"      {line}")
            else:
                print("    ⚠ 页面无文本内容")
        except Exception as e:
            print(f"    扫描失败: {e}")
        print()

    def _scan_elements(self, tag_name, label):
        """扫描指定类型的元素"""
        print(f"  🔍 扫描所有{label}...")
        try:
            elements = self.driver.find_elements(By.TAG_NAME, tag_name)
            if elements:
                print(f"    找到 {len(elements)} 个{label}:")
                for idx, elem in enumerate(elements[:10]):
                    try:
                        if tag_name == "input":
                            elem_type = elem.get_attribute('type') or 'text'
                            elem_name = elem.get_attribute('name') or ''
                            elem_id = elem.get_attribute('id') or ''
                            elem_class = elem.get_attribute('class') or ''
                            print(f"      [{idx}] type='{elem_type}' name='{elem_name}' id='{elem_id}' class='{elem_class}'")
                        elif tag_name == "button":
                            btn_text = elem.text.strip()
                            btn_class = elem.get_attribute('class') or ''
                            print(f"      [{idx}] text='{btn_text}' class='{btn_class}'")
                    except Exception:
                        pass
            else:
                print(f"    未找到{label}")
        except Exception as e:
            print(f"    扫描失败: {e}")
        print()

    def _scan_user_elements(self, retry_count=5, retry_interval=0.5):
        """扫描购票人相关元素（支持重试）

        Args:
            retry_count: 重试次数，默认 5 次
            retry_interval: 重试间隔（秒），默认 0.5 秒

        Returns:
            bool: 是否找到任意用户元素
        """
        print("  🔍 扫描购票人元素...")

        for attempt in range(retry_count):
            if attempt > 0:
                print(f"  第 {attempt + 1} 次尝试...")
                time.sleep(self._pace(retry_interval))

            try:
                found_any = False
                for user in self.config.users:
                    xpath = f"//*[contains(text(), '{user}')]"
                    user_elements = self.driver.find_elements(By.XPATH, xpath)

                    if user_elements:
                        if not found_any and attempt == 0:
                            print(f"  找到 {len(user_elements)} 个包含 '{user}' 的元素")
                        found_any = True
                        if attempt == 0:  # 只在第一次尝试时显示详细信息
                            for idx, elem in enumerate(user_elements[:3]):
                                try:
                                    text = elem.text.strip()
                                    tag = elem.tag_name
                                    class_attr = elem.get_attribute('class') or ''
                                    print(f"    [{idx}] <{tag}> class='{class_attr}' text='{text}'")
                                except Exception:
                                    pass
                    else:
                        if attempt == 0:
                            print(f"  ⚠ 未找到包含 '{user}' 的元素")

                if found_any:
                    if attempt > 0:
                        print(f"  ✓ 第 {attempt + 1} 次尝试成功找到用户元素")
                    print()
                    return True

            except Exception as e:
                if attempt == 0:
                    print(f"  扫描异常: {e}")

        print(f"  ⚠ {retry_count} 次尝试后仍未找到用户元素")
        print()
        return False

    def _try_select_user_method1(self, user, users_to_select, user_selected):
        """方法1: 查找并点击包含用户名的div"""
        if user_selected >= len(users_to_select):
            return user_selected

        try:
            print(f"    尝试方法1: 查找并点击包含用户名的div")
            xpath_expression = f"//div[contains(text(), '{user}')]"
            user_elements = self.driver.find_elements(By.XPATH, xpath_expression)

            if not user_elements:
                print(f"      未找到包含 '{user}' 的div")
                return user_selected

            print(f"      找到 {len(user_elements)} 个包含 '{user}' 的div")

            best_match = None
            for elem in user_elements:
                try:
                    elem_text = elem.text.strip()
                    if elem_text == user:
                        best_match = elem
                        break
                    elif len(elem_text) < 30 and user in elem_text:
                        if best_match is None:
                            best_match = elem
                except Exception:
                    continue

            if not best_match:
                print(f"      未找到合适的div元素")
                return user_selected

            checkbox_selectors = [
                "following-sibling::*//i[contains(@class, 'iconfont')]",
                "following-sibling::*[1]//i",
                "following-sibling::i",
                "..//following-sibling::*//i[contains(@class, 'iconfont')]",
                "..//following-sibling::i",
                "..//i[contains(@class, 'iconfont')]",
                "..//i[contains(@class, 'icon')]",
                "..//i[contains(@class, 'check')]",
                "following-sibling::*[1]//input",
                "following-sibling::*[1]//span",
                "..//following-sibling::*//input",
                "../..//input[@type='checkbox']",
                "..//label",
            ]

            for selector in checkbox_selectors:
                try:
                    checkbox = best_match.find_element(By.XPATH, selector)
                    elem_tag = checkbox.tag_name
                    elem_class = checkbox.get_attribute('class') or ''
                    print(f"        找到可点击元素: <{elem_tag}> class='{elem_class}'")
                    self.driver.execute_script("arguments[0].click();", checkbox)
                    print(f"  ✓ 已选择: {user}\n")
                    time.sleep(self._get_wait_time())
                    return user_selected + 1
                except Exception:
                    continue

            print(f"        未找到复选框/icon，直接点击div本身")
            try:
                self.driver.execute_script("arguments[0].click();", best_match)
                print(f"  ✓ 已点击: {user}\n")
                time.sleep(self._get_wait_time())
                return user_selected + 1
            except Exception:
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", best_match)
                    time.sleep(0.2)
                    self.driver.execute_script("arguments[0].click();", best_match)
                    print(f"  ✓ 已点击（滚动后）: {user}\n")
                    time.sleep(self._get_wait_time())
                    return user_selected + 1
                except Exception as e:
                    print(f"        点击失败: {e}")

        except Exception as e:
            print(f"    方法1失败: {e}")

        return user_selected

    def _try_select_user_method2(self, user, users_to_select, user_selected):
        """方法2: 通过复选框和label选择"""
        if user_selected >= len(users_to_select):
            return user_selected

        try:
            print(f"    尝试方法2: 查找所有复选框")
            all_checkboxes = self.driver.find_elements(By.XPATH, "//input[@type='checkbox']")
            all_labels = self.driver.find_elements(By.TAG_NAME, 'label')

            print(f"      找到 {len(all_checkboxes)} 个复选框")
            print(f"      找到 {len(all_labels)} 个标签")

            for label in all_labels:
                try:
                    label_text = label.text.strip()
                    if user in label_text:
                        label_for = label.get_attribute('for')
                        if label_for:
                            checkbox = self.driver.find_element(By.ID, label_for)
                            if not checkbox.is_selected():
                                checkbox.click()
                                print(f"        通过label选择: {label_text}")
                                print(f"  ✓ 已选择: {user}\n")
                                time.sleep(self._get_wait_time())
                                return user_selected + 1
                except Exception:
                    continue

            if user_selected < len(users_to_select):
                for checkbox in all_checkboxes:
                    try:
                        parent = checkbox.find_element(By.XPATH, '..')
                        nearby_text = parent.text.strip()

                        if user in nearby_text:
                            if not checkbox.is_selected():
                                checkbox.click()
                                print(f"        通过附近文本选择: {nearby_text}")
                                print(f"  ✓ 已选择: {user}\n")
                                time.sleep(self._get_wait_time())
                                return user_selected + 1
                    except Exception:
                        continue

        except Exception as e:
            print(f"    方法2失败: {e}")

        return user_selected

    def _try_select_user_method3(self, user, users_to_select, user_selected):
        """方法3: 点击包含用户名的元素"""
        if user_selected >= len(users_to_select):
            return user_selected

        try:
            print(f"    尝试方法3: 点击包含用户名的元素")
            xpath = f"//*[contains(text(), '{user}')]"
            user_elements = self.driver.find_elements(By.XPATH, xpath)

            for elem in user_elements[:10]:
                try:
                    elem_text = elem.text.strip()
                    if elem_text == user or (len(elem_text) < 30 and user in elem_text):
                        print(f"        尝试点击: {elem_text}")
                        elem.click()
                        print(f"  ✓ 已点击: {user}\n")
                        time.sleep(self._get_wait_time())
                        return user_selected + 1
                except Exception:
                    continue

        except Exception as e:
            print(f"    方法3失败: {e}")

        return user_selected

    def _try_select_user_method4(self, user, users_to_select, user_selected):
        """方法4: 使用JavaScript查找并点击"""
        if user_selected >= len(users_to_select):
            return user_selected

        try:
            print(f"    尝试方法4: 使用JavaScript查找并点击")
            js_script = f"""
            var divs = document.getElementsByTagName('div');
            var targetDivs = [];
            for (var i = 0; i < divs.length; i++) {{
                if (divs[i].textContent.includes('{user}') &&
                    divs[i].textContent.trim() === '{user}' &&
                    divs[i].offsetParent !== null) {{
                    targetDivs.push(divs[i]);
                }}
            }}
            return targetDivs;
            """
            target_divs = self.driver.execute_script(js_script)

            if not target_divs:
                print(f"      未找到精确匹配 '{user}' 的div")
                return user_selected

            print(f"      找到 {len(target_divs)} 个匹配的div")
            div = target_divs[0]

            find_icon_script = """
            var div = arguments[0];
            var nextSibling = div.nextElementSibling;
            if (nextSibling) {
                var icons = nextSibling.getElementsByTagName('i');
                for (var i = 0; i < icons.length; i++) {
                    if (icons[i].className.indexOf('iconfont') !== -1) {
                        return icons[i];
                    }
                }
            }
            var parent = div.parentElement;
            if (parent) {
                var parentSibling = parent.nextElementSibling;
                if (parentSibling) {
                    var icons = parentSibling.getElementsByTagName('i');
                    for (var i = 0; i < icons.length; i++) {
                        if (icons[i].className.indexOf('iconfont') !== -1) {
                            return icons[i];
                        }
                    }
                }
            }
            return div;
            """

            target_elem = self.driver.execute_script(find_icon_script, div)
            elem_tag = target_elem.tag_name
            elem_class = target_elem.get_attribute('class') or ''

            try:
                self.driver.execute_script("arguments[0].click();", target_elem)
                print(f"      ✓ 已通过JavaScript点击: <{elem_tag}> class='{elem_class}'")
                print(f"  ✓ 已选择: {user}\n")
                time.sleep(self._pace(0.5))
                return user_selected + 1
            except Exception as e:
                print(f"      点击失败: {e}")

        except Exception as e:
            print(f"    方法4失败: {e}")

        return user_selected

    def _select_users(self, ticket_count, users_to_select):
        """选择观演人员"""
        user_selected = 0

        for i, user in enumerate(users_to_select):
            # 使用循环索引 i 而不是 user_selected，避免计数错误
            print(f"  正在选择: {user} ({i + 1}/{ticket_count})")

            if user_selected >= ticket_count:
                print(f"    ⚠ 已选够 {ticket_count} 人，跳过: {user}")
                continue

            new_user_selected = self._try_select_user_method1(user, users_to_select, user_selected)
            if new_user_selected > user_selected:
                user_selected = new_user_selected
            else:
                new_user_selected = self._try_select_user_method2(user, users_to_select, user_selected)
                if new_user_selected > user_selected:
                    user_selected = new_user_selected
                else:
                    new_user_selected = self._try_select_user_method3(user, users_to_select, user_selected)
                    if new_user_selected > user_selected:
                        user_selected = new_user_selected
                    else:
                        new_user_selected = self._try_select_user_method4(user, users_to_select, user_selected)
                        if new_user_selected > user_selected:
                            user_selected = new_user_selected

            if user_selected <= i:
                print(f"  ⚠ 未找到用户: {user}")

        print(f"\n***已选择 {user_selected}/{ticket_count} 个观众***")

        if user_selected > 0:
            print(f"  ✓ 已选择的观众: {users_to_select[:user_selected]}")
        if user_selected < ticket_count:
            print(f"  ⚠ 未选择的观众: {users_to_select[user_selected:ticket_count]}")
        print()

    def _scan_submit_buttons(self):
        """扫描提交按钮"""
        print("  🔍 扫描提交按钮...")
        try:
            submit_candidates = []

            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            for btn in all_buttons:
                try:
                    btn_text = btn.text.strip()
                    btn_class = btn.get_attribute('class') or ''
                    # 仅用于打印，刻意不含「支付」类关键词，免得日志让人误以为支付按钮也是提交按钮。
                    if any(keyword in btn_text for keyword in ['提交订单', '提交', '确认']):
                        submit_candidates.append(('button', btn, btn_text, btn_class))
                        print(f"    [button] text='{btn_text}' class='{btn_class}'")
                except Exception:
                    pass

            for tag in ['div', 'span']:
                elements = self.driver.find_elements(By.TAG_NAME, tag)
                for elem in elements:
                    try:
                        elem_text = elem.text.strip()
                        if elem_text in ['立即提交', '提交订单', '提交', '确认']:
                            elem_class = elem.get_attribute('class') or ''
                            view_name = elem.get_attribute('view-name') or ''
                            submit_candidates.append((tag, elem, elem_text, elem_class, view_name))
                            print(f"    [{tag}] text='{elem_text}' class='{elem_class}' view-name='{view_name}'")
                    except Exception:
                        pass

            if not submit_candidates:
                print("    ⚠ 未找到明显的提交按钮")
        except Exception as e:
            print(f"    扫描失败: {e}")
        print()

    # 支付环节守卫。抢到票后必须停在支付环节，由用户自己付款；
    # 下面两个守卫保证脚本任何情况下都不会点到支付按钮。

    PAYMENT_BUTTON_KEYWORDS = ('支付', '付款', '收银')

    def _is_pay_button_text(self, text):
        """文案里带「支付 / 付款 / 收银」的按钮，一律不点。"""
        t = (text or '').strip()
        return any(k in t for k in self.PAYMENT_BUTTON_KEYWORDS)

    def _is_payment_page(self):
        """判断当前是不是已经在支付环节（收银台）。纯只读判断，不做任何点击。

        只用强信号，避免把「订单确认页」误判成支付页，那会导致订单提交不了。
        """
        try:
            title = self.driver.title or ''
            url = self.driver.current_url or ''
        except Exception:
            return False
        if '收银台' in title or '订单提交成功' in title:
            return True
        low = url.lower()
        if 'cashier' in low or '/pay' in low:
            return True
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return False
        return '订单提交成功' in body

    def _try_submit_by_text(self, submit_button_texts):
        """方法1-2: 通过元素文本查找"""
        for btn_text in submit_button_texts:
            for tag in ['button', 'div', 'span']:
                try:
                    xpath = f"//{tag}[contains(text(), '{btn_text}')]"
                    submit_btn = self.driver.find_element(By.XPATH, xpath)
                    # 守卫：文案含"支付/付款/收银"就直接跳过，绝不自动付款
                    actual_text = (submit_btn.text or '').strip()
                    if self._is_pay_button_text(actual_text):
                        print(f"  ⚠ 跳过疑似支付按钮: '{actual_text}'")
                        continue
                    print(f"  ✓ 找到<{tag}>: {btn_text}")
                    submit_btn.click()
                    print('***订单已提交***\n')
                    return True
                except Exception:
                    continue

            try:
                xpath = f"//span[text()='{btn_text}']"
                submit_btn = self.driver.find_element(By.XPATH, xpath)
                if self._is_pay_button_text((submit_btn.text or '').strip()):
                    print("  ⚠ 跳过疑似支付按钮（精确匹配命中）")
                    continue
                print(f"  ✓ 找到<span>(精确匹配): {btn_text}")
                try:
                    parent = submit_btn.find_element(By.XPATH, '..')
                    parent.click()
                except Exception:
                    submit_btn.click()
                print('***订单已提交***\n')
                return True
            except Exception:
                continue

        return False

    def _try_submit_by_view_name(self):
        """方法3: 通过view-name属性查找"""
        try:
            xpath = "//div[@view-name='TextView']//span[contains(text(), '提交')]"
            submit_btn = self.driver.find_element(By.XPATH, xpath)
            parent_div = submit_btn.find_element(By.XPATH, '..')
            if self._is_pay_button_text(parent_div.text):
                print("  ⚠ 跳过疑似支付按钮（view-name 命中）")
                return False
            print(f"  ✓ 找到div[@view-name='TextView']")
            parent_div.click()
            print('***订单已提交***\n')
            return True
        except Exception:
            return False

    def _try_submit_by_class(self):
        """方法4: 通过class查找"""
        submit_button_classes = [
            'submit-button',
            'submit-btn',
            'confirm-button',
            # 原有的 'pay-button' 已移除：那是收银台的支付按钮，多账号改造后要求绝不自动付款。
            'bui-btn-contained',
        ]

        for class_name in submit_button_classes:
            try:
                xpath = f"//*[contains(@class, '{class_name}')]"
                submit_btn = self.driver.find_element(By.XPATH, xpath)
                print(f"  ✓ 通过class找到按钮: {class_name}")
                submit_btn.click()
                print('***订单已提交***\n')
                return True
            except Exception:
                continue

        return False

    def _try_submit_by_original_xpath(self):
        """方法5: 原有的XPath"""
        try:
            submit_btn = self.driver.find_element(
                value='//*[@id="dmOrderSubmitBlock_DmOrderSubmitBlock"]/div[2]/div/div[2]/div[2]/div[2]',
                by=By.XPATH)
            print("  ✓ 通过原有XPath找到按钮")
            submit_btn.click()
            print('***订单已提交***\n')
            return True
        except Exception:
            return False

    def _submit_order(self):
        """提交订单。

        多账号改造后，这里只负责「提交订单」这一件事：
        提交成功后立刻停手，把浏览器留在支付环节，由用户手动付款。
        绝不点击任何支付按钮。
        """
        if self._is_payment_page():
            print('***当前已在支付环节，脚本到此为止，请手动完成支付***\n')
            return

        # 提交前先关遮挡：「手机扫码购买更便捷」的二维码浮层正好压在提交按钮上，不关点不着。
        self.dismiss_obstructions(rounds=3)
        # 类名不标准的浮层选择器抓不到，这里直接按「谁盖住了提交按钮」清一遍，不依赖类名。
        self._unblock_submit_buttons()

        print('***准备提交订单***\n')

        self._scan_submit_buttons()

        # 刻意去掉原来的 '立即支付' / '去支付' / '支付'：那是收银台按钮，点下去等于替用户付款。
        submit_button_texts = ['立即提交', '提交订单', '提交', '确认']

        if (self._try_submit_by_text(submit_button_texts) or
            self._try_submit_by_view_name() or
            self._try_submit_by_class() or
            self._try_submit_by_original_xpath()):
            return

        print(f"  ⚠ 所有方法都失败，请手动点击提交按钮\n")

    # 新版 H5 订单确认页（order-pay.html / h5-ultron-buy/index.html）类名，取自线上 769.*.js 真实 chunk：观演人区
    # .bui-ultron-viewer-header / -viewer-list，项 .bui-ultron-viewer-item（整行可点才 toggle），姓名 .bui-ultron-viewer-info-name，
    # 提示 .bui-ultron-viewer-tips，复选框 .bui-checkbox-input（React 受控），协议 .dmprotocol-box .bui-checkbox-input，提交 button.pay-submit-btn。

    H5_ORDER_VIEWER_ROW = '.bui-ultron-viewer-item'
    H5_ORDER_VIEWER_NAME = '.bui-ultron-viewer-info-name'
    H5_ORDER_VIEWER_TIPS = '.bui-ultron-viewer-tips'
    H5_ORDER_PROTOCOL_INPUT = '.dmprotocol-box .bui-checkbox-input'
    H5_ORDER_SUBMIT_BTN = '.pay-submit-btn'

    # 类名兜底。前端每次发版都可能换掉 CSS Modules 的名字（.bui-ultron-* 这代就换过），所以下面两处
    # 不能只认一个写死的类名：观演人「行」退到「class 里带 viewer-item 的」，再不行就按文本找名字、
    # 往上爬到「只有一个复选框」那层，后一种不吃类名。
    H5_ORDER_VIEWER_ROW_ANY = '[class*="viewer-item"]'
    H5_ORDER_VIEWER_TIPS_ANY = '[class*="viewer-tips"]'

    # 第二代 H5 订单页（h5-ultron-buy）真实结构。类名是 viwer：大麦把 viewer 拼错了，照它拼才对，改回 viewer 整套选择器
    # 都匹配不上。整行 <div onClick>（无 class，toggle 挂这里），姓名容器 div.viwer-info-name（可能带 ...-tag「特权观演人」），
    # 右侧勾选态 i.iconfont.icondanxuan-xuanzhong_（未选 ...weixuan_）。整页没有 input[type=checkbox]，勾选态是图标字体画的，「读 checked」的老判据永远读到 0。
    H5_ORDER_VIEWER_NAME_2 = '.viwer-info-name'
    # 兜底用：类名再改时按子串找，但要把「特权观演人」那个小标签排掉，否则一行被数成两行、下标整体错位。
    H5_ORDER_VIEWER_NAME_2_ANY = (
        '[class*="viwer-info-name"]:not([class*="name-tag"])')
    H5_ORDER_VIEWER_TIPS_2 = '.viwer-info-tips'
    H5_ORDER_VIEWER_ROW_DISABLED = '[class*="viewer-list-disable"]'
    # 勾选态图标整套都叫 icondanxuan-*（单选）/ iconduoxuan-*（多选），只有末尾后缀不同，按后缀子串认。
    H5_ORDER_PICK_ON = 'i[class*="xuanzhong"]'
    H5_ORDER_PICK_OFF = 'i[class*="weixuan"]'

    # 协议行的文案特征。第二代底部（协议 + 提交）由服务端模板渲染，类名随时可能变，找不着时只能按文案找；
    # JS 那边还有条硬条件兜底：这行必须含「同意」或「阅读」。清单里不许出现「实名制购票」，演出信息卡片里就有，
    # 脚本会把整张卡片当成协议行、报「有协议框但没勾上」（2026-10-09 实跑踩的）。
    H5_PROTOCOL_TEXT_MARKS = ('我已阅读', '已阅读并同意', '同意《', '购票协议',
                              '同意并继续', '阅读并同意', '购票须知')
    # 提交按钮的文案特征（同上，服务端模板渲染，类名不固定）。长词（≥3 字，比如「提交订单」）才允许
    # 包含匹配，光杆短词只许精确匹配（按钮就叫「提交」才算）。精确匹配在 H5_SUBMIT_BY_TEXT_JS 里。
    H5_SUBMIT_TEXT_MARKS = ('提交订单', '立即提交', '确认下单', '确认支付',
                            '立即支付', '去支付', '立即购买', '立即购票',
                            '确认购买', '去结算',
                            '提交', '下单', '确认', '购买')

    # 下单页的地址特征。必须按地址认，不能只按元素认：主流下单页是
    # m.damai.cn/app/dmfe/h5-ultron-buy/index.html?…&buyNow=true，DOM 全异步渲染，从跳过来到
    # 观演人列表出现隔着几百毫秒；只按元素认会在刚跳过来那一瞬判成「老版 PC 页」，拿老选择器点一个还不存在的按钮。
    H5_ORDER_URL_MARKS = ('order-pay', 'h5-ultron-buy', 'ultron-buy')

    # 观演人列表是不是渲染出来了（异步拉取，必须等）
    H5_VIEWER_PROBE_JS = """
    var out = {rows: 0, listKids: 0, boxes: 0, names2: 0, picks: 0};
    try {
      out.rows = document.querySelectorAll(
        '.bui-ultron-viewer-item, [class*="viewer-item"]').length;
      var l = document.querySelector('[class*="viewer-list"]');
      if (l) out.listKids = l.children.length;
      out.boxes = document.querySelectorAll('input[type=checkbox]').length;
      // 第二代：姓名容器 + 图标勾选态。
      // 这一页没有复选框，boxes 永远是 0；只盯 boxes 会一直等到超时，
      //   然后被判成「列表没渲染出来」。
      out.names2 = document.querySelectorAll(
        '.viwer-info-name, [class*="viwer-info-name"]:not([class*="name-tag"])'
        ).length;
      out.picks = document.querySelectorAll(
        'i[class*="xuanzhong"], i[class*="weixuan"]').length;
    } catch (e) {}
    return out;
    """

    # 按姓名找「一行观演人」，完全不依赖类名
    H5_ROW_BY_NAME_JS = """
    var want = (arguments[0] || '').replace(/\\s+/g, '');
    if (!want) return null;
    function norm(el) { return (el.textContent || '').replace(/\\s+/g, ''); }
    var all = document.body ? document.body.querySelectorAll('*') : [];
    var exact = null, prefix = null;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.children && el.children.length) continue;   // 只看叶子
      var t = norm(el);
      if (!t) continue;
      if (t === want) { exact = el; break; }
      if (!prefix && t.indexOf(want) === 0 && t.length <= want.length + 6) prefix = el;
    }
    var hit = exact || prefix;
    if (!hit) return null;
    // ① 先找「同时含勾选图标」的祖先，第二代整行的判据。
    //    这一页没有复选框，只有 <i class="icondanxuan-xuanzhong_|weixuan_">，
    //    下一段的「只有一个复选框」在这页上永远不成立。
    var node = hit;
    for (var g = 0; g < 10 && node && node !== document.body; g++) {
      if (node.querySelector('i[class*="xuanzhong"], i[class*="weixuan"]')) return node;
      node = node.parentElement;
    }
    // ② 从姓名元素往上爬，取「里面只有一个复选框」的那层，第一代的判据。
    //    用「只有一个」当判据：多一个就爬到列表容器上去了，点它会点错东西。
    node = hit;
    for (var d = 0; d < 10 && node && node !== document.body; d++) {
      if (node.querySelectorAll('input[type=checkbox]').length === 1) return node;
      node = node.parentElement;
    }
    // ③ 有的皮肤用 svg 画勾、两样都没有：退到往上第 4 层
    //    （第二代实测：姓名 → 姓名容器 → 内层 → 内层包一层 → 整行）
    node = hit;
    for (var k = 0; k < 4 && node && node.parentElement; k++) node = node.parentElement;
    return node;
    """

    # 结构快照：观演人区认不出来时，把真实结构写进日志（只记 class 和文本片段）
    H5_DUMP_JS = """
    function brief(el) {
      var c = (el.getAttribute('class') || '').replace(/\\s+/g, '.').slice(0, 150);
      var t = (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 46);
      var i = (el.getAttribute('id') || '').slice(0, 40);
      // 把这一层里的图标 class 也带上，第二代的勾选态就是图标。
      var icons = [];
      var kids = el.querySelectorAll('i, svg, input');
      for (var k = 0; k < kids.length && k < 6; k++) {
        var kc = (kids[k].getAttribute('class') || '').slice(0, 60);
        icons.push(kids[k].tagName.toLowerCase() + (kc ? '.' + kc.replace(/\\s+/g, '.') : ''));
      }
      return el.tagName.toLowerCase() + (i ? '#' + i : '')
        + (c ? '.' + c : '') + '   [' + t + ']'
        + (icons.length ? '   <' + icons.join(' ') + '>' : '');
    }
    var out = {title: document.title, url: location.href, lines: [],
               nameSeen: false, chain: [], nodeCount: 0,
               boxes: 0, boxLike: 0, pickOn: 0, pickOff: 0, viwerCount: 0};
    try {
      var hits = document.querySelectorAll(
        '[class*="viewer"], [class*="Viewer"], [class*="viwer"]');
      out.nodeCount = hits.length;
      for (var i = 0; i < hits.length && i < 30; i++) out.lines.push(brief(hits[i]));
      out.boxes = document.querySelectorAll('input[type=checkbox]').length;
      out.boxLike = document.querySelectorAll('[class*="checkbox"]').length;
      out.pickOn = document.querySelectorAll('i[class*="xuanzhong"]').length;
      out.pickOff = document.querySelectorAll('i[class*="weixuan"]').length;
      out.viwerCount = document.querySelectorAll('.viwer-info-name').length;
      var want = (arguments[0] || '').replace(/\\s+/g, '');
      if (want) {
        var all = document.querySelectorAll('body *');
        for (var j = 0; j < all.length; j++) {
          var el = all[j];
          if (el.children && el.children.length) continue;
          if ((el.textContent || '').replace(/\\s+/g, '') === want) {
            out.nameSeen = true;
            var n = el;
            for (var d = 0; d < 8 && n; d++) { out.chain.push(brief(n)); n = n.parentElement; }
            break;
          }
        }
      }
    } catch (e) { out.error = String(e); }
    return out;
    """

    # 「一次往返说清一件事」的三个探针。预选人慢是因为每一步都要单独跟浏览器说一次话，夹具上（本地、
    # 零延迟）实测：按名找一行 21 次往返、勾一个观演人 55 次、commit_order 全链路 176 次；真站还要叠网络延迟。
    # 下面三个探针把「N 行 × 每行 4 问」压成一次问完。

    # 行枚举 + 逐行读状态，只写一份的公共 JS。H5_ROWS_INFO_JS 给出的「第 i 行」是下标，调用方拿它去
    # _h5_viewer_rows() 取第 i 个元素；两份逻辑一漂移，下标就指向另一个人（勾错观演人是这里最贵的错，还不报错）。
    # 行枚举支持两代：第一代有 .bui-ultron-viewer-item 这种行类名，第二代没有，只能从 .viwer-info-name 往上爬到含勾选图标的祖先。
    H5_ROWS_LIB_JS = r"""
    function _t(el) {
      return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    // 从「姓名容器」往上爬到「整行」。
    // 判据是「这一层里有勾选图标」，图标是第二代的勾选态载体，
    // 它在的那一层就是整行（toggle 的 onClick 就挂在这一层）。
    function _rowOf(el) {
      var n = el;
      for (var d = 0; d < 10 && n && n !== document.body; d++) {
        if (n.querySelector('i[class*="xuanzhong"], i[class*="weixuan"]')) return n;
        n = n.parentElement;
      }
      // 连图标都没有（又换皮肤了）：退到往上 3 层，第二代实测的那一层
      n = el;
      for (var k = 0; k < 3 && n && n.parentElement; k++) n = n.parentElement;
      return n || el;
    }
    function _rows() {
      var out = [];
      var a = document.querySelectorAll('.bui-ultron-viewer-item');
      if (!a.length) a = document.querySelectorAll('[class*="viewer-item"]');
      for (var i = 0; i < a.length; i++) out.push(a[i]);
      if (!out.length) {
        var b = document.querySelectorAll('.viwer-info-name');
        if (!b.length) {
          b = document.querySelectorAll(
            '[class*="viwer-info-name"]:not([class*="name-tag"])');
        }
        for (var j = 0; j < b.length; j++) {
          var r = _rowOf(b[j]);
          if (out.indexOf(r) < 0) out.push(r);
        }
      }
      return out;
    }
    // 读一行的姓名。第二代的姓名容器里还可能塞着「特权观演人」小标签，
    // 得把它抠掉，否则姓名和配置里的人名对不上，会判成「页面上没有这个人」。
    function _nameOf(row) {
      var c = row.querySelector('.bui-ultron-viewer-info-name')
           || row.querySelector('[class*="viewer-info-name"]')
           || row.querySelector('.viwer-info-name')
           || row.querySelector(
                '[class*="viwer-info-name"]:not([class*="name-tag"])')
           || row.querySelector('[class*="viewer-name"]');
      if (!c) return '';
      var t = _t(c);
      var tag = c.querySelector('.viwer-info-name-tag')
             || c.querySelector('[class*="name-tag"]');
      if (tag) {
        var tt = _t(tag);
        if (tt && t.indexOf(tt) >= 0) t = t.replace(tt, '').trim();
      }
      return t;
    }
    // 读一行的勾选态。true/false 是明确结论，null 是「这一页读不出来」，
    // 两者绝不能混：读不出来时调用方不许当成「没勾上」去反复瞎点，
    // 那样即使点中了也会被自己再点回去。
    function _pick(row) {
      var boxes = row.querySelectorAll('.bui-checkbox-input');
      if (!boxes.length) boxes = row.querySelectorAll('input[type=checkbox]');
      if (boxes.length) return !!boxes[0].checked;
      // 第二代：勾选态是 <i class="iconfont icondanxuan-xuanzhong_|weixuan_">
      if (row.querySelector('i[class*="xuanzhong"]')) return true;
      if (row.querySelector('i[class*="weixuan"]')) return false;
      return null;
    }
    // 「本场不可用」的节点判据：第一代看 -tips，第二代看 viwer-info-tips
    // 或 viewer-list-disable（isDisabled 时整块加这个 class）。
    function _tipsEl(row) {
      return row.querySelector('.bui-ultron-viewer-tips')
          || row.querySelector('[class*="viewer-tips"]')
          || row.querySelector('.viwer-info-tips')
          || row.querySelector('[class*="viewer-list-disable"]');
    }
    """

    # 一次读出所有观演人行的状态，代替「每行各读 4 次」。一行都读不到时返回 []（调用方要退回逐行读，别当成「页面上没人」）。
    H5_ROWS_INFO_JS = "\n" + H5_ROWS_LIB_JS + r"""
    var rows = _rows();
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var txt = _t(r);
      out.push({
        i: i,
        name: _nameOf(r),
        selected: _pick(r),
        // 和 _h5_row_disabled() 同一个判据：先看提示节点，再退到文案
        disabled: !!_tipsEl(r) ||
                  /本场不可用|本场不支持|本场不参与|不可选/.test(txt),
        text: txt.slice(0, 200)
      });
    }
    return out;
    """

    # 把行元素数组本身取回来（给 `_h5_viewer_rows()` 的第二代兜底）。和 H5_ROWS_INFO_JS 拼同一份 _rows()，顺序必然一致。
    H5_VIEWER_ROWS_JS = "\n" + H5_ROWS_LIB_JS + "\nreturn _rows();\n"

    # 按姓名精确找一行（1 次往返）。刻意只认完全相等，点错观演人的代价远大于多花几次往返；
    # 前缀模糊匹配留在最后的兜底里（H5_ROW_BY_NAME_JS），不给它优先权。
    H5_ROW_EXACT_JS = "\n" + H5_ROWS_LIB_JS + r"""
    var want = (arguments[0] || '').replace(/\s+/g, '');
    if (!want) return null;
    // ① 先把两代的「行」都枚举出来，逐行比姓名，判据与
    //    _h5_viewer_rows() / H5_ROWS_INFO_JS 完全一致，不会找错人，
    //    也不会因为「这一页没有复选框」而永远返回 null。
    var rows = _rows();
    for (var i = 0; i < rows.length; i++) {
      if (_nameOf(rows[i]).replace(/\s+/g, '') === want) return rows[i];
    }
    // ② 第一代皮肤换过类名时靠它：全页找叶子文本，再爬到
    //    「里面只有一个复选框」的那层。
    function norm(el) { return (el.textContent || '').replace(/\s+/g, ''); }
    var all = document.body ? document.body.querySelectorAll('*') : [];
    var hit = null;
    for (var j = 0; j < all.length; j++) {
      var el = all[j];
      if (el.children && el.children.length) continue;   // 只看叶子
      if (norm(el) === want) { hit = el; break; }
    }
    if (!hit) return null;
    var node = hit;
    for (var d = 0; d < 10 && node && node !== document.body; d++) {
      if (node.querySelectorAll('input[type=checkbox]').length === 1) return node;
      node = node.parentElement;
    }
    return null;
    """

    # 读「一行」的完整状态（1 次往返，代替原来的 2~3 次）。姓名也在里面：按姓名找行时逐行调用，多读一次就多一圈往返。
    H5_ROW_STATE_JS = "\n" + H5_ROWS_LIB_JS + r"""
    var row = arguments[0];
    if (!row) return null;
    var sel = _pick(row);
    return {
      name: _nameOf(row),
      // hasBox 的语义是「这一行的勾选态读得出来吗」，不只是
      //   「这里有没有复选框」。第二代没有复选框、勾选态在图标上；
      //   只按 input 判会把这一页判成「读不出来」，于是反复瞎点。
      hasBox: sel !== null,
      selected: sel,
      hasTips: !!_tipsEl(row),
      text: _t(row).slice(0, 300)
    };
    """

    # 一行「外观指纹」：标签名 + class + aria 勾选态拼一串。两代选择器全对不上时（又换皮肤了）用它当最后一道：
    # 点一下前后指纹变了，就说明这次点击确实改动了这一行。代价是只能判「变了没有」，判不出「变成什么样」。
    H5_ROW_SIG_JS = r"""
    var row = arguments[0];
    if (!row) return null;
    var parts = [];
    function walk(el, depth) {
      if (!el || depth > 3 || parts.length > 80) return;
      parts.push(el.tagName + '|' + (el.getAttribute('class') || '') + '|' +
                 (el.getAttribute('aria-checked') || '') + '|' +
                 (el.checked === undefined ? '' : (el.checked ? '1' : '0')));
      var kids = el.children || [];
      for (var i = 0; i < kids.length; i++) walk(kids[i], depth + 1);
    }
    walk(row, 0);
    return parts.join('~');
    """

    # 按文案找协议行（类名不可靠时用）。返回 {row, box}，box 可能为 null。
    H5_PROTOCOL_BY_TEXT_JS = r"""
    var marks = arguments[0] || [];
    function _t(el) {
      return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    function has(el) {
      var t = _t(el);
      if (!t || t.length > 200) return false;
      // 硬条件：协议行必须含「同意」或「阅读」。
      //   只按 marks 匹配的话，「实名制购票」这种词会把演出信息卡片
      //   整张认成协议行（2026-10-09 实跑：脚本对着卡片报「没勾上」，
      //   连点 8 次提交都没动静，真凶被这句话盖住了两轮）。
      if (t.indexOf('同意') < 0 && t.indexOf('阅读') < 0) return false;
      for (var i = 0; i < marks.length; i++) if (t.indexOf(marks[i]) >= 0) return true;
      return false;
    }
    var all = document.querySelectorAll('div, span, label, p, li, a, section');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (!has(el)) continue;
      // 往上找「一行协议」：整体文案仍然只是那句话、且够窄的那层。
      // 再往上就会爬到整页正文（里面也含这几个字），点它会点错地方。
      var node = el, best = el;
      for (var d = 0; d < 4 && node && node !== document.body; d++) {
        var t = _t(node);
        if (!t || t.length > 200) break;
        best = node;
        node = node.parentElement;
      }
      var box = best.querySelector('input[type=checkbox]')
             || best.querySelector('[class*="checkbox"], [class*="checkBox"]')
             || best.querySelector('[role="checkbox"]');
      // 勾选态还有第三种载体：图标字体。
      //   这一代大麦的勾选框就是 i.icondanxuan-xuanzhong_（选中）/
      //   i.icondanxuan-weixuan_（未选）画的（观演人那边已经逐字证实过），
      //   协议用的多半是同一个组件。只找 input/[class*=checkbox] 会漏掉它，
      //   于是「明明找到了协议框，却永远勾不上」，大麦照样把提交拦下。
      var icon = best.querySelector('i[class*="xuanzhong"], i[class*="weixuan"]');
      return {row: best, box: box || null, icon: icon || null,
              text: _t(best).slice(0, 80)};
    }
    return null;
    """

    # 协议区结构快照：拿「按文案找到的那一行」当输入，把这一带彻底拍下来（2026-10-09 实跑脚本报「有协议框但
    # 没勾上」，日志里协议区一个字都没有，只能猜）。拍的是：这行什么标签、有哪些 class、行里有没有 input、
    # 图标什么 class、页面上共几个 input（哪个可见、哪个 checked）。
    H5_PROTOCOL_DUMP_JS = r"""
    var el = arguments[0];
    var out = {found: false, tag: '', cls: '', text: '', kids: [],
               icons: [], inputs: [], toggles: 0, error: ''};
    function _t(e2) {
      return ((e2 && (e2.innerText || e2.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    function _cls(e2) {
      var c = e2 && e2.className;
      return (typeof c === 'string' ? c : '').slice(0, 60);
    }
    try {
      if (el) {
        out.found = true;
        out.tag = el.tagName.toLowerCase();
        out.cls = _cls(el);
        out.text = _t(el).slice(0, 120);
        var kids = el.querySelectorAll('*');
        for (var i = 0; i < kids.length && i < 25; i++) {
          out.kids.push('<' + kids[i].tagName.toLowerCase()
                        + (kids[i].id ? '#' + kids[i].id : '')
                        + '> ' + _cls(kids[i]) + '  [' + _t(kids[i]).slice(0, 30) + ']');
        }
      }
      var icons = document.querySelectorAll(
        'i[class*="xuanzhong"], i[class*="weixuan"]');
      for (var k = 0; k < icons.length && k < 8; k++) {
        var p = icons[k].parentElement;
        out.icons.push(_cls(icons[k]) + ' | 父=' + (p ? p.tagName.toLowerCase() : '')
                       + '.' + _cls(p));
      }
      var ins = document.querySelectorAll('input');
      for (var j = 0; j < ins.length && j < 10; j++) {
        var r = ins[j].getBoundingClientRect();
        out.inputs.push(ins[j].type + (ins[j].id ? '#' + ins[j].id : '')
                        + '.' + _cls(ins[j])
                        + ' checked=' + !!ins[j].checked
                        + ' disabled=' + !!ins[j].disabled
                        + ' 可见=' + (r.width > 0 && r.height > 0));
      }
      out.toggles = document.querySelectorAll(
        'input[type=checkbox], [role="checkbox"], [class*="checkbox"],'
        + ' [class*="xuanzhong"], [class*="weixuan"]').length;
    } catch (e) {
      out.error = '' + e;
    }
    return out;
    """

    # 按文案找提交按钮（类名不可靠时用）。
    H5_SUBMIT_BY_TEXT_JS = r"""
    var marks = arguments[0] || [];
    function _t(el) {
      return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    // 标签必须扫全，不能只列 button/div/span/a/section。
    //   底部那一条（协议 + 提交）是服务端模板渲染的，标签不由前端包决定：
    //   真站出现过 `<div class="..."><p>提交订单</p></div>` 这种，
    //   白名单里没有 p，而外层 div 有子元素会被「叶子优先」跳过，
    //   结果文案明明在页面上，却报「找不到提交订单按钮」（2026-10-08 实跑日志）。
    //   改成扫所有元素：叶子 + 短文案 + 可见 + 有尺寸，四条已经足够筛掉容器。
    var all = document.querySelectorAll('*');
    var best = null, bestTop = -1;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = _t(el);
      if (!t || t.length > 12) continue;           // 按钮文案都很短
      // 分两档匹配（2026-10-09）：长词才准包含，光杆词只准精确。
      //   只按「包含」匹配的话，「提交」二字会命中页面上任何一句带它的话
      //   （实测脚本就对着非按钮连点 8 次，日志上只剩「点了没反应」）；
      //   可光删光杆词又会漏掉按钮文字本来就叫「提交」的那种。
      var ok = false;
      for (var m = 0; m < marks.length; m++) {
        if (t === marks[m]) { ok = true; break; }
        if (marks[m].length >= 3 && t.indexOf(marks[m]) >= 0) { ok = true; break; }
      }
      if (!ok) continue;
      // 叶子优先：有子元素的容器会把整块正文的文案也算进去
      if (el.children && el.children.length) continue;
      var st = getComputedStyle(el);
      if (!st || st.display === 'none' || st.visibility === 'hidden'
          || st.opacity === '0') continue;
      var r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 12) continue;
      // 提交按钮在页面底部，取最靠下的那个
      if (r.top > bestTop) { bestTop = r.top; best = el; }
    }
    return best;
    """

    # 等页面底部（协议条 +「提交订单」那条）真的渲染出来。2026-10-08 实跑里「观演人 1/1 勾好了，下一行就是
    # 找不到提交按钮」，原因是列表和底部不是同一批渲染的，脚本只等了列表。底部是服务端模板下发的，判据只能用文案在不在。
    H5_BOTTOM_READY_JS = r"""
    var marks = arguments[0] || [];
    var out = {readyState: '', hasText: false, hasBtn: false,
               bottomCount: 0, bottom: [], ready: false};
    function _t(el) {
      return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    try { out.readyState = document.readyState || ''; } catch (e0) {}
    var body = document.body ? (document.body.innerText || '') : '';
    for (var m = 0; m < marks.length; m++) {
      // 光杆词（「提交」「下单」…）不参与「底部好了没」的判断，
      // 页面上随便一句话带这俩字就会让这里误判成「底部已就绪」
      if (marks[m].length < 3) continue;
      if (body.indexOf(marks[m]) >= 0) { out.hasText = true; break; }
    }
    var sels = ['[class*="submit"]', '[class*="Submit"]', '[class*="pay"]',
                '[class*="Pay"]', 'button', '[class*="btn"]', '[class*="Btn"]'];
    for (var s = 0; s < sels.length && !out.hasBtn; s++) {
      try {
        var lst = document.querySelectorAll(sels[s]);
        for (var q = 0; q < lst.length; q++) {
          var rq = lst[q].getBoundingClientRect();
          if (rq.width >= 20 && rq.height >= 12) { out.hasBtn = true; break; }
        }
      } catch (e1) {}
    }
    var lim = (window.innerHeight || 800) * 0.6;
    var all = document.querySelectorAll('*');
    var bucket = [];
    for (var k = 0; k < all.length; k++) {
      var e2 = all[k];
      if (e2.children && e2.children.length) continue;
      var t2 = _t(e2);
      if (!t2 || t2.length > 16) continue;
      var r2 = e2.getBoundingClientRect();
      if (r2.top < lim || r2.width < 16 || r2.height < 10) continue;
      out.bottomCount++;
      bucket.push({top: Math.round(r2.top),
                   s: '<' + e2.tagName.toLowerCase() + '> ' + t2});
      if (bucket.length >= 100) break;
    }
    // 取「最靠下」的那几条，底部那一条就在这里，别被上面一长串列表占满名额
    bucket.sort(function (a, b) { return b.top - a.top; });
    for (var z = 0; z < bucket.length && z < 12; z++) out.bottom.push(bucket[z].s);
    out.ready = !!(out.hasText && (out.hasBtn || out.bottomCount > 0));
    return out;
    """

    # 找不到提交按钮时，把「按钮那一片长什么样」抓回来。原来的快照只覆盖观演人区，可按钮找不到时观演人区
    # 再详细也没用，日志里只有一句「找不到提交订单按钮」，等于让人拿不到任何可改的线索。
    H5_SUBMIT_AREA_JS = r"""
    var marks = arguments[0] || [];
    var out = {frames: [], cands: [], bottom: [], submitLike: [],
               has: {}, disabled: 0, error: ''};
    function _cls(el) {
      var c = el && el.className;
      return (typeof c === 'string' ? c : '').slice(0, 70);
    }
    function _t(el) {
      return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
    }
    function _vis(el) {
      var st = getComputedStyle(el);
      return !!(st && st.display !== 'none' && st.visibility !== 'hidden');
    }
    try {
      var fr = document.querySelectorAll('iframe');
      for (var i = 0; i < fr.length && i < 5; i++) {
        out.frames.push((fr[i].id || '(无id)') + ' | ' + _cls(fr[i]) + ' | '
                        + String(fr[i].src || '').slice(0, 70));
      }
      // 1) 类名/标签像「按钮」的，全列出来
      var sels = ['[class*="submit"]', '[class*="Submit"]', '[class*="pay"]',
                  '[class*="Pay"]', 'button', '[class*="btn"]', '[class*="Btn"]',
                  '[id*="submit"]', '[id*="pay"]'];
      var seen = [];
      for (var s = 0; s < sels.length; s++) {
        var list = document.querySelectorAll(sels[s]);
        for (var j = 0; j < list.length && out.cands.length < 25; j++) {
          var el = list[j];
          if (seen.indexOf(el) >= 0) continue;
          seen.push(el);
          if (!_vis(el)) continue;
          var r = el.getBoundingClientRect();
          if (r.width < 10 || r.height < 8) continue;
          out.cands.push(el.tagName.toLowerCase() + '#' + (el.id || '')
                         + '.' + _cls(el) + '  [' + _t(el).slice(0, 24) + ']'
                         + ' top=' + Math.round(r.top) + ' h=' + Math.round(r.height));
        }
      }
      // 2) 页面下半部的短文本叶子，提交条一定在这一带
      // 必须按最靠下取，不能按 DOM 顺序取前 N 个：
      //   2026-10-09 实跑里 10 行观演人（姓名 + 身份证各一条）就把 20 个名额
      //   全占满了，真正在最底部的协议条和提交条一个都没进快照，
      //   而那恰恰是出问题的地方。等于「快照拍了，但拍的是没用的那半页」。
      var lim = innerHeight * 0.6;
      var all = document.querySelectorAll('*');
      var bucket = [];
      for (var k = 0; k < all.length; k++) {
        var e2 = all[k];
        if (e2.children && e2.children.length) continue;
        var t2 = _t(e2);
        if (!t2 || t2.length > 16) continue;
        if (!_vis(e2)) continue;
        var r2 = e2.getBoundingClientRect();
        if (r2.top < lim || r2.width < 16 || r2.height < 10) continue;
        bucket.push({top: Math.round(r2.top),
                     s: '<' + e2.tagName.toLowerCase() + '.' + _cls(e2) + '> '
                        + t2 + '   top=' + Math.round(r2.top)
                        + ' h=' + Math.round(r2.height)});
        if (bucket.length >= 100) break;
      }
      bucket.sort(function (a, b) { return b.top - a.top; });
      for (var z2 = 0; z2 < bucket.length && z2 < 20; z2++) {
        out.bottom.push(bucket[z2].s);
      }
      // 3) 「像提交按钮」的东西，四条路一起找，不筛可见性。
      //    2026-10-09：用户说「有按钮，点了没反应」，可上一版快照里
      //      连一个「提交」都没抓到，说明按钮的文字或载体在旧判据之外：
      //        · 按钮文字可能是 `value` / `aria-label`（不算 innerText）；
      //        · 可能是原生 `<button>`，或文案压根不叫「提交订单」；
      //        · 也可能整个条在 iframe 里。
      //      所以四条路都走一遍，并记下「为什么算它是按钮」。
      out.submitLike = [];
      function _label(e3) {
        var s = _t(e3);
        if (!s) { try { s = e3.value || ''; } catch (e0) {} }
        if (!s) { try { s = e3.getAttribute('aria-label') || ''; } catch (e1) {} }
        if (!s) { try { s = e3.title || ''; } catch (e2) {} }
        return (s || '').replace(/\s+/g, ' ').trim();
      }
      for (var s2 = 0; s2 < all.length; s2++) {
        var e3 = all[s2];
        var tg = e3.tagName.toLowerCase();
        var cl = _cls(e3);
        var t3 = _label(e3);
        var why = '';
        if (tg === 'button') why = '原生button';
        else if (tg === 'input' && /submit|button/i.test(e3.type || '')) why = 'input-' + e3.type;
        else if (/submit|btn|buy|confirm/i.test(cl + ' ' + (e3.id || ''))) why = '类名';
        else {
          for (var m3 = 0; m3 < marks.length; m3++) {
            if (t3 === marks[m3]) { why = '文案精确'; break; }
            if (marks[m3].length >= 3 && t3.indexOf(marks[m3]) >= 0) { why = '文案含'; break; }
          }
        }
        if (!why) continue;
        if (tg !== 'button' && tg !== 'input'
            && e3.children && e3.children.length) continue;
        var r3 = e3.getBoundingClientRect();
        out.submitLike.push('[' + why + '] <' + tg + (e3.id ? '#' + e3.id : '')
                            + '> cls=' + cl + ' [' + t3.slice(0, 20) + ']'
                            + ' top=' + Math.round(r3.top)
                            + ' w=' + Math.round(r3.width)
                            + ' h=' + Math.round(r3.height)
                            + ' 可见=' + _vis(e3));
        if (out.submitLike.length >= 15) break;
      }
      var body = document.body ? (document.body.innerText || '') : '';
      out.has = {submit: body.indexOf('提交') >= 0, pay: body.indexOf('支付') >= 0,
                 buy: body.indexOf('购买') >= 0, order: body.indexOf('订单') >= 0};
      out.disabled = document.querySelectorAll(
        '[disabled], [class*="disabled"], [class*="disable"]').length;
    } catch (e) {
      out.error = '' + e;
    }
    return out;
    """

    # 「滚进视野 + 算视口坐标 + 命中测试」合成一次往返。老写法三个独立步骤、中间还插 time.sleep(0.15) 等滚动；
    # 其实 scrollIntoView 是同步的（behavior:instant 绕开 CSS 平滑滚动），同一 JS 里紧接着读 getBoundingClientRect 已是滚动后位置。
    H5_CLICK_POINT_JS = r"""
    var el = arguments[0];
    if (!el) return null;
    try { el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'}); }
    catch (e) {
      try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e2) {}
    }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return null;
    var vw = innerWidth, vh = innerHeight;
    var cands = [[0.5, 0.5], [0.25, 0.3], [0.5, 0.3], [0.75, 0.3],
                 [0.25, 0.7], [0.5, 0.7], [0.75, 0.7]];
    for (var i = 0; i < cands.length; i++) {
      var x = Math.round(r.left + r.width * cands[i][0]);
      var y = Math.round(r.top + r.height * cands[i][1]);
      if (x < 1 || y < 1 || x > vw - 2 || y > vh - 2) continue;
      var hit = document.elementFromPoint(x, y);
      if (!hit || !(hit === el || el.contains(hit))) continue;
      // 别点到「目标内部的链接」上（2026-10-09 用户提到「有时会跳到淘宝」）。
      //   我们常拿「往上几层」的容器当候选（防 onClick 挂在外层），
      //   而那一层里往往有《购票须知》这种 <a>，甚至淘宝/支付宝的推广位，
      //   中心点正好压在链接上时，一按下去页面就跑了：
      //   轻则白点一次，重则脚本把「页面跳转」当成「提交成功」，整段判断全错。
      //   目标自己就是 <a> 时不设限（按钮本来就有做成链接的）。
      if (el.tagName.toLowerCase() !== 'a') {
        var a = (hit.closest ? hit.closest('a[href]') : null);
        if (a && a !== el && el.contains(a)) continue;   // 换下一个候选点
      }
      return {x: x, y: y};
    }
    return null;
    """

    # 一次问清「页面现在什么状态」：地址、标题、正文、toast。老的提交轮询一轮里分别读 title、current_url、
    # body.text 和 toast 的四五个选择器，等于每轮 5 次往返，15 轮下来光问路就 75 次。
    H5_PAGE_PROBE_JS = r"""
    var out = {url: '', title: '', body: '', toast: ''};
    try { out.url = location.href; } catch (e) {}
    try { out.title = document.title || ''; } catch (e) {}
    try {
      out.body = ((document.body && (document.body.innerText || '')) || '').slice(0, 4000);
    } catch (e) {}
    try {
      // 选择器必须比原来宽得多。老写法只有 toast/message 三类，
      //   而第二代订单页的提示是服务端模板下发的，类名不由前端包决定，
      //   实跑里连点 4 次「提交订单」，日志里一条提示都没有，
      //   人只能看到「点了没反应」，根本不知道大麦为什么拒（2026-10-08）。
      //   所以这里把弹层类的类名都扫一遍，并限定「短文本 + 可见」防误报。
      var sels = ['.bui-toast', '[class*="toast"]', '[class*="Toast"]',
                  '[class*="message"]', '[class*="Message"]',
                  '[class*="notice"]', '[class*="Notice"]',
                  '[class*="error"]', '[class*="Error"]',
                  '[class*="dialog"]', '[class*="Dialog"]',
                  '[class*="modal"]', '[class*="Modal"]',
                  '[class*="popup"]', '[class*="Popup"]'];
      for (var i = 0; i < sels.length; i++) {
        var els = document.querySelectorAll(sels[i]);
        for (var j = 0; j < els.length; j++) {
          var el = els[j], st = getComputedStyle(el);
          if (!st || st.display === 'none' || st.visibility === 'hidden'
              || st.opacity === '0') continue;
          var t = ((el.innerText || el.textContent) || '').trim();
          if (!t || t.length > 120) continue;      // 提示都是一句话
          var r = el.getBoundingClientRect();
          if (r.width < 8 || r.height < 8) continue;
          out.toast = t;
          return out;
        }
      }
    } catch (e) {}
    return out;
    """

    def _probe_page(self):
        """一次往返问清页面状态。取不到返回 {}。

        这是下单环节唯一的探针：把 title / url / body / toast 合成一句话问。
        """
        try:
            r = self.driver.execute_script(self.H5_PAGE_PROBE_JS)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _payment_state_from_probe(probe):
        """用探针结果判断「是不是已经在收银台」。语义与 _is_payment_page() 一致。

        只用强信号，避免把订单确认页误判成支付页，那会导致订单提交不了。
        """
        if not probe:
            return False
        title = probe.get('title') or ''
        if '收银台' in title or '订单提交成功' in title:
            return True
        low = (probe.get('url') or '').lower()
        if 'cashier' in low or '/pay' in low:
            return True
        return '订单提交成功' in (probe.get('body') or '')

    def _risk_hit_in_probe(self, probe):
        """探针结果里有没有风控页的话；命中返回那个词，没命中返回 ''。

        为什么单独要一条：`_is_risk_page()` 只在「刚进订单页」那一下查过，
          而风控页恰恰是**点了提交之后**才顶上来的，后面这一路没人再看它。
        用探针已经拿到的正文/标题/提示判，不额外往返。

        搜索面是全页正文，不只是 toast：风控页是把整页换掉，不是弹一句话。
        """
        if not probe:
            return ''
        for key in ('body', 'title', 'toast'):
            text = probe.get(key) or ''
            for w in self.RISK_PAGE_WORDS:
                if w in text:
                    return w
        return ''

    def _h5_rows_info(self):
        """一次读出所有观演人行的状态。读不到（类名全换了）返回 None。

        返回 None 和返回 [] 是两件事：
          · None = 「这个方法用不了」→ 调用方必须退回逐行读
          · []   = 「页面上确实一行都没有」
          混掉这两个，换过类名的页面就会被误判成「页面上没有这个人」，
          而那正是「观演人没勾上、订单也没提交」的老毛病。
        """
        try:
            r = self.driver.execute_script(self.H5_ROWS_INFO_JS)
        except Exception:
            return None
        if not isinstance(r, list) or not r:
            return None
        return [x for x in r if isinstance(x, dict)]

    def _h5_row_state(self, row):
        """一次往返读出一行的 name/selected/disabled 原始信息。失败返回 None。"""
        try:
            r = self.driver.execute_script(self.H5_ROW_STATE_JS, row)
        except Exception:
            return None
        return r if isinstance(r, dict) else None

    def _h5_wait_until(self, pred, timeout=0.6, interval=0.04):
        """轮询等一个条件成立，成立就立刻返回。

        代替「点完先固定睡 0.3 秒再看结果」。受控组件在下一帧就把 checked
          改过去了（实测 40ms 内），固定睡 0.3 秒等于按最坏情况付钱，
          一个人的 0.3 秒看不出来，勾 4 个人就是 1.2 秒，而且这几百毫秒
          在开票窗口里是实打实的。

        实现直接委托 `_poll_until`：这两处是同一件事，各留一份实现迟早会
          漂移成两种行为（一份改了、另一份没改），那是最难查的一类 bug。
        """
        return self._poll_until(pred, timeout=timeout, interval=interval)

    def _is_h5_order_page(self):
        """当前是不是新版 H5 订单确认页（order-pay.html / h5-ultron-buy）。

        判据先看地址，再看结构（观演人列表 / 提交按钮），两个都不能少：
          · 只看结构：页面刚跳过来、DOM 还在渲染时会被判成老 PC 页（真站事故）；
          · 只看地址：大麦换个域名前缀就全断。
        标题不参与判定，风控页的 title 也叫「确认购买」，两者一模一样。
        """
        try:
            url = (self.driver.current_url or '').lower()
        except Exception:
            url = ''
        for mark in self.H5_ORDER_URL_MARKS:
            if mark in url:
                # 关掉「不选座」时，同一个应用里会先落「选座」那一步，地址前缀一样。地址命中不等于已走到确认那一步，
                # 所以再把选座特征排掉，免得抢在选座前面去下单。
                return not any(h in url for h in self.H5_SEAT_URL_HINTS)
        for sel in (self.H5_ORDER_SUBMIT_BTN, self.H5_ORDER_VIEWER_ROW,
                    self.H5_ORDER_VIEWER_ROW_ANY,
                    self.H5_ORDER_VIEWER_NAME_2):
            try:
                if self.driver.find_elements(By.CSS_SELECTOR, sel):
                    return True
            except Exception:
                continue
        return False

    def _h5_find_all(self, sel):
        """找元素，出错就返回空表（H5 页面的上下文随时可能被刷新掉）。"""
        try:
            return self.driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            return []

    def _h5_wait_viewer_list(self, timeout=8.0):
        """等观演人列表真的渲染出来再动手。返回是否等到。

        这是「大麦里明明有这个人，却一个都没勾上」最直接的原因：
          订单页的观演人是接口异步拉的，脚本原来只等
          max(1, page_load_delay/2) = 1 秒就去数行数；列表还没渲染出来，
          `_h5_viewer_rows()` 就是空表，于是判成「页面上没有这个人」，
          `picked(0) < ticket_count(1)` 成立，整单直接放弃，
          而且它不报错、不抛异常，表现就是「点了自动提交，观演人没选、订单也没提交」。

          1 秒这个值是人脑能接受的等待，不是网页能保证的加载时间。
          等不到的代价是白跑一趟抢票，多等 3 秒的代价是几乎为零，必须偏保守。

        等的同时要认风控页：它顶在订单页那个地址上渲染、标题也叫「确认购买」，
          观演人区永远是空的。进页面那一下查过风控（`_commit_order_on_h5` 开头），
          但风控页往往是等一会儿才渲染出来的，那一下就查早了、什么都看不到。
          不在这里补一次，这 8 秒就是白等，日志还会把它说成「观演人列表没渲染出来」，
          真正的原因（被拦下了）看不到。命中记在 `self._h5_risk_word`，由调用方决定怎么报。
        """
        deadline = time.time() + max(1.0, timeout)
        last = {}
        self._h5_risk_word = ''
        while True:
            self._h5_risk_word = self._risk_word_now()
            if self._h5_risk_word:
                return False
            try:
                last = self.driver.execute_script(self.H5_VIEWER_PROBE_JS) or {}
            except Exception:
                last = {}
            if (last.get('rows') or last.get('listKids')
                    or last.get('names2') or last.get('picks')):
                return True
            if time.time() >= deadline:
                self._note('  ⚠ 等了 {:.0f} 秒，观演人列表还是没渲染出来'
                           '（探针：{!r}）'.format(max(1.0, timeout), last))
                return False
            # 探针只是一次 structure 查询，很便宜；「列表出来了」直接决定开票那
            # 一下能不能勾上人。拍子带抖动（约 0.12~0.19 秒），timeout 上界一秒没缩。
            time.sleep(self._pace(0.15))

    def dump_h5_viewer_area(self, name=''):
        """把订单页观演人区的真实结构写进日志 + order_page_dump.txt。

        大麦前端一改名，写死的类名就全废，而这件事只在真站上才能发现
        （夹具是我们自己写的，永远同意自己）。所以认不出来时不要静默失败，
        把「页面上到底长什么样」留下来，下一次就能照着改。
        只记 class 与文本片段，不落证件号全文。
        """
        try:
            info = self.driver.execute_script(self.H5_DUMP_JS, name or '') or {}
        except Exception as e:
            self._note('  （连结构快照都拿不到：{}）'.format(e))
            return

        try:
            shape = self.driver.execute_script(self.PAGE_SHAPE_JS) or {}
        except Exception:
            shape = {}
        lines = ['=== 订单页观演人区结构快照 ===',
                 'title   : {}'.format(info.get('title')),
                 'url     : {}'.format(info.get('url')),
                 # 这两行是拿来分辨「真订单页，只是没配上这个人」和「被风控拦下的提示页」的：
                 # 后者标题也叫「确认购买」、地址也带 order-pay.html，只有正文不一样。
                 '风控词命中: {}'.format(self._risk_word_now() or '（没命中）'),
                 '正文可见字数: {}；提到「观演人」: {}；提到「提交订单」: {}'.format(
                     shape.get('len'), shape.get('viewer'), shape.get('submit')),
                 'class 含 viewer/viwer 的节点数: {}'.format(info.get('nodeCount')),
                 'input[type=checkbox] 数: {}'.format(info.get('boxes')),
                 'class 含 checkbox 的节点数: {}'.format(info.get('boxLike')),
                 '勾选图标（选中 icondanxuan-xuanzhong_）数: {}'.format(
                     info.get('pickOn')),
                 '勾选图标（未选 icondanxuan-weixuan_）数: {}'.format(
                     info.get('pickOff')),
                 '姓名容器 .viwer-info-name 数: {}'.format(info.get('viwerCount')),
                 '页面上出现过「{}」这个文本: {}'.format(name, info.get('nameSeen'))]
        if info.get('lines'):
            lines.append('-- 含 viewer 的节点（最多 30 个）--')
            lines.extend('  ' + x for x in info['lines'])
        if info.get('chain'):
            lines.append('-- 「{}」所在元素往上 6 层 --'.format(name))
            lines.extend('  ' + x for x in info['chain'])
        if info.get('error'):
            lines.append('脚本异常: {}'.format(info['error']))
        text = '\n'.join(lines)

        for ln in lines:
            self._note(ln)
        # 诊断产物默认写项目根目录（用户一眼能找到）；测试里会被指到临时目录，免得跑一次回归就在仓库里留个带真实姓名的文件。
        dump_dir = getattr(self, '_dump_dir', None) or _default_dump_dir()
        path = os.path.join(dump_dir, 'order_page_dump.txt')
        try:
            with io.open(path, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            self._note('  （结构快照已写入 {}，可发给开发者定位）'.format(path))
        except Exception as e:
            self._note('  （快照写文件失败：{}）'.format(e))

    def dump_h5_submit_area(self):
        """找不到提交按钮时，把按钮那一带的结构写进日志 + order_page_submit_dump.txt。

        为什么单独做一份：`dump_h5_viewer_area` 只覆盖观演人区。真站上
          「观演人勾得上、按钮找不到」是完全可能同时发生的（2026-10-08 的
          实跑日志就是这样：观演人 1/1 勾好了，下一行就是「找不到提交订单按钮」）。
          这种情况下观演人区快照一点用都没有，反而会让人以为问题在观演人那边。
        """
        try:
            info = self.driver.execute_script(
                self.H5_SUBMIT_AREA_JS, list(self.H5_SUBMIT_TEXT_MARKS)) or {}
        except Exception as e:
            self._note('  （连按钮区快照都拿不到：{}）'.format(e))
            return

        # 这两行原来直接取 driver.title / _short_url()，页面正在渲染或跳转时会抛异常（StaleElement / no such window），
        # 而这段不在 try 里，结果整个快照一份都出不来，日志只剩一句「找不到提交订单按钮」。快照是拿来救命的，自己先崩最要不得。
        try:
            title = self.driver.title
        except Exception as e:
            title = '(读不到标题：{})'.format(e)
        try:
            url = self._short_url()
        except Exception as e:
            url = '(读不到地址：{})'.format(e)
        lines = ['=== 订单页「提交按钮」区结构快照 ===',
                 'title   : {}'.format(title),
                 'url     : {}'.format(url)]
        has = info.get('has') or {}
        lines.append('正文里出现过：提交={} 支付={} 购买={} 订单={}'.format(
            has.get('submit'), has.get('pay'), has.get('buy'), has.get('order')))
        lines.append('disabled/disable 类节点数: {}'.format(info.get('disabled')))
        lines.append('iframe 数: {}'.format(len(info.get('frames') or [])))
        for x in (info.get('frames') or []):
            lines.append('  iframe  ' + x)
        if info.get('cands'):
            lines.append('-- 像按钮的节点（tag#id.class [文字] top/h）--')
            lines.extend('  ' + x for x in info['cands'])
        else:
            lines.append('-- 像按钮的节点：一个都没有 --')
        if info.get('bottom'):
            lines.append('-- 页面下半部的短文本叶子（提交条就在这一带）--')
            lines.extend('  ' + x for x in info['bottom'])
        if info.get('submitLike'):
            lines.append('-- 页面上所有含「提交/下单」的短元素（不筛可见性）--')
            lines.extend('  ' + x for x in info['submitLike'])
        else:
            lines.append('-- 页面上含「提交/下单」的短元素：一个都没有 --')
        if info.get('error'):
            lines.append('脚本异常: {}'.format(info['error']))
        text = '\n'.join(lines)

        for ln in lines:
            self._note(ln)
        dump_dir = getattr(self, '_dump_dir', None) or _default_dump_dir()
        path = os.path.join(dump_dir, 'order_page_submit_dump.txt')
        try:
            with io.open(path, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            self._note('  （按钮区快照已写入 {}，可发给开发者定位）'.format(path))
        except Exception as e:
            self._note('  （按钮区快照写文件失败：{}）'.format(e))

    def dump_h5_protocol_area(self, row=None):
        """协议区结构快照，协议勾不上时，把这一行的真实 DOM 记进日志 + 落盘。

        为什么单做一份：2026-10-09 实跑里脚本报「有协议框但没勾上」，
          而日志里关于协议区的信息一个字都没有，「页面下半部短文本」
          那一段被 10 行观演人（姓名 + 证件各一条）占满了名额，
          协议条和提交条一个都没进快照。出问题的地方恰恰是没拍到的地方。
        """
        if row is None:
            try:
                row, _box, _icon = self._protocol_by_text()
            except Exception:
                row = None
        try:
            info = self.driver.execute_script(self.H5_PROTOCOL_DUMP_JS, row) or {}
        except Exception as e:
            self._note('  （连协议区快照都拿不到：{}）'.format(e))
            return

        lines = ['=== 订单页「购票协议」区结构快照 ===']
        if not info.get('found'):
            lines.append('按文案没能定位到协议行（{} 一个都没匹配上）'.format(
                '、'.join(self.H5_PROTOCOL_TEXT_MARKS)))
        else:
            lines.append('协议行: <{}.{}>  [{}]'.format(
                info.get('tag'), info.get('cls'), info.get('text')))
            for x in (info.get('kids') or []):
                lines.append('    ' + x)
        lines.append('-- 页面上的勾选图标（最多 8 个）--')
        for x in (info.get('icons') or []):
            lines.append('    ' + x)
        lines.append('-- 页面上的 input（最多 10 个）--')
        for x in (info.get('inputs') or []):
            lines.append('    ' + x)
        if info.get('error'):
            lines.append('脚本异常: {}'.format(info['error']))
        text = '\n'.join(lines)

        for ln in lines:
            self._note(ln)
        dump_dir = getattr(self, '_dump_dir', None) or _default_dump_dir()
        path = os.path.join(dump_dir, 'order_page_protocol_dump.txt')
        try:
            with io.open(path, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            self._note('  （协议区快照已写入 {}，可发给开发者定位）'.format(path))
        except Exception as e:
            self._note('  （协议区快照写文件失败：{}）'.format(e))

    def _h5_click_point(self, el):
        """滚进视野 + 算出「真的落在它上面」的视口坐标，一次往返做完。

        不能用 el.rect，那是文档坐标，页面一滚动就点到视口外面去了。
          必须用 getBoundingClientRect 的视口坐标 + elementFromPoint 命中测试。
        也不用 _view_point_on()：那个方法刻意不滚动（工装契约，被 S25 钉着），
          元素在首屏之外时它会老老实实返回 None。这里是「要去点它」，得先滚。
        """
        try:
            r = self.driver.execute_script(self.H5_CLICK_POINT_JS, el)
        except Exception:
            return None
        if not isinstance(r, dict):
            return None
        try:
            return int(r['x']), int(r['y'])
        except Exception:
            return None

    # 「按钮中心点命中的到底是谁」，不是按钮自己就是被盖着了。2026-10-09 用户说「点提交订单没反应，有时还跳到淘宝」，
    # 两件事合起来很像按钮那带被一个链接盖着：点下去要么命中链接，要么事件被吃掉。这条能一眼看清。
    H5_BLOCKER_JS = r"""
    var el = arguments[0];
    if (!el) return '';
    try { el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'}); }
    catch (e0) {
      try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e1) {}
    }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return '';
    var x = Math.round(r.left + r.width / 2);
    var y = Math.round(r.top + r.height / 2);
    if (x < 1 || y < 1 || x > innerWidth - 2 || y > innerHeight - 2) return '';
    var hit = document.elementFromPoint(x, y);
    if (!hit) return '';
    if (hit === el || el.contains(hit)) return '';
    function _d(e2) {
      var c = e2 && e2.className;
      c = (typeof c === 'string' ? c : '').slice(0, 40);
      return '<' + e2.tagName.toLowerCase() + (e2.id ? '#' + e2.id : '') + '.' + c + '>';
    }
    var t = ((hit.innerText || hit.textContent) || '').replace(/\s+/g, ' ').trim();
    var a = hit.closest ? hit.closest('a[href]') : null;
    return _d(hit) + (t ? ' [' + t.slice(0, 20) + ']' : '')
           + (a ? '  ← 它是个链接：' + String(a.href || '').slice(0, 90) : '')
           + '  z-index=' + (getComputedStyle(hit).zIndex || 'auto');
    """

    def _h5_blocker_of(self, el):
        """按钮中点命中的到底是谁？是它自己就返回 ''，否则返回遮挡物描述。"""
        try:
            return self.driver.execute_script(self.H5_BLOCKER_JS, el) or ''
        except Exception:
            return ''

    def _h5_click(self, el):
        """点一个 H5 元素：滚到视野里，找「真的能点到它」的视口坐标，发 CDP 真实鼠标事件。

        这一整套 H5 页面（选座页 / 订单页）都不认 JS 造出来的 click，
        选座页那边已经实测过了。

        三步合成一次往返（H5_CLICK_POINT_JS），中间那 0.15 秒的「等滚动落定」
          也去掉了，scrollIntoView 是同步的，同一个 JS 里紧接着读 rect
          拿到的就是滚动后的位置。
        """
        for _ in range(2):
            pt = self._h5_click_point(el)
            if pt and self._click_point_cdp(pt[0], pt[1]):
                return True
        return self._js_click(el)

    def _h5_row_name(self, row):
        """读这一行的姓名。名字读不出来就返回 ''（由调用方走文本兜底）。

        走一次往返的状态探针（姓名 + 勾选态 + 不可用一起拿）。
          原来要 2 次（find_elements + text），而按姓名找一行时它是逐行
          调用的，10 行就是 20 次往返。
        """
        st = self._h5_row_state(row)
        if st is not None and 'name' in st:
            return st.get('name') or ''
        for sel in (self.H5_ORDER_VIEWER_NAME,
                    '[class*="viewer-info-name"]',
                    '[class*="viewer-name"]'):
            for el in self._h5_find_all_in(row, sel):
                t = (el.text or '').strip()
                if t:
                    return t
        return ''

    def _h5_find_all_in(self, row, sel):
        try:
            return row.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            return []

    def _h5_row_selected(self, row):
        """这一行勾上了没有，读 React 受控复选框的 checked。

        读不到就返回 None（别当成「没勾上」去反复瞎点）。
        走一次往返的状态探针；探针不可用时退回「find_elements + is_selected」。
        """
        st = self._h5_row_state(row)
        if st is not None:
            return st.get('selected') if st.get('hasBox') else None
        inp = self._h5_find_all_in(row, '.bui-checkbox-input')
        if not inp:
            # 类名换过就退到「这行里任意一个复选框」
            inp = self._h5_find_all_in(row, 'input[type=checkbox]')
        if inp:
            try:
                return bool(inp[0].is_selected())
            except Exception:
                return None
        # 第二代没有复选框，勾选态是图标字体画的，别在这里返回 None（None 会被调用方当成「还没勾上」反复点，点中了也被点回去）。
        if self._h5_find_all_in(row, self.H5_ORDER_PICK_ON):
            return True
        if self._h5_find_all_in(row, self.H5_ORDER_PICK_OFF):
            return False
        return None

    def _h5_row_disabled(self, row):
        """这一行的观演人是不是「本场不可用」。

        得先认出来并跳过：真实页面里点这种行会跳走去改证件，
        脚本会莫名其妙被带离订单页。
        走一次往返的状态探针；探针不可用时退回「选择器 + 文案」两条判据。
        """
        st = self._h5_row_state(row)
        if st is not None:
            if st.get('hasTips'):
                return True
            return self._is_disabled_text(st.get('text') or '')
        for sel in (self.H5_ORDER_VIEWER_TIPS, self.H5_ORDER_VIEWER_TIPS_ANY,
                    self.H5_ORDER_VIEWER_TIPS_2,
                    self.H5_ORDER_VIEWER_ROW_DISABLED):
            if self._h5_find_all_in(row, sel):
                return True
        return self._is_disabled_text(self._safe_text(row))

    @staticmethod
    def _is_disabled_text(text):
        """「本场不可用」的文案判据。

        只认「本场不可用 / 本场不支持」这种明确说法，用宽泛的「不可用」容易把
        正常行误判掉，误判的代价是这个人被跳过，那正是「明明有这个人却没勾上」。
        """
        return any(w in text for w in
                   ('本场不可用', '本场不支持', '本场不参与', '不可选'))

    def _h5_viewer_rows(self):
        rows = self._h5_find_all(self.H5_ORDER_VIEWER_ROW)
        if rows:
            return rows
        rows = self._h5_find_all(self.H5_ORDER_VIEWER_ROW_ANY)
        if rows:
            return rows
        # 第二代（h5-ultron-buy）没有「行」这个类名，只能从姓名容器往上爬到整行。这里走 JS 枚举，和
        # H5_ROWS_INFO_JS / H5_ROW_EXACT_JS 共用同一份 _rows()，保证「第 i 行」各处指同一元素；少了这步
        # _h5_viewer_rows() 永远是空表，空表会被读成「页面上没这个人」，整单放弃且不报错。
        try:
            return self.driver.execute_script(self.H5_VIEWER_ROWS_JS) or []
        except Exception:
            return []

    def _h5_viewer_row(self, name):
        """按姓名找观演人那一行。只认「名字完全相等」，避免点错人。

        优先级（省往返的排前面，但正确性优先）：
          ① 一次读全表（1 次）→ 在 Python 里比对姓名 → 取回那一行（1 次）= 2 次
             这一层最稳：用的就是 _h5_viewer_rows() 那批元素，行序完全一致。
             正常情况（真站类名没变）都走这里，从 21 次往返降到 2 次。
          ② 原来那条逐行读姓名的路，只在前两层都没命中时才走，代价 20+ 次。
             它有个别的路替代不了的性质：只在真正的观演人行里找，不会找错。
          ③ 按文本精确找（1 次，不吃类名）+ 前缀兜底，前端换过类名时靠它。
        """
        want = (name or '').strip()
        if not want:
            return None

        # ① 一次读全表 + 按姓名定位
        infos = self._h5_rows_info()
        if infos is not None:
            idx = None
            for it in infos:
                if (it.get('name') or '').strip() == want:
                    idx = it.get('i')
                    break
            if idx is not None:
                rows = self._h5_viewer_rows()
                if 0 <= idx < len(rows):
                    return rows[idx]

        for row in self._h5_viewer_rows():
            if self._h5_row_name(row) == want:
                return row

        # ③ 类名全对不上时的第二道：按文本精确找，再往上爬到「里面只有一个复选框」那层，这一招不吃任何类名。
        try:
            row = self.driver.execute_script(self.H5_ROW_EXACT_JS, want)
        except Exception:
            row = None
        if row is not None:
            return row

        # 最后一道：允许前缀匹配（原来的兜底，命中面更宽也更容易找错人，只留最后，不给优先权）。
        try:
            return self.driver.execute_script(self.H5_ROW_BY_NAME_JS, want)
        except Exception:
            return None

    def _h5_selected_names(self):
        """当前页面上已经勾上的观演人姓名（只读，用来校验结果）。

        一次往返读出全表勾选态。老写法是逐行「读姓名 + 读勾选」，
          10 行 = 40 次往返；而它每次勾完人都要被调用一次做校验。
        """
        infos = self._h5_rows_info()
        if infos is not None:
            return [(it.get('name') or '(读不到姓名)')
                    for it in infos if it.get('selected')]
        out = []
        for row in self._h5_viewer_rows():
            if self._h5_row_selected(row):
                out.append(self._h5_row_name(row) or '(读不到姓名)')
        return out

    def _h5_click_viewer_row(self, row, attempt=1):
        """点「一行观演人」。返回是否真的把这一行点成了。

        顺序不能反：第一下点整行，点不动才退到点复选框。
          理由是真实实现把 toggle 挂在整行的 onClick 上，整行就是
          「用户本来会点的地方」；而复选框是受控组件，它自己很可能也带
          onChange，两个处理器一起触发就是一来一回，等于没点。

        为什么不直接点复选框（一开始就是这么写的，被测试打回来了）：
          点 `<input>` 会让浏览器原生把 checked 翻成 true，而 App 层如果
          拒绝这次选择（比如人数已满），状态并没有跟着变，这时读回 checked
          就会误判成「勾上了」。点整行不会有这个问题：整行没有原生状态。
          而且一行里只有复选框有原生 checked，判断「成没成」这件事本身
          就该以 App 的状态为准。
        """
        if attempt <= 1:
            self._h5_click(row)
            return True
        for sel in ('.bui-checkbox-input', 'input[type=checkbox]',
                    '[class*="checkbox"]'):
            els = self._h5_find_all_in(row, sel)
            if els:
                self._h5_click(els[0])
                return True
        self._h5_click(row)
        return True

    def _h5_viewer_picked(self, name, row=None):
        """这个观演人现在勾上了吗，一次往返问清（勾完人的自检就靠它）。

        名字里必须带 viewer：类里已经有一个 `_h5_picked()`，是选座页
          判断「座位选上没有」的。重名会把那个覆盖掉，选座流程会直接崩
          （Python 取最后一个定义），这种错只会在真跑选座时才暴露。
        """
        infos = self._h5_rows_info()
        if infos is not None:
            want = (name or '').strip()
            return any(it.get('selected') and (it.get('name') or '').strip() == want
                       for it in infos)
        r = self._h5_viewer_row(name) if row is None else row
        return bool(r is not None and self._h5_row_selected(r))

    def select_viewer_on_h5(self, name, tries=3):
        """在 H5 订单页勾选一个观演人。勾上了返回 True。

        「勾上了没有」不看点击是否成功，而是回读勾选态，
        点空、被 toast 拦下来都算没勾上。

        两代页面的勾选态载体不同：第一代是受控复选框的 checked，
          第二代是图标字体（`i.icondanxuan-xuanzhong_`）。两种都读不出来时，
          退到「点一下、看这一行的外观指纹变没变」，那一招不吃任何类名。
        """
        row = self._h5_viewer_row(name)
        if row is None:
            w = self._risk_word_now()
            if w:
                # 这时候说「订单页上没有这个人」会把人带偏：整页被换成风控提示页了，
                # 观演人区当然一个都没有，跟「名单里没配这个人」是两回事。
                self._note(f'  ⚠ 页面上出现风控提示（正文命中「{w}」）—— 大麦把这一页拦下了，'
                           f'不是「{name}」没配上。停手，不再重试。')
                return False
            self._note(f'  ⚠ 订单页上没有找到叫「{name}」的观演人')
            self.dump_h5_viewer_area(name)
            return False
        sel_state = self._h5_row_selected(row)
        if sel_state:
            self._note(f'  · 「{name}」本来就是勾着的')
            return True
        if self._h5_row_disabled(row):
            self._note(f'  ⚠ 「{name}」这一场不可用（大麦标了原因），跳过')
            return False

        # 勾选态读不出来（两代选择器全对不上）才启用指纹兜底。读得出就别用，它只能判「变了没有」，判不出「变成什么样」。
        sig_before = None
        if sel_state is None:
            sig_before = self._h5_viewer_signature(name)

        for attempt in range(1, max(1, tries) + 1):
            self._h5_click_viewer_row(row, attempt)
            # 点完轮询到勾上就立刻走，不再固定睡 0.3 秒。受控组件下一帧就改好 checked（实测 40ms 内），
            # 固定睡是按最坏情况付钱；勾 4 个人白付 1.2 秒，那点时间在开票窗口里是实打实的。等不到才走原来的重试逻辑。
            if self._h5_wait_until(lambda: self._h5_viewer_picked(name, row),
                                   timeout=0.6):
                return True
            tip = self._h5_toast_text()
            if tip and ('已选齐' in tip or '最多' in tip):
                self._note(f'  ⚠ 大麦提示：{tip}（说明人数已经够了）')
                return False
            if sig_before is not None:
                now = self._h5_viewer_signature(name)
                if now and now != sig_before:
                    self._note('  · 「{}」的勾选态读不出来，但这一行点完确实变了'
                               '（外观指纹变化）→ 认定为已勾上'.format(name))
                    return True
            if attempt < max(1, tries):
                self._note(f'  · 第 {attempt} 次点「{name}」没生效，换个位置再试')
                # 页面可能重渲染过，重新取一行再点
                row = self._h5_viewer_row(name) or row
        # 怎么点都没成，把页面真实结构留一份，下次照着改。认不出来时静默失败，正是这次事故拖这么久的原因。
        self.dump_h5_viewer_area(name)
        return False

    def _h5_row_signature(self, row):
        """这一行当时的「外观指纹」（标签名 + class + aria/checked 拼起来）。

        勾选态两代判据都读不出来时的最后一道。代价是只能判「变了没有」，
        所以只在 `_h5_row_selected()` 返回 None 时才用。
        """
        try:
            return self.driver.execute_script(self.H5_ROW_SIG_JS, row)
        except Exception:
            return None

    def _h5_viewer_signature(self, name):
        """按姓名取当前那一行的外观指纹。

        必须每次重新定位，不能抱着点击前那个元素：真页面的列表是 React
          渲染的，点一下整块会被重建，旧元素立刻失效（StaleElement），
          拿失效元素取指纹只会得到 None，兜底就永远不触发。
        """
        want = (name or '').strip()
        infos = self._h5_rows_info()
        rows = self._h5_viewer_rows()
        if infos:
            for it in infos:
                if (it.get('name') or '').strip() == want:
                    idx = it.get('i')
                    if isinstance(idx, int) and 0 <= idx < len(rows):
                        return self._h5_row_signature(rows[idx])
        row = self._h5_viewer_row(name)
        return self._h5_row_signature(row) if row is not None else None

    def select_users_on_h5(self, users_to_select):
        """按配置顺序勾选观演人，返回实际勾上的数量。"""
        picked = 0
        for i, name in enumerate(users_to_select, 1):
            if self.select_viewer_on_h5(name):
                picked += 1
                self._note(f'  ✓ 已勾选观演人 {i}/{len(users_to_select)}：{name}')
            else:
                self._note(f'  ✗ 没能勾上观演人：{name}')
        return picked

    def agree_protocol_on_h5(self):
        """勾上「我已阅读并同意…」。

        不勾这一下，大麦点「提交订单」会弹「请先阅读并同意…」并且不提交，
        这正是「勾了自动提交却什么都没发生」的一个直接原因。

        两代页面都要认。第一代（order-pay.html）协议是
          `.dmprotocol-box .bui-checkbox-input`；第二代的底部是服务端模板
          渲染的（`dmProtocol` / `dmSubmit` 只是模板组件名，DOM 由接口下发），
          类名不由前端包决定，所以类名找不着时再按文案兜一层。

        勾选态有三种载体，一个都不能少（2026-10-09 实跑踩的）：
          ① 真 `input[type=checkbox]` → 读 `checked`；
          ② 图标字体（`i.icondanxuan-xuanzhong_` / `icondanxuan-weixuan_`），
             这一代大麦的勾选框就是它画的，协议和观演人用的是同一个组件；
          ③ 什么都没有 → 只能看点完外观变不变（指纹）。
          原来只认 ①：真站上协议行里那个「影子」input 永远不 checked，
          于是脚本报「有协议框但没勾上」，而大麦正因为没勾协议把 8 次提交全拦了。

        返回 True=勾上了 / False=有协议框但没勾上 / None=页面上没有协议框。
        """
        row, box, icon = self._find_protocol_row()
        if row is None:
            return None

        if self._h5_protocol_state(box, icon) is True:
            return True

        # 点谁：先图标，再复选框，最后整行。整行放最后，行里通常有《购票须知》那种链接，点到会跳走离开订单页。
        for t in (icon, box, row):
            if t is None:
                continue
            self._h5_click(t)
            if self._h5_wait_until(self._h5_protocol_state_now, timeout=0.7):
                return True

        # 三种载体全读不出来：这一行的勾选态只能靠外观变化判断。和观演人那边同理：点一下可能整块重渲染、
        # 旧元素立刻失效，所以指纹每次重新定位再取。
        if box is None and icon is None:
            sig = self._h5_protocol_signature(row)
            self._h5_click(row)
            self._h5_wait_until(lambda: self._h5_protocol_signature(row) != sig,
                                timeout=0.6)
            now = self._h5_protocol_signature(row)
            if sig and now and now != sig:
                return True
        return False

    def _find_protocol_row(self):
        """找「我已阅读并同意」那一行。返回 (row, box, icon)，找不到是三个 None。

        先按第一代类名（`.dmprotocol-box .bui-checkbox-input`），
        再按文案，第二代的底部是服务端模板下发的，类名不固定。
        """
        try:
            boxes = [b for b in self.driver.find_elements(
                By.CSS_SELECTOR, self.H5_ORDER_PROTOCOL_INPUT)
                if self._visible(b)]
        except Exception:
            boxes = []
        if boxes:
            box = boxes[0]
            row = None
            try:
                row = box.find_element(By.XPATH, '..')
            except Exception:
                pass
            return row, box, self._h5_pick_icon(row)

        row, box, icon = self._protocol_by_text()
        if row is None:
            return None, None, None
        if icon is None:
            icon = self._h5_pick_icon(row)
        if box is None and icon is None:
            # 再宽一点：这一行里任何 input 都算（模板可能换个 type/id）
            try:
                els = row.find_elements(By.CSS_SELECTOR, 'input')
            except Exception:
                els = []
            if els:
                box = els[0]
        return row, box, icon

    def _h5_pick_icon(self, row):
        """这一行里的勾选图标（选中/未选都算）。没有就返回 None。"""
        if row is None:
            return None
        try:
            found = row.find_elements(
                By.CSS_SELECTOR,
                'i[class*="xuanzhong"], i[class*="weixuan"]')
        except Exception:
            return None
        return found[0] if found else None

    def _h5_protocol_state(self, box, icon):
        """协议行勾没勾上：True / False / None（读不出来）。

        图标优先于 input：这一代大麦的勾选框是图标字体画的，
          页面里可能还留着一个模板自带的「影子」input，它永远不 checked，
          先读它就会得出「没勾上」的错误结论，然后一次接一次地点。
        """
        if icon is not None:
            try:
                cls = icon.get_attribute('class') or ''
            except Exception:
                cls = ''
            if 'xuanzhong' in cls:
                return True
            if 'weixuan' in cls:
                return False
        if box is not None:
            try:
                return bool(box.is_selected())
            except Exception:
                pass
        return None

    def _h5_protocol_state_now(self):
        """重新定位协议行再读态，点完可能整块被重渲染，旧元素立刻失效。"""
        row, box, icon = self._find_protocol_row()
        if row is None:
            return False
        return self._h5_protocol_state(box, icon)

    def _h5_protocol_signature(self, row):
        """协议行的外观指纹。元素失效了就按文案重新找一行再取。"""
        s = self._h5_row_signature(row)
        if s is not None:
            return s
        r2, _box, _icon = self._protocol_by_text()
        return self._h5_row_signature(r2) if r2 is not None else None

    def _protocol_by_text(self):
        """按文案找协议行。返回 (row, box, icon)；找不到是 (None, None, None)。"""
        try:
            r = self.driver.execute_script(self.H5_PROTOCOL_BY_TEXT_JS,
                                           list(self.H5_PROTOCOL_TEXT_MARKS))
        except Exception:
            return None, None, None
        if not isinstance(r, dict):
            return None, None, None
        return r.get('row'), r.get('box'), r.get('icon')

    @staticmethod
    def _protocol_checked(box):
        try:
            return bool(box.is_selected())
        except Exception:
            return False

    def _h5_toast_text(self):
        """页面上正在显示的 toast 文案（大麦用它报错，比如「所需观演人已选齐」）。

        走一次往返的页面探针（和「地址/标题/正文」一起拿）。
          老写法要按 4 个选择器逐个找、再逐个问「显示了吗」，最多 8 次往返，
          而它在重试循环里每轮都要问一次。
        """
        probe = self._probe_page()
        if probe:
            return (probe.get('toast') or '').strip() or None
        # 探针拿不到（JS 上下文异常）时退回逐条查
        for sel in ('.bui-toast', '[class*="toast"]', '[class*="Toast"]',
                    '[class*="message"]'):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if not self._visible(el):
                        continue
                    t = (el.text or '').strip()
                except Exception:
                    continue
                if t:
                    return t
        return None

    # 「排队/拥挤」：服务端没收下这次请求，重提就能过。依据是大麦自己的用户指引，官方口径就是「不要退出，
    # 持续点击提交」。这类提示下观演人和协议都是好的，但要尽快重提：老写法固定等满 3 秒才重试一次，三次就 9 秒。
    H5_BUSY_WORDS = ('排队', '拥挤', '人数过多', '稍后再试', '稍后重试',
                     '系统繁忙', '繁忙', '被挤爆', '太火爆', '火爆')
    # 这类是脚本自己的问题（人没勾、协议没勾），重提一万次也没用，只会白连累账号，见到就停手、把浏览器留给用户。
    H5_SUBMIT_STOP_WORDS = ('观演人', '协议', '须知', '实名', '证件', '操作异常')
    # 被排队拦下后，隔多久重提：随机在区间内取值，别用固定节奏。
    H5_BUSY_RETRY_RANGE = (0.15, 0.35)
    # 一次下单最多点几次「提交订单」（含被排队拦掉的那些）
    H5_SUBMIT_ATTEMPTS = 8
    # 跳到这些域名不算提交成功，是误点了页面上的外链（《购票须知》、淘宝/支付宝推广位都在订单页底部那带）。
    H5_NOT_PAY_HOSTS = ('taobao.com', 'tmall.com', 'alipay.com', 'alibaba.com')

    def _h5_wait_bottom(self, timeout=15.0):
        """等页面底部（协议条 +「提交订单」那一条）渲染出来。返回是否等到。

        为什么必须有这一步：观演人列表和底部不是同一批渲染的，
          而 `_h5_wait_viewer_list()` 只等列表，列表一出来脚本就往下走，
          那一瞬间底部还是空的，于是「找不到提交订单按钮」直接整单放弃。
          2026-10-08 实跑日志里这三件事全在同一秒内发生，就是这个原因。

          等到就立刻返回（正常 1~3 秒），所以多给几秒几乎不付代价；
          而漏等的代价是白跑一趟抢票。
        """
        deadline = time.time() + max(1.0, timeout)
        last = {}
        while True:
            try:
                last = self.driver.execute_script(
                    self.H5_BOTTOM_READY_JS,
                    list(self.H5_SUBMIT_TEXT_MARKS)) or {}
            except Exception:
                last = {}
            if last.get('ready'):
                return True
            if time.time() >= deadline:
                self._note('  ⚠ 等了 {:.0f} 秒，页面底部（提交条/协议条）'
                           '还是没渲染出来（探针：readyState={} 有提交文案={} '
                           '有按钮类节点={} 下半部短文本数={}）'.format(
                               max(1.0, timeout), last.get('readyState'),
                               last.get('hasText'), last.get('hasBtn'),
                               last.get('bottomCount')))
                for x in (last.get('bottom') or [])[:12]:
                    self._note('      下半部：{}'.format(x))
                return False
            time.sleep(self._pace(0.12))

    def _h5_submit_candidates(self):
        """「提交订单」按钮的候选列表，按可信度从高到低排。

        为什么不能只有一个：底部是服务端模板渲染的，写死的 `.pay-submit-btn`
          经常对不上；按文案找到的又常常是里层的 `<p>提交订单</p>`，
          而 onClick 挂在外层容器上，点里层可能根本不触发。
          2026-10-08 实跑里按钮「找到了」却连点 4 次毫无反应，就卡在这里。
          所以把「类名按钮 / 文案叶子 / 叶子往上两层」都列为候选，逐个试。
        """
        out = []
        try:
            for el in self.driver.find_elements(By.CSS_SELECTOR,
                                                self.H5_ORDER_SUBMIT_BTN):
                out.append(el)
        except Exception:
            pass
        try:
            leaf = self.driver.execute_script(
                self.H5_SUBMIT_BY_TEXT_JS, list(self.H5_SUBMIT_TEXT_MARKS))
        except Exception:
            leaf = None
        node = leaf
        for _ in range(3):                      # 叶子本身 + 往上 2 层
            if node is None:
                break
            out.append(node)
            try:
                node = node.find_element(By.XPATH, '..')
            except Exception:
                node = None
        # ③ 原生按钮也收（放最后，优先级最低）：真站按钮可能是 <button> 或 <input type=submit>，
        #    文字不一定是「提交订单」，但载体本身就是按钮。
        for sel in ('button', 'input[type=submit]', 'input[type=button]'):
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if self._visible(el):
                        out.append(el)
            except Exception:
                pass
        # 去重（同一个元素可能被两条路子都找到）
        seen, uniq = set(), []
        for el in out:
            try:
                key = el.id
            except Exception:
                key = id(el)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(el)
        return uniq

    def _h5_submit_button(self):
        """取「提交订单」按钮；取不到返回 None。

        第一代靠 `.pay-submit-btn`。第二代的底部是服务端模板渲染的
          （`dmSubmit` 只是模板组件名，真正的 DOM 由接口下发），类名不由
          前端包决定，类名找不到时按文案找，取页面最靠下的那个。
        """
        try:
            return self.driver.find_element(By.CSS_SELECTOR,
                                            self.H5_ORDER_SUBMIT_BTN)
        except Exception:
            pass
        try:
            return self.driver.execute_script(
                self.H5_SUBMIT_BY_TEXT_JS, list(self.H5_SUBMIT_TEXT_MARKS)) or None
        except Exception:
            return None

    @staticmethod
    def _diff_new_text(old, new):
        """正文里新冒出来的短句，大麦的提示往往就是加一句话进正文。

        为什么要这一层：老写法只按类名抓 toast，而 2026-10-08 / 10-09
          两次实跑都是「连点 8 次提交，一条提示都没抓到」，人只能看到
          「点了没反应」，完全不知道大麦为什么拒。类名抓不到时，
          「正文里新多出来的那句话」就是最可靠的线索。
        只报短句（≤ 60 字）且含中文的，防误报。
        """
        if not old or not new or old == new:
            return ''
        old_lines = set(x.strip() for x in old.split('\n') if x.strip())
        for line in new.split('\n'):
            line = line.strip()
            if not line or len(line) > 60:
                continue
            if line in old_lines:
                continue
            if not any('\u4e00' <= c <= '\u9fff' for c in line):
                continue
            return line
        return ''

    # 「点下去到底有没有真的发出请求」，这是判「点空了/被挡住」还是「请求发出去了、被服务端静默拒了」的唯一硬证据
    # （2026-10-09：按钮在、协议也勾了，脚本和人都点不动、大麦一个字都不回）。用 performance API 记一笔，点击前后比一比。
    H5_PERF_MARK_JS = r"""
    window.__damaiPerfMark = (performance.getEntriesByType('resource') || []).length;
    return window.__damaiPerfMark;
    """

    H5_PERF_DIFF_JS = r"""
    var all = performance.getEntriesByType('resource') || [];
    var mark = window.__damaiPerfMark || 0;
    var out = [];
    for (var i = mark; i < all.length && out.length < 12; i++) {
      out.push(String(all[i].name || '').slice(0, 110)
               + '  [' + Math.round(all[i].duration) + 'ms]');
    }
    return out;
    """

    def _h5_body_text(self):
        """页面正文（截断过的），用来对比点击前后新冒出来的提示。"""
        try:
            return self.driver.execute_script(
                "return ((document.body && (document.body.innerText || '')) || '')"
                ".slice(0, 4000);") or ''
        except Exception:
            return ''

    def _h5_poll_after_submit(self, url_before, timeout=3.0, body_before=''):
        """点完提交后，盯着页面看它给什么反应，一有结论就立刻返回。

        返回最后一次的探针结果（dict）。判定顺序：
          · 跳走了 / 进收银台  → 立刻返回（成功，没什么可等的）
          · 弹了「排队/拥挤」  → 立刻返回（调用方要尽快重提，不能等满 3 秒）
          · 弹了「观演人没勾」 → 立刻返回（这类等多久都不会变）
          · 其它提示或毫无反应 → 继续轮询到 timeout

        每轮只发一次往返（H5_PAGE_PROBE_JS 把地址/标题/正文/toast 一起拿）。
          老写法每轮要问 5 次（title、current_url、body.text、toast 的几个
          选择器），15 轮就是 75 次往返，而且必须等满 3 秒才知道没戏。
        """
        deadline = time.time() + max(0.2, timeout)
        interval = 0.04
        last = {}
        while True:
            last = self._probe_page()
            if self._payment_state_from_probe(last):
                return last
            if last.get('url') and last.get('url') != url_before:
                return last
            tip = (last.get('toast') or '').strip()
            if not tip and body_before:
                # toast 一个都没抓到时的最后一道：正文里新冒出来的短句。大麦的提示不一定落在我们认得的选择器里，
                # 但总归会把一句话加进正文，捞出来当提示，人就能看见它到底说了什么。
                tip = self._diff_new_text(body_before, last.get('body') or '')
                if tip:
                    last['toast'] = tip
            if tip and (any(k in tip for k in self.H5_BUSY_WORDS)
                        or any(k in tip for k in self.H5_SUBMIT_STOP_WORDS)):
                return last          # 已经能定性了，不必再等
            if self._risk_hit_in_probe(last):
                # 风控页同样「已能定性」，而且是最该立刻停手的一种，再等满 3 秒只是白拖时间。
                return last
            if time.time() >= deadline:
                return last
            time.sleep(interval)
            interval = min(interval * 1.6, 0.2)

    def _submit_order_on_h5(self):
        """点「提交订单」，并且真的校验提交结果。

        老写法点完就打印「订单已提交」，不管有没有生效，
        用户看到的是一次「明明点了，却什么都没发生」。
        这里点完一定回头确认：跳转了 / 进收银台了 / 大麦弹了什么提示。

        开票瞬间最常见的失败不是「脚本错了」，而是服务端排队：
          点下去弹一句「当前排队人数过多，请稍后再试」。观演人勾了、协议勾了，
          只是这一下没被收下。这种情况必须尽快重提，
          老写法每轮固定等 3 秒，8 次机会要 24 秒，窗口早关了。
        """
        if self._is_payment_page():
            self._note('***当前已在支付环节，脚本到此为止，请手动完成支付***')
            return True

        # 先等底部渲染出来。观演人列表和底部不是同一批渲染，只等列表就动手的话底部还是空的，直接判「找不到按钮」整单放弃。
        self._h5_wait_bottom()

        cands = self._h5_submit_candidates()
        if not cands:
            self._note('  ⚠ 找不到「提交订单」按钮（类名和文案都没找到），'
                       '页面结构可能又变了')
            # 先抓按钮区，再抓观演人区，两份都留着，免得分不清是哪一半变了
            self.dump_h5_submit_area()
            self.dump_h5_viewer_area()
            return False

        probe0 = self._probe_page()
        url_before = probe0.get('url') or ''
        busy_rounds = 0
        # 换候选只在「点了毫无反应」时才做。被排队拦下是点中了、服务端没收，必须继续点同一个元素；换候选反而会点到
        # 里层文案（onClick 挂外层）而一次都点不响，白白烧掉开票窗口里最宝贵的重试机会（S31 钉着这一点）。
        idx = 0

        for attempt in range(1, max(1, self.H5_SUBMIT_ATTEMPTS) + 1):
            btn = cands[idx % len(cands)]
            self._note(f'***提交订单（第 {attempt} 次）***')
            # 点之前先记下正文，点完要对比出「新多出来的那句话」
            body_before = self._h5_body_text()
            # 再记一个「已发出的请求数」的基线，点完比一比
            try:
                self.driver.execute_script(self.H5_PERF_MARK_JS)
            except Exception:
                pass
            self._h5_click(btn)

            probe = self._h5_poll_after_submit(url_before, body_before=body_before)

            pay = self._payment_state_from_probe(probe)
            risk = self._risk_hit_in_probe(probe)

            # 页面既像收银台、正文里又有风控话时就都摆出来。报哪边都可能是错的，代价正好相反：报「抢到了」
            # 其实被拦，人以为票到手了（整套工具最贵的一种错）；报「被拦了」其实已站在付款页，人会错过付款。
            # 脚本分不清，所以只把看到的摆出来，让人自己看一眼再停手。（收银台判据里的 '/pay' in url 会误中顶在订单页地址上渲染的拦截页，写成一条是不留敞口。）
            if pay and risk:
                self._note('  ⚠ 页面同时出现了「像支付页」和「像风控页」的特征'
                           '（风控命中「{}」）—— 脚本不下结论。'.format(risk))
                self._note('     它可能已经到付款页了，也可能只是被拦下后停在这个地址上。')
                self._note('     浏览器给你留着，请自己看一眼；脚本不再重复提交。')
                return False

            if pay:
                self._note('***订单已提交，停在支付环节 —— 付款请你自己来***')
                return True

            # 风控页排在「跳转 = 提交成功」前面判。大麦拦下提交时有时连地址一起换，那就是一次跳转；先判跳转会把
            # 「被拦了」报成「订单已提交」，等于报假结果。放这儿还有个好处：不再往下点第 2…8 次（大麦正在拦这个账号，接着锤只会被拦更久）。
            if risk:
                self._note('  ⚠ 大麦返回风控提示页（命中「{}」）—— 这次提交被拦下了，'
                           '脚本停手，不再重复点。'.format(risk))
                self._note('    本脚本不做风控规避（不换 IP、不伪造指纹、不解验证码），'
                           '也不重试。')
                self._note('    建议：等十几分钟再试，或者换个网络重新来。')
                return False

            if probe.get('url') and probe.get('url') != url_before:
                gone = probe.get('url') or ''
                # 「页面跳了」不等于「提交成功了」（2026-10-09）。我们点的那片里可能有外链（《购票须知》、淘宝/支付宝推广位），
                # 点偏了一样会跳走，而老写法一律当成功，等于把「误点了一次」报成「抢到了」。跳到这几个域名的明确不算。
                if any(d in gone for d in self.H5_NOT_PAY_HOSTS):
                    self._note('  ⚠ 页面跳到了：{}'.format(gone[:120]))
                    self._note('     这是**误点了页面上的外链**，不是提交成功 —— '
                               '抢票没成功，浏览器留给你手动处理。')
                    return False
                self._note('***订单已提交（页面已跳转）***')
                return True

            tip = (probe.get('toast') or '').strip()
            if tip:
                self._note(f'  ⚠ 大麦提示：{tip}')
                if any(k in tip for k in self.H5_SUBMIT_STOP_WORDS):
                    self._note('  ⚠ 这条得人工处理，脚本不再重试')
                    return False
                if any(k in tip for k in self.H5_BUSY_WORDS):
                    busy_rounds += 1
                    if attempt >= max(1, self.H5_SUBMIT_ATTEMPTS):
                        break
                    wait = random.uniform(*self.H5_BUSY_RETRY_RANGE)
                    self._note('  · 这是排队/拥挤提示 —— 请求没被受理，'
                               '{:.2f} 秒后立刻重提'.format(wait))
                    time.sleep(wait)
                    # 整块可能重渲染过，取一次最新的元素；但不换候选（idx 不变），排队说明点中了，换反而点不响。
                    cands = self._h5_submit_candidates() or cands
                    continue

            # 页面毫无反应时先看网络：这个点击到底发没发出请求？发出了 = 服务端静默拒的（页面/账号层面的事，
            # 脚本改不动）；没发出 = 没点中（被挡住、或这个元素根本不挂 onClick）。两种情况在画面上长得一模一样，只有网络能分开。
            try:
                reqs = self.driver.execute_script(self.H5_PERF_DIFF_JS) or []
            except Exception:
                reqs = []
            if reqs:
                self._note('  · 这一次点击发出了 {} 个请求：'.format(len(reqs)))
                for x in reqs[:5]:
                    self._note('      ' + x)
            else:
                self._note('  · 这一次点击**一个请求都没发出来** —— 多半是没点中'
                           '（被挡住 / 这个元素不挂 onClick），不是服务端拒的')
                blk = self._h5_blocker_of(btn)
                if blk:
                    self._note('      按钮上面盖着的是：{}'.format(blk))
                else:
                    self._note('      按钮中点没被别的元素盖着 —— 那更像是'
                               '这个元素自己不挂点击事件')
            # 换下一个候选（点了毫无反应 = 多半点空了）
            if attempt < max(1, self.H5_SUBMIT_ATTEMPTS):
                idx += 1
                cands = self._h5_submit_candidates() or cands

        if busy_rounds:
            self._note('  ⚠ 连续 {} 次都被「排队」拦下 —— 这通常是这一场真没票了，'
                       '不是脚本的问题。'.format(busy_rounds))
            self._note('     大麦的订单页会在余票回流时重新放行，可以先留着浏览器继续点。')
        else:
            self._note('  ⚠ 点了「提交订单」但页面没有任何变化 —— 这次大概率没提交上。')
            # 一定要把底部抓下来。老写法到这儿只剩一句话，人根本不知道大麦为什么没受理（协议没勾？按钮禁用？）。
            self.dump_h5_submit_area()
        self._note('     浏览器给你留着，可以自己点一下看看大麦弹什么。')
        return False

    def _commit_order_on_h5(self, ticket_count, users_to_select):
        """新版 H5 订单页：勾观演人，（开了自动才）勾协议，提交，再校验。

        「勾购票协议」这一步只在用户明确开了「自动提交订单」时才做。
          替人在「我已阅读并同意…」上打勾是有法律含义的动作，用户没开自动下单
          就等于没授权这件事。老 PC 那条路本来就从不碰协议，这样两边才一致。
        """
        self._note('  识别到新版 H5 订单确认页（地址：{}）'.format(self._short_url()))
        self._note(f'  配置里要选的观演人：{list(users_to_select) or "（空）"}')

        # 地址对上不等于就是真订单页：大麦拦请求时吐一个地址仍带 order-pay.html、正文写着「操作异常」的提示页。
        # 不先认出来，就会在这儿干等观演人列表直到超时，日志只剩「解析到 0 行观演人」，看不出真实原因。
        if self._is_risk_page():
            self._note('  ⚠ 大麦返回的是提示页，不是订单确认页 —— 这次请求被拦下了，'
                       '脚本停手，不再重试。')
            self._note('    建议：等十几分钟再试，或者就在这个浏览器里手动继续。')
            return False

        # 先等列表渲染出来。订单页的观演人是异步拉的，等不到就数出 0 行，接着判成「页面上没有这个人」而整单放弃。
        self._h5_wait_viewer_list()
        if getattr(self, '_h5_risk_word', ''):
            # 风控页顶在订单页地址上渲染、标题也叫「确认购买」，等观演人这几秒里才渲染出来是常事
            # （进页面那一下查过一次，那次查早了）。再往下就是白勾、白点，而每多点一次，
            # 大麦那边只会拦得更久，所以认出来就停手。
            self._note('  ⚠ 大麦返回的是风控提示页（正文命中「{}」），不是订单确认页。'.format(
                self._h5_risk_word))
            self._note('    这次请求被拦下了 —— 脚本停手，不勾观演人、也不点提交。')
            self._note('    浏览器留给你：可以等十几分钟再试，或就在这个窗口里手动继续。')
            self.dump_h5_viewer_area(users_to_select[0] if users_to_select else '')
            return False

        rows = self._h5_viewer_rows()
        infos = self._h5_rows_info()
        if infos is not None:
            # 一次往返读完所有行状态。老写法逐行调 3 次（读姓名 / 读勾选态 / 读是否不可用），10 行就是 70 次往返，
            # 而这只是打日志用的。
            self._note(f'  订单页上解析到 {len(infos)} 行观演人')
            for it in infos:
                self._note('    · [{}] {} | 已勾选={} | 本场不可用={}'.format(
                    (it.get('i') or 0) + 1, it.get('name') or '(读不到姓名)',
                    it.get('selected'), it.get('disabled')))
        else:
            self._note(f'  订单页上解析到 {len(rows)} 行观演人')
            for i, r in enumerate(rows, 1):
                self._note('    · [{}] {} | 已勾选={} | 本场不可用={}'.format(
                    i, self._h5_row_name(r) or '(读不到姓名)',
                    self._h5_row_selected(r), self._h5_row_disabled(r)))
        if not rows and infos is None:
            # 写死的类名一个都没匹配上。先试按姓名找，找得到说明只是类名换了、不用打扰用户；连姓名都找不到，才是真认不出这页。
            probe = (users_to_select[0] if users_to_select else '')
            if probe and self._h5_viewer_row(probe) is not None:
                self._note('  · 按已知类名没匹配到观演人行，但按姓名找到了'
                           '「{}」—— 前端大概换过类名，已走文本兜底'.format(probe))
            else:
                self.dump_h5_viewer_area(probe)

        picked = self.select_users_on_h5(users_to_select)
        self._note(f'***已选择 {picked}/{ticket_count} 个观众***')
        selected = self._h5_selected_names()
        if selected:
            self._note(f'  ✓ 页面上勾中的观演人：{"、".join(selected)}')

        if not self.config.if_commit_order:
            self._note('  （未开启「自动提交订单」，停在确认页等你手动提交）')
            self._note('    观演人已经替你勾好；「我已阅读并同意」那一下留给你本人点。')
            return False

        if picked < ticket_count:
            # 等列表那一步之后、勾人这几下之间页面也可能被换成风控页，再认一次再下结论。
            w = self._risk_word_now()
            if w:
                self._note('  ⚠ 勾观演人这几下里大麦换成了风控提示页（正文命中「{}」）'
                           '—— 停手，不再重试。'.format(w))
                return False
            self._note('  ⚠ 观演人没勾够，大麦不会接受提交。浏览器留给你手动补。')
            return False

        # 底部（协议条 + 提交条）比观演人列表晚渲染。不等的话协议会被判成「页面上没有购票协议框」而跳过，可大麦那边
        # 照样因没勾选拦下提交，日志只看到「没有协议框，跳过」+「点了没反应」，看不出这两件事是一回事。底部已渲染好则立刻返回。
        self._h5_wait_bottom()

        proto = self.agree_protocol_on_h5()
        if proto:
            self._note('  ✓ 已勾选「我已阅读并同意」购票协议')
        elif proto is False:
            self._note('  ⚠ 有协议框但没勾上 —— 大麦会拦下提交，请手动勾一下')
            # 勾不上就一定要把协议区拍下来。上次（2026-10-09）日志里只有这一句，协议区长什么样全靠猜。
            self.dump_h5_protocol_area()
        else:
            self._note('  · 这一场页面上没有购票协议框，跳过')
            # 底部明明渲染出来了却说找不到协议框，这本身就是信号。真站吃过亏：日志写着「没有协议框，跳过」，
            # 而大麦正因为没勾协议把提交拦了，提交那边只显示「点了没反应」，两行日志看着毫不相干。所以把底部留一行。
            try:
                info = self.driver.execute_script(
                    self.H5_BOTTOM_READY_JS,
                    list(self.H5_SUBMIT_TEXT_MARKS)) or {}
                for x in (info.get('bottom') or [])[:8]:
                    self._note('      底部：{}'.format(x))
            except Exception:
                pass

        return self._submit_order_on_h5()

    def commit_order(self):
        """提交订单。

        现在要认两种订单确认页：
          · 老版 PC 页（buy__button / view-name 那一套）→ 走原来的老逻辑
          · 新版 H5 页（order-pay.html、h5-ultron-buy/index.html）
            → 走 _commit_order_on_h5

        不再拿 self.status 当门槛。原来要求 status == 3，而「点完购买先落到
          选座页」那条分支会 break 出主循环、status 还停在 2，于是整段下单逻辑
          被静默跳过，表现就是「勾了自动提交订单，结果观演人没选、订单也没提交」。
          改成的判据是「人是不是真的站在订单确认页上」，这才是不该出错的那个条件。
        """
        if not (self._is_order_confirmation_page() or self._is_h5_order_page()):
            # 提前返回也留一句话：静默跳过是最难查的一类 bug。
            self._note('  （当前不在订单确认页上，跳过下单这一步）')
            return

        self._note('***开始确认订单***')

        ticket_count = len(self.config.users)
        users_to_select = self.config.users[:ticket_count]

        # 先认页面形态再决定要不要等。fast_mode 下那 1 秒死等不如 _h5_wait_viewer_list() 真等到列表渲染。
        # 这一支刻意不走 dismiss_obstructions / _unblock_submit_buttons：那两个是给老 PC 页写的，靠「谁盖着
        # 按钮就隐藏谁」的命中测试，在这页会误伤真实内容（实测隐藏掉 2 层），观演人列表被隐藏就再也勾不上。
        if self._is_h5_order_page():
            self._note('  这是新版 H5 订单页，走 H5 那一套（不套用老 PC 的隐藏浮层兜底）')
            if self.scan_layer_present():
                self.dismiss_scan_layer()
            return self._commit_order_on_h5(ticket_count, users_to_select)

        # 老 PC 页才需要这段等待（它的购票人是异步渲染的，且没有等列表的探针）
        if not self.config.fast_mode:
            print('⏳ 等待订单确认页加载...\n')
            time.sleep(self.config.page_load_delay)
        else:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(max(1, self.config.page_load_delay / 2))
            except Exception:
                time.sleep(self.config.page_load_delay)

        self._note('  这是老版 PC 订单页，走原来的逻辑')
        self.dismiss_obstructions(rounds=3)
        self._unblock_submit_buttons()

        # 数量已在详情页选择，订单确认页无需再选。快速模式减少输出但保留关键信息。
        if not self.config.fast_mode:
            print(f"  购票数量: {ticket_count} 张（已在详情页选择）\n")
            print(f"  配置观众: {self.config.users}")
            print(f"  需要选择观众: {ticket_count} 个\n")

        try:
            if not self.config.fast_mode:
                self._scan_page_info()
                self._scan_page_text()
                self._scan_elements("input", "输入框")
                self._scan_elements("button", "按钮")

            # 扫描用户元素（支持自动重试：5次，每次间隔0.5秒）
            user_found = self._scan_user_elements(retry_count=5, retry_interval=0.5)

            self._select_users(ticket_count, users_to_select)

        except Exception as e:
            print("***购票人信息选择过程出现异常***\n")
            print(f"  异常信息: {e}")
            print("\n  建议:")
            print("    1. 在浏览器中手动选择购票人")
            if not self.config.fast_mode:
                print("    2. 查看上方扫描输出，确认用户名格式")
            print("    3. 检查用户名是否与配置一致")
            print(f"    4. 确保选择 {ticket_count} 个观众\n")

        if self.config.fast_mode:
            time.sleep(0.1)  # 快速模式：几乎不等待
        else:
            time.sleep(0.5)  # 正常模式：等待0.5秒

        if self.config.if_commit_order:
            self._submit_order()

    def _select_details_core(self, mobile):
        """详情页的「选什么」三步：城市、场次、票价。返回是否全部按配置选中。

        这三步的结果是要不要继续买的闸门（见 choose_ticket）：
          城市选错买错城市，场次选错买错日期，票档选错买错价格。
          任何一步没按配置选中，调用方都必须停下，绝不能拿着页面
          默认选中的那份往下买（2026-10-10 客户实测：要 80 元、80 缺货，
          页面默认选着 280 元，老写法直接把 280 买了）。
        """
        if mobile:
            select_city = self.select_city_on_page
            select_date = self.select_date_on_page
            select_price = self.select_price_on_page
        else:
            select_city = self.select_city_on_page_pc
            select_date = self.select_date_on_page_pc
            select_price = self.select_price_on_page_pc

        success = True

        if self.config.city and success:
            if not self.config.fast_mode:
                print("***选择城市***")
                print(f"  目标城市: {self.config.city}")
            success = select_city()
            if not self.config.fast_mode:
                print()

        # 2. 选择场次（PC 详情页的「日历日期」那步也在这里面，见 select_date_on_page_pc）。闸门要连日历一起看：
        # 只填了日历日期也是要干活的（点日历格）；移动端没有月历格子这种形态，所以那条路不认日历日期。
        only_cal = (not mobile) and bool(
            str(getattr(self.config, 'calendar_date', '') or '').strip())
        if (self.config.dates or only_cal) and success:
            if not self.config.fast_mode:
                print("***选择场次***")
                print(f"  目标场次: {self.config.dates}")
                if only_cal:
                    print(f"  日历日期: {self.config.calendar_date}")
            success = select_date()
            if not self.config.fast_mode:
                print()

        if self.config.prices and success:
            if not self.config.fast_mode:
                print("***选择票价***")
                print(f"  目标票价: {self.config.prices}")
            success = select_price()
            if not self.config.fast_mode:
                print()

        return success

    def select_details_page_mobile(self):
        """在移动端详情页完成所有选择：城市、场次、票价、数量（优化版：快速连续执行）

        返回 True/False：城市/场次/票价是否都按配置选中了。
        数量不算在成功里，它不依赖库存，选不上不该触发「等档」重试。
        """
        if not self.config.fast_mode:
            print("⏳ 开始在移动端详情页进行选择...\n")

        success = self._select_details_core(mobile=True)

        # 4. 选择数量（只在三步都选成之后；数量选不上不算失败）
        if success and len(self.config.users) > 1:
            if not self.config.fast_mode:
                print("***选择购票数量***")
                print(f"  目标数量: {len(self.config.users)} 张")
            self.select_quantity_on_page()
            if not self.config.fast_mode:
                print()

        print("***详情页选择完成***\n")
        return success

    def select_details_page_pc(self):
        """在PC端详情页完成所有选择：城市、场次、票价、数量（优化版：快速连续执行）

        返回 True/False：城市/场次/票价是否都按配置选中了（数量不算）。
        """
        if not self.config.fast_mode:
            print("⏳ 开始在PC端详情页进行选择...\n")
            print("***扫描页面元素***\n")
            self.scan_page_elements()
            print()

        success = self._select_details_core(mobile=False)

        if success and len(self.config.users) > 1:
            if not self.config.fast_mode:
                print("***选择购票数量***")
                print(f"  目标数量: {len(self.config.users)} 张")
            self._select_quantity_on_page(platform="PC端")
            if not self.config.fast_mode:
                print()

        print("***详情页选择完成***\n")
        return success

    # 一次往返读回一批元素的文本，替代「每个候选单独读一次 el.text」：后者在详情页那三处（城市 / 场次 / 票价）
    # 上是 N 次往返，_select_option_by_config 更是「场次个数 × 元素个数」地读，最坏十几次。语义对齐 el.text：隐藏元素返回 ''。
    BATCH_TEXT_JS = r"""
    var els = arguments[0] || [], out = [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i], t = '';
      try {
        var cs = getComputedStyle(el);
        var r = el.getBoundingClientRect();
        if (cs && cs.display !== 'none' && cs.visibility !== 'hidden'
            && r.width > 0 && r.height > 0) {
          t = ((el.innerText || el.textContent) || '');
        }
      } catch (e) {}
      out.push(t);
    }
    return out;
    """

    def _batch_texts(self, elements):
        """一次往返读回一批元素的文本。顺序与入参一一对应。

        取不到（上下文异常 / 返回长度对不上）就逐个退回 el.text，
        宁可慢一点，也不能让调用方拿到一份错位的文本去点错东西。
        """
        els = list(elements or [])
        if not els:
            return []
        try:
            got = self.driver.execute_script(self.BATCH_TEXT_JS, els)
            if isinstance(got, list) and len(got) == len(els):
                return [('' if t is None else str(t)) for t in got]
        except Exception:
            pass
        out = []
        for el in els:
            try:
                out.append(el.text or '')
            except Exception:
                out.append('')
        return out

    def _click_element_by_text(self, text_content, tag_names=None, exact_match=False):
        """通过文本内容点击元素

        Args:
            text_content: 要查找的文本内容
            tag_names: 要搜索的标签名列表，默认为['div', 'span', 'button']
            exact_match: 是否精确匹配文本

        Returns:
            bool: 是否成功点击
        """
        tag_names = tag_names or ['div', 'span', 'button']

        for tag in tag_names:
            try:
                if exact_match:
                    xpath = f"//{tag}[text()='{text_content}']"
                else:
                    xpath = f"//{tag}[contains(text(), '{text_content}')]"
                elements = self.driver.find_elements(By.XPATH, xpath)
                for elem in elements[:5]:  # 只尝试前5个
                    try:
                        elem_text = elem.text.strip()
                        if (exact_match and elem_text == text_content) or \
                           (not exact_match and text_content in elem_text):
                            # 先点元素本身，点不动再退到父元素。与 _find_and_click_element 同一个毛病：写
                            # `for target in [elem, elem.find_element('..')]` 会让父元素无条件被查一次（白花一趟），
                            # 而且父元素查不到时抛出的异常会连累 elem 本身也点不成。
                            clicked_ok = False
                            try:
                                elem.click()
                                clicked_ok = True
                            except Exception:
                                try:
                                    elem.find_element(By.XPATH, '..').click()
                                    clicked_ok = True
                                except Exception:
                                    clicked_ok = False
                            if clicked_ok:
                                time.sleep(self._pace(0.5))
                                return True
                            continue
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _find_and_click_element(self, search_text, max_results=10, skip_keywords=None,
                                print_results=True, root=None):
        """查找并点击包含指定文本的元素

        Args:
            search_text: 要搜索的文本
            max_results: 最大尝试结果数
            skip_keywords: 需要跳过的关键词列表
            print_results: 是否打印搜索结果
            root: 限定在这个元素里面找（None = 整个页面）。
                  挑场次时必须限定在场次列表里：「日期标题」那类元素
                    在页面别处到处都是，点它不会选中任何场次，却会被
                    当成「点到了」，后面就拿着页面默认那场往下买了。

        Returns:
            bool: 是否成功点击
        """
        skip_keywords = skip_keywords or []
        # normalize-space(text())：text() 取直接子文本节点（正好避开「点到 html/body/整个列表」的坑），再套
        # normalize-space 就把「2026.12.27 19:30␣␣下本」里的双空格抹平了。老写法 contains(text(), X) 是字面比较，真站排版空格让它永远对不上。
        want = self._norm_ws(search_text)
        lit = self._xpath_literal(want)
        xpath = "//*[contains(normalize-space(text()), {})]".format(lit)
        if root is not None:
            elements = root.find_elements(
                By.XPATH,
                ".//*[contains(normalize-space(text()), {})]".format(lit))[:max_results]
        else:
            elements = self.driver.find_elements(By.XPATH, xpath)[:max_results]

        # 带数字的搜索词（票价）不能用 contains 判命中：搜「80」会先命中「280 元」、搜「80.0元」会先命中「280.0元」，
        # 于是「80 元 缺货」被 skip 后把 280 当 80 点了（2026-10-10 客户实测）。数字必须按「独立的数」比（见 _num_tokens）。
        s_text = str(search_text).strip()
        want_nums = self._num_tokens(s_text)

        if print_results:
            print(f"  找到 {len(elements)} 个包含 '{search_text}' 的元素")

        # 一次往返读回所有候选文本，再本地挑。老写法「读一个、判一个、点一个」，每个候选单独一次 el.text，
        # 10 个候选就是 10 次往返，而其中大多数只是被判成「不匹配」然后丢掉。
        texts = self._batch_texts(elements)

        for idx, elem in enumerate(elements):
            try:
                elem_text = (texts[idx] or '').strip()
                if not elem_text or any(kw in elem_text for kw in skip_keywords):
                    continue
                if want_nums and not all(n in self._num_tokens(elem_text) for n in want_nums):
                    continue                # 子串撞上了别的数（80.0元 撞 280.0元），不是这一项

                if print_results and len(elem_text) < 100:
                    print(f"    [{idx}] {elem_text}")

                # 先点元素本身，点不动再退到父元素。老写法把 [elem, 父元素] 在循环前就构造好，父元素一定被查过一次
                # （即使 elem 一点就中）；更糟的是它要抛异常（读文本到点击之间元素被重渲染成 stale），异常会被外层
                # except 接走，连 elem 本身都点不到，表现是「页面上明明有这一项，却怎么都选不中」。
                clicked_ok = False
                try:
                    elem.click()
                    clicked_ok = True
                except Exception:
                    try:
                        elem.find_element(By.XPATH, '..').click()
                        clicked_ok = True
                    except Exception:
                        clicked_ok = False
                if clicked_ok:
                    wait_time = 0.2 if self.config.fast_mode else 0.5
                    time.sleep(self._pace(wait_time))
                    if print_results:
                        print(f"  ✓ 已点击: {elem_text}")
                    return True
                continue
            except Exception:
                continue

        if print_results:
            print(f"  ⚠ 未找到匹配的元素")
        return False

    def _scan_elements_by_class(self, class_names, label):
        """扫描指定class的元素"""
        print(f"  🔍 扫描{label}...")
        try:
            for selector in class_names:
                try:
                    elements = self.driver.find_elements(By.CLASS_NAME, selector)
                    if elements:
                        print(f"  ✓ 找到 class='{selector}': {len(elements)} 个")
                        for idx, elem in enumerate(elements[:3]):
                            try:
                                text = elem.text.strip()[:50]
                                if text:
                                    print(f"      [{idx}] {text}")
                            except Exception:
                                pass
                        return True
                except Exception:
                    pass
            return False
        except Exception as e:
            print(f"    扫描失败: {e}")
            return False

    def scan_page_elements(self):
        """扫描页面元素，用于调试"""
        try:
            print("【1】查找城市相关元素:")
            city_selectors = ['bui-dm-tour', 'tour-list', 'city-list', 'sku-tour']
            self._scan_elements_by_class(city_selectors, "城市")

            print("\n【2】查找场次相关元素:")
            date_selectors = ['sku-times-card', 'sku-times', 'date-list', 'tour-list']
            self._scan_elements_by_class(date_selectors, "场次")

            print("\n【3】查找票价相关元素:")
            price_selectors = ['sku-tickets-card', 'sku-ticket', 'price-list', 'ticket-list']
            self._scan_elements_by_class(price_selectors, "票价")

            print("\n【4】查找所有包含日期的文本:")
            try:
                all_elements = self.driver.find_elements(By.XPATH, "//*[contains(text(), '月') or contains(text(), '日')]")
                seen = set()
                for elem in all_elements[:20]:
                    try:
                        text = elem.text.strip()
                        if text and 3 < len(text) < 100 and text not in seen:
                            print(f"  - {text}")
                            seen.add(text)
                    except Exception:
                        pass
            except Exception:
                pass

            print("\n【5】查找所有包含价格的文本:")
            try:
                all_elements = self.driver.find_elements(By.XPATH, "//*[contains(text(), '¥') or contains(text(), '元')]")
                seen = set()
                for elem in all_elements[:20]:
                    try:
                        text = elem.text.strip()
                        if text and text not in seen and len(text) < 50:
                            print(f"  - {text}")
                            seen.add(text)
                    except Exception:
                        pass
            except Exception:
                pass

        except Exception as e:
            print(f"  扫描异常: {e}")

    def select_city_on_page_pc(self):
        """在PC端详情页选择城市（支持模糊匹配）"""
        try:
            if self.driver.find_elements(value='bui-dm-tour', by=By.CLASS_NAME):
                city_name_element_list = self.driver.find_element(
                    value='bui-dm-tour', by=By.CLASS_NAME
                ).find_elements(value='tour-card', by=By.CLASS_NAME)

                if not self.config.fast_mode:
                    print(f"  找到 {len(city_name_element_list)} 个城市选项:\n")
                    cities = []
                    for city_elem in city_name_element_list:
                        try:
                            city_text = city_elem.text.strip()
                            if city_text:
                                cities.append(city_text)
                        except Exception:
                            pass
                    for idx, city_text in enumerate(cities):
                        print(f"    [{idx}] {city_text}")
                    print()

                # 匹配城市（无需显示详情）：一次往返读回所有候选文本再本地比对。老写法把 city_name_element.text
                # 写在循环里，3 个城市 3 次往返；而且「先读文本再点它」这段时间里元素一被重渲染，点击就撞上 stale。批量读完立刻点，窗口最短。
                texts = self._batch_texts(city_name_element_list)
                for idx, city_name_element in enumerate(city_name_element_list):
                    try:
                        if self.config.city in (texts[idx] or ''):
                            if not self.config.fast_mode:
                                print(f"  ✓ 匹配成功: {(texts[idx] or '').strip()}\n")
                            city_name_element.click()
                            time.sleep(self._get_wait_time(short=True))
                            return True
                    except Exception:
                        continue

            if not self.config.fast_mode:
                print(f"  尝试通用文本搜索...")
            return self._find_and_click_element(
                self.config.city,
                max_results=10,
                print_results=not self.config.fast_mode
            )

        except Exception as e:
            if not self.config.fast_mode:
                print(f"  城市选择异常: {e}")
            return False

    # 日历月份标签允许这些写法：「2026年12月」「‹ 2026年12月 ›」「12月」。文本放宽到 40 字、先去空白再比：
    # 真站月份行经常和左右箭头按钮、礼拜表头渲染在同一个元素里，按「恰好等于」永远认不出（2026-10-10 用户实测：
    # 填 2026年12月26，日历纹丝不动）。「月」后面不许跟数字/日/份，不然场次「2026年12月26日」也会被当成标签。
    _CAL_LABEL_JS = """
var month = arguments[0];
var all = document.body ? document.body.querySelectorAll('*') : [];
var best = null, bestLen = 1e9;
for (var i=0;i<all.length;i++){
  var t = ((all[i].textContent||'')+'').replace(/\\s+/g,'');
  if (!t || t.length > 40) continue;
  var m = t.match(/(20\\d{2})?年?(\\d{1,2})月(?!\\d|日|份)/);
  if (!m || parseInt(m[2],10) !== month) continue;
  if (t.length < bestLen){ best = t; bestLen = t.length; }
}
return best;
"""

    # 日历格子：文本恰好是目标日、可见、不带置灰类名，并且某个较小的祖先（去空白后不到 600 字）里含月份标签。
    # 这样票档的「280 元」、场馆信息里的「12.26-12.29」都不会被当成日历格点掉。
    _CAL_CELL_JS = """
var day = arguments[0], label = arguments[1];
function norm(s){ return ((s||'')+'').replace(/\\s+/g,''); }
function clsOf(el){
  return (typeof el.className === 'string' ? el.className
          : (el.className && el.className.baseVal) || '');
}
var DEAD_SELF = /disabled|invalid|grey|gray|dim|expired|soldout|sold-out|off|other/i;
// 往上层查时收窄：`off` / `other` 这种短词挂在容器类名上的概率太高
// （xxx-off、wh_other_dayhide），一旦命中会把整张日历判成置灰。
var DEAD_UP = /disabled|invalid|expired|soldout|sold-out|grey|gray/i;
var cands = [];
var all = document.querySelectorAll('td,div,span,li,a,button,p,em,i,b');
for (var i=0;i<all.length;i++){
  var el = all[i];
  var t = norm(el.textContent);
  if (t !== day && t !== day + '日') continue;
  var r = el.getBoundingClientRect();
  if (r.width < 5 || r.height < 5) continue;
  // 置灰标记有时在格子自己身上，有时在外一层（真站就是外一层），
  // 所以自己用宽表、往上三层用窄表。
  if (DEAD_SELF.test(clsOf(el))) continue;
  var dead = false, q = el.parentElement;
  for (var up=0; up<3 && q && q !== document.body; up++){
    if (DEAD_UP.test(clsOf(q))){ dead = true; break; }
    q = q.parentElement;
  }
  if (dead) continue;
  var st = document.defaultView.getComputedStyle(el);
  if (st.display === 'none' || st.visibility === 'hidden' || +st.opacity === 0) continue;
  if (label){
    var ok = false, p = el.parentElement;
    while (p && p !== document.body){
      var pt = norm(p.textContent);
      if (pt.indexOf(label) !== -1 && pt.length < 600){ ok = true; break; }
      p = p.parentElement;
    }
    if (!ok) continue;
  }
  cands.push(el);
}
return cands.length ? cands[cands.length-1] : null;
"""

    # 数一数「文本恰好是 1~31 的纯数字」的可见元素，判断这页像不像有日历格。像（≥5 个）却认不出月份标签时，
    # 值得留一份快照；一个都没有 = 普通列表式页面，静默走老路，一个字都别打。
    _CAL_GRID_JS = """
var all = document.querySelectorAll('td,div,span,li,a,button');
var n = 0;
for (var i=0;i<all.length;i++){
  var t = ((all[i].textContent||'')+'').trim();
  if (!/^\\d{1,2}$/.test(t)) continue;
  var v = parseInt(t,10);
  if (v < 1 || v > 31) continue;
  var r = all[i].getBoundingClientRect();
  if (r.width < 5 || r.height < 5) continue;
  n++;
}
return n;
"""

    # 详情页「场次 / 票档」两张列表有两套形态：① 老 PC 页场次 div.sku-times-card > div.bui-dm-sku-card-item，票档
    # div.sku-tickets-card > …div.item-content。② 2026-10 起新详情页（Vue）：容器都是 div.perform__order__select，
    # 靠里面 div.select_left 文案区分（日期那张多 …__calendar，场次那张多 …__performs），条目 div.select_right_list_item。一律用 [class*=…]；容器必须按 .select_left 文案找：② 里三张列表共用类名，直接 querySelector 拿到的是日期那张。
    OLD_TIMES_BOX = '[class*="sku-times-card"]'
    OLD_TIMES_ITEM = '[class*="sku-card-item"]'
    OLD_TICKETS_BOX = '[class*="sku-tickets-card"]'
    OLD_TICKETS_ITEM = '[class*="item-content"]'
    NEW_ITEM = '[class*="select_right_list_item"]'

    # 按「列表左边那个标签」找容器，两套形态都认。返回 {found, box, shape}；shape = 'old' | 'new'，调用方据此选条目选择器。
    _SKU_BOX_JS = """
function norm(s){ return ((s||'')+'').replace(/\\s+/g,' ').trim(); }
var label = arguments[0], oldCls = arguments[1];
var old = document.querySelector('[class*="' + oldCls + '"]');
if (old) return {found: true, box: old, shape: 'old'};
var boxes = document.querySelectorAll('[class*="perform__order__select"]');
for (var i=0;i<boxes.length;i++){
  var l = boxes[i].querySelector('.select_left');
  if (l && norm(l.textContent) === label) return {found: true, box: boxes[i], shape: 'new'};
}
return {found: false};
"""

    def _sku_box(self, label):
        """找「场次」/「票档」那张列表的容器。

        返回 (box元素, 条目选择器)；页面上没有这张列表 → (None, None)。

        这一步是「把匹配限定在这张列表里」的前提：全文碰运气会命中页面
          别处的日期标题，点它不选中任何场次，却会被当成「点到了」。
        """
        old_cls = 'sku-times-card' if label == '场次' else 'sku-tickets-card'
        try:
            r = self.driver.execute_script(self._SKU_BOX_JS, label, old_cls) or {}
        except Exception:
            return None, None
        if not r.get('found'):
            return None, None
        box = r.get('box')
        if box is None:
            return None, None
        if r.get('shape') == 'new':
            return box, self.NEW_ITEM
        return box, (self.OLD_TIMES_ITEM if label == '场次'
                     else self.OLD_TICKETS_ITEM)

    # 「列表刷出来了没有」：数渲染出来的条目，返回 >0 就绪 / 0 容器在但还没内容 / -1 页面上压根没这个容器。
    # 2026-10-10 客户实测：日历点完后场次列表异步重渲染（先清空、再按新日期拉回来），点完立刻匹配拿到的是空列表，
    # 判据得是结果不是过程。arguments[0] 既可以是选择器字符串，也可以是元素本身（_sku_box 给的是元素）。
    _LIST_READY_JS = """
var box = arguments[0];
if (typeof box === 'string') box = document.querySelector(box);
if (!box) return -1;
var items = box.querySelectorAll(arguments[1]);
var n = 0;
for (var i=0;i<items.length;i++){
  var el = items[i], cs = getComputedStyle(el), r = el.getBoundingClientRect();
  if (cs.display === 'none' || cs.visibility === 'hidden') continue;
  if (r.width <= 0 || r.height <= 0) continue;
  if (((el.innerText || el.textContent || '') + '').trim().length === 0) continue;
  n++;
}
return n;
"""

    # 老判据：页面上有没有「12月27日 / 12-27 / 12.27」这类字样。只在「这页没有卡片式列表容器」时兜底，见 _day_text_seen。
    _DAY_TEXT_JS = """
var re = new RegExp(arguments[0]);
var all = document.querySelectorAll('div,span,li,td,a,button,p');
for (var i=0;i<all.length;i++){
  var t = ((all[i].textContent||'')+'').trim();
  if (t.length > 0 && t.length <= 60 && re.test(t)) return true;
}
return false;
"""

    # 「这一天在这一份页面里已经点过了」的页面内标记。等档循环每 2 秒左右重来一次，每次都重新点日历格子，
    # 而重点一次会把场次列表清空重拉，于是永远卡在「刚点完、列表还没回来」这个窗口里匹配不到。标记存在 window 上，整页刷新（driver.get）会清掉。
    _CAL_PICKED_JS = "try { return window.__damai_cal_picked || ''; } catch (e) { return ''; }"
    _CAL_MARK_JS = ("try { window.__damai_cal_picked = arguments[0]; return true; }"
                    " catch (e) { return false; }")

    def _list_ready(self, box_sel, item_sel):
        """卡片列表里有几个可见、有文本的条目。>0 就绪，0 还没渲染，-1 没这个容器。"""
        try:
            return int(self.driver.execute_script(self._LIST_READY_JS, box_sel, item_sel) or 0)
        except Exception:
            return -1

    def _wait_list_ready(self, box_sel, item_sel, timeout=3.0):
        """等列表渲染出内容，再往下匹配。

        返回 True = 已经有条目了；False = 等超时还是没有（调用方照样去匹配，
        匹配不上就是空跑一次，比不匹配更安全）。没这个容器时立刻返回，
        普通列表式页面一秒都不多等（-1 不是 0）。
        """
        deadline = time.time() + timeout
        while True:
            n = self._list_ready(box_sel, item_sel)
            if n != 0:                      # >0 有内容 / -1 没容器 → 都别再等
                return n > 0
            if time.time() >= deadline:
                return False
            time.sleep(self._pace(0.2))

    def _times_box(self):
        """场次列表容器（页面上没有就返回 None）。见 _sku_box。"""
        box, _item = self._sku_box('场次')
        return box

    def _session_texts(self):
        """场次列表里每一条的原文（连续空白已归一化）。

        读不出来（这页没有已知形态的列表容器）→ `[]`。

        它同时是两处的判据：
          · 日历那一步：拿「点击前 / 点击后」比出差异才算点成，
            「列表非空」不够（点完那一瞬还挂着上一天的旧条目）；
          · 方法3「去掉日期再比」的硬闸门：只有列表里所有条目都没写日期时
            才允许走那条路（否则「19:30 下本」会命中上一天的同名场次）。
        """
        box, item_sel = self._sku_box('场次')
        if box is None:
            return []
        try:
            items = box.find_elements(By.CSS_SELECTOR, item_sel)
        except Exception:
            return []
        if not items:
            return []
        return [self._norm_ws(t) for t in self._batch_texts(items)]

    def _day_text_seen(self, month, day):
        """页面上有没有「12月27日 / 12-27 / 12.27」这类字样（老判据，兜底用）。"""
        try:
            return bool(self.driver.execute_script(
                self._DAY_TEXT_JS, r'{}[^0-9]{}'.format(month, day)))
        except Exception:
            return False

    def _dump_session_area(self, reason):
        """场次一条都对不上时，把「场次区到底长什么样」写进 session_pick_dump.txt。

        和 date_pick_dump.txt 同一个道理：真站的场次卡片文案和 DOM 我们只能
          照截图仿，与其再猜一轮选择器，不如直接要现场，用户把文件发回来，
          下一次就能修准。每个地址最多写一次（等档循环会反复走到这里）。

        返回 True = 这次真的写了（调用方据此决定要不要开口）。
        """
        try:
            if getattr(self, '_sess_dump_urls', None) is None:
                self._sess_dump_urls = set()
            if getattr(self, '_sess_empty_urls', None) is None:
                self._sess_empty_urls = set()
            try:
                url = self.driver.current_url or '?'
            except Exception:
                url = '?'
            key = url.split('?')[0]

            box, item_sel = self._sku_box('场次')
            info = self.driver.execute_script(
                self._SESSION_AREA_JS, box, item_sel or self.NEW_ITEM, '场次') or {}
            items = list(info.get('items') or [])
            found = bool(info.get('found'))

            if not items:
                if key not in self._sess_empty_urls:
                    self._sess_empty_urls.add(key)
                    self._note('  ⚠ 场次列表里一条都没匹配上（容器{}）—— {}（继续等）'
                               .format('在' if found else '没找到', reason))
                if found:
                    # 容器在、内容还没渲染，这种现场没信息量，别写下去把好的现场挤掉（等档循环每秒都走到这里）。
                    return False
                # 容器压根没找到是另一回事：那不是「还没渲染」，是「这页的列表根本不长这样」，2026-10-10 客户现场
                # 就是它，而老代码正是把这个情况静默吞掉的，手上一个字的证据都没有。这里照样留一份，只是每个地址仍只写一次。
                if key in self._sess_dump_urls:
                    return False
                self._sess_dump_urls.add(key)
                reason = '这页没有已知的场次列表容器（类名不认识）'
            else:
                if key in self._sess_dump_urls:
                    return False
                self._sess_dump_urls.add(key)

            try:
                title = self.driver.title
            except Exception:
                title = '(读不到)'
            dump_dir = getattr(self, '_dump_dir', None) or _default_dump_dir()
            path = os.path.join(dump_dir, 'session_pick_dump.txt')
            with io.open(path, 'w', encoding='utf-8') as f:
                f.write('=== 场次区现场快照（{}） ===\n'.format(reason))
                f.write('title: {}\nurl : {}\n场次: {}\n日历日期: {}\n'.format(
                    title, url, self.config.dates,
                    getattr(self.config, 'calendar_date', '')))
                f.write('容器: {}（{}）\n条目选择器: {}\n条目数(全部): {}\n\n'.format(
                    '找到了' if found else '没找到', info.get('hint') or '按标签找',
                    item_sel, info.get('count')))
                f.write('--- 页面上这些条目（原始文案） ---\n')
                for i, t in enumerate(items):
                    f.write('[{}] {}\n'.format(i, t))
                f.write('\n--- 场次区结构 ---\n')
                f.write((info.get('html') or '(拿不到)') + '\n')
            self._note('  （场次区现场快照已写入 {}，可发给开发者定位）'.format(path))
            return True
        except Exception as e:
            self._note('  （场次区快照写失败：{}）'.format(e))
            return False

    # 把「场次区」原样挖出来：容器在不在、有几个条目、每个条目的文案。arguments[0] = 容器元素（_sku_box 给的；
    # None = 没找到），arguments[1] = 条目选择器，arguments[2] = 这一栏的标题（'场次'，容器没找到时兜底取样）。存成 dict 回传（Selenium 会把 JS 对象转成 Python dict）。
    _SESSION_AREA_JS = """
function norm(s){ return ((s||'')+'').replace(/\\s+/g,' ').trim(); }
var box = arguments[0], itemSel = arguments[1], label = arguments[2];
if (!box){
  // 已知容器都没找到 → 退一步：找页面上那个写着「场次」的标签，把它的
  // 上一级容器挖出来，这样快照里至少有「这页到底长什么样」，下次能修准。
  var all = document.querySelectorAll('*'), hit = null;
  for (var i=0;i<all.length;i++){
    if (norm(all[i].textContent) === label){ hit = all[i]; break; }
  }
  if (!hit) return {found: false, count: 0, items: [], html: '',
                    hint: '连「' + label + '」这个标签都没找到'};
  var p = hit;
  for (var d=0; d<3 && p.parentElement && p.parentElement !== document.body; d++){
    p = p.parentElement;
  }
  return {found: false, count: 0, items: [],
          html: (p.outerHTML || '').slice(0, 6000),
          hint: '按「' + label + '」标签兜底取样'};
}
var items = box.querySelectorAll(itemSel), out = [];
for (var i=0;i<items.length && i<30;i++){
  var t = norm(items[i].innerText || items[i].textContent);
  if (t) out.push(t.slice(0, 120));
}
return {found: true, count: items.length, items: out,
        html: (box.outerHTML || '').slice(0, 6000)};
"""

    def _dump_date_area(self, reason, label):
        """日历认不出/点不动时，把「日期那一带」的 DOM 快照写进 logs/date_pick_dump.txt。

        真站的日历 DOM 我们没见过（S50 的夹具是照截图仿的），再猜一轮
          选择器不如直接要现场，用户把那个文件发回来，下一次就能修准。
          每个地址只写一次（等档循环每秒都会走到这里，不节流会刷爆磁盘）。
        """
        try:
            if getattr(self, '_cal_dump_urls', None) is None:
                self._cal_dump_urls = set()
            try:
                url = self.driver.current_url or '?'
            except Exception:
                url = '?'
            key = url.split('?')[0]
            if key in self._cal_dump_urls:
                return
            self._cal_dump_urls.add(key)
            try:
                title = self.driver.title
            except Exception:
                title = '(读不到)'
            html = self.driver.execute_script("""
var label = arguments[0];
var root = null;
if (label){
  var all = document.querySelectorAll('*');
  for (var i=0;i<all.length;i++){
    var t = ((all[i].textContent||'')+'').replace(/\\s+/g,'');
    if (t === label){ root = all[i]; break; }
  }
}
if (root){
  var p = root;
  while (p.parentElement && p.parentElement !== document.body
         && ((p.parentElement.textContent||'').length < 3000)) p = p.parentElement;
  root = p;
}
return ((root || document.body).outerHTML || '').slice(0, 8000);
""", label) or '(拿不到页面结构)'
            dump_dir = getattr(self, '_dump_dir', None) or _default_dump_dir()
            path = os.path.join(dump_dir, 'date_pick_dump.txt')
            with io.open(path, 'w', encoding='utf-8') as f:
                f.write('=== 日历选日期现场快照（{}） ===\n'.format(reason))
                f.write('title: {}\nurl : {}\n日历日期: {}\n场次: {}\n\n'.format(
                    title, url, getattr(self.config, 'calendar_date', ''),
                    self.config.dates))
                f.write(html + '\n')
            self._note('  ⚠ {}。现场快照已写到 {}（发给开发者可以定位）'.format(
                reason, path))
        except Exception as e:
            self._note('  （日期区快照写失败：{}）'.format(e))

    def _note_cal_once(self, key, text):
        """日历那句「我点了」做节流：每个地址每次刷新后最多 30 秒一条。

        等档循环每 2 秒左右走一遍，不节流会把日志刷成一堵墙（第一版就是），
        真正有用的记录反被挤掉；但完全不说话又看不出「它还活着」。
        """
        try:
            if getattr(self, '_cal_note_at', None) is None:
                self._cal_note_at = {}
            try:
                url = (self.driver.current_url or '?').split('?')[0]
            except Exception:
                url = '?'
            k = (url, key)
            now = time.time()
            if now - self._cal_note_at.get(k, 0.0) < 30.0:
                return
            self._cal_note_at[k] = now
        except Exception:
            pass
        self._note(text)

    def _pc_click_calendar_day(self):
        """日历控件选日期（PC 详情页的另一种形态，2026-10-10 客户实测）。

        这种页面「日期」是一个月历格子，场次列表只显示「当前选中那天」
        的场次，你要 27 号，就必须先点日历里的「27」，否则场次列表里
        根本不会出现 27 号的场次文本，后面的 class 选择器 / 全文搜索
        永远匹配不到。所以这一步要摆在场次匹配前面。

        只认 `config.calendar_date`（界面上单独那个「日历日期（选填）」框），
          不碰 `config.dates`。二者是两件事：

            · dates         = 详情页「场次」那一栏的原文
                              （"12月26日 周六 19:30 下本"）；
            · calendar_date = 月历格子要点的那一天（"2026-12-26"）。

          以前只有一个框，逼着一个值同时干两件事：能匹配场次的写法日历认不出，
          日历认得出的纯日期场次又匹配不上，用户实测「场次选不对、日期也
          选不对」。拆开之后各管各的。

        没填就不猜：calendar_date 为空 → 直接 False，连快照都不写。
          绝大多数详情页是列表式的、根本没有月历格子，不该被这一步打扰。

        其余规矩（每一条都是被真站教育出来的）：

        · 页面上要有月份标签（「2026年12月」「‹ 2026年12月 ›」「12月」都认），
          且月份号必须和配置对上；页面上根本没有月份标签、也不像有日历格
          （纯数字小格子 < 5 个）→ 就是普通列表式页面，静默走老路。
        · 只点「日历容器里、文本恰好是目标『日』、类名不像置灰」的格子；
          点用原生点击（真事件序列），JS 事件只是兜底，有的日历组件
          只听 mousedown/mouseup，element.click() 的合成 click 不够。
        · 月份对不上不替你翻月（翻月按钮各家长得不一样，瞎点不如不点），
          继续等外层循环；点了没反应/那天没场次也如实 False。
        · 认不出、点不动 → 写 date_pick_dump.txt 现场快照（每地址一次）。
        """
        raw = str(getattr(self.config, 'calendar_date', '') or '')
        cfg = next((ln.strip() for ln in raw.splitlines() if ln.strip()), '')
        if not cfg:
            return False                                 # 没填 = 这页没日历格
        md = self._date_tokens(cfg)                      # (月, 日)
        if md is None:
            if not self.config.fast_mode:
                print('  ⚠ 「日历日期」认不出月/日（要写成 2026-12-26 或 12月26日）：%s' % cfg)
            return False
        month, day = md
        try:
            # ① 月份标签：带不带年份都认，月份号必须对上
            label = self.driver.execute_script(self._CAL_LABEL_JS, month)
            if not label:
                # 像不像有日历格？像就值得留现场，不像就走老路
                grid = self.driver.execute_script(self._CAL_GRID_JS) or 0
                if grid >= 5:
                    self._dump_date_area(
                        '没认出 {} 月的日历月份标签（可能要翻月）'.format(month), None)
                return False

            key = '{}-{}'.format(month, day)

            # ② 目标格子（这一份页面上已经点过就不点了，见 _CAL_PICKED_JS）
            try:
                picked = (self.driver.execute_script(self._CAL_PICKED_JS) or '') == key
            except Exception:
                picked = False

            if picked:
                # 这一份页面已经点过了（等档循环每 2 秒左右会再来），列表那会儿就换好了，没什么可等，直接算这步做完。
                return True

            # 点击之前的列表内容，用来判断「换成新的了没有」。这是被真页面教育出来的：日历点完那一瞬，列表里
            # 还挂着上一天的旧条目（实测 0.2 秒后才换掉），所以「列表里有 2 项」在旧条目上照样成立，拿它当「就绪」会让整个匹配对着旧的一天做。
            before = self._session_texts()

            cell = self.driver.execute_script(
                self._CAL_CELL_JS, str(day), label)
            if cell is None:
                self._dump_date_area(
                    '认出了「{}」但没找到 {} 号的可点格子（可能那天不可选，'
                    '也可能日历结构和预想的不一样）'.format(label, day), label)
                return False

            # ③ 点：原生点击优先（真事件序列），不行再发补的事件序列
            try:
                cell.click()
            except Exception:
                try:
                    self.driver.execute_script("""
var a = arguments[0];
['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){
  a.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}));
});
""", cell)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", cell)

            # ④ 等列表换成新的才算点成（判据是结果，不是「看见了日期字样」）。老判据是「页面上某处出现 `12月27日`
            # 字样」，而日历选中态、日期标题里到处都是，等于什么都没验；「列表非空」也不够，点完那一瞬还是旧条目，得跟点击前的内容比出差异。
            deadline = time.time() + 6.0
            while True:
                now = self._session_texts()
                if now and now != before:
                    # 点成了才打标记：0.8 秒后等档循环再来时，别再点一遍把列表重置掉。打标记要放在确认换成新的之后，
                    # 提前打就成了「点没点成都说点过了」，往后每轮都直接返回 True。
                    try:
                        self.driver.execute_script(self._CAL_MARK_JS, key)
                    except Exception:
                        pass
                    self._note_cal_once(key, '  ✓ 已点日历里的「%d」号，%d 月的场次列表'
                                             '已换成新的（%d 项）' % (day, month, len(now)))
                    return True
                if not now and not before and self._day_text_seen(month, day):
                    # 这一页没有已知形态的列表容器（读不出条目），退回老判据，行为一字不变：认不出类名的页面原来怎么走还怎么走。
                    self._note_cal_once(key, '  ✓ 已点日历里的「%d」号，%d 月的场次刷出来了'
                                             % (day, month))
                    return True
                if time.time() >= deadline:
                    break
                time.sleep(self._pace(0.25))
            self._dump_date_area(
                '点了日历的「{}」号但 {} 月的场次列表始终没换'.format(day, month), label)
            return False
        except Exception as e:
            if not self.config.fast_mode:
                print('  日历选日期异常（忽略，走老路）: %s' % e)
            return False

    def select_date_on_page_pc(self):
        """在PC端详情页选日期 / 场次。两件事，各自独立、各自可选。

        ① 「日历日期」（config.calendar_date，界面上单独一个框，选填）
           详情页的「日期」是月历格子时，先把那一天点掉，不点，场次
           列表里根本没有那天的场次文本，后面两条路都搜不到。
           没填 = 跳过（见 _pc_click_calendar_day，一个快照都不写）。

        ② 「场次」（config.dates，可以多行）
           在下面的场次列表里按页面原文匹配：class 选择器 + 全文搜索
           （+ 填了日历日期时再补一轮「去掉日期部分」的写法，见方法3）。

        返回 True/False，该做的都做成了才算 True：
          · 只填了日历日期 → 日历点成了就是 True（当天场次由页面自己选）；
          · 只填了场次     → 老逻辑（能不能匹配上）；
          · 两个都填       → 日历点了、场次也认上了才 True。
        """
        try:
            # ① 日历（选填；没填就是 False，静默跳过）
            cal_ok = self._pc_click_calendar_day()

            dates = [str(d).strip() for d in (self.config.dates or [])
                     if str(d or '').strip()]
            if not dates:
                # 没配场次：日历点成了（或本来就没日历）就算这一步做完了。
                if not cal_ok and not self.config.fast_mode:
                    print("  没有配置场次，也没有日历日期 —— 用页面默认选中的那场")
                return bool(cal_ok)

            # ①′ 「去掉日期」那一组写法（方法3 用）先算好。只在填了日历日期、且配置里写的日期就是日历上点的那天时才
            # 算，不然就成了「拿日历抵场次」：客户配 26 号场次、日历却点 27 号（自相矛盾的配置），照旧必须如实 False，宁可不动手也不能想当然地买。见 S50 反向 3。
            loose = []
            if str(getattr(self.config, 'calendar_date', '') or '').strip():
                cal_t = self._date_tokens(str(self.config.calendar_date).strip())
                for d in dates:
                    if cal_t is None or self._date_tokens(d) != cal_t:
                        continue
                    s = self._strip_date_part(d)
                    if s and s not in dates and s not in loose:
                        loose.append(s)

            # ② 匹配场次（方法1 条目比对，方法2 文本搜索，方法3 去日期再比）。整体包一个重试窗口，而不是「等列表
            # 非空就往下比」：日历点完那一瞬列表里还挂着上一天的旧条目，实测 0.2 秒后才换掉，判据只能是匹配得上，
            # 匹配不上就等 0.25 秒重来。重试期间日志闭嘴，只在放弃那一刻打搜索过程（一页 10 条候选 × 24 轮会把日志刷爆）。
            deadline = time.time() + 6.0
            said_once = False
            last_texts = None
            while True:
                box, item_sel = self._sku_box('场次')
                items = (box.find_elements(By.CSS_SELECTOR, item_sel)
                         if box is not None else [])
                texts = [self._norm_ws(t) for t in self._batch_texts(items)] \
                    if items else []

                # 列表内容变了就打一遍（不变就不打），现场日志里就能一眼看出「先挂着 26 号的旧条目、0.2 秒后
                # 换成 27 号」这个过渡，而不用每 0.25 秒刷一屏。
                if not self.config.fast_mode and texts != last_texts:
                    if texts:
                        print("  场次列表（{} 项）：".format(len(texts)))
                        for idx, t in enumerate(texts):
                            print("    [{}] {}".format(idx, t))
                        print()
                    else:
                        print("  场次列表还是空的，等它渲染…")
                last_texts = texts

                # 方法1: 按容器里的条目逐个比配置（老 PC 页 / 新详情页都走这条）。老写法是 find_elements(By.CLASS_NAME,
                # 'sku-times-card')，新详情页那套类名一个都没有，于是这步整个被跳过，只剩「全文碰运气」（那条路也必然失败，见方法2）。
                if self._select_option_by_config(dates, items):
                    return True

                # 方法2: 通用文本搜索，限定在场次列表里找。日期要先展开成页面可能的写法：配置写 2026-12-27、页面写
                # 「12月27日」，contains 只能拿字面碰。root=场次容器，全文找会点到页面别处的「日期标题」，那不叫选场次；容器整个找不到才退回全文找（老行为）。
                if not self.config.fast_mode and not said_once:
                    print("  尝试通用文本搜索...")
                if self._click_session_by_text(dates, box, verbose=False):
                    return True

                # 方法3: 去掉日期部分再比。日历点完后「场次」栏有时不再写日期（列表只写「19:30 下本」），用户按别处
                # 看到的原文抄了带日期的就永远匹配不上。必须同时满足「列表里所有条目都没有日期」才允许走这条路，否则会命中上一天的同名场次。
                if (loose and texts
                        and all(self._date_tokens(t) is None for t in texts)):
                    if not self.config.fast_mode and not said_once:
                        print("  这一页的场次栏不写日期，改按「{}」找".format(loose[0]))
                    if self._click_session_by_text(loose, box, verbose=False):
                        if not self.config.fast_mode:
                            print("  ✓ 匹配到（这一页的场次没写日期）：{}".format(loose[0]))
                        return True

                said_once = True
                if time.time() >= deadline:
                    break
                time.sleep(self._pace(0.25))

            # 放弃：把搜索过程打一遍（这是最需要细节的那一次），再留现场快照。
            if not self.config.fast_mode:
                print("  重试了 {:.0f} 秒还是没匹配上，最后一次搜索明细：".format(6.0))
            box, _item_sel = self._sku_box('场次')
            self._click_session_by_text(dates, box, verbose=not self.config.fast_mode)
            if loose:
                self._click_session_by_text(loose, box,
                                            verbose=not self.config.fast_mode)
            if not self.config.fast_mode:
                print(f"  ⚠ 未找到匹配的场次")
            # 现场快照：我们看到了哪些场次文案，但一条都对不上（每个地址一次）
            self._dump_session_area('配置的场次在页面上的场次列表里一条都对不上')
            self._hint_if_calendar_page()
            return False

        except Exception as e:
            if not self.config.fast_mode:
                print(f"  场次选择异常: {e}")
            return False

    def _click_session_by_text(self, values, box, verbose):
        """在（限定于 `box` 的）场次列表里，按页面可能的写法逐条搜文本并点。

        `values` 里的每一条都会先展开成页面可能出现的写法（见
        `_date_search_candidates`），再交给 `_find_and_click_element`。
        `box` 为 None 时退回全文搜索，那是「不认识这一页的类名」时的老行为。
        """
        for value in values:
            for cand in self._date_search_candidates(value):
                if self._find_and_click_element(
                        cand, max_results=10, skip_keywords=['无票', '售罄'],
                        print_results=verbose, root=box):
                    return True
        return False

    def _hint_if_calendar_page(self):
        """场次没匹配上时：如果这页像是月历格子、而「日历日期」又没填，
        就点一句「先填日历日期」。

        只在「像日历格」且「日历日期空着」时才说，而且每个地址只说一次
          （等档循环每秒都会走到这里，不节流会把日志刷爆）。
          绝大多数详情页不是这种形态，不能拿这句话去打扰所有人。
        """
        try:
            if str(getattr(self.config, 'calendar_date', '') or '').strip():
                return                       # 填了就别啰嗦，问题在别处
            if getattr(self, '_cal_hint_urls', None) is None:
                self._cal_hint_urls = set()
            try:
                key = (self.driver.current_url or '?').split('?')[0]
            except Exception:
                key = '?'
            if key in self._cal_hint_urls:
                return
            self._cal_hint_urls.add(key)
            if (self.driver.execute_script(self._CAL_GRID_JS) or 0) >= 5:
                self._note(
                    '  ⚠ 这一页的日期是**月历格子**（场次只显示选中那天的）：'
                    '请在「日历日期（选填）」里填要抢的那天（例 2026-12-26），'
                    '光填「场次」是选不中的 —— 页面 DOM 里压根没有那天的场次')
        except Exception:
            pass

    def select_price_on_page_pc(self):
        """在PC端详情页选择票价（支持模糊匹配）

        票档列表是跟着场次走的：场次一点，它也会异步重渲染。
          所以这里先等列表出条目再匹配，和场次那一步同一个道理
          （「等到了 A」不等于「B 也好了」，每一块要有自己的等待）。
        """
        try:
            # 「280.0元」再补一个「280」：带单位的写法没法按字面匹配页面文案
            want_prices = self._price_match_values(self.config.prices)

            # 方法1: 按容器里的条目逐个比配置（老 PC 页 / 新详情页都走这条）。老写法是 `find_elements(By.CLASS_NAME,
            #   'sku-tickets-card')`，新详情页那套类名一个都没有，于是这步整个被跳过。
            pbox, pitem = self._sku_box('票档')
            self._wait_list_ready(pbox, pitem)
            if pbox is not None:
                sku_name_element_list = pbox.find_elements(By.CSS_SELECTOR, pitem)

                if not self.config.fast_mode:
                    print(f"  找到 {len(sku_name_element_list)} 个票价选项:\n")
                    prices = []
                    for elem in sku_name_element_list:
                        try:
                            text = self._norm_ws(elem.text)
                            if text:
                                prices.append(text)
                        except Exception:
                            pass
                    for idx, text in enumerate(prices):
                        print(f"    [{idx}] {text}")
                    print()

                if self._select_option_by_config(want_prices, sku_name_element_list, ['缺', '售罄', '无票']):
                    return True

            # 方法2: 通用文本搜索（模糊匹配），同样限定在票档列表里
            if not self.config.fast_mode:
                print(f"  尝试通用文本搜索...")
            for price in want_prices:
                if self._find_and_click_element(price, max_results=15, skip_keywords=['缺货', '售罄', '无票'], print_results=not self.config.fast_mode, root=pbox):
                    return True

            if not self.config.fast_mode:
                print(f"  ⚠ 未找到匹配的票价")
            return False

        except Exception as e:
            if not self.config.fast_mode:
                print(f"  票价选择异常: {e}")
            return False

    def select_quantity_on_page_pc(self):
        """在PC端详情页选择数量"""
        return self._select_quantity_on_page(platform="PC端")

    def _select_quantity_on_page(self, platform="移动端"):
        """在详情页选择数量（PC端和移动端通用）

        Args:
            platform: 平台标识，用于日志输出
        """
        from selenium.common.exceptions import WebDriverException

        try:
            target_count = len(self.config.users)
            print(f"  【{platform}详情页】目标数量: {target_count} 张")

            success = self._try_select_quantity_by_buttons(target_count)

            if not success:
                success = self._try_set_quantity_directly(target_count)

            if not success:
                print(f"  ⚠ 未找到数量选择器，将使用默认数量 (1 张)")

            return True

        except (AttributeError, TypeError, ValueError) as e:
            print(f"  ❌ 数量选择配置错误: {e}")
            return True
        except WebDriverException as e:
            print(f"  ⚠ WebDriver 异常，继续执行: {e}")
            return True
        except Exception as e:
            print(f"  ⚠ 未预期的异常: {e}")
            return True  # 不阻塞流程

    def _try_select_quantity_by_buttons(self, target_count):
        """通过点击 + 按钮选择数量

        Args:
            target_count: 目标数量

        Returns:
            bool: 是否成功选择
        """
        from selenium.common.exceptions import NoSuchElementException, WebDriverException

        selectors_to_try = [
            ("//div[contains(@class, 'cafe-c-input-number')]//a[contains(@class, 'handler-up')]", "cafe-c-input-number 结构"),
            ("//a[contains(@class, 'cafe-c-input-number-handler-up')]", "cafe-c-input-number-handler-up"),
            ("//div[contains(@class, 'number_right_info')]//a[last()]", "number_right_info"),
            ("//*[contains(@class, 'cafe-c-input-number')]//a[contains(text(), '+')]", "cafe-input-number + 按钮"),
            ("//a[contains(@class, 'handler-up')]", "通用 handler-up"),
        ]

        for selector, method_name in selectors_to_try:
            try:
                plus_btns = self.driver.find_elements(By.XPATH, selector)
                if plus_btns:
                    print(f"    ✓ 找到 + 按钮 ({method_name}): {len(plus_btns)} 个")

                    if self._click_plus_buttons(plus_btns, target_count):
                        return True
            except (NoSuchElementException, WebDriverException):
                continue

        return False

    def _click_plus_buttons(self, plus_btns, target_count):
        """点击 + 按钮增加数量

        Args:
            plus_btns: 按钮元素列表
            target_count: 目标数量

        Returns:
            bool: 是否成功
        """
        from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

        for btn in plus_btns[:3]:
            try:
                class_attr = btn.get_attribute('class') or ''
                if 'disabled' in class_attr.lower():
                    continue

                if btn.is_displayed() and btn.is_enabled():
                    for i in range(target_count - 1):
                        self.driver.execute_script("arguments[0].click();", btn)
                        # 点完盯「输入框的值涨到第 i+2 张了没有」，涨到就走。老写法每点一次固定睡 0.25 秒，
                        # 买 4 张白付 0.75 秒；上界仍是 0.25 秒/次，慢的时候一点都不比以前少等。
                        want = str(i + 2)
                        self._poll_until(
                            lambda w=want: self._qty_value() == w,
                            timeout=0.25, interval=0.04)

                    current_val = self._get_quantity_input_value()
                    if current_val:
                        print(f"    输入框当前值: {current_val}")

                    print(f"  ✓ 已选择 {target_count} 张票")
                    return True
            except StaleElementReferenceException:
                continue
            except WebDriverException:
                continue

        return False

    # 一次往返读数量输入框的值（轮询用）。走 JS 而不是 `find_element(...).get_attribute('value')`：后者两次往返，而轮询每 40 毫秒问一遍，差一倍就是差一倍。
    QTY_VALUE_JS = r"""
    var i = document.querySelector('.cafe-c-input-number-input');
    if (!i) {
      var box = document.querySelector('[class*="cafe-c-input-number"]');
      i = box ? box.querySelector('input') : null;
    }
    if (!i) i = document.querySelector('[class*="number"] input');
    if (!i) return null;
    var v = i.value;
    return v == null ? null : ('' + v);
    """

    def _qty_value(self):
        """数量输入框的当前值；读不到返回 None。"""
        try:
            return self.driver.execute_script(self.QTY_VALUE_JS)
        except Exception:
            return None

    def _get_quantity_input_value(self):
        """获取数量输入框的值

        Returns:
            str: 输入框的值，失败返回 None
        """
        from selenium.common.exceptions import NoSuchElementException

        input_selectors = [
            "//input[contains(@class, 'cafe-c-input-number-input')]",
            "//div[contains(@class, 'cafe-c-input-number')]//input",
        ]
        for inp_sel in input_selectors:
            try:
                input_elem = self.driver.find_element(By.XPATH, inp_sel)
                return input_elem.get_attribute('value')
            except NoSuchElementException:
                continue
        return None

    def _try_set_quantity_directly(self, target_count):
        """直接设置输入框的值

        Args:
            target_count: 目标数量

        Returns:
            bool: 是否成功
        """
        from selenium.common.exceptions import NoSuchElementException, JavascriptException, WebDriverException

        try:
            input_selector = "//input[contains(@class, 'cafe-c-input-number-input')]"
            input_elem = self.driver.find_element(By.XPATH, input_selector)
            print(f"    找到输入框，直接设置值")

            self.driver.execute_script(f"""
                arguments[0].value = '{target_count}';
                arguments[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
                arguments[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
                arguments[0]._value = '{target_count}';
                if (arguments[0]._v_model) {{
                    arguments[0]._v_model = '{target_count}';
                }}
            """, input_elem)

            # 盯「值真的变成目标了没有」（上界仍是原来那个固定 sleep 的 0.3 秒）
            want = str(target_count)
            self._poll_until(lambda: self._qty_value() == want,
                             timeout=0.3, interval=0.04)
            new_val = input_elem.get_attribute('value')
            print(f"    设置后输入框值: {new_val}")

            if new_val == str(target_count):
                print(f"  ✓ 已选择 {target_count} 张票")
                return True
        except NoSuchElementException:
            pass
        except (JavascriptException, WebDriverException) as e:
            print(f"    直接设置输入框失败: {e}")

        return False

    def select_city_on_page(self):
        """在页面选择城市（移动端）"""
        try:
            return self._find_and_click_element(
                self.config.city,
                max_results=10,
                print_results=not self.config.fast_mode
            )
        except Exception as e:
            if not self.config.fast_mode:
                print(f"  城市选择异常: {e}")
            return False

    def select_date_on_page(self):
        """在页面选择场次（移动端）"""
        try:
            if not self.config.fast_mode:
                print(f"  搜索场次: {self.config.dates}")
            # 日期先展开成页面可能的写法（见 select_date_on_page_pc 同处的说明）
            for date in self.config.dates:
                for cand in self._date_search_candidates(date):
                    if self._find_and_click_element(cand, max_results=10, skip_keywords=['无票', '售罄'], print_results=not self.config.fast_mode):
                        if not self.config.fast_mode:
                            print(f"  ✓ 已选择场次: {date}\n")
                        return True

            if not self.config.fast_mode:
                print(f"  ⚠ 未找到匹配的场次")
            return False
        except Exception as e:
            if not self.config.fast_mode:
                print(f"  场次选择异常: {e}")
            return False

    def select_price_on_page(self):
        """在页面选择票价（移动端）"""
        try:
            if not self.config.fast_mode:
                print("  扫描票价元素...")
                price_candidates = self.driver.find_elements(By.XPATH, "//*[contains(text(), '¥') or contains(text(), '元')]")
                seen = set()
                for elem in price_candidates[:15]:
                    try:
                        text = elem.text.strip()
                        if text and text not in seen and len(text) < 50:
                            print(f"    - {text}")
                            seen.add(text)
                    except Exception:
                        pass
                print()

            for price in self._price_match_values(self.config.prices):
                if not self.config.fast_mode:
                    print(f"  尝试匹配: {price}")
                if self._find_and_click_element(price, max_results=10,
                                                skip_keywords=['缺货', '售罄', '无票'],
                                                print_results=not self.config.fast_mode):
                    if not self.config.fast_mode:
                        print(f"  ✓ 已选择票价: {price}\n")
                    return True

            if not self.config.fast_mode:
                print(f"  ⚠ 未找到匹配的票价")
            return False
        except Exception as e:
            if not self.config.fast_mode:
                print(f"  票价选择异常: {e}")
            return False

    def select_quantity_on_page(self):
        """在页面选择数量（移动端）"""
        return self._select_quantity_on_page(platform="移动端")

    def finish(self):
        self.driver.quit()
