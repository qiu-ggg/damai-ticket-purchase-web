# -*- coding: utf-8 -*-
"""只读探针：打开大麦登录页，把输入框和 iframe 的真实结构打出来。

用途：auto_fill_login 报「没认出账号输入框」时，用它看看页面到底长什么样，
再据此改 LOGIN_ID_SELECTORS / 决定要不要切 iframe。

安全边界：① 只 driver.get 一次，然后读 DOM，不填表、不点击、不登录、不下单；② 用临时
Profile 目录，绝不碰 profiles/<你的账号> 里的登录态；③ 跑完立刻 driver.quit()。

用法：
    cd D:/damai-ticket-purchase
    <venv-python> tools/probe_login_page.py

（回环地址已在脚本里自动从系统代理摘出去，不用再手工加 NO_PROXY 了）
"""

import os
import sys
import tempfile
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
PROJ = os.path.dirname(BASE)      # tools/ 挪到仓库根：仓库根在这
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # tools/ 自己进 sys.path
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "damai"))

from chromedriver_locator import relax_local_proxy  # noqa: E402

# 必须赶在建 driver 之前：本机全局代理会劫走发往本地 ChromeDriver 的请求，症状是每个 driver.xxx 都报 "Message: unhandled request"。
relax_local_proxy()

import account_runner as R  # noqa: E402


def describe_inputs(driver, where):
    rows = []
    try:
        els = driver.find_elements("css selector", "input")
    except Exception as e:
        print("    （读不到 input：{}）".format(e))
        return rows
    for el in els:
        try:
            rows.append({
                "id": el.get_attribute("id") or "",
                "name": el.get_attribute("name") or "",
                "type": el.get_attribute("type") or "",
                "placeholder": el.get_attribute("placeholder") or "",
                "cls": (el.get_attribute("class") or "")[:60],
                "shown": el.is_displayed(),
            })
        except Exception:
            continue
    print("  [{}] input 共 {} 个：".format(where, len(rows)))
    for r in rows:
        print("    id={!r} name={!r} type={!r} ph={!r} shown={} cls={!r}".format(
            r["id"], r["name"], r["type"], r["placeholder"][:24], r["shown"], r["cls"]))
    return rows


def main():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    tmp = tempfile.mkdtemp(prefix="probe-login-")
    print("临时 Profile:", tmp)
    print("探针只读：不填表、不点击、不登录。\n")

    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-data-dir=" + tmp)

    driver = None
    try:
        driver = webdriver.Chrome(service=Service(R.get_chromedriver_path()), options=options)
        print("打开:", R.LOGIN_URL)
        driver.get(R.LOGIN_URL)

        # 关键观察点：每隔 1 秒看一次主文档里能不能找到登录输入框，花了几秒就是差的时间。
        from selenium.webdriver.common.by import By
        started = time.time()
        first_hit = None
        for i in range(1, 21):
            time.sleep(1)
            for how, sel in R.LOGIN_ID_SELECTORS:
                try:
                    for el in driver.find_elements(how, sel):
                        if el.is_displayed() and el.is_enabled():
                            first_hit = (i, sel)
                            break
                except Exception:
                    pass
                if first_hit:
                    break
            if first_hit:
                break
        print("\n主文档里第一次能命中账号输入框：第 {} 秒，选择器 {}".format(
            first_hit[0], first_hit[1]) if first_hit else
            "\n主文档里 20 秒内始终没命中任何账号输入框！")
        print("当前 URL:", driver.current_url)
        print("页面标题:", driver.title)

        print()
        describe_inputs(driver, "主文档")

        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        print("\n  iframe 共 {} 个：".format(len(frames)))
        for idx, fr in enumerate(frames):
            try:
                print("   [{}] src={!r} shown={}".format(
                    idx, (fr.get_attribute("src") or "")[:100], fr.is_displayed()))
            except Exception:
                continue

        for idx in range(len(frames)):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(idx)
                describe_inputs(driver, "iframe[{}]".format(idx))
            except Exception as e:
                print("    iframe[{}] 进不去：{}".format(idx, e))
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        print("\n  「密码登录」页签在不在主文档：",
              "在" if _tab_probe(driver) else "没找到（或已在密码页）")

        # 提交按钮：登录按钮也在 iframe 里，得确认现有选择器在 frame 内命中得了
        print("\n=== 按钮 / 可点击元素 ===")
        for idx in range(len(frames) + 1):
            if idx == 0:
                driver.switch_to.default_content()
                where = "主文档"
            else:
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(frames[idx - 1])
                    where = "iframe[{}]".format(idx - 1)
                except Exception:
                    continue
            try:
                els = driver.find_elements(
                    "css selector", "button, a, input[type=submit], div[class*=btn], span[class*=btn]")
            except Exception:
                continue
            print("  [{}] 候选 {} 个：".format(where, len(els)))
            for el in els:
                try:
                    txt = (el.text or "").strip().replace("\n", " ")[:20]
                    if not txt:
                        continue
                    print("    <{}> text={!r} id={!r} cls={!r} shown={}".format(
                        el.tag_name, txt, el.get_attribute("id") or "",
                        (el.get_attribute("class") or "")[:50], el.is_displayed()))
                except Exception:
                    continue
            print("    现有 LOGIN_SUBMIT_SELECTORS 命中：",
                  _submit_probe(driver))
        driver.switch_to.default_content()

        # 直接调线上那句真代码：enter_login_frame（只切 frame + find，不填不点）
        print("\n=== 直接验证 account_runner.enter_login_frame（只 find，不 fill）===")
        t0 = time.time()
        ok, where = R.enter_login_frame(driver, timeout=20)
        print("  返回：ok={} where={}（耗时 {:.1f}s）".format(ok, where, time.time() - t0))
        if ok:
            print("  账号框选择器命中：", _id_probe(driver))
            print("  提交按钮命中：", _submit_probe(driver))
            print("  说明：切进去之后，线上代码就能填表了（本探针不填）")
        print("\n人工验证字样:", R.detect_manual_check(driver) or "无")
        print("耗时 {:.1f}s".format(time.time() - started))
        return 0
    except Exception as e:
        print("探针失败：{}".format(e))
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _id_probe(driver):
    """当前上下文里账号输入框命中的是哪个选择器。"""
    for how, sel in R.LOGIN_ID_SELECTORS:
        try:
            for el in driver.find_elements(how, sel):
                if el.is_displayed() and el.is_enabled():
                    return sel
        except Exception:
            continue
    return None


def _submit_probe(driver):
    """当前上下文里提交按钮命中的是哪个选择器。"""
    for how, sel in R.LOGIN_SUBMIT_SELECTORS:
        try:
            for el in driver.find_elements(how, sel):
                if el.is_displayed() and el.is_enabled():
                    return sel
        except Exception:
            continue
    return None


def _enter_like_fix(driver):
    """按「修复后」的逻辑试一次：主文档找不到就逐个 iframe 找。"""
    from selenium.webdriver.common.by import By
    driver.switch_to.default_content()
    if _id_probe(driver):
        return True, "主文档"
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for fr in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
        except Exception:
            continue
        if _id_probe(driver):
            return True, "iframe"
    driver.switch_to.default_content()
    return False, "没找到"


def _tab_probe(driver):
    """只查有没有那个页签，不点它（保持只读）。"""
    from selenium.webdriver.common.by import By
    for txt in R.PASSWORD_TAB_TEXTS:
        xp = ("//*[self::a or self::span or self::div or self::button or self::li]"
              "[normalize-space(text())='{}']".format(txt))
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    sys.exit(main())
