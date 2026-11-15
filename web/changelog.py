# -*- coding: utf-8 -*-
"""更新日志：那份 txt 从哪儿读。

界面（`damai_wizard`）要点开它、自检要能单独测它、打包要能单独验它，所以放在一个零依赖的
小模块里：不 import tkinter、不 import selenium，才能在「窗口都起不来」的机器上单独跑出来。

这份文件就是一份纯文本，你自己填，程序原样显示、不解析不排版，第一行习惯写版本号
（例如 `v0.3`）。按顺序找这几个位置，找到第一个就用：
  ① `app_dir()/更新日志.txt` —— exe 形态 = 交付文件夹里那份；源码形态 = `web/更新日志.txt`
  ② `data_dir()/更新日志.txt`（exe 放在只读目录时，数据目录会退到 AppData）
  ③ `resource_dir()/更新日志.txt`
  ④ 源码形态专用：仓库根 `dist/更新日志.txt`、仓库根 `更新日志.txt`

一个都找不到就返回 `None`：界面照样开窗口，只是把「该把文件放哪儿」写清楚，绝不编内容、
也绝不抛异常，打不开更新日志不是能拦住客户的理由。第 ① 条同时管两个形态，是因为
`app_dir()` 在源码形态下就是 `web/`（见 `app_paths`），不用写两遍 `if sys.frozen`。
"""

import io
import os

try:
    import app_paths
except Exception:                                          # pragma: no cover
    app_paths = None

# 只认这个名字。改名要连 build.py 的随包逻辑一起改。
FILE_NAME = "更新日志.txt"

# 读多大。更新日志是给人看的，几十 KB 已经非常长了；真要更大也不该由这个窗口扛。
MAX_CHARS = 200000

# 先 utf-8（Windows 记事本现在默认存 UTF-8），再退回 GBK：老机器上用记事本另存过的文件可能是 GBK，读不出来会变一串问号。
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")


def candidates():
    """候选路径，按优先级排列（可能包含不存在的）。

    顺序是行为：第 1 条是 exe 同级那份，改顺序 = 改了「谁说了算」。
      自检里有一条钉着它（V6.x）。
    """
    if app_paths is None:                              # pragma: no cover
        return [os.path.abspath(FILE_NAME)]

    repo = os.path.dirname(app_paths.app_dir())
    raw = [
        # 1. 程序目录 / exe 同级，交付形态就是它
        os.path.join(app_paths.app_dir(), FILE_NAME),
        # 2. 数据目录（exe 在只读目录时会退到 AppData）
        os.path.join(app_paths.data_dir(), FILE_NAME),
        # 3. 随包资源目录
        os.path.join(app_paths.resource_dir(), FILE_NAME),
        # 4./5. 源码形态：仓库根的那两份（发布方在 `dist/` 里填）
        os.path.join(repo, "dist", FILE_NAME),
        os.path.join(repo, FILE_NAME),
    ]
    out = []
    for path in raw:
        path = os.path.abspath(path)
        if path not in out:                            # 源码形态下前三条会撞在一起
            out.append(path)
    return out


def read_file(path):
    """读一个具体的文件。读不出来就返回 None，不抛异常。"""
    if not path or not os.path.isfile(path):
        return None
    for enc in _ENCODINGS:
        try:
            with io.open(path, "r", encoding=enc) as f:
                text = f.read(MAX_CHARS)
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception:
            return None
        # BOM 在 utf-8-sig 那条已经吃掉了；GBK 解出来的可能带一个孤零零的 \\ufeff
        return text.replace("\ufeff", "").strip()


def find_path():
    """第一个存在的候选路径；一个都没有就返回 None。"""
    for path in candidates():
        if os.path.isfile(path):
            return path
    return None


def read():
    """返回 `(正文, 文件路径)`。

    找不到文件时正文是 `None`（调用方自己决定怎么告诉用户）。
    """
    path = find_path()
    if path is None:
        return None, None
    return read_file(path), path


def hint():
    """没找到文件时，给用户一段「该放哪儿」的话。"""
    where = os.path.join(app_paths.app_dir(), FILE_NAME) if app_paths is not None \
        else FILE_NAME
    return ("没找到更新日志文件（{}）。\n\n"
            "把它放到下面这个位置就行（文件名必须是这四个字）：\n"
            "    {}\n\n"
            "也可以直接问给你这份程序的人要一份最新的。".format(FILE_NAME, where))


if __name__ == "__main__":                                 # pragma: no cover
    text, path = read()
    if path is None:
        print(hint())
    else:
        print("找到：{}".format(path))
        print("-" * 50)
        print(text or "(文件是空的)")
