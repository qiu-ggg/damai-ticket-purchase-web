# -*- coding: utf-8 -*-
"""环境检测（web/env_check.py）自检。在 web/ 目录跑：
`python tools/check_env_check.py`

用户的原话是「检查启动的这个设备是否有支持的环境，如果没有就跳出来提示要下缺少的东西，
提供下载链接」。所以最要紧的两条：缺什么一定要有下载链接，没链接等于把「自己去搜」又推回给
用户；检测本身不许抛异常、不许动手 —— 它正是在「环境有问题」的机器上跑的，而且「点一下
检测」不该变成「意外改了环境」。另外钉住结构：每一项都得有
key/name/level/detail/fix/url 这几个字段，`level` 只能是 ok/warn/fail 三种（GUI 是按 level
上色的，多一种就会渲染成灰的）。

不联网、不真去装东西：网络那一项被换成了假的，其余项在本机自身的环境上跑（本来就是只读的）。
"""
import os
import re
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
                          # tools/ 挪到仓库根之后得显式拼这一层
if WEB not in sys.path:
    sys.path.insert(0, WEB)

import env_check as ec                                              # noqa: E402

_results = []


def check(name, passed, detail=""):
    """detail 一律转成字符串再拼，直接传数字/列表会 TypeError，
    「测试脚本自己崩了」比「断言失败」难查多了。"""
    _results.append((name, bool(passed), str(detail)))
    print("  [{}] {}{}".format("PASS" if passed else "!!", name,
                               ("  <<< " + str(detail)) if detail != "" else ""))
    return passed


# A. 结构

def group_a_shape():
    print("\n[A] 每一项的结构都要规整（GUI 按 level 上色，多一种就渲染成灰的）")

    results = ec.run()
    check("A1 跑得出结果", bool(results), len(results))

    keys = [r["key"] for r in results]
    check("A2 每一项都有 key", all(keys), keys)
    check("A3 key 不重复", len(keys) == len(set(keys)), keys)

    allowed = {ec.LEVEL_OK, ec.LEVEL_WARN, ec.LEVEL_FAIL}
    bad = [r for r in results if r.get("level") not in allowed]
    check("A4 level 只有 ok/warn/fail 三种", not bad,
          [r.get("level") for r in bad])

    for field in ("name", "detail", "fix", "url", "url_text"):
        missing = [r["key"] for r in results if field not in r]
        check("A5 每一项都有 {} 字段".format(field), not missing, missing)

    check("A6 该查的七项都在",
          set(keys) >= {"system", "python", "runner", "chrome",
                        "chromedriver", "network", "datadir"},
          sorted(keys))

    check("A7 describe() 是一段能直接贴出去的纯文本",
          isinstance(ec.describe(results), str)
          and "环境检测结果" in ec.describe(results)
          and "结论" in ec.describe(results))

    # 抬头必须带版本号：这份报告常是客户整段复制发来的，没版本号就得回问「你装的是哪一版」。
    #   断言钉「格式」不钉具体数字：改 version.py 的版本号不该让测试变红，
    #   但「抬头有没有版本」这件事必须一直为真。
    head = ec.describe(results).splitlines()[0]
    check("A9 报告抬头带版本号（客户发来的报告能对上版本）",
          re.search(r"v\d+\.\d+", head) is not None, head)
    check("A10 版本模块没随包时也不崩（只印标题）",
          _describe_without_version_module(ec, results))
    check("A8 tip() 是一句话",
          isinstance(ec.tip(results), str) and "\n" not in ec.tip(results),
          ec.tip(results))


def _describe_without_version_module(ec, results):
    """把 `import version` 掐断，看 describe() 会不会跟着崩。

    `env_check` 故意不依赖任何东西（它得在「窗口都起不来」的机器上跑出结果），
    所以版本模块的 import 是包在 try/except 里的。这条断言就是钉住那个 except，
    不然哪天有人把 try 去掉，报告会整个出不来，而「本机版本模块在不在」
    这件事在打包机上永远是「在」，测不出来。
    """
    import builtins

    real = builtins.__import__

    def boom(name, *a, **kw):
        if name == "version":
            raise ImportError("模拟：版本模块没随包")
        return real(name, *a, **kw)

    builtins.__import__ = boom
    try:
        text = ec.describe(results)
    except Exception as exc:
        return "崩了：{!r}".format(exc)
    finally:
        builtins.__import__ = real
    return isinstance(text, str) and text.startswith("环境检测结果")


