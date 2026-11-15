# -*- coding: utf-8 -*-
"""`tools/clean_profile_cache.py` 的本地验证套件。

这个工具动的是用户真实浏览器数据目录，所以它自己的用例必须回答两个问题：① 该清的清了
没有？② 不该碰的碰了没有？后者更重要：清缓存清掉登录态，用户第二天打开就是「登录失效」，
而且他会以为是网站的问题。

第 2 问的答案不能靠「读一遍代码觉得没问题」，得靠断言。所以这里造一个尽可能像真 Profile
的假目录（Cache / Local Storage / Cookies / Network / Service Worker / Local State /
无关文件 / profiles 外面的旁观者文件），清完之后逐个回查。

`Local State` 那一条是单独的、最要紧的一条：Windows 上 Chrome 的 Cookie 用一把存在
`Local State` 里的密钥（DPAPI 包裹）加密。删掉它，现有 Cookie 全部解不开，表现和「清了
缓存就掉登录」一模一样，但根因完全不同。所以它必须在保护名单里，而且必须有断言钉着。

用法（仓库根目录）：
    python tools/check_clean_cache.py
退出码 0 = 全过。
"""

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import clean_profile_cache as cc  # noqa: E402

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  <<< " + str(extra)) if extra else ""))


def write(path, text="x" * 64):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def build_fake_profiles():
    """造一份「长得像真的」profiles。返回 (root, 快照 dict)。"""
    root = tempfile.mkdtemp(prefix="damai_profiles_")
    assert os.path.abspath(root).startswith(os.path.abspath(tempfile.gettempdir())), root
    p = os.path.join(root, "profiles")
    prof = os.path.join(p, "Account_01")

    # 该清的
    for rel in ("Default/Cache", "Default/Code Cache", "Default/GPUCache",
                "BrowserMetrics", "component_crx_cache", "WasmTtsEngine",
                "GrShaderCache"):
        write(os.path.join(prof, rel, "f.bin"), "cache" * 100)
    write(os.path.join(prof, "CrashpadMetrics-active.pma"), "metrics" * 100)

    # 死也不能碰的
    write(os.path.join(prof, "Local State"), "cookie-key-here")          # 密钥家
    write(os.path.join(prof, "Default/Cookies"), "cookie-bytes-1234")
    write(os.path.join(prof, "Default/Network/Cookies"), "more-cookie-bytes")
    write(os.path.join(prof, "Default/Local Storage/leveldb/000003.log"), "site-data")
    write(os.path.join(prof, "Default/Service Worker/CacheStorage/x/y"), "sw-data")
    write(os.path.join(prof, "Default/Preferences"), "{}")
    write(os.path.join(prof, "Default/History"), "history-bytes")
    write(os.path.join(prof, "Default/Web Data"), "webdata")
    write(os.path.join(prof, "Default/Sessions/abc"), "session")
    # 跟缓存无关的自己的文件
    write(os.path.join(prof, "note.txt"), "my own note")

    # 另一个账号（内容很少）+ profiles 外面的旁观者
    write(os.path.join(p, "Account_02", "Default/Cache/f.bin"), "c" * 64)
    write(os.path.join(root, "旁观者.txt"), "别动我")

    keep = {
        "cookies": os.path.join(prof, "Default/Cookies"),
        "net_cookies": os.path.join(prof, "Default/Network/Cookies"),
        "local_state": os.path.join(prof, "Local State"),
        "local_storage": os.path.join(prof, "Default/Local Storage/leveldb/000003.log"),
        "sw": os.path.join(prof, "Default/Service Worker/CacheStorage/x/y"),
        "prefs": os.path.join(prof, "Default/Preferences"),
        "history": os.path.join(prof, "Default/History"),
        "webdata": os.path.join(prof, "Default/Web Data"),
        "sessions": os.path.join(prof, "Default/Sessions/abc"),
        "note": os.path.join(prof, "note.txt"),
        "bystander": os.path.join(root, "旁观者.txt"),
        "profile_dir": prof,
        "profiles_root": p,
    }
    sizes = {}
    for k, path in keep.items():
        if os.path.isfile(path):
            sizes[k] = os.path.getsize(path) + os.path.getmtime(path)
    return root, keep, sizes


