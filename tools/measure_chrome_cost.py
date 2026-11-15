# -*- coding: utf-8 -*-
"""量一量：一个自动化 Chrome 到底吃多少内存 / 占几个进程。

「多开最多开几个」这个问题，网上给的数是拍脑袋的：有人说一个 Chrome 100 MB，于是 16 GB 的
机器就敢说开 10 个。实际是：任务管理器里你看到的「Chrome 内存」通常是主进程那一行，不是
整套；一个自动化 Chrome 会裂成主进程 + GPU + 渲染 + 网络服务 + 实用程序 + 崩溃上报等一堆
进程，加起来才是它真正的代价；抢票页面比 about:blank 重得多，所以这里量出来的是下限。所以
别抄别人的数，量自己的 —— 这个工具会真的按你指定的参数开 N 个浏览器，等它们稳定下来，把
整套进程树的内存加起来，再告诉你「这台机器现在还能开几个」。

用法（tools/ 在仓库根，不在 web 里了）：
    <有 selenium 的解释器> tools/measure_chrome_cost.py                 # 量 1 个
    <有 selenium 的解释器> tools/measure_chrome_cost.py --n 1,3          # 依次量 1 个、3 个
    <有 selenium 的解释器> tools/measure_chrome_cost.py --flags light    # 用轻量化参数
    <有 selenium 的解释器> tools/measure_chrome_cost.py --flags baseline # 跟改造前一样

它会真的开浏览器窗口（about:blank，不会访问任何网站），量完自己关掉，跑的时候别去动那些
窗口；用 `--n 5` 之前先确认自己内存够。全程用临时 Profile，跑完删掉，不碰 `accounts.json` /
`accounts.log` / 你任何账号的 Profile。
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "web")
                             # tools/ 挪到仓库根后，ROOT 仍指 web 源码目录（名字没改，
                             #   免得动下面几十处用法）
sys.path.insert(0, ROOT)

import chrome_flags                                                  # noqa: E402
import resource_guard                                                # noqa: E402


def _make_options(profile_dir, flags, index):
    from selenium import webdriver
    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-data-dir=" + profile_dir)
    if flags == "baseline":
        # 改造前的原样：只有上面那三条
        pass
    else:
        chrome_flags.add_to(opts, chrome_flags.build_args(
            light=True, keep_awake=(flags == "light+awake")))
        chrome_flags.add_to(opts, chrome_flags.window_args(index))
    return opts


def measure_one(n, flags, settle):
    """开 n 个浏览器，量内存与启动耗时。

    启动耗时也要量，别只量内存：错峰间隔该设多少，取决于一个 Chrome 多久
      能起来，不取决于内存。拍一个「3 秒应该够了」很容易偏。

    返回 dict：ok / per_bytes / total_bytes / procs_ours / starts / err
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    from chromedriver_locator import resolve_chromedriver

    tmp = tempfile.mkdtemp(prefix="measure_chrome_")
    profiles = {}
    drivers = []
    result = {"ok": False, "per_bytes": 0, "total_bytes": 0, "procs_ours": 0,
              "starts": [], "err": ""}
    try:
        driver_path = resolve_chromedriver()
        for i in range(n):
            name = "A{:02d}".format(i + 1)
            pdir = os.path.join(tmp, name)
            os.makedirs(pdir, exist_ok=True)
            profiles[name] = pdir
            t0 = time.time()
            try:
                d = webdriver.Chrome(service=Service(driver_path),
                                     options=_make_options(pdir, flags, i))
            except Exception as exc:
                result["err"] = "第 {} 个就起不来了：{}".format(i + 1, exc)
                return result
            result["starts"].append(time.time() - t0)
            d.get("about:blank")
            drivers.append(d)

        # 等它稳定：Chrome 启动后会陆续拉起 GPU / 网络 / 工具进程，
        # 刚起来的瞬间测量会明显偏低（漏掉还没拉起来的那些）。
        time.sleep(settle)

        fp = resource_guard.footprint(profiles)
        live = [v for v in fp["by_profile"].values() if v]
        total = sum(live)
        result["total_bytes"] = total
        result["per_bytes"] = total // len(live) if live else 0
        # 这几个实例一共裂成了几个进程，这个数最能说明「为什么不是 100MB」
        result["procs_ours"] = sum((fp.get("by_count") or {}).values())
        result["all_count"] = fp["count"]
        result["ok"] = True
        return result
    finally:
        for d in drivers:
            try:
                d.quit()
            except Exception:
                pass
        # quit() 之后可能还有残留（比如退出没干净），按 Profile 再收一遍
        for pdir in profiles.values():
            resource_guard.close_by_profile(pdir)
        time.sleep(1.0)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="量自动化 Chrome 的内存代价")
    ap.add_argument("--n", default="1", help="开几个，可以逗号分隔依次量，如 1,3,5")
    ap.add_argument("--flags", default="light",
                    choices=["light", "light+awake", "baseline"],
                    help="light=轻量化参数（默认）；light+awake=再关掉后台降速；"
                         "baseline=改造前的原样")
    ap.add_argument("--settle", type=float, default=8.0,
                    help="启动后等多少秒再量（默认 8 秒，Chrome 拉子进程需要时间）")
    args = ap.parse_args(argv)

    sizes = []
    for part in str(args.n).split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            sizes.append(int(part))
    if not sizes:
        print("--n 得是正整数，比如 1 或 1,3,5")
        return 2

    try:
        import selenium                                              # noqa: F401
    except ImportError:
        print("这个解释器没有 selenium，量不了。")
        print("用装了 selenium 的那个：")
        print("  <装了 selenium 的 python> tools/measure_chrome_cost.py")
        return 2

    mem = resource_guard.memory_info()
    print("测量前：本机内存共 {}，可用 {}（已用 {}%）".format(
        resource_guard.format_bytes(mem["total"]),
        resource_guard.format_bytes(mem["avail"]),
        mem["percent"] if mem["percent"] is not None else "?"))
    print("=" * 68)

    # 必须先放开 localhost 代理：本机代理会拦掉发给 ChromeDriver 的请求，
    #   症状是一句完全看不懂的 `unhandled request`（项目里已经踩过一次）。
    from chromedriver_locator import relax_local_proxy
    relax_local_proxy()

    peak = 0
    rows = []
    for n in sizes:
        print("\n正在开 {} 个浏览器（参数：{}，稳定 {} 秒后测量）…".format(n, args.flags, args.settle))
        r = measure_one(n, args.flags, args.settle)
        if not r["ok"]:
            print("  【失败】{}".format(r["err"]))
            print("  → 这套参数有问题，先别用。")
            return 1
        starts = r["starts"]
        avg_start = sum(starts) / len(starts) if starts else 0
        print("  成功：{} 个浏览器都起来了".format(n))
        print("  整套内存合计：{}".format(resource_guard.format_bytes(r["total_bytes"])))
        print("  平均每个：{}（{} 个进程；整套进程树，不是任务管理器里主进程那一行）".format(
            resource_guard.format_bytes(r["per_bytes"]), r["procs_ours"]))
        print("  启动耗时：平均 {:.1f} 秒，最慢 {:.1f} 秒{}".format(
            avg_start, max(starts) if starts else 0,
            "（并发起多个时明显变慢 = 它们在互相抢 CPU）" if n > 1 else ""))
        rows.append((n, r["per_bytes"], r["total_bytes"], avg_start))
        if r["per_bytes"]:
            peak = max(peak, r["per_bytes"])

    print("\n" + "=" * 68)
    if len(rows) > 1:
        print("小结：")
        for n, per, total, avg in rows:
            print("  {:>2} 个：平均每个 {:>10}  合计 {:>10}  平均启动 {:.1f} 秒".format(
                n, resource_guard.format_bytes(per),
                resource_guard.format_bytes(total), avg))
        base = rows[0]
        if len(rows) > 1 and base[0] >= 1 and base[3] > 0:
            print("  → 从 {} 个加到 {} 个，单个启动从 {:.1f} 秒变成 {:.1f} 秒{}".format(
                base[0], rows[-1][0], base[3], rows[-1][3],
                "（变慢了，说明错峰启动很有必要）" if rows[-1][3] > base[3] * 1.3
                else "（没明显变慢）"))
    if peak:
        mem = resource_guard.memory_info()
        per_mb = max(1, peak // (1024 * 1024))
        print("\n实测：一个自动化浏览器约 {}".format(resource_guard.format_bytes(peak)))
        print("  （注意这是 about:blank 的量，真实抢票页面会更重，当下限看）")
        print("  按这个数，本机（可用 {}）现在还能开 {} 个".format(
            resource_guard.format_bytes(mem["avail"]),
            resource_guard.max_windows(avail=mem["avail"], per_window_mb=per_mb,
                                       total=mem["total"])))
        print("  想让估算用它，把 resource_guard.DEFAULT_PER_WINDOW_MB 改成 {}".format(per_mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