# B. 缺东西就必须有下载链接

def group_b_download_links():
    print("\n[B] 缺东西的时候一定要给出下载链接")

    real_major = None
    real_find_env = None
    real_find_bundled = None
    real_find_cached = None
    try:
        import chromedriver_locator as loc
        real_major = loc.chrome_major_version
        real_find_env = loc.find_chromedriver_in_env
        real_find_bundled = loc.find_bundled_chromedriver
        real_find_cached = loc.find_cached_chromedriver

        # 造一个「没有 Chrome」的机器
        loc.chrome_major_version = lambda: None
        r = ec.check_chrome()
        check("B1.1 没装 Chrome 时判成 fail", r["level"] == ec.LEVEL_FAIL, r["level"])
        check("B1.2 而且给了下载链接", bool(r.get("url")), r.get("url"))
        check("B1.3 链接是 https 的", str(r.get("url", "")).startswith("https://"),
              r.get("url"))
        check("B1.4 有可读的按钮文字", bool((r.get("url_text") or "").strip()),
              r.get("url_text"))
        check("B1.5 也说清了怎么办", "怎么办" not in r["fix"] and bool(r["fix"]),
              r["fix"][:40])

        # 有 Chrome，但一份驱动都找不到
        loc.chrome_major_version = lambda: "155"
        loc.find_chromedriver_in_env = lambda: None
        loc.find_bundled_chromedriver = lambda: None
        loc.find_cached_chromedriver = lambda major: None
        r = ec.check_chromedriver()
        check("B2.1 没驱动时是 warn（程序会自己去下）",
              r["level"] == ec.LEVEL_WARN, r["level"])
        check("B2.2 同样给了下载链接", bool(r.get("url")), r.get("url"))
        check("B2.3 链接指向 chromedriver 下载页",
              "chromedriver" in str(r.get("url", "")).lower()
              or "chrome-for-testing" in str(r.get("url", "")).lower(),
              r.get("url"))

        # 驱动版本对不上，必须报错并给链接
        loc.find_chromedriver_in_env = lambda: r"C:\fake\chromedriver.exe"
        loc.chromedriver_major_version = lambda p: "120"
        r = ec.check_chromedriver()
        check("B3.1 版本对不上时判 fail", r["level"] == ec.LEVEL_FAIL, r["level"])
        check("B3.2 说清了两边的版本号",
              "155" in r["detail"] and "120" in r["detail"], r["detail"])
        check("B3.3 也给了下载链接", bool(r.get("url")), r.get("url"))

        # 「缺什么就给什么下载链接」这条规矩，整体上也要成立
        allres = ec.run()
        bad = [x for x in ec.failures(allres) if not x.get("url")]
        check("B4 所有 fail 项都带下载链接（没有例外）", not bad,
              [x["key"] for x in bad])
        check("B5 download_items() 挑的正是带链接的失败项",
              all(x["level"] == ec.LEVEL_FAIL and x.get("url")
                  for x in ec.download_items(allres)))
    finally:
        import chromedriver_locator as loc
        if real_major is not None:
            loc.chrome_major_version = real_major
            loc.find_chromedriver_in_env = real_find_env
            loc.find_bundled_chromedriver = real_find_bundled
            loc.find_cached_chromedriver = real_find_cached
            loc.chromedriver_major_version = _REAL_DRIVER_VER


_REAL_DRIVER_VER = None


# C. 网络那一项

