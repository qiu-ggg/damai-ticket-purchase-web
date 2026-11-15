# -*- coding: utf-8 -*-
"""一条命令打完收工。在项目根目录跑：`.build-venv/Scripts/python packaging/build.py`

产物：`dist/大麦抢票/`（整个文件夹发出去）。

随包的 chromedriver 只是离线兜底：客户机器的 Chrome 版本多半跟打包机不一样，程序运行时
仍按版本匹配、必要时自己下载，带这份是为了客户那边网络不通时还有东西可用。带上去前会验版本，
对不上就不带（版本错的反而会挡住自动下载）。

随包的说明文档：说明书得跟着走，否则第一个问题就是「怎么用」，所以会拷进产物文件夹
（源文档还在原处，改完重跑即可）。

`更新日志.txt`：客户点界面右上角版本号旁的「?」看的就是它。源文件在 `dist/更新日志.txt`
（发布方自己填），每次打包重拷一份，见 `copy_changelog()`。内容是发布方写的，脚本一个字
都不许动（2026-10-10 脚本顺手改了它、把用户手写那版冲掉过一次）；两份不一致时以「修改时间
新的那份」为准，见 `rescue_changelog()`，打包前先跑它把交付文件夹里那份收回来。
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")

APP_NAME = "大麦抢票"

# 每份文档：源文件该放进哪个产物文件夹、叫什么名字。说明文档现在全在仓库根的
#   `doc/` 里，`web/` 只留会跑起来的代码。现在没有需要随包的说明，要加就往这个列表里塞。
DOCS = []

# 以前随过包、现在不该在产物里的东西。每次打包顺手清一遍：不然「不再随包」只对全新
#   目录有效，老目录里那两份会一直跟着发出去（打包是往旧目录上覆盖的）。
STALE_DOCS = [
    (APP_NAME, "使用说明.md"),
    (APP_NAME, "抢票能力说明.md"),
]

# 打完包要从产物里清掉的东西：都是每台机器自己生成的运行时文件，跟着发出去就变成「客户一打开
#   带着上一台的账号和密码」。`credentials.json` 尤其要清（里面是明文密码）；`logs/` 整个目录也清
#   （里面写着抢了哪场票）。
RUNTIME_JUNK = ["accounts.json", "accounts.log",
                "credentials.json", "damai_wizard.log",
                "notify_secret.txt", "notify_state.json",
                # 认不出页面时落的现场快照（含演出名 / 场次 / URL），打包形态落在 exe 同级，不在 logs/ 里。
                "order_page_dump.txt", "order_page_submit_dump.txt",
                "order_page_protocol_dump.txt",
                "date_pick_dump.txt", "session_pick_dump.txt"]
# damai/ 是运行时才生成的（向导给旧版单账号路径写一份 damai/config.json），装着
#   「观演人真名 + 目标演出链接」，是本机数据，没理由跟着包发给客户。
RUNTIME_JUNK_DIRS = ["profiles", "runs", "logs", "damai"]


def copy_docs():
    """把客户版说明拷进对应的产物文件夹，并清掉不再随包的旧说明。"""
    # 先清「以前有、现在不要」的（见 STALE_DOCS）：源文件都在仓库里，删的只是产物里的拷贝，随时能恢复。
    for folder, name in STALE_DOCS:
        p = os.path.join(DIST, folder, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                print("  已不随包，清掉旧产物里的：{} / {}".format(folder, name))
            except OSError as e:
                print("  ! 清不掉 {}：{!r}".format(p, e))

    for src, folder, name in DOCS:
        target_dir = os.path.join(DIST, folder)
        if not os.path.isdir(target_dir):
            continue
        if not os.path.isfile(src):
            print("  ! 找不到说明文档：{}".format(src))
            continue
        dst = os.path.join(target_dir, name)
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            print("  ! 拷贝失败：{!r}".format(e))
            continue
        print("  {} / {}".format(folder, name))


def _read_bytes(path):
    """读文件原始字节；读不到返回 None（用它跟「空文件」区分开）。"""
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def _backup_file(path, stamp):
    """把「马上要被覆盖」的那份拷到 `dist/_backup/`。只拷副本，不动原件。"""
    try:
        bak_dir = os.path.join(DIST, "_backup")
        if not os.path.isdir(bak_dir):
            os.makedirs(bak_dir)
        base, ext = os.path.splitext(os.path.basename(path))
        bak = os.path.join(bak_dir, "{}-{}{}".format(base, stamp, ext))
        shutil.copy2(path, bak)
        print("    旧那份已备份：{}".format(bak))
        return bak
    except Exception as e:
        print("    ! 备份失败：{!r}".format(e))
        return None


def rescue_changelog():
    """打包之前先把交付文件夹里那份更新日志收回来。

    为什么非要这步：发布方多半直接改交付文件夹里的 `dist/大麦抢票/更新日志.txt`
      （点界面「?」时窗口底部写着读它）。而 `park_existing_dist()` 紧接着把整个文件夹
      挪到 `_old/`，`copy_changelog()` 再从 `dist/更新日志.txt` 拷一份进去，刚写的
      那几行就被覆盖了（2026-10-10 真发生过）。所以打包前先比这两份，内容不一样时以
      修改时间新的为准（交付那份新就收回成源，源那份新就备份交付那份），只拷/挪不删。
    """
    src = os.path.join(DIST, "更新日志.txt")
    dst = os.path.join(DIST, APP_NAME, "更新日志.txt")
    if not os.path.isfile(dst):
        return
    a, b = _read_bytes(src), _read_bytes(dst)
    if a is not None and a == b:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        dst_newer = os.path.getmtime(dst) > os.path.getmtime(src) or a is None
    except Exception:
        dst_newer = True
    if dst_newer:
        if a is not None:
            _backup_file(src, stamp)
        try:
            shutil.copy2(dst, src)
        except Exception as e:
            print("  ! 收回失败：{!r}".format(e))
            return
        print("  交付文件夹里的更新日志比较新，已收回成 dist/更新日志.txt"
              "（这次打包会带上你写的那版）")
    else:
        _backup_file(dst, stamp)
        print("  ! 交付文件夹里的更新日志和 dist/更新日志.txt 不一致 ——"
              " 以源文件为准，交付那份已备份（想去掉这次覆盖，把备份拷回"
              " dist/更新日志.txt）")


def copy_changelog():
    """把「更新日志.txt」拷进交付文件夹，客户点版本号旁那个「?」看的就是它。

    源文件在 `dist/更新日志.txt`（发布方自己填，不属于源码、也不进源码 zip）。每次打包
      都得重拷：`park_existing_dist()` 会把上一轮的 `dist/大麦抢票/` 整个挪到 `_old/`，
      上次拷进去那份跟着走了，不重拷客户的交付目录里就会凭空消失（程序照常开窗口，只是
      显示「没找到更新日志」）。源文件不存在时不编内容，只说清「交付目录里会没有这一份」。
    """
    src = os.path.join(DIST, "更新日志.txt")
    if not os.path.isfile(src):
        # `--dist` 指到别处时 dist/ 下没有源文件，退回仓库根那份
        fallback = os.path.join(ROOT, "dist", "更新日志.txt")
        if os.path.isfile(fallback):
            src = fallback
    target_dir = os.path.join(DIST, APP_NAME)
    if not os.path.isdir(target_dir):
        print("  ! 主程序目录不存在，跳过：{}".format(target_dir))
        return False
    dst = os.path.join(target_dir, os.path.basename(src))
    if not os.path.isfile(src):
        print("  ! 找不到更新日志源文件：{}".format(src))
        print("    （客户点「?」会看到「没找到更新日志」）")
        return False
    # 交付文件夹里已有一份、内容又不一样，覆盖前先备份。正常流程走到这儿 dst 不存在
    #   （`park_existing_dist()` 早挪走了），留着是给 `--dist` 打到已有目录或被打断过兜底。
    if os.path.isfile(dst) and _read_bytes(dst) != _read_bytes(src):
        _backup_file(dst, time.strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(src, dst)
    except Exception as e:
        print("  ! 拷贝失败：{!r}".format(e))
        return False
    print("  已随包：{}".format(dst))
    return True


def clean_runtime_junk():
    """清掉产物里上一轮跑出来的运行时文件。

    打完包在本机试跑一次，`accounts.json` / `credentials.json` / `profiles/` 就会落在
    exe 旁边；直接压包发出去，客户一打开就带着别人的账号和密码，`verify.py` 也会报。
    """
    target_dir = os.path.join(DIST, APP_NAME)
    removed = []
    for name in RUNTIME_JUNK:
        p = os.path.join(target_dir, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed.append(name)
            except OSError as e:
                print("  ! 删不掉 {}：{!r}".format(p, e))
    for name in RUNTIME_JUNK_DIRS:
        p = os.path.join(target_dir, name)
        if os.path.isdir(p):
            try:
                shutil.rmtree(p)
                removed.append(name + "/")
            except OSError as e:
                print("  ! 删不掉 {}：{!r}".format(p, e))
    if removed:
        print("  已清掉：{}".format("、".join(removed)))
    else:
        print("  本来就是干净的。")


def park_existing_dist():
    """打包前把上一轮的产物改名挪开，别让 PyInstaller 去删它。

    PyInstaller 的 COLLECT 碰到同名输出目录会直接 `rmtree`，而本机这一步会被安全删除
      拦截器挡下（一次 1000+ 个文件，超过 50 的阈值），打包就在最后一步整段失败，日志
      只有一行 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`，看着像「打包坏了」，其实只是删不动
      旧目录。改名不是删除、不触发拦截。挪到 `dist/_old/<时间戳>/`，上一轮的留着能对照。
    """
    shop = os.path.join(DIST, "_old", time.strftime("%Y%m%d-%H%M%S"))
    moved = []
    for name in (APP_NAME,):
        src = os.path.join(DIST, name)
        if not os.path.isdir(src):
            continue
        try:
            os.makedirs(shop, exist_ok=True)
            shutil.move(src, os.path.join(shop, name))
            moved.append(name)
        except OSError as e:
            print("  ! 挪不动 {}：{!r}".format(src, e))
    if moved:
        print("  上一轮产物已挪到：{}".format(shop))
    return moved


def _pyinstaller(spec_name, work_name):
    spec = os.path.join(HERE, spec_name)
    cmd = [
        sys.executable, "-m", "PyInstaller", spec,
        "--noconfirm", "--clean",
        "--distpath", DIST,
        "--workpath", os.path.join(WORK, work_name),
    ]
    print("\n" + "=" * 62)
    print("打包 {}".format(work_name))
    print("=" * 62)
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode == 0


def grab_chromedriver():
    """找一份跟本机 Chrome 匹配的驱动，拷到主程序目录里。"""
    sys.path.insert(0, WEB)
    try:
        import chromedriver_locator as loc
    except Exception as e:
        print("  ! 没法加载驱动查找模块：{!r}".format(e))
        return False

    major = None
    try:
        major = loc.chrome_major_version()
    except Exception:
        pass
    print("  本机 Chrome 主版本：{}".format(major or "读不到"))
    print("  正在找一份匹配的 ChromeDriver（可能要联网下载，稍等）……")

    try:
        path = loc.resolve_chromedriver()
    except Exception as e:
        print("  ! 找驱动时出错：{!r}".format(e))
        return False

    if not path:
        print("  ! 没找到可用的 ChromeDriver。不影响打包 —— "
              "客户机器上程序会自己尝试下载。")
        return False

    target_dir = os.path.join(DIST, APP_NAME)
    if not os.path.isdir(target_dir):
        print("  ! 主程序目录不存在，跳过：{}".format(target_dir))
        return False

    dst = os.path.join(target_dir, "chromedriver.exe")
    try:
        shutil.copy2(path, dst)
    except Exception as e:
        print("  ! 拷贝失败：{!r}".format(e))
        return False
    print("  已随包：{}".format(dst))
    print("  （来自 {}）".format(path))
    return True


def main():
    global DIST
    if "--dist" in sys.argv[1:]:
        # 把产物打到别的目录去。正常打包用不到，但上一轮的产物目录可能改不了名也删不掉
        #   （进程拿它当工作目录、或杀软/索引器持着句柄），而 PyInstaller 的 COLLECT 上来
        #   就 rmtree 同名目录，删不动就卡在最后一步。这时 `--dist` 打到干净目录再搬回去即可。
        i = sys.argv.index("--dist")
        if i + 1 >= len(sys.argv):
            print("--dist 后面要跟目录路径")
            return 1
        DIST = os.path.abspath(sys.argv[i + 1])
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("这个脚本要在打包专用环境里跑：")
        print("    .build-venv/Scripts/python packaging/build.py")
        return 1

    print("项目根目录：{}".format(ROOT))
    print("打包解释器：{}".format(sys.executable))
    print("产物目录：{}".format(DIST))

    # 必须在 park_existing_dist() 之前：先确认交付文件夹里那份更新日志有没有更新，别让重打包把它带走。
    rescue_changelog()

    park_existing_dist()

    ok_app = _pyinstaller("damai_app.spec", "damai")

    if ok_app:
        print("\n--- 随包放一份 ChromeDriver ---")
        grab_chromedriver()

    print("\n" + "=" * 62)
    print("随包文件")
    print("=" * 62)
    if ok_app:
        print("\n[主程序]")
        clean_runtime_junk()
        # 更新日志要在清完之后拷：它算交付内容，不是运行时数据。
        print("\n[更新日志]")
        copy_changelog()
    print("\n[说明文档]")
    copy_docs()

    print("\n" + "=" * 62)
    print("结果")
    print("=" * 62)
    for name, ok in ((APP_NAME, ok_app),):
        d = os.path.join(DIST, name)
        exe = os.path.join(d, name + ".exe")
        if ok and os.path.isfile(exe):
            size = _dir_size(d)
            print("  [OK] {}  ({:.1f} MB)".format(d, size / 1024.0 / 1024.0))
        else:
            print("  [失败] {}".format(d))

    print("""
------------------------------------------------------------------
要发给客户的是 **整个文件夹**（不是单拎 exe 出来）：
      {}

打完先跑一遍自检，确认能起来再发：
      .build-venv/Scripts/python packaging/verify.py
------------------------------------------------------------------""".format(os.path.join(DIST, APP_NAME)))
    return 0 if ok_app else 1


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


if __name__ == "__main__":
    sys.exit(main())