def main():
    print("=" * 66)
    print("清缓存工具：该清的清了，不该碰的没碰")
    print("=" * 66)

    print("\n[A] 白名单自己干不干净（这是第一道防线）")
    names = cc.all_cache_names()
    for bad in ("cookies", "local state", "local storage", "service worker",
                "preferences", "network", "indexeddb", "history", "web data",
                "sessions", "login data", "bookmarks"):
        check("保护名单里的「{}」没混进清理白名单".format(bad), bad not in names)

    weird = [n for n in (cc.CACHE_DIRS + cc.CACHE_FILES + cc.DEFAULT_CACHE_DIRS)
             if n.startswith("/") or n.startswith("\\") or ".." in n
             or os.sep in n or "/" in n]
    check("白名单里全是相对名字（没有绝对路径、没有 ..、没有分隔符）",
          not weird, weird)

    print("\n[B] 默认只报告：一件都不动")
    root, keep, sizes = build_fake_profiles()
    try:
        buf = io.StringIO()
        hits, nbytes = cc.run(keep["profiles_root"], apply=False, say=buf.write)
        check("算出有可清的东西", hits > 0, "命中 {} 项".format(hits))
        check("只报告时，缓存目录原封不动",
              os.path.isdir(os.path.join(keep["profile_dir"], "Default/Cache")))
        check("只报告时连 _cache_trash 都没建",
              not os.path.exists(os.path.join(keep["profiles_root"], cc.TRASH_DIR)))
        check("报告里说明了「一件都没动」", "一件都没动" in buf.getvalue())

        print("\n[C] --apply：该清的清了")
        buf = io.StringIO()
        cc.run(keep["profiles_root"], apply=True, say=buf.write)
        for rel in ("Default/Cache", "Default/Code Cache", "Default/GPUCache",
                    "BrowserMetrics", "component_crx_cache", "WasmTtsEngine",
                    "GrShaderCache", "CrashpadMetrics-active.pma"):
            check("清了 {}".format(rel),
                  not os.path.exists(os.path.join(keep["profile_dir"], rel)))

        print("\n[D] 登录态与站点数据：一个都不能少")
        for key, why in (("cookies", "Cookie 文件"),
                         ("net_cookies", "Network/Cookies"),
                         ("local_state", "Local State（Cookie 解密密钥的家）"),
                         ("local_storage", "Local Storage"),
                         ("sw", "Service Worker"),
                         ("prefs", "Preferences"),
                         ("history", "History"),
                         ("webdata", "Web Data"),
                         ("sessions", "Sessions"),
                         ("note", "跟缓存无关的自己的文件"),
                         ("bystander", "profiles 目录**外面**的旁观者文件")):
            path = keep[key]
            same_size = (os.path.isfile(path)
                         and os.path.getsize(path) + os.path.getmtime(path) == sizes.get(key))
            check("{} 还在，且字节没变".format(why),
                  os.path.exists(path) and same_size, path)
        check("账号目录本身还在",
              os.path.isdir(keep["profile_dir"]))

        print("\n[E] 是「挪」不是「删」")
        trash = os.path.join(keep["profiles_root"], cc.TRASH_DIR)
        stamps = os.listdir(trash) if os.path.isdir(trash) else []
        check("回收站建好了", len(stamps) == 1, stamps)
        if stamps:
            moved = os.path.join(trash, stamps[0], "Account_01", "Default", "Cache", "f.bin")
            check("被清的东西在回收站里，内容原样",
                  os.path.isfile(moved)
                  and io.open(moved, encoding="utf-8").read() == "cache" * 100)
            check("回收站里也保留了账号层级（还原时知道往哪挪）",
                  os.path.isdir(os.path.join(trash, stamps[0], "Account_01")))

        print("\n[F] 再跑一次 / 白名单被人加错东西")
        buf = io.StringIO()
        hits2, _ = cc.run(keep["profiles_root"], apply=True, say=buf.write)
        check("第二次跑命中 0 项（幂等，不会反复搬）", hits2 == 0, "命中 {}".format(hits2))
        check("第二次没建新的回收站",
              len(os.listdir(trash)) == 1, os.listdir(trash))

        old = cc.CACHE_DIRS
        try:
            cc.CACHE_DIRS = ("../跑到外面去",)
            try:
                cc.plan_for(keep["profile_dir"])
                raised = False
            except AssertionError:
                raised = True
            check("白名单被人塞进 `..` 时，plan_for 直接拒绝（不跑到 Profile 外面）",
                  raised)
        finally:
            cc.CACHE_DIRS = old

        print("\n[G] 自检别乱写")
        check("自检没往仓库里写 _cache_trash",
              not os.path.exists(os.path.join(HERE, cc.TRASH_DIR)))
        check("自检全程在临时目录里",
              os.path.abspath(root).startswith(os.path.abspath(tempfile.gettempdir())))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 66)
    if bad:
        print("有 {} 项没过：".format(len(bad)))
        for n in bad:
            print("  - " + n)
    else:
        print("全部通过（{} 项）".format(len(RESULTS)))
    print("=" * 66)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