def group_c_network():
    print("\n[C] 网络：连不上要如实说，而且不许把原因说成别的")

    real_conn = socket.create_connection
    try:
        socket.create_connection = lambda addr, timeout=None: (_ for _ in ()).throw(
            OSError("假装连不上"))
        r = ec.check_network()
        check("C1 连不上时判 fail", r["level"] == ec.LEVEL_FAIL, r["level"])
        check("C2 说清是哪个域名连不上",
              any(h in r["detail"] for h in ec.PROBE_HOSTS), r["detail"][:80])
        check("C3 给了排查方向（代理 / 防火墙）",
              "代理" in r["fix"] or "防火墙" in r["fix"], r["fix"][:60])
        check("C4 网络问题不该硬塞一个下载链接",
              not r.get("url"), r.get("url"))
    finally:
        socket.create_connection = real_conn

    r = ec.check_network()
    check("C5 本机能连上大麦（这条是真连了一次）",
          r["level"] == ec.LEVEL_OK, r["detail"][:60])


# D. 只看看、不动手 + 从不抛

def group_d_readonly_and_never_raise():
    print("\n[D] 只看不动手；任何一项炸了都不许把整体带崩")

    # D1：跑一遍，确认没有新文件落到程序目录里
    before = set(os.listdir(WEB))
    ec.run()
    after = set(os.listdir(WEB))
    check("D1 跑一遍环境检测没有在程序目录里留下新文件",
          before == after, sorted(after - before))

    # D2：每一项单独炸，run() 仍然返回完整结构
    real_checks = ec.CHECKS

    def boom():
        raise RuntimeError("故意炸的")

    try:
        ec.CHECKS = tuple([boom] * len(real_checks))
        results = ec.run()
        check("D2.1 所有检查都炸了，run() 依然不抛、返回同样多的条目",
              len(results) == len(real_checks), len(results))
        check("D2.2 每一条都如实写成「查不了」",
              all("查不了" in r["detail"] for r in results),
              results[0]["detail"][:60])
    finally:
        ec.CHECKS = real_checks

    # D3：only= 只跑指定项（自检脚本要能挑着跑）
    only = ec.run(only=["network"])
    check("D3 only= 只跑指定项", len(only) == 1 and only[0]["key"] == "network",
          [r["key"] for r in only])

    # D4：空输入不许崩
    check("D4 worst([]) 不崩", ec.worst([]) == ec.LEVEL_OK)
    check("D4.2 failures([]) 是空表", ec.failures([]) == [])
    check("D4.3 describe([]) 能出文本", isinstance(ec.describe([]), str))


# E. 结论怎么算

def group_e_levels():
    print("\n[E] 整体结论的算法")

    ok = {"key": "a", "name": "A", "level": ec.LEVEL_OK, "detail": "", "fix": "",
          "url": None, "url_text": "打开"}
    warn = dict(ok, level=ec.LEVEL_WARN, name="W")
    fail = dict(ok, level=ec.LEVEL_FAIL, name="F", url="https://x/", url_text="下载")

    check("E1 全 ok → ok", ec.worst([ok, ok]) == ec.LEVEL_OK)
    check("E2 有 warn → warn", ec.worst([ok, warn]) == ec.LEVEL_WARN)
    check("E3 有 fail → fail（压过 warn）",
          ec.worst([ok, warn, fail]) == ec.LEVEL_FAIL)
    check("E4 tip 全绿时说没问题", "没问题" in ec.tip([ok, ok]), ec.tip([ok, ok]))
    check("E5 tip 有 fail 时点名缺什么",
          "缺少" in ec.tip([ok, fail]) and "F" in ec.tip([ok, fail]),
          ec.tip([ok, fail]))
    check("E6 tip 只有 warn 时说「需要注意」",
          "注意" in ec.tip([ok, warn]), ec.tip([ok, warn]))
    check("E7 describe 里 fail 项带上了链接",
          "https://x/" in ec.describe([ok, fail]))



def main():
    global _REAL_DRIVER_VER
    print("=" * 62)
    print("环境检测自检")
    print("=" * 62)

    try:
        import chromedriver_locator as loc
        _REAL_DRIVER_VER = loc.chromedriver_major_version
    except Exception:
        pass

    group_a_shape()
    group_b_download_links()
    group_c_network()
    group_d_readonly_and_never_raise()
    group_e_levels()

    bad = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 62)
    if bad:
        print("失败 {} 项：".format(len(bad)))
        for n in bad:
            print("  - " + n)
    else:
        print("全部通过（{} 项）".format(len(_results)))
    print("=" * 62)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
