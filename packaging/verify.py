# -*- coding: utf-8 -*-
"""打完包之后自己验一遍，不用点界面。在项目根目录跑：

    python packaging/verify.py

验的是最容易被「临时解压目录」坑掉的那几件事：

1. 模块是不是都打进去了（`--runner --paths`）：漏了某个裸模块名，平时要点到
   「全部启动」才炸，这里立刻能看出来。
2. 数据是不是落在 exe 同级目录，而不是临时解压目录 —— 打包版头号故障，
   落错的症状是「每次打开账号全空」。
3. 随包的 chromedriver 在不在、版本对不对。
4. 随包的说明文档和更新日志在不在、内容对不对。

返回码 0 表示全过。
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_DIR = os.path.join(ROOT, "dist", "大麦抢票")
APP_EXE = os.path.join(APP_DIR, "大麦抢票.exe")

_results = []


def check(name, passed, detail=""):
    _results.append((name, bool(passed), detail))
    print("  [{}] {}{}".format("OK" if passed else "!!", name,
                               ("  <<< " + detail) if detail else ""))
    return passed


def _decode(raw):
    """exe 的输出走 Windows 控制台代码页（GBK），不一定能按 UTF-8 解。

    按 UTF-8 解 GBK 不报错、只会全变乱码，检查项找不到关键字就误判失败。
      依次试 utf-8 / gbk / latin-1，别用 `text=True` 让它自己猜。
    """
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run(exe, args, timeout=120):
    """跑一条命令，返回 (returncode, 解码后的输出)。"""
    try:
        r = subprocess.run([exe] + list(args), capture_output=True, timeout=timeout)
        return r.returncode, _decode((r.stdout or b"") + (r.stderr or b""))
    except subprocess.TimeoutExpired:
        return None, "超时（{} 秒）".format(timeout)
    except Exception as e:
        return None, repr(e)



def verify_app():
    print("\n--- 主程序 ---")
    if not check("exe 存在", os.path.isfile(APP_EXE), APP_EXE):
        return

    # 先存后清再还。下面每条都真起 exe，exe 一起会在同级建出数据文件和 logs/，收尾得清掉它们
    #   才能验「包里没有用户数据」；2026-10-08 直接清把用户自己的 accounts.json / profiles/ 一起删了。
    park_runtime_junk()

    # ① 角色分发 + 模块收集
    rc, out = run(APP_EXE, ["--runner", "--paths"])
    check("`--runner` 能起来（说明模块都打进去了）", rc == 0,
          "" if rc == 0 else out.strip()[-300:])

    # 上面那条太弱：`--paths` 只要 exe 能起、app_paths 在就返回 0。真会出事的是抢票路径用到的裸模块名
    #   （spec 里手工列的 hiddenimports），漏一个就程序能开、界面正常，点到「全部启动」才 ImportError。
    check("`--paths` 把随包模块逐个 import 了一遍",
          "随包模块自检" in out, out.strip()[-160:])
    bad_modules = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[!!]")]
    check("随包模块一个都没漏（无 [!!] 行）", not bad_modules,
          "；".join(bad_modules[:4]))
    ok_modules = [ln.split("] ", 1)[1].strip()
                  for ln in out.splitlines()
                  if ln.strip().startswith("[OK] ") and "] " in ln]
    # 这几条是抢票路径的关键模块，少一个都不行
    for must in ("concert", "config", "check_environment", "account_runner",
                 "task_manager", "cred_store", "chromedriver_locator",
                 "notify", "notify_settings", "env_check",
                 "version"):
        check("随包里有 {}".format(must), must in ok_modules,
              "实际 import 成功的：{}".format(len(ok_modules)))

    if rc == 0:
        for line in out.strip().splitlines():
            print("        " + line)

        # ② 数据目录必须落在 exe 同级，而不是临时解压目录
        data_line = ""
        for line in out.splitlines():
            if line.startswith("数据目录："):
                data_line = line.split("：", 1)[1].strip()
        if data_line:
            same = os.path.normcase(os.path.abspath(data_line)) == \
                os.path.normcase(os.path.abspath(APP_DIR))
            check("数据目录就是 exe 同级目录", same,
                  "" if same else "实际是 {}".format(data_line))
            check("数据目录不是临时解压目录（_MEI）",
                  "_MEI" not in data_line.upper(), data_line)
            check("数据目录确实可写", os.access(data_line, os.W_OK), data_line)

    # ③.5 环境检测命令行版（和界面按钮同一份实现）；客户窗口起不来就靠它把报告发回来。
    rc, out = run(APP_EXE, ["--runner", "--env"], timeout=120)
    check("`--runner --env` 能跑", rc in (0, 2), "退出码 = {}".format(rc))
    check("报告里有标题", "环境检测结果" in out, out.strip()[:80])
    # 抬头必须带版本号：报告是客户整段复制发来的，没版本号就得回问「你装的哪一版」。
    check("报告抬头带着版本号（客户发来的报告能对上版本）",
          re.search(r"v\d+\.\d+", out) is not None, out.strip().splitlines()[0][:80])
    check("报告里有结论那一行", "结论" in out)
    for must in ("Chrome 浏览器", "网络", "数据目录", "抢票组件"):
        check("报告里查了「{}」".format(must), must in out)
    # 专治「exe 里的 selenium 收没收全」：上面 PYZ 那条看「模块在不在包里」，这条看「真能 import
    #   起来」。少了它，懒加载的 chrome.options 没打进包也能一路全绿，直到点「开始抢票」才 ModuleNotFoundError。
    check("报告里「抢票组件」是 [OK]（exe 里 selenium 真能加载）",
          "[OK] 抢票组件" in out,
          next((l.strip() for l in out.splitlines() if "抢票组件" in l),
               "报告里根本没有这一项"))
    if rc == 2:
        print("        !! 本机环境有 fail 项（不影响打包结论，但值得看一眼）：")
        for line in out.splitlines():
            if line.strip().startswith("[X]"):
                print("        " + line.strip())

    # ④ 驱动
    drv = os.path.join(APP_DIR, "chromedriver.exe")
    has_drv = os.path.isfile(drv)
    check("随包带了 chromedriver.exe", has_drv,
          "" if has_drv else "没带（客户机器上会尝试自动下载，网络不通就不行）")

    # ⑤ 别把用户数据打进包。先清掉自己刚跑出来的运行时文件再查：上面几条都真起了 exe，exe 一起就会建出
    #   `logs/`。不管它，自检跑一次 dist/ 就多一个 logs/，下次永远失败。
    removed = clean_runtime_after_run()
    if removed:
        print("        （已清掉本次自检跑出来的：{}）".format("、".join(removed)))

    check("包里没有 accounts.json（不该带用户数据）",
          not os.path.isfile(os.path.join(APP_DIR, "accounts.json")))
    # 短信密钥同理：AccessKeySecret 没理由跟着包走。
    check("包里没有 notify_secret.txt（短信密钥是本机的）",
          not os.path.isfile(os.path.join(APP_DIR, "notify_secret.txt")))
    check("包里没有 credentials.json（账号密码是本机的）",
          not os.path.isfile(os.path.join(APP_DIR, "credentials.json")))
    # logs/ 下写着「什么时候抢了哪场票、哪个账号在用」（accounts.log /
    #   damai_wizard.log / runs/），整块都不该跟着走。
    check("包里没有 logs/（日志目录整块都不该跟着走）",
          not os.path.isdir(os.path.join(APP_DIR, "logs")))
    # damai/ 是运行时生成的（向导保存时给旧版单账号路径写 damai/config.json），装着观演人真名 + 演出链接，也是本机数据。
    check("包里没有 damai/（旧版单账号配置是本机数据）",
          not os.path.isdir(os.path.join(APP_DIR, "damai")))

    # ⑥ 包里的向导到底是不是刚改过的那一份
    verify_bundle_is_current()

    # 收尾：清掉自检跑出来的，再把开跑前那份原样放回，否则这个「检查」自己就成了破坏者。
    restore_runtime_after_run()


# 自检前存下来的运行时文件：[临时目录, [文件名…]]，栈式（可重入）
_parked_runtime = []

# 兜底：中途抛异常 / 提前 return 也要把东西放回去
atexit.register(lambda: restore_runtime_after_run())


def _runtime_junk_names():
    """自检会碰到的运行时文件名。清单和 `clean_runtime_after_run()` 一致。"""
    return ("accounts.json", "accounts.log", "credentials.json",
            "damai_wizard.log", "notify_secret.txt", "notify_state.json",
            "logs", "runs", "profiles", "damai")


def _make_park_dir():
    """建一个和交付目录同卷的停车目录，建在 `dist/` 下。

    不能落在 `tempfile.mkdtemp()` 的默认位置（C 盘 `%TEMP%`，项目却在 D 盘）：
      跨卷时 `shutil.move()` 退化成「先 copytree 再 rmtree」，停车目录一出现
      `profiles/`（842 个文件），rmtree 就被「单次删超 50 个文件要确认」拦下，
      异常逃出去、verify.py 死在半途，文件两边各一份（2026-10-08 真踩过）。
      同卷 `os.rename()` 是原子的、不涉及删除，没这个问题。
    """
    parent = os.path.dirname(os.path.abspath(APP_DIR)) or "."
    park = tempfile.mkdtemp(prefix=".damai_park_", dir=parent)
    # 必须同卷，否则下面的 os.rename 会退化成复制+删除
    if os.path.splitdrive(os.path.abspath(park))[0].lower() != \
            os.path.splitdrive(os.path.abspath(APP_DIR))[0].lower():
        raise RuntimeError("停车目录和交付目录不在同一个卷：{} vs {}"
                           .format(park, APP_DIR))
    return park


def park_runtime_junk():
    """把交付目录里已有的运行时文件挪去旁边存好。返回 (停车目录, 清单)。

    存在的意义只有一条：别让这个自检变成删用户数据的元凶 —— 脚本要真起 exe，
      验「包里没有账号和日志」时收尾得清掉这些名字，而它们和用户自己那份同名。
      先存后清再还才不会误伤。

    只用 `os.rename()`，不碰 `shutil.move()`：同卷 rename 原子，不会出现
      「复制成功、删除被拦、半途抛异常」把文件卡在两边。
    """
    park = _make_park_dir()
    saved = []
    # 先登记再搬：搬到一半抛异常时，atexit 的兜底也知道该去哪儿找（老写法反过来，出事时兜底不知情）。
    _parked_runtime.append((park, saved))
    for name in _runtime_junk_names():
        src = os.path.join(APP_DIR, name)
        if not os.path.exists(src):
            continue
        try:
            os.rename(src, os.path.join(park, name))
            saved.append(name + ("/" if os.path.isdir(os.path.join(park, name)) else ""))
        except OSError:
            pass
    if saved:
        print("        （先把交付目录里已有的运行时文件挪去旁边存着：{}）"
              .format("、".join(saved)))
    return park, saved


def restore_runtime_after_run():
    """清掉自检跑出来的，再把开跑前存的那份原样放回来。可重复调用。

    搬回来同样只用 `os.rename()`（同卷、原子、不涉及删除）。停车目录里剩的东西
      一律不删，宁可留个目录让人自己看，也不能再出现「清理反过来吃掉数据」。
    """
    if not _parked_runtime:
        return []
    park, _saved = _parked_runtime.pop()
    clean_runtime_after_run()
    back = []
    if os.path.isdir(park):
        for name in sorted(os.listdir(park)):
            src = os.path.join(park, name)
            dst = os.path.join(APP_DIR, name)
            try:
                # dst 正常应已清掉；万一还在（比如自检跑出个同名空壳），先挪走，别当场删。
                if os.path.lexists(dst):
                    os.rename(dst, dst + ".superseded")
                os.rename(src, dst)
                back.append(name)
            except OSError:
                pass
        # 只剩空目录才收掉（单个 rmdir，不是批量删）
        try:
            if not os.listdir(park):
                os.rmdir(park)
        except OSError:
            pass
    if back:
        print("        （已把自检前那份放回来：{}）".format("、".join(back)))
    return back


# 自检跑出来的 junk 最多几个文件（logs/ 两三个、damai/ 一个）。目录里超过这个数，只可能是
#   「park 没挪走、那是用户自己那份」，宁可留着也不能删 —— 这种体量会被「单次删超 50 个文件要确认」拦下（2026-10-08 踩过）。
_BULK_GUARD = 50


def _too_many_files(path):
    """目录里条目数是不是超过 _BULK_GUARD（够数就提前返回，不数完）。"""
    n = 0
    for _root, dirs, files in os.walk(path):
        n += len(files) + len(dirs)
        if n > _BULK_GUARD:
            return True
    return False


def clean_runtime_after_run():
    """清掉本脚本自己跑 exe 留下的一堆运行时文件，返回清掉了哪些。

    上面每条都真起 exe，exe 一跑就在同级建出数据文件和 `logs/`（设计如此）。
      不清的话跑完自检的 `dist/` 就不是交付态，而人往往「验完就发」，会把
      日志一起发出去。清单跟 `build.py` 的 `RUNTIME_JUNK` 一致。

    单个目录超过 `_BULK_GUARD` 个条目就跳过不删（见上面的常量注释）。
    """
    removed = []
    for name in ("accounts.json", "accounts.log",
                 "credentials.json", "damai_wizard.log",
                 "notify_secret.txt", "notify_state.json",
                 "order_page_dump.txt", "order_page_submit_dump.txt",
                 "order_page_protocol_dump.txt",
                 "date_pick_dump.txt", "session_pick_dump.txt"):
        p = os.path.join(APP_DIR, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed.append(name)
            except OSError:
                pass
    for name in ("logs", "runs", "profiles", "damai"):
        p = os.path.join(APP_DIR, name)
        if not os.path.isdir(p):
            continue
        if _too_many_files(p):
            print("        （{} 里有超过 {} 个条目，像是你自己的东西，没动它）"
                  .format(name, _BULK_GUARD))
            continue
        try:
            shutil.rmtree(p, ignore_errors=True)
            if not os.path.isdir(p):
                removed.append(name + "/")
        except OSError:
            pass
    return removed


def verify_bundle_is_current():
    """把 exe 里那坨字节码掏出来，确认它是当前这份源码编出来的。

    改完源码忘了重打、或打了一半失败（PyInstaller 清旧目录被拦之类），`dist/`
      里还是上一版 exe：光看时间戳不可靠，客户双击的还是旧界面，而所有
      「源码级」自检全绿。

    做法：PyInstaller 把主脚本以 marshal 字节码放进 exe 归档，取出来搜源码里
      独有的字符串（函数名 + 一段 docstring）。取不出归档（PyInstaller 版本变了 /
      换解释器）就报「跳过」，不算失败。

    标记挑「改完源码才会变」的：每修一个会废掉整套功能的 bug 就补一条
      （`fit_window` 窗口自适应；`startup_precheck` 对应「点开始抢票弹找不到
      accounts.bat」，忘了重打时 dist/ 里还是会弹错的旧 exe，源码侧却全绿）。
    """
    print("\n--- 包里的向导是不是当前这份源码 ---")
    try:
        import marshal                                              # noqa: F401
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:                                        # pragma: no cover
        print("  [--] 跳过（这个解释器里没有 PyInstaller：{!r}）".format(exc))
        return
    try:
        blob = CArchiveReader(APP_EXE).extract("damai_wizard")
    except Exception as exc:                                        # pragma: no cover
        check("能从 exe 里取出 damai_wizard 的字节码", False, repr(exc))
        return
    src = os.path.join(ROOT, "web", "damai_wizard.py")
    # 两个都取自「改完源码就会变」的地方：函数名（co_names）和它的 docstring（常量）。
    #   marshal 会合并重复字符串，包里通常只出现一次，判据是「有没有」，不是「几处」。
    for marker, why in ((b"fit_window", "窗口自适应函数（高 DPI 下按钮被切掉那件事）"),
                        ("按内容实际需要给窗口定尺寸".encode("utf-8"),
                         "它上面那段说明"),
                        (b"startup_precheck",
                         "开抢前自检（打包版不该再找 accounts.bat）"),
                        ("打包形态：两样都不要".encode("utf-8"),
                         "它上面那段说明"),
                        # 2026-10-09：抢票子进程不再弹控制台，两条启动路都走 `spawn_runner`。不能反向断言
                        #   「包里没有 CREATE_NEW_CONSOLE」，那个字面量在回退分支里还在。
                        (b"spawn_runner",
                         "起抢票子进程的入口（不再弹控制台窗口）"),
                        (b"DAMAI_SHOW_CONSOLE",
                         "回退开关（设了它才重新弹窗）")):
        check("exe 里的 damai_wizard 含「{}」（{}）".format(
                  marker.decode("utf-8"), why),
              blob.count(marker) >= 1,
              "包里 {} 处 —— 一处都没有，说明 dist/ 里还是上一版 exe，重打一次"
              .format(blob.count(marker)))
    src_bytes = open(src, "rb").read()
    check("源码 {} 里确实有这些标记（上面几条不是在验空气）"
          .format(os.path.basename(src)),
          src_bytes.count(b"fit_window") >= 1
          and src_bytes.count(b"startup_precheck") >= 1
          and src_bytes.count(b"def spawn_runner") >= 1
          and src_bytes.count(b"DAMAI_SHOW_CONSOLE") >= 1)
    # 反向那条更要紧：源码里不许再「不分形态地拦 accounts.bat」，那个检查只该留在
    #   `startup_precheck` 里。只查它不查解释器：解释器别处本来就该查一次，不算 bug。
    check("源码里查 accounts.bat 只出现在 startup_precheck 一处"
          "（别处再拦一次，打包版就又进不去了）",
          src_bytes.count(b"os.path.exists(ACCOUNTS_BAT)") == 1,
          "出现 {} 处".format(src_bytes.count(b"os.path.exists(ACCOUNTS_BAT)")))

    # 两个「源码里碰不到、只有 exe 才会犯」的错，都从 PYZ 归档里看：① 控制台编码 —— `--runner`
    #   直接跑时输出到代码页 936 的中文控制台，缺 `_setup_console_encoding` 兜底，第一条带 ⏳ 的
    #   print 就 UnicodeEncodeError；② selenium 懒加载，漏收 `chrome.options` 就点不动「开始抢票」。
    print("\n--- 包里的 runner / selenium 收全了没有 ---")
    try:
        pyz = CArchiveReader(APP_EXE).open_embedded_archive("PYZ.pyz")
        pyz_names = set(pyz.toc)
    except Exception as exc:                                        # pragma: no cover
        check("能从 exe 里读出 PYZ 归档", False, repr(exc))
        pyz_names = set()
    if pyz_names:
        check("exe 的 PYZ 里有 account_runner（--runner 角色全靠它）",
              "account_runner" in pyz_names)
        check("exe 的 PYZ 里有 selenium.webdriver.chrome.options"
              "（懒加载，漏收就点不动「开始抢票」）",
              "selenium.webdriver.chrome.options" in pyz_names,
              "没收到 —— 这样的包一开抢就 ModuleNotFoundError，重打一次")
        # PYZ 里存的是 marshal 过的 code 对象，不是文件字节：搜名字得先 marshal 回
        #   字节，直接 .count() 会报 'code' object has no attribute 'count'。
        try:
            import marshal
            ar_blob = marshal.dumps(pyz.extract("account_runner"))
        except Exception:                                           # pragma: no cover
            ar_blob = b""
        check("exe 里的 account_runner 含「_setup_console_encoding」"
              "（GBK 控制台下带符号的日志会直接抛异常）",
              ar_blob.count(b"_setup_console_encoding") >= 1,
              "包里 0 处 —— dist/ 里还是没修编码的那版 exe，重打一次")
        ar_src = os.path.join(ROOT, "web", "account_runner.py")
        check("源码 account_runner.py 里确实有这个标记（不是在验空气）",
              open(ar_src, "rb").read().count(b"_setup_console_encoding") >= 1)

        # 「日历日期」是独立字段（2026-10-10 用户要求：日历与场次分开填），必须从界面
        #   一路透传到子进程。account_runner.build_config 是链路中间一环，少它就是白拆。
        check("exe 里的 account_runner 会把 calendar_date 透传给 concert",
              ar_blob.count(b"calendar_date") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「日历和场次共用一个框」那版，重打一次")
        check("源码 account_runner.py 里也确实有这个标记（不是在验空气）",
              open(ar_src, "rb").read().count(b"calendar_date") >= 1)

        # 2026-10-10：浏览器中途被关掉时 choose_ticket 会「干净返回」。account_runner 原来在它后面无条件
        #   发「抢到了，去付款」并记进 held，等于把「关窗口」报成「抢到了」；修法是先看 browser_closed。
        check("exe 里的 account_runner 会先看 browser_closed 再报「抢到了」",
              ar_blob.count(b"browser_closed") >= 1,
              "包里 0 处 —— dist/ 里还是「关掉窗口也发抢到短信」那版，重打一次")
        check("源码 account_runner.py 里也确实有这个标记（不是在验空气）",
              open(ar_src, "rb").read().count(b"browser_closed") >= 1)

        # 「改了源码忘了重打」的哨兵。下单页换成了 m.damai.cn/app/dmfe/h5-ultron-buy/，页面判定没跟上
        #   就把新页当老 PC 页、拿老选择器点不存在的按钮，订单不响地不提交（修在 web/damai/concert.py）。
        try:
            import marshal
            con_blob = marshal.dumps(pyz.extract("concert"))
        except Exception:                                           # pragma: no cover
            con_blob = b""
        check("exe 里的 concert 认得出「h5-ultron-buy」这种下单页地址"
              "（漏了就是拿老选择器点空按钮、订单不提交）",
              con_blob.count(b"h5-ultron-buy") >= 1,
              "包里 0 处 —— dist/ 里还是没修页面判定的那版 exe，重打一次")
        con_src = os.path.join(ROOT, "web", "damai", "concert.py")
        check("源码 concert.py 里确实有这个标记（不是在验空气）",
              open(con_src, "rb").read().count(b"h5-ultron-buy") >= 1)

        # 第二条哨兵：地址认出来了、观演人还是勾不上。二代订单页换了套 DOM，类名 .viwer-info-name
        #   （大麦把 viewer 拼错了），整页没有 input[type=checkbox]、勾选态用图标字体画，照一代的选择器 0 命中还不报错。
        check("exe 里的 concert 认得第二代类名「.viwer-info-name」"
              "（大麦拼错了 viewer，照正确拼写找是 0 命中）",
              con_blob.count(b"viwer-info-name") >= 1,
              "包里 0 处 —— dist/ 里还是没修观演人勾选的那版 exe，重打一次")
        check("exe 里的 concert 认得图标勾选态「icondanxuan-xuanzhong_」"
              "（这一页没有复选框，读 checked 永远是 0）",
              con_blob.count(b"icondanxuan-xuanzhong_") >= 1,
              "包里 0 处 —— 同上，重打一次")
        con_src_bytes = open(con_src, "rb").read()
        check("源码 concert.py 里确实有这两个标记（不是在验空气）",
              con_src_bytes.count(b"viwer-info-name") >= 1
              and con_src_bytes.count(b"icondanxuan-xuanzhong_") >= 1)

        # 第三条哨兵：提交按钮的「按文案找」兜底。2026-10-08 23:10 实跑里观演人勾好了（1/1），下一行却
        #   「找不到『提交订单』按钮」：底部由服务端模板渲染、标签文案不固定，找按钮的 JS 只扫
        #   button/div/span/a/section，`<div><p>提交订单</p></div>` 的 p 不在白名单、外层 div 被「叶子优先」跳过；文案清单也补了「立即购票」等几个词。
        check("exe 里的 concert 有「立即购票」这类新按钮文案"
              "（原来只有提交订单/立即提交/确认下单…）",
              con_blob.count("立即购票".encode("utf-8")) >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是没修提交按钮的那版，重打一次")
        check("exe 里的 concert 认不出按钮时会留「按钮区」快照"
              "（order_page_submit_dump.txt）",
              con_blob.count(b"order_page_submit_dump.txt") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("源码 concert.py 里也确实有这两个新标记（不是在验空气）",
              con_src_bytes.count("立即购票".encode("utf-8")) >= 1
              and con_src_bytes.count(b"order_page_submit_dump.txt") >= 1)

        # 第四条哨兵：底部（协议条 + 提交条）比观演人列表晚渲染。2026-10-08 23:10 实跑里「进订单页 /
        #   勾观演人 / 找不到提交按钮」全在同一秒：脚本只等列表，列表一出就往下走，那一瞬底部还是空的，
        #   判「找不到按钮」整单放弃且不报错（同一页晚 16 秒进的另一路就找得到）。底部没出来时协议框也找不到。
        check("exe 里的 concert 会等页面底部渲染出来"
              "（H5_BOTTOM_READY_JS；不等就是「人勾好了、按钮找不到」）",
              con_blob.count(b"H5_BOTTOM_READY_JS") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是抢跑的那版，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"H5_BOTTOM_READY_JS") >= 1)

        # 第五条哨兵：协议那一行的勾选态也可能是图标字体画的。2026-10-09 00:28 实跑里「观演人勾好了、
        #   按钮也找到了」，下一行却「有协议框但没勾上」，接着连点 8 次提交全无反应。协议勾选态原来只认
        #   `input.checked`，而这代用 `i.icondanxuan-xuanzhong_` 画、input 永远不 checked；修法是「图标优先于 input」+ 勾不上时拍协议区快照。
        check("exe 里的 concert 带「协议区结构快照」（勾不上时不用再靠猜）",
              con_blob.count(b"H5_PROTOCOL_DUMP_JS") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「协议只读 input.checked」那版，重打一次")
        check("exe 里的 concert 会把协议区快照写成 order_page_protocol_dump.txt",
              con_blob.count(b"order_page_protocol_dump.txt") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("源码 concert.py 里也确实有这两个标记（不是在验空气）",
              con_src_bytes.count(b"H5_PROTOCOL_DUMP_JS") >= 1
              and con_src_bytes.count(b"order_page_protocol_dump.txt") >= 1)

        # 第六条哨兵：按钮找不到时把「页面上所有含提交/下单的短元素」列出来（submitLike），回答「这页到底
        #   有没有提交按钮」。文案清单同时删掉了光杆「提交」「下单」，否则按「包含」匹配会对着任何带这俩字的句子猛点。
        check("exe 里的 concert 会把「含提交/下单的短元素」拍下来（submitLike）",
              con_blob.count(b"submitLike") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「对着假按钮猛点」那版，重打一次")

        # 第六条半哨兵：日历式详情页（2026-10-10 客户截图实测）里「日期」是月历格子、
        #   场次只渲染当前选中那天。不先点目标日子，DOM 里根本没那天的场次文本（客户：27 号选不上）。
        check("exe 里的 concert 会点日历格（_pc_click_calendar_day）",
              con_blob.count(b"_pc_click_calendar_day") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「27 号选不中」那版，重打一次")
        check("源码 concert.py 里也确实有日历标记（不是在验空气）",
              con_src_bytes.count(b"_pc_click_calendar_day") >= 2)

        # 第六条大半哨兵：「日历日期」是跟场次分开的独立字段（2026-10-10 用户要求）。以前一个值既匹配场次
        #   又点日历格子，两头都不对（用户：「场次选不对，日期也选不对」）；字段名必须真落到 exe 里，否则等于没拆。
        check("exe 里的 concert 读的是独立的 calendar_date（不是在读场次）",
              con_blob.count(b"calendar_date") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「日历和场次共用一个框」那版，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"calendar_date") >= 1)

        # 第六条又半哨兵：日历点完「场次/票档一个都没选上」（2026-10-10 客户现场）。真站场次列表异步重渲染：
        #   点日历格先清空再拉回新那天的场次，原来那句「已点 27 号」只验「页面某处有日期字样」，于是拿空列表
        #   去匹配、永远配不上；等档循环每 2 秒左右重点一遍日历又把列表清空，锁死在竞态里。现在等列表出条目（_LIST_READY_JS）。
        check("exe 里的 concert 会等场次/票档列表渲染出条目（_LIST_READY_JS）",
              con_blob.count(b"_LIST_READY_JS") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「对着空列表匹配」那版，重打一次")
        check("exe 里的 concert 同一天不会重复点日历格（_CAL_MARK_JS）",
              con_blob.count(b"_CAL_MARK_JS") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("exe 里的 concert 会把「场次区」现场快照写成 session_pick_dump.txt",
              con_blob.count(b"session_pick_dump.txt") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("源码 concert.py 里也确实有这几个新标记（不是在验空气）",
              con_src_bytes.count(b"_LIST_READY_JS") >= 2
              and con_src_bytes.count(b"_CAL_MARK_JS") >= 2
              and con_src_bytes.count(b"session_pick_dump.txt") >= 1)

        # 第六条又四分之三哨兵：详情页新形态（2026-10-10 客户实测 id=1086775381505）。老代码只认
        #   sku-times-card / sku-tickets-card，新页按类名查是空的；条目文案还带排版空格（「2026.12.27
        #   19:30␣␣下本」两个空格），XPath 字面比较永远对不上。现在按 .select_left 的文本找容器、比对前归一化空白。
        check("exe 里的 concert 认得详情页新形态的容器（perform__order__select）",
              con_blob.count(b"perform__order__select") >= 1,
              "包里 0 处 —— dist/ 里的 exe 还是「详情页选不中场次」那版，重打一次")
        check("exe 里的 concert 比对前会归一化空白（_norm_ws）",
              con_blob.count(b"_norm_ws") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("exe 里的 concert 有 XPath 字面量转义（_xpath_literal）",
              con_blob.count(b"_xpath_literal") >= 1,
              "包里 0 处 —— 同上，重打一次")
        check("源码 concert.py 里也确实有这几个新标记（不是在验空气）",
              con_src_bytes.count(b"perform__order__select") >= 2
              and con_src_bytes.count(b"_norm_ws") >= 2
              and con_src_bytes.count(b"_xpath_literal") >= 2)

        # 选座页价位图例哨兵（2026-10-10 客户现场）。客户报「不自动选座了」，日志每 0.3 秒刷「价位 [480, 680, …] 里没有
        #   配置要的 280」，可页面上明明有 280：选中态那档的文案载体是 `.price__package`、写成「280.0元」（换类名又不带
        #   「￥」），解析只认 `.price__text` + 「￥数字」就整项跳过、永不选座；修法是选择器认两套类名。盯的是 `price__package` 这个值、不是 `H5_PRICE_TEXT_SELECTOR` 这个名（后者修复前就有，盯名字忘了重打照样恒真）。
        check("exe 里的 concert 认得出选中态价位的类名（price__package）",
              con_blob.count(b"price__package") >= 1,
              "包里 0 处 —— dist/ 里还是「选座页读不到配置价位」那版，重打一次")
        check("exe 里的 concert 认得出「浏览器已经没了」（BROWSER_GONE_MARKS）",
              con_blob.count(b"BROWSER_GONE_MARKS") >= 1,
              "包里 0 处 —— 关掉窗口会连抛异常把整轮打断，重打一次")
        check("exe 里的 concert 会把「浏览器没了」记下来（_mark_browser_gone）",
              con_blob.count(b"_mark_browser_gone") >= 1,
              "包里 0 处 —— 同上，重打一次")
        # 提交订单被风控拦下的哨兵（2026-10-10 客户现场）。大麦在提交那步甩出一页「检测到您的访问异常」，
        #   旧代码认不出，会接着反复点提交、还把「被拦下」当成「订单已提交」报出去。修法是新增
        #   `_risk_hit_in_probe` 认这页（3 处调用），13 个历史产物实测命中全 0；风控页正文大麦随时会改，钉它早晚失效。
        check("exe 里的 concert 认得出「提交被风控拦下」（_risk_hit_in_probe）",
              con_blob.count(b"_risk_hit_in_probe") >= 1,
              "包里 0 处 —— dist/ 里还是「提交被风控拦下还接着点」那版，重打一次")
        # 风控页晚渲染的哨兵（2026-10-11 客户现场）。客户报「选完座点提交还是显示访问异常」，日志里是
        #   「等了 8 秒观演人列表还是没渲染出来」→「观演人没勾够」，把真实原因（被拦下了）盖住了。原因是
        #   风控页顶在订单页地址上、标题也叫「确认购买」，`_commit_order_on_h5` 只在刚进页面那一下查过一次
        #   风控，那下查早了；等观演人列表那 8 秒里从不复查，风控页整个漏掉。修法是等列表的循环里每轮复
        #   查一次（`_risk_word_now` 六处接入），并把命中词和正文长度写进结构快照。盯的 `_risk_word_now`
        #   在修复前那版里是 0 处（实测过），不是「修复前就有」的名字。
        check("exe 里的 concert 在等观演人那几秒里复查风控页（_risk_word_now）",
              con_blob.count(b"_risk_word_now") >= 1,
              "包里 0 处 —— dist/ 里还是「进页面查一次风控、风控页晚渲染就白等 8 秒」那版，重打一次")
        check("exe 里的 concert 结构快照带正文长度和风控词（PAGE_SHAPE_JS）",
              con_blob.count(b"PAGE_SHAPE_JS") >= 1,
              "包里 0 处 —— 同上；日志里分辨不出「订单页没配这个人」和「被风控拦下」，重打一次")
        check("源码 concert.py 里也确实有这几个标记（不是在验空气）",
              con_src_bytes.count(b"price__package") >= 1
              and con_src_bytes.count(b"H5_PRICE_TEXT_SELECTOR") >= 2
              and con_src_bytes.count(b"BROWSER_GONE_MARKS") >= 2
              and con_src_bytes.count(b"_mark_browser_gone") >= 2
              and con_src_bytes.count(b"_risk_hit_in_probe") >= 3
              and con_src_bytes.count(b"_risk_word_now") >= 4
              and con_src_bytes.count(b"RISK_WORD_JS") >= 2
              and con_src_bytes.count(b"PAGE_SHAPE_JS") >= 2
              and con_src_bytes.count(b"_h5_risk_word") >= 4)
        # 节奏加抖动/降速的哨兵（2026-10-11 用户要求）。改之前全工程只有主循环那一次刷新带抖动，
        #   其余等待全是精确到毫秒的固定间隔，等档圈严格 3 秒一次跑几个小时 —— 这种节奏真人做不出来，
        #   是客户账号被风控盯上的主嫌疑。修法是新增 `_pace()`（间隔乘 0.8~1.25 的随机数）并把等档圈
        #   的三个常量放慢。盯 `PACE_LO`：它在修复前那版里是 0 处，不是「修复前就有」的名字。
        check("exe 里的 concert 给等待加了抖动（_pace / PACE_LO）",
              con_blob.count(b"_pace") >= 1
              and con_blob.count(b"PACE_LO") >= 1,
              "包里 0 处 —— dist/ 里还是「所有等待都是固定间隔」那版，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"_pace") >= 10
              and con_src_bytes.count(b"PACE_LO") >= 2
              and con_src_bytes.count(b"PACE_HI") >= 2)
        # 票价「带单位」子串误命中的哨兵（2026-10-11 客户报「选 80 元跳到 280」）。改之前
        #   `_option_text_match` 只在配置值是纯数字时按「独立的数」比，客户写「80.0元」就退回字面
        #   子串，`'80.0元' in '280.0元'` 是 True，于是点中了 280 那一档；`_find_and_click_element`
        #   的同类守卫也只在纯数字时生效。修法是新增 `_nums_covered`（配置值里每个数都必须在页面
        #   文案里作为独立的数出现），子串分支接上它，另一处按同一判据改写。盯 `_nums_covered`：
        #   它在修复前那版里是 0 处，不是「修复前就有」的名字。
        check("exe 里的 concert 挡住了带单位票价的子串误命中（_nums_covered）",
              con_blob.count(b"_nums_covered") >= 1,
              "包里 0 处 —— dist/ 里还是「配置写 80.0元 会点中 280.0元」那版，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"_nums_covered") >= 2)
        # 同一天客户报的那条的另一半：配置写页面原文时也被展开成裸数字。`_price_match_values`
        #   会把配置值额外展开出裸数字候选，本意是让「80.0元」也能选中页面上写「80元」的那档；
        #   但无条件展开时，配置写「全价票188元（站席）」也会多出一个 `188`，于是走纯数字分支
        #   命中了页面上的「（坐席）」那一项。修法是展开前先判这值是不是「就是一个价」
        #   （`_is_bare_price`：只剩数字和价钱写法，没有别的字）且只含一个数，顺带把数字写法归一
        #   （`_norm_num_text`：去千分位逗号、去掉整数后面的 .0），好让「80.0元」对上「80元」。
        #   盯 `_is_bare_price` / `_norm_num_text` / `_BARE_PRICE_NOISE_RE`：这三个名字在修复前
        #   那版里都是 0 处。
        check("exe 里的 concert 只对「就是一个价」的值展开裸数字（_is_bare_price / _norm_num_text）",
              con_blob.count(b"_is_bare_price") >= 1
              and con_blob.count(b"_norm_num_text") >= 1
              and con_blob.count(b"_BARE_PRICE_NOISE_RE") >= 1,
              "包里 0 处 —— dist/ 里还是「配置写全价票188元（站席）会点中（坐席）」那版，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"_is_bare_price") >= 2
              and con_src_bytes.count(b"_norm_num_text") >= 2
              and con_src_bytes.count(b"_BARE_PRICE_NOISE_RE") >= 1)

        # 第七条哨兵：「任务画面」后端（把每个窗口缩成缩略图）。它在独立模块 window_wall
        #   里、不在 concert，得单独挖出来验；PyInstaller 静态分析 import，花式导入会悄悄漏掉。
        try:
            wall_blob = marshal.dumps(pyz.extract("window_wall"))
            wall_ok = (wall_blob.count(b"PrintWindow") >= 1
                       and wall_blob.count(b"thumb_b64") >= 1
                       and wall_blob.count(b"_grab_rgb_iconic") >= 1)
        except Exception:
            wall_ok = False
        check("exe 里带「任务画面」后端（window_wall：截图 + 最小化窗口离屏恢复）",
              wall_ok,
              "exe 的 PYZ 里没有 window_wall（或缺最小化截图那条路）—— "
              "画面墙在交付版里会打不开 / 最小化的窗口没画面，重打一次")
        check("源码 concert.py 里也确实有这个标记（不是在验空气）",
              con_src_bytes.count(b"submitLike") >= 1)

        # 第八条哨兵：实时状态链路（2026-10-09）。「不管任务从哪条路起、都算到账号那一行，还要细到 刷新中/
        #   轮询中/待提交订单/需要验证码/登录输错」跨四个模块，少一个都不报错、只是功能静默失灵，所以每模块各挖一个 ASCII 特征名钉住。
        def _blob_has(mod, *marks):
            # 一般模块在 PYZ 里，主脚本 damai_wizard 在外层 CArchive，两处都找一遍。
            try:
                b = marshal.dumps(pyz.extract(mod))
            except Exception:
                try:
                    b = CArchiveReader(APP_EXE).extract(mod)
                except Exception:
                    return False, "exe 里找不到 {} 模块（PYZ / CArchive 都试了）".format(mod)
            missing = [m for m in marks if b.count(m.encode()) < 1]
            return (not missing), "缺: {}".format(missing or "")

        ok, why = _blob_has("live_status", "pid_alive", "HEARTBEAT_SEC")
        check("exe 里带实时状态模块（live_status）", ok,
              why + " —— 面板读不到细状态，重打一次")
        ok, why = _blob_has("account_runner", "_RunnerTee", "_STATE_BY_OUTPUT",
                            "detect_login_error", "_hold_until_all_closed")
        check("exe 里 account_runner 带 print 转发 + 状态识别 + 跑完守浏览器",
              ok, why + " —— 状态永远不会更新 / 跑完浏览器被收走，重打一次")
        ok, why = _blob_has("task_manager", "kill_tree", "_alive_fast")
        check("exe 里 task_manager 带 kill_tree（停「启动页起的」任务）",
              ok, why)
        ok, why = _blob_has("damai_wizard", "_attach_live", "ALERT_STATES",
                            "focus_browser", "install_clipboard_menu")
        check("exe 里面板带实时状态合并 / 标红 / 双击开浏览器 / 输入框右键菜单",
              ok, why)
        # 第九条哨兵：「画面」列（每行一个实时缩略图，2026-10-09）和它的投递箱；不能子线程 after。
        ok, why = _blob_has("damai_wizard", "_maybe_shoot", "SHOT_H",
                            "_drain_inbox")
        check("exe 里面板带「画面」列（缩略图 + 投递箱）", ok,
              why + " —— 窗口列表形态会缺一块，重打一次")

        # 第十条哨兵：版本号（2026-10-10 用户要求升到 v0.2）。版本号是用户第一眼看到的东西，也是报障时
        #   唯一能对上号的线索。这里拿源码里的 APP_VERSION 去比、不写死字符串，以后升版本这条断言自动跟着走。
        try:
            _src_ver = re.search(
                r'APP_VERSION\s*=\s*"([^"]+)"',
                open(os.path.join(ROOT, "web", "version.py"),
                     encoding="utf-8").read()).group(1)
        except Exception as exc:
            _src_ver = None
        if _src_ver:
            ok, why = _blob_has("version", _src_ver)
            check("exe 里的版本号 == 源码里的（{}）".format(_src_ver), ok,
                  why + " —— exe 还是老版本，重打一次")
        else:
            check("读得到源码里的 APP_VERSION", False, "web/version.py 里没解析到")
        check("源码 version.py 里也确实有这个标记（不是在验空气）",
              _src_ver is not None
              and b'APP_VERSION = "' + _src_ver.encode() + b'"'
              in open(os.path.join(ROOT, "web", "version.py"), "rb").read())

        # 第十一条哨兵：更新日志（2026-10-10 用户要求：版本号旁加个「?」点开看）。
        #   模块零依赖、只读一个 txt，源码形态正常；exe 要是漏了，客户点「?」只会看到「缺少文件」。
        ok, why = _blob_has("changelog", "find_path", "candidates",
                            "更新日志.txt")
        check("exe 里带更新日志模块（changelog）", ok,
              why + " —— 点「?」会说缺少文件，重打一次")


def verify_docs():
    """说明书和更新日志得跟着走。"""
    print("\n--- 随包说明 ---")
    # 2026-10-09：`大麦抢票/` 里那两份客户版说明不再随包（发布方决定），这里改成反向
    #   检查：产物里还有就报红，免得哪天打包逻辑改回去又把它们带出来。
    for name in ("使用说明.md", "抢票能力说明.md"):
        p = os.path.join(APP_DIR, name)
        ok = not os.path.isfile(p)
        check("大麦抢票 / {} 不随包（产物里不该有）".format(name), ok,
              "" if ok else "还在：{} —— 删掉它，或跑一次 build.py "
                            "（它会按 STALE_DOCS 清）".format(p))
    # 更新日志相反，是要随包的：客户点版本号旁那个「?」看的就是它，少了它那个圆圈
    #   点开只说「没找到更新日志」。源文件在 dist/更新日志.txt，打包时自动拷过来。
    p = os.path.join(APP_DIR, "更新日志.txt")
    check("大麦抢票 / 更新日志.txt 随包（点「?」看的就是它）",
          os.path.isfile(p),
          "" if os.path.isfile(p) else "缺 {} —— 跑一次 build.py 会从 "
                                       "dist/更新日志.txt 拷过来".format(p))
    # 内容也得对得上。比字节能挡住两种事故：拷进旧版本，或脚本顺手改内容
    #   （2026-10-10 出过一次：脚本改文案把发布方手写那版冲掉了）。
    src = os.path.join(ROOT, "dist", "更新日志.txt")
    if os.path.isfile(src) and os.path.isfile(p):
        same = open(src, "rb").read() == open(p, "rb").read()
        check("随包那份更新日志与 dist/更新日志.txt 一字不差",
              same, "" if same else "内容不一致 —— 重新跑一次 build.py；"
                                    "老是红就看 rescue_changelog()")


def main():
    print("=" * 62)
    print("打包结果自检")
    print("=" * 62)
    verify_app()
    verify_docs()

    bad = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 62)
    if bad:
        print("有 {} 项没过：".format(len(bad)))
        for n in bad:
            print("  - " + n)
    else:
        print("全部通过（{} 项）".format(len(_results)))
    print("=" * 62)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
