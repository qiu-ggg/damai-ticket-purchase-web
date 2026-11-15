# -*- coding: utf-8 -*-
"""清理各账号浏览器 Profile 里的纯缓存，登录态一个不碰。

每个账号一个独立的 Chrome Profile（`profiles/<账号号>`），它既是登录态的载体，也是一个会越长
越大的目录。跑得越多，这些东西越多：`BrowserMetrics/` 每次启动写一个 4 MB 的 .pma（指标）；
`Default/Cache/` HTTP 缓存，默认上限几百 MB；`Default/Code Cache/` V8 编译产物；
`Default/GPUCache/` 显存里的东西落盘；`component_crx_cache/` 组件下载缓存（TTS 引擎、证书
吊销列表…）；`WasmTtsEngine/` 语音合成引擎组件，20 MB 起步。实测（2026-10-08，本机）：跑了
4 次以上的 `profiles/Account_01` 159 MB，只跑过一两次的 `profiles/Account_02` 28 MB，同一套
结构，差的全是上面这些。Profile 越大 Chrome 启动越慢，多开时就会出现「这个账号的浏览器先
出来、那个半天不出来」，抢票时慢的那一个就是白丢几秒。

判据只有一条：删了之后 Chrome 能不能自己重建。能重建，白名单（`CACHE_DIRS` /
`CACHE_FILES`）；不能重建，碰都不碰：登录 Cookie、`Local Storage`、`IndexedDB`、
`Service Worker`、书签、历史、扩展数据、`Preferences`。其中 `Local State` 是最隐蔽的一个：
它不在 `Default/` 里，看着像个无关紧要的状态文件，但 Windows 上 Chrome 的 Cookie 是用一把
存在 `Local State` 里的密钥（DPAPI 包裹的 AES key）加密的，删了它现有 Cookie 全部解不开，
等于把登录态全废掉。所以它必须在保护名单里，并且有断言钉着（`tools/check_clean_cache.py`
的 C6）。

安全做法：只挪不删（命中的目录整块挪到 `<profiles>/_cache_trash/<时间戳>/` 下，要还原直接
挪回来，确认没问题了再自己删掉 `_cache_trash`）；默认只报告，要真清得显式加 `--apply`；每个
要动的路径都先断言确实在 profiles 根目录之下（别让通配符跑飞）；白名单本身也被断言检查过 ——
里面不许出现受保护的名字（C1）。

用法：
    python tools/clean_profile_cache.py                 # 只看看能清多少
    python tools/clean_profile_cache.py --apply         # 真清（挪到 _cache_trash）
    python tools/clean_profile_cache.py --profiles D:/某处/profiles
"""

import argparse
import datetime
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 白名单：Chrome 会自己重建的缓存 / 组件数据

# Profile 根目录下的
CACHE_DIRS = (
    "BrowserMetrics",              # 指标 .pma，每次启动 +4 MB
    "Crashpad",                    # 崩溃转储
    "GrShaderCache",               # GPU 着色器缓存
    "ShaderCache",
    "GPUPersistentCache",
    "GraphiteDawnCache",
    "component_crx_cache",         # 组件下载缓存（会重新下载）
    "extensions_crx_cache",
    "WasmTtsEngine",               # 语音合成组件，20 MB 起步
    "segmentation_platform",
    "OptimizationHints",
    "OptimizationGuideModelsManifest",
    "optimization_guide_hint_cache_store",
    "CertificateRevocation",       # 证书吊销列表（会重新拉）
    "ActorSafetyLists",
    "hyphen-data",                 # 断词表
    "ZxcvbnData",                  # 密码强度词典
    "Subresource Filter",
    "PKIMetadata",
    "Crowd Deny",
    "SafetyTips",
    "TrustTokenKeyCommitments",
    "FirstPartySetsPreloaded",
    "MEIPreload",
    "OriginTrials",
    "SSLErrorAssistant",
    "ScreenAI",
    "AmountExtractionHeuristicRegexes",
)
# Profile 根目录下按文件名的
CACHE_FILES = (
    "CrashpadMetrics-active.pma",
    "CrashpadMetrics.pma",
)

# `Default/` 下的（有些老版本会在根目录，所以两处都找）
DEFAULT_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "DawnWebGPUCache",
    "DawnGraphiteCache",
    "Media Cache",
    "Application Cache",
)

# 保护名单：只用于自查（断言白名单里没混进这些东西），绝不参与删除

PROTECTED = (
    "Cookies", "Cookies-journal",
    "Login Data", "Login Data For Account",
    "Local Storage", "Session Storage", "IndexedDB", "File System",
    "Service Worker", "CacheStorage",
    "Network",                     # Cookies 就存在 Network/ 里
    "Preferences", "Secure Preferences",
    "History", "Web Data", "Bookmarks", "Favicons", "Sessions",
    "Local State",                 # 它是 Cookie 解密密钥的家，见文件头
    "Extensions", "Extension State", "Sync Data", "Sync App Settings",
    "Shared Dictionary",
)

