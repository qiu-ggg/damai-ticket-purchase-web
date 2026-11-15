# -*- coding: utf-8 -*-
"""更新日志这份 txt 的守护检查。

管两件事：① 发布方写的内容不许被程序改写。`web/changelog.py` 只读不写，向导点开那个
「?」只显示、不动文件；这里读一遍再比字节，钉死它。② 打包不许把发布方写的那版冲掉。
发布方是直接改交付文件夹里那份（界面窗口底部写的路径就是它），而每次打包都会把旧文件夹
挪走、再从 `dist/更新日志.txt` 拷一份新的进去，`packaging/build.py` 的
`rescue_changelog()` 就是为此：内容不一样时以「改得晚的那份」为准。

2026-10-10 真出过一次：脚本顺手改了这份 txt，把用户手写的那版覆盖了。所以这些用例的
判据全部用「文件里到底是什么字」，不用「函数被调用过」。

跑法（项目根目录）：
    .build-venv/Scripts/python tools/check_changelog.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import changelog                                        # noqa: E402
import build                                            # noqa: E402

PASS = []
FAIL = []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print("  {} {}".format("OK  " if ok else "FAIL", name)
          + ("" if ok or not extra else "   << " + str(extra)))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def setup(tmp):
    """在临时目录里造一个假的 dist/：源文件 + 交付文件夹各一份。"""
    build.DIST = tmp
    order = os.path.join(tmp, build.APP_NAME)
    if os.path.isdir(order):
        shutil.rmtree(order)
    os.makedirs(order)
    with open(os.path.join(tmp, "更新日志.txt"), "w", encoding="utf-8") as f:
        f.write("源文件：发布方还没改过\n")
    with open(os.path.join(order, "更新日志.txt"), "w", encoding="utf-8") as f:
        f.write("交付文件夹：这一份是老的\n")
    return (os.path.join(tmp, "更新日志.txt"), os.path.join(order, "更新日志.txt"))


print("=" * 62)
print("更新日志守护检查")
print("=" * 62)

print("\n[0] 仓库里份文件的存在与读取")
real_src = os.path.join(ROOT, "dist", "更新日志.txt")
real_dst = os.path.join(ROOT, "dist", "大麦抢票", "更新日志.txt")
check("0.1 dist/更新日志.txt 在（发布方要填的那份）", os.path.isfile(real_src), real_src)
if os.path.isfile(real_src):
    before = open(real_src, "rb").read()
    text, path = changelog.read()
    check("0.2 读得出来（不是 None）", bool(text), path)
    check("0.3 读到的就是文件里那些字",
          bool(text) and text.strip() == before.decode("utf-8-sig").strip(),
          repr((text or "")[:40]))
    after = open(real_src, "rb").read()
    check("0.4 读一遍不改动这个文件（只读，绝不写）", before == after)

print("\n[1] 发布方改了交付文件夹里那份（这次事故的场景）")
tmp = os.path.join(tempfile.gettempdir(), "_check_changelog")
build.DIST = tmp
if os.path.isdir(tmp):
    shutil.rmtree(tmp)
os.makedirs(tmp)
# 写盘之前先断言：目标确实在临时目录里
assert os.path.abspath(build.DIST).startswith(
    os.path.abspath(tempfile.gettempdir())), "DIST 不在临时目录里，停"

src, dst = setup(tmp)
with open(dst, "w", encoding="utf-8") as f:
    f.write("我写的：启动页新增日历日期填写框\n")
now = time.time()
os.utime(src, (now - 600, now - 600))          # 源文件：十分钟前
os.utime(dst, (now, now))                      # 交付那份：刚刚改的
build.rescue_changelog()
check("1.1 源文件被收回成「我写的那版」（下次打包带上它）",
      read(src) == "我写的：启动页新增日历日期填写框\n", read(src))
check("1.2 交付文件夹那份一个字没动",
      read(dst) == "我写的：启动页新增日历日期填写框\n", read(dst))
check("1.3 旧源文件进了 dist/_backup/ 而不是被删掉",
      os.path.isdir(os.path.join(tmp, "_backup"))
      and len(os.listdir(os.path.join(tmp, "_backup"))) == 1,
      os.listdir(os.path.join(tmp, "_backup")) if os.path.isdir(
          os.path.join(tmp, "_backup")) else "没这个目录")
bak = os.path.join(tmp, "_backup")
if os.path.isdir(bak) and os.listdir(bak):
    b = os.listdir(bak)[0]
    check("1.4 备份里就是被替掉的那份内容",
          read(os.path.join(bak, b)) == "源文件：发布方还没改过\n")

print("\n[2] 源文件比较新（交付文件夹那份是过期的）")
src, dst = setup(tmp)
with open(src, "w", encoding="utf-8") as f:
    f.write("源：最新版\n")
now = time.time()
os.utime(dst, (now - 600, now - 600))
os.utime(src, (now, now))
build.rescue_changelog()
check("2.1 源文件没被改", read(src) == "源：最新版\n", read(src))
_bak_dir = os.path.join(tmp, "_backup")
baks = os.listdir(_bak_dir) if os.path.isdir(_bak_dir) else []
check("2.2 过期的那份进的备份，不是被删", len(baks) >= 1,
      baks if baks else "连 _backup 目录都没有")
check("2.3 备份内容是过期那份",
      any(read(os.path.join(_bak_dir, n)) == "交付文件夹：这一份是老的\n"
          for n in baks), baks)

print("\n[3] 边界：两份一致、交付那份还没有")
src, dst = setup(tmp)
with open(dst, "w", encoding="utf-8") as f:
    f.write("源文件：发布方还没改过\n")
shutil.rmtree(os.path.join(tmp, "_backup"), ignore_errors=True)
before = sorted(os.listdir(tmp))
build.rescue_changelog()
check("3.1 一样就什么都不做", sorted(os.listdir(tmp)) == before, sorted(os.listdir(tmp)))
src, dst = setup(tmp)
os.remove(dst)
shutil.rmtree(os.path.join(tmp, "_backup"), ignore_errors=True)
before = sorted(os.listdir(tmp))
build.rescue_changelog()
check("3.2 交付那份不存在时不炸、不写东西",
      sorted(os.listdir(tmp)) == before, sorted(os.listdir(tmp)))
check("3.3 源文件原样", read(src) == "源文件：发布方还没改过\n")

print("\n[4] 脚本不许自己编内容，只许整份拷")

def _body(src_text, fn_name):
    """抠出一个顶层函数的函数体（到下一个顶格 def 为止）。"""
    i = src_text.find("def {}(".format(fn_name))
    if i < 0:
        return None
    j = src_text.find("\ndef ", i)
    return src_text[i:] if j < 0 else src_text[i:j]


build_txt = open(os.path.join(ROOT, "packaging", "build.py"), encoding="utf-8").read()
for fn in ("copy_changelog", "rescue_changelog"):
    body = _body(build_txt, fn)
    check("4.x build.py 有 {}（拷/收回都在这儿）".format(fn), body is not None)
    if body:
        check("4.x {} 里没有 .write( —— 它只整份拷，不生成内容".format(fn),
              ".write(" not in body, body[:80])

cl_txt = open(os.path.join(ROOT, "web", "changelog.py"), encoding="utf-8").read()
check("4.x web/changelog.py 只读不写（没有任何写模式 open）",
      '"w"' not in cl_txt and "'w'" not in cl_txt
      and '"a"' not in cl_txt and "'a'" not in cl_txt,
      "出现了 'w'/'a' 模式的 open")

shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 62)
print("结果：{} 项断言，通过 {}，失败 {}".format(len(PASS) + len(FAIL), len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for n in FAIL:
        print("   -", n)
print("=" * 62)
sys.exit(1 if FAIL else 0)