TRASH_DIR = "_cache_trash"


def all_cache_names():
    """白名单全集（小写去重），自查用。"""
    names = set()
    for n in CACHE_DIRS + CACHE_FILES + DEFAULT_CACHE_DIRS:
        names.add(n.lower())
    return names


def find_profiles_root(explicit=None):
    """找 profiles 目录。

    源码形态在 `web/profiles/`，打包形态在 `<exe 同级>/profiles/`。
    两个都不在就返回 None（不猜，也不去全盘搜）。
    """
    if explicit:
        return os.path.abspath(explicit)
    candidates = [
        os.path.join(ROOT, "web", "profiles"),
        os.path.join(ROOT, "dist", "大麦抢票", "profiles"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def list_profiles(root):
    """profiles 根下面每个账号一个目录。顺手跳过工具自己的回收站。"""
    out = []
    try:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if name == TRASH_DIR or not os.path.isdir(p):
                continue
            out.append(p)
    except OSError:
        pass
    return out


def plan_for(profile_dir):
    """算出这个 Profile 里该挪走什么。返回 [(绝对路径, 说明)]。

    每一条都先断言在 profile_dir 之下，白名单里没有 `..`、也没有绝对路径，
      但这条断言是防「以后有人往白名单里加东西」的，代价只有一行。

    `displayed=true` 之类的属性文件不在这里：只按「名字在不在白名单」判。
    """
    hits = []
    base = os.path.abspath(profile_dir)

    def add(path):
        ap = os.path.abspath(path)
        if not (ap == base or ap.startswith(base + os.sep)):
            raise AssertionError("要动的路径跑到 Profile 外面去了：{}".format(ap))
        if os.path.exists(ap):
            hits.append(ap)

    for name in CACHE_DIRS:
        add(os.path.join(profile_dir, name))
    for name in CACHE_FILES:
        add(os.path.join(profile_dir, name))
    for name in DEFAULT_CACHE_DIRS:
        add(os.path.join(profile_dir, "Default", name))
        add(os.path.join(profile_dir, name))           # 老版本可能在根目录
    return hits


def dir_size(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for r, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total


def human(n):
    return "{:.1f} MB".format(n / 1024.0 / 1024.0)


def run(root, apply=False, say=print):
    """返回 (命中文件数, 释放字节数)。apply=False 时只报告。"""
    profiles = list_profiles(root)
    if not profiles:
        say("{} 下面没有账号目录。".format(root))
        return 0, 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    trash_root = os.path.join(root, TRASH_DIR, stamp)
    total_hits, total_bytes, moved = 0, 0, 0

    for prof in profiles:
        hits = plan_for(prof)
        if not hits:
            say("  {:<16} 没有可清的（要么本来就干净，要么还没跑过）".format(
                os.path.basename(prof)))
            continue
        size = sum(dir_size(h) for h in hits)
        total_hits += len(hits)
        total_bytes += size
        say("  {:<16} 可清 {} 项，约 {}".format(
            os.path.basename(prof), len(hits), human(size)))

        if not apply:
            for h in hits:
                say("      · {}".format(os.path.relpath(h, root)))
            continue

        for h in hits:
            rel = os.path.relpath(h, root)
            dst = os.path.join(trash_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.move(h, dst)
                moved += 1
            except OSError as e:
                say("      ! 挪不动 {}：{!r}".format(rel, e))

    if not apply:
        say("\n以上只是报告（一件都没动）。要真清加 --apply。")
        return total_hits, total_bytes

    if moved:
        say("\n已挪走 {} 项，腾出约 {}。".format(moved, human(total_bytes)))
        say("挪到：{}".format(trash_root))
        say("是**挪**不是删 —— 确认一切正常后自己删掉这个目录即可；")
        say("  要还原就把里面的东西挪回对应的账号目录。")
    else:
        say("\n没有需要挪的。")
    return total_hits, total_bytes


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="清理各账号浏览器 Profile 里的纯缓存（登录态不碰，只挪不删）")
    ap.add_argument("--profiles", default="", help="profiles 目录；不填自动找")
    ap.add_argument("--apply", action="store_true",
                    help="真的清（默认只报告）")
    args = ap.parse_args(argv)

    root = find_profiles_root(args.profiles or None)
    if not root:
        print("找不到 profiles 目录。用 --profiles 指定一个。")
        return 1
    print("profiles 目录：{}".format(root))
    print("模式：{}".format("清理（挪到 {}）".format(TRASH_DIR) if args.apply else "只报告"))
    print()
    run(root, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
