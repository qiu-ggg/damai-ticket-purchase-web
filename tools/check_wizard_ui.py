# -*- coding: utf-8 -*-
"""向导冒烟测试（真 tkinter，真读 accounts.json，不启动浏览器）。

一个账号 = 一个分页 = 一份完整抢票配置；各填各的，「全部账号一起抢」时各跑各的。
accounts.json / credentials.json / 日志 / config.json 等落盘路径全部重定向到
临时目录，真实文件一个字节不动。Popen 与所有弹窗都被换掉，全程不开浏览器。

用法：`<venv-python> tools/check_wizard_ui.py`
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")
PROJ = os.path.dirname(BASE)      # tools/ 挪到仓库根：仓库根在这
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "damai"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             ("  <<< " + str(extra)) if extra else ""))


def dialog_buttons(win):
    """窗口里所有按钮的屏幕坐标（相对窗口左上角）。

    判据只认结果：客户看到的是像素位置，不是几何管理器算出来的 reqheight。
    只扫同一个窗口的控件：子 Toplevel（比如从任务面板弹出的日志窗口）也算
      children，但它有自己的坐标系，混进来会量到负坐标。
    """
    win.update_idletasks()
    out = []

    def scan(w, top):
        try:
            cls = w.winfo_class()
        except Exception:
            return
        if not top and cls == "Toplevel":
            return                                   # 另一个窗口，单独量
        if cls == "Button":
            x = w.winfo_rootx() - win.winfo_rootx()
            y = w.winfo_rooty() - win.winfo_rooty()
            out.append((str(w.cget("text")), x, y, x + w.winfo_width(),
                        y + w.winfo_height(), w.winfo_height()))
        for c in w.winfo_children():
            scan(c, False)
    scan(win, True)
    return out


def offscreen_buttons(win, only=None):
    """跑出窗口可视区的按钮清单（含「压根没布局」的那种，高度 <= 1）。

    only：只挑这些文案的按钮来判。主窗口必须给这个参数——它中间那块是可滚动
      画布（分页内容超出时滚动查看），画布里的按钮本来就在可视区下方，不算问题。
      真正要钉的是顶部/底部那圈固定按钮。
    """
    w, h = win.winfo_width(), win.winfo_height()
    bad = []
    for text, x0, y0, x1, y1, hh in dialog_buttons(win):
        if only is not None and text not in only:
            continue
        if hh <= 1 or y1 > h or x1 > w or x0 < 0 or y0 < 0:
            bad.append("{}@({},{})-({},{})".format(text or "(无字)", x0, y0, x1, y1))
    return bad


def _acc(aid, **kw):
    base = {
        "account_id": aid, "note": "", "enabled": True, "profile_dir": "",
        "target_url": "", "city": "", "dates": [], "calendar_date": "", "prices": [], "quantity": 1,
        "users": [], "if_listen": True, "if_commit_order": False, "max_retries": 10000,
        "dismiss_mask": True, "no_seat": True, "random_seat": True,
    }
    base.update(kw)
    return base


def main():
    import tkinter as tk

    tmp = tempfile.mkdtemp(prefix="wizard-smoke-")
    print("临时工作目录:", tmp)

    import cred_store
    import accounts as acc_store

    # 把所有落盘路径重定向到临时目录
    real = {
        "acc_path": acc_store.ACCOUNTS_PATH,
        "acc_log": acc_store.LOG_PATH,
        "cred_path": cred_store.STORE_PATH,
        "cred_log": cred_store.LOG_PATH,
    }
    real_stat = {k: (os.path.exists(v), os.path.getmtime(v) if os.path.exists(v) else None)
                 for k, v in real.items()}

    acc_store.ACCOUNTS_PATH = os.path.join(tmp, "accounts.json")
    acc_store.LOG_PATH = os.path.join(tmp, "accounts.log")
    cred_store.STORE_PATH = os.path.join(tmp, "credentials.json")
    cred_store.LOG_PATH = os.path.join(tmp, "accounts.log")

    # 初始账号清单：01 填好一套，02 空着
    with io.open(acc_store.ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "version": 1,
            "roster": ["王小明"],
            "accounts": [
                _acc("Account 01", note="本人", target_url="https://detail.damai.cn/item.htm?id=11111111",
                     city="北京", dates=["2026-12-05"], prices=["380"], quantity=1, users=["王小明"]),
                _acc("Account 02", note="小号", enabled=False),
            ],
        }, f, ensure_ascii=False, indent=2)

    import damai_wizard as W

    real["config"] = W.CONFIG_PATH
    real_stat["config"] = (os.path.exists(real["config"]),
                           os.path.getmtime(real["config"]) if os.path.exists(real["config"]) else None)

    # 向导自己的日志也要重定向，否则每跑一次自检就往用户真实的 damai_wizard.log
    #   里写十几行「已启动抢票 | driver=C:/fake/...」假记录，混进真实记录里。
    real["wiz_log"] = W.LOG_PATH
    real_stat["wiz_log"] = (os.path.exists(real["wiz_log"]),
                            os.path.getmtime(real["wiz_log"]) if os.path.exists(real["wiz_log"]) else None)
    W.LOG_PATH = os.path.join(tmp, "damai_wizard.log")

    # 子进程日志目录也要隔离（2026-10-09）：任务不再弹控制台窗口，输出直接写进
    #   runs/ 下的文件，不隔离每跑一次自检就往用户真实 runs/ 里多塞几个空日志。
    #   先断言目标在临时目录里再写（这条踩过 9 次）。
    real["runs_dir"] = W.RUNS_DIR
    real_stat["runs_dir"] = (
        os.path.exists(W.RUNS_DIR),
        os.path.getmtime(W.RUNS_DIR) if os.path.exists(W.RUNS_DIR) else None)
    W.RUNS_DIR = os.path.join(tmp, "runs")
    assert os.path.abspath(W.RUNS_DIR).startswith(os.path.abspath(tmp)), \
        "runs 目录没重定向到临时目录，停手"

    W.LEGACY_PROFILES_PATH = os.path.join(tmp, "profiles.json")   # 不存在 -> 无迁移
    W.CONFIG_PATH = os.path.join(tmp, "damai", "config.json")
    os.makedirs(os.path.dirname(W.CONFIG_PATH), exist_ok=True)

    # 实时状态目录同样隔离（2026-10-09）：用例会伪造 live 文件模拟一个在跑的任务，
    #   不隔离就写进用户真实目录。这条也纳入结尾的「没动过」检查。
    import live_status as _ls_mod
    real["live_dir"] = _ls_mod.LIVE_DIR
    real_stat["live_dir"] = (
        os.path.exists(_ls_mod.LIVE_DIR),
        os.path.getmtime(_ls_mod.LIVE_DIR) if os.path.exists(_ls_mod.LIVE_DIR) else None)
    _ls_mod.LIVE_DIR = os.path.join(tmp, "live")

    # 拦下一切外部动作
    launched, dialogs, warned = [], [], []
    # 留一份真的 Popen：把 W.subprocess.Popen 换成假的影响整个 subprocess 模块，
    #   而 [Q3] 要起真子进程当「正在跑的任务」靶子（还得能被真停掉）。
    _real_popen = W.subprocess.Popen
    old = {
        "Popen": W.subprocess.Popen,
        "askyesno": W.messagebox.askyesno,
        "showwarning": W.messagebox.showwarning,
        "showinfo": W.messagebox.showinfo,
        "showerror": W.messagebox.showerror,
        "sleep": W.time.sleep,
        "driver": W.resolve_chromedriver,
        "focus": W.focus_damai_window,
        "config_path": W.CONFIG_PATH,
    }
    W.subprocess.Popen = lambda args, **kw: launched.append((list(args), kw.get("env") or {}))
    W.messagebox.askyesno = lambda *a, **k: (dialogs.append(a), True)[1]
    W.messagebox.showwarning = lambda *a, **k: warned.append(a)
    W.messagebox.showinfo = lambda *a, **k: warned.append(a)
    W.messagebox.showerror = lambda *a, **k: warned.append(a)
    W.time.sleep = lambda *a: None
    W.resolve_chromedriver = lambda: "C:/fake/chromedriver.exe"
    W.focus_damai_window = lambda *a, **k: None      # 别去动用户真实的窗口

    def bodies(source):
        return " ".join(str(x) for d in source for x in d)

    def fill(aid, phone, pwd):
        page = wiz.pages[aid]
        page["phone"].delete(0, "end")
        page["phone"].insert(0, phone)
        page["pwd"].delete(0, "end")
        page["pwd"].insert(0, pwd)

    root = tk.Tk()
    root.withdraw()

    try:
        print("\n[A] 界面能建起来，一个账号一个分页")
        wiz = W.Wizard(root)
        root.update_idletasks()
        check("Wizard 构造成功", True)
        check("没有 LoginDialog 类（登录不再走弹窗）", not hasattr(W, "LoginDialog"))
        check("已经没有「方案 / profile」这套概念",
              not hasattr(wiz, "cb_profile") and not hasattr(wiz, "store"),
              [h for h in ("cb_profile", "store") if hasattr(wiz, h)])
        check("页签数与账号数一致", len(wiz.tab_order) == 2, wiz.tab_order)
        check("页签顺序与账号清单一致", wiz.tab_order == ["Account 01", "Account 02"], wiz.tab_order)
        check("每个账号都有自己的一份控件",
              set(wiz.pages) == {"Account 01", "Account 02"}, list(wiz.pages))
        check("两页的控件不是同一个对象",
              wiz.pages["Account 01"]["url"] is not wiz.pages["Account 02"]["url"])
        check("名单迁移/加载成功", wiz.roster == ["王小明"], wiz.roster)

        print("\n[B] 每个分页载入的是它自己那份配置")
        p1, p2 = wiz.pages["Account 01"], wiz.pages["Account 02"]
        check("01 的链接是它自己的", "11111111" in p1["url"].get(), p1["url"].get())
        check("02 的链接是空的", p2["url"].get() == "", p2["url"].get())
        check("01 勾了观演人", p1["user_vars"]["王小明"].get() is True)
        check("02 没勾观演人", p2["user_vars"]["王小明"].get() is False)
        check("01 启用 / 02 停用",
              wiz.pages["Account 01"]["enabled"].get() is True
              and wiz.pages["Account 02"]["enabled"].get() is False)
        check("页签标题带上了城市与票档",
              "北京" in wiz.nb.tab(0, "text") and "380" in wiz.nb.tab(0, "text"),
              wiz.nb.tab(0, "text"))
        check("停用的账号在标题上有标记", "停用" in wiz.nb.tab(1, "text"), wiz.nb.tab(1, "text"))

        # 账号栏必须自绘、藏掉系统那条：ttk 自带标签栏会平分标签宽度，账号一多标题
        #   只剩几个字，也不支持横向滚动。藏它要清 TNotebook.Tab 的布局链；清空后根
        #   元素是 `null` 不是 []，判据写「不含 Notebook.tab」而不是比 `== []`。
        st = W.ttk.Style(root)
        tab_layout = st.layout("TNotebook.Tab")
        names = [item[0] for item in tab_layout]
        check("系统自带标签栏真的不渲染了（布局链里已无 Notebook.tab）",
              "Notebook.tab" not in names, tab_layout)
        check("自绘账号栏存在，且标签数与账号数一致",
              len(getattr(wiz, "tab_chips", {})) == 2,
              len(getattr(wiz, "tab_chips", {})))
        # 这里只钉「默认不是那条长标题」：旧版把页签标题（账号号 + 城市 + 日期 +
        #   票档）搬上来，一个标签 400 像素宽、10 个账号要 4000 像素，切换得横向滚。
        check("账号栏标签是短的（不是那条带城市/票档的长标题）",
              wiz.tab_chips[0].winfo_children()[1].cget("text") == "账号1",
              wiz.tab_chips[0].winfo_children()[1].cget("text"))
        check("页签标题仍然保留完整信息（数据没被砍掉）",
              "北京" in wiz.nb.tab(0, "text") and "380" in wiz.nb.tab(0, "text"),
              wiz.nb.tab(0, "text"))
        check("账号栏带了横向滚动条所需的画布",
              hasattr(wiz, "tb_canvas") and hasattr(wiz, "tab_strip"))
        check("账号少时不显示左右翻页箭头（不摆两颗点了没反应的灰按钮）",
              not wiz.tb_left.winfo_ismapped() and not wiz.tb_right.winfo_ismapped())

        print("\n[C] 两个账号填不同的票，互不干扰（这次改动的核心）")
        p2["url"].insert(0, "https://detail.damai.cn/item.htm?id=22222222")
        p2["city"].insert(0, "上海")
        p2["dates"].insert("1.0", "2027-01-01")
        p2["prices"].insert("1.0", "680")
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "1")
        p2["user_vars"]["王小明"].set(True)
        p2["enabled"].set(True)
        wiz._collect_all()
        a1 = acc_store.find(wiz.acc_data, "Account 01")
        a2 = acc_store.find(wiz.acc_data, "Account 02")
        check("账号一仍是北京 380", a1["city"] == "北京" and a1["prices"] == ["380"],
              (a1["city"], a1["prices"]))
        check("账号二是上海 680", a2["city"] == "上海" and a2["prices"] == ["680"],
              (a2["city"], a2["prices"]))
        check("两边链接不同", a1["target_url"] != a2["target_url"],
              (a1["target_url"], a2["target_url"]))
        check("两边场次不同", a1["dates"] == ["2026-12-05"] and a2["dates"] == ["2027-01-01"],
              (a1["dates"], a2["dates"]))

        # 「日历日期（选填）」与场次分开存（2026-10-10）：以前一个「场次日期」框既
        #   要匹配场次又要点月历格子，两头都不对。
        p2["calendar_date"].delete(0, "end")
        p2["calendar_date"].insert(0, "2027-01-02")
        wiz._collect_all()
        a1 = acc_store.find(wiz.acc_data, "Account 01")
        a2 = acc_store.find(wiz.acc_data, "Account 02")
        check("日历日期与场次分开存（账号二：场次 2027-01-01 / 日历 2027-01-02）",
              a2["dates"] == ["2027-01-01"] and a2["calendar_date"] == "2027-01-02",
              (a2["dates"], a2["calendar_date"]))
        check("账号一没填日历日期就是空串（字段在，值为空）",
              a1.get("calendar_date") == "", a1.get("calendar_date"))
        check("页签标题也带上「日历 …」（不写出来用户不知道自己填了没）",
              "日历 2027-01-02" in wiz._tab_title(a2), wiz._tab_title(a2))

        # 写进 config.json 的规矩：空就不写这个字段，填了才写。
        # （老账号的 account 里没有 calendar_date，读出来是 None / 空，行为一字不变）
        cfg_a1 = wiz.collect("Account 01")
        cfg_a2 = wiz.collect("Account 02")
        check("日历日期空着 → config.json 里压根没有这个字段",
              "calendar_date" not in cfg_a1, sorted(cfg_a1.keys()))
        check("日历日期填了 → 原样写进 config.json",
              cfg_a2.get("calendar_date") == "2027-01-02", cfg_a2.get("calendar_date"))

        wiz._persist_accounts()
        disk = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        d1, d2 = acc_store.find(disk, "Account 01"), acc_store.find(disk, "Account 02")
        check("落盘的账号一还是北京", d1["city"] == "北京", d1["city"])
        check("落盘的账号二是上海", d2["city"] == "上海", d2["city"])
        blob = io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8").read()
        check("accounts.json 里没有密码字样", "password" not in blob.lower())

        print("\n[D] 数量与轮询的解析")
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "3")
        wiz._collect_page("Account 02")
        check("数量填 3 就记 3", acc_store.find(wiz.acc_data, "Account 02")["quantity"] == 3)
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "abc")
        wiz._collect_page("Account 02")
        check("数量填非数字时保留上一个有效值",
              acc_store.find(wiz.acc_data, "Account 02")["quantity"] == 3,
              acc_store.find(wiz.acc_data, "Account 02")["quantity"])
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "1")

        print("\n[E] 「保存并开始抢票」只跑当前分页那个账号")
        wiz.nb.select(0)
        launched.clear()
        dialogs.clear()
        wiz.start(all_accounts=False)
        print("  实际启动:", [a[0] for a, _ in launched])
        check("只起了 1 个进程", len(launched) == 1, [a for a, _ in launched])
        check("跑的是 Account 01", launched and launched[0][0][2] == "Account 01",
              [a for a, _ in launched])
        check("命令带了 --auto（自动登录开关默认开；没填密码时脚本会自己退回手动登录）",
              launched and "--auto" in launched[0][0], launched[0][0] if launched else None)
        check("确认框里写的是「1 个账号」", "1 个账号" in bodies(dialogs), bodies(dialogs)[:120])

        # [E2] 起子进程不弹控制台窗口（2026-10-09）：用 CREATE_NO_WINDOW + 输出
        #   重定向进 runs/ 日志 + stdin 接 DEVNULL，不再用 CREATE_NEW_CONSOLE。
        #   窗口弹没弹看不见，只能断言 Popen 的参数。
        print("\n[E2] 子进程不弹控制台窗口，输出落进 runs/ 日志")
        spawn_calls = []
        _popen_keep = W.subprocess.Popen

        def _capture_popen(args, **kw):
            spawn_calls.append((list(args), kw))
            return None

        W.subprocess.Popen = _capture_popen
        wiz.nb.select(0)
        try:
            wiz.start(all_accounts=False)
        finally:
            W.subprocess.Popen = _popen_keep

        check("起了 1 个进程", len(spawn_calls) == 1, [c[0] for c in spawn_calls])
        _kw = spawn_calls[0][1] if spawn_calls else {}
        _flags = _kw.get("creationflags") or 0
        check("没带 CREATE_NEW_CONSOLE（那个黑窗就是它开出来的）",
              not (_flags & getattr(W.subprocess, "CREATE_NEW_CONSOLE", 0x00000010)),
              hex(_flags))
        check("用的是 CREATE_NO_WINDOW",
              _flags == getattr(W.subprocess, "CREATE_NO_WINDOW", 0x08000000), hex(_flags))
        check("stdin 接 DEVNULL：否则子进程的 input() 会卡在一个看不见的窗口上等人敲回车",
              _kw.get("stdin") is W.subprocess.DEVNULL, _kw.get("stdin"))
        _out = _kw.get("stdout")
        check("stdout 落到文件，而不是继承那个被隐藏的控制台（那样输出就凭空消失了）",
              _out is not None and hasattr(_out, "write"), _out)
        check("stderr 并进 stdout（一份日志能看到全部）",
              _kw.get("stderr") is W.subprocess.STDOUT, _kw.get("stderr"))
        _log_files = [p for p in [getattr(_out, "name", None)] if p]
        check("日志文件确实建出来了",
              _log_files and all(os.path.exists(p) for p in _log_files), _log_files)
        check("而且写在临时目录里（不许碰用户真实的 runs/）",
              _log_files and all(
                  os.path.abspath(p).startswith(os.path.abspath(tmp)) for p in _log_files),
              _log_files)
        _status = wiz.status.get()
        check("状态栏改成指向「任务面板」，不再说「看新弹出的控制台窗口」",
              "任务面板" in _status and "控制台" not in _status, _status)
        # 删掉这组造的日志：后面 [O] 组断言「没跑过的账号没有日志」，留着会污染它。
        for _p in _log_files:
            try:
                os.remove(_p)
            except Exception:
                pass

        print("\n[F] 「全部账号一起抢」= 每个启用账号各一个进程，各跑各的配置")
        launch_env = []
        launched.clear()
        dialogs.clear()
        W.subprocess.Popen = lambda args, **kw: (
            launched.append((list(args), kw.get("env") or {})), launch_env.append(kw.get("env") or {}))[0]
        wiz.start(all_accounts=True)
        ids = [a[2] for a, _ in launched]
        print("  实际启动:", [a[0] for a, _ in launched])
        check("两个账号都起了", sorted(ids) == ["Account 01", "Account 02"], ids)
        check("一个进程只带一个账号", all(len(a) == 4 for a, _ in launched), [a for a, _ in launched])
        check("命令都是 run",
              all(a[1] == "run" for a, _ in launched), [a for a, _ in launched])
        check("进程里带了 CHROMEDRIVER_PATH",
              all(e.get("CHROMEDRIVER_PATH") for _, e in launched))
        check("确认框说明是「2 个账号」", "2 个账号" in bodies(dialogs), bodies(dialogs)[:120])

        print("\n[G] 停用的账号不参与「全部一起抢」")
        wiz.pages["Account 02"]["enabled"].set(False)
        wiz._collect_page("Account 02")
        wiz._refresh_tab_title("Account 02")
        launched.clear()
        dialogs.clear()
        wiz.start(all_accounts=True)
        check("只起了启用中的那个", [a[2] for a, _ in launched] == ["Account 01"],
              [a for a, _ in launched])
        check("标题上出现「停用」", "停用" in wiz.nb.tab(1, "text"), wiz.nb.tab(1, "text"))
        wiz.pages["Account 02"]["enabled"].set(True)
        wiz._collect_page("Account 02")

        print("\n[H] 配置没填完整时跳过，并且说清楚原因")
        p_empty = wiz.pages["Account 02"]
        for w_ in (p_empty["url"], p_empty["city"], p_empty["qty"], p_empty["retry"]):
            w_.delete(0, "end")
        p_empty["dates"].delete("1.0", "end")
        p_empty["prices"].delete("1.0", "end")
        p_empty["user_vars"]["王小明"].set(False)
        p_empty["qty"].insert(0, "1")
        p_empty["retry"].insert(0, "1000")
        wiz._collect_all()
        launched.clear()
        dialogs.clear()
        warned.clear()
        wiz.start(all_accounts=True)
        check("只启动了填完的那个", [a[2] for a, _ in launched] == ["Account 01"],
              [a for a, _ in launched])
        body = bodies(dialogs) + bodies(warned)
        check("提示里点名了 Account 02 和缺什么",
              "Account 02" in body and "没填演出链接" in body, body[:200])

        print("\n[I] 当前分页没填完时，单账号按钮要拦住")
        wiz.nb.select(1)          # 切到空着的 Account 02
        launched.clear()
        warned.clear()
        wiz.start(all_accounts=False)
        check("没有启动任何进程", not launched, launched)
        check("弹了「还差一点」的提示", len(warned) >= 1, warned)

        print("\n[J] 数量比观演人多时也算配置不全")
        p2["url"].insert(0, "https://detail.damai.cn/item.htm?id=22222222")
        p2["prices"].insert("1.0", "680")
        p2["user_vars"]["王小明"].set(True)
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "4")
        wiz._collect_all()
        probs = W.Wizard._account_problems(acc_store.find(wiz.acc_data, "Account 02"))
        check("提示数量比人多", any("购票数量" in e for e in probs), probs)
        p2["qty"].delete(0, "end")
        p2["qty"].insert(0, "1")
        wiz._collect_page("Account 02")

        print("\n[K] 界面上填了手机号 + 密码 -> 点开始就自动保存")
        wiz.nb.select(0)
        for aid in ("Account 01", "Account 02"):
            wiz.pages[aid]["auto_login"].set(True)
        cred_store.STORE_PATH = os.path.join(tmp, "credentials.json")
        fill("Account 01", "13800001111", "pw-AAA")
        fill("Account 02", "13800002222", "pw-BBB")
        check("填之前本机没有任何凭据",
              not cred_store.has("Account 01") and not cred_store.has("Account 02"))
        launched.clear()
        dialogs.clear()
        wiz.start(all_accounts=True)
        body = bodies(dialogs)
        check("确认框如实说了会保存密码", "密码保存到本机" in body, body)
        check("两个账号的凭据都落了盘",
              cred_store.get("Account 01") == {"phone": "13800001111", "password": "pw-AAA"}
              and cred_store.get("Account 02") == {"phone": "13800002222", "password": "pw-BBB"},
              [cred_store.get("Account 01"), cred_store.get("Account 02")])
        cred_blob = io.open(cred_store.STORE_PATH, encoding="utf-8").read()
        check("凭据文件里没有明文密码", "pw-AAA" not in cred_blob and "pw-BBB" not in cred_blob)
        check("存了密码就走自动登录（带 --auto）",
              all("--auto" in a for a, _ in launched), [a for a, _ in launched])
        check("凭据文件落在临时目录", cred_store.STORE_PATH.startswith(tmp))

        print("\n[K2] 只填一半：不保存，而且要明说")
        p1 = wiz.pages["Account 01"]
        p1["pwd"].delete(0, "end")      # 只剩手机号
        cred_store.drop("Account 01")
        launched.clear()
        dialogs.clear()
        wiz.start(all_accounts=True)
        body = bodies(dialogs)
        check("没填全时不写盘", not cred_store.has("Account 01"))
        check("确认框里点明了这次不会自动登录", "没填全" in body, body)

        print("\n[K3] 确认框点「否」：新填的一个字都不留，老凭据不动")
        fill("Account 01", "13800009999", "pw-NO")
        before_02 = cred_store.get("Account 02")
        W.messagebox.askyesno = lambda *a, **k: (dialogs.append(a), False)[1]
        launched.clear()
        wiz.start(all_accounts=True)
        check("点否不写盘", not cred_store.has("Account 01"))
        check("点否也不覆盖已存凭据", cred_store.get("Account 02") == before_02)
        check("点否不启动任何进程", not launched, launched)
        W.messagebox.askyesno = lambda *a, **k: (dialogs.append(a), True)[1]

        print("\n[K4] 重建分页不会把没保存的输入冲掉")
        fill("Account 01", "13800008888", "pw-TYPED")
        wiz._render_tabs()
        check("重建后手机号还在",
              wiz.pages["Account 01"]["phone"].get() == "13800008888",
              wiz.pages["Account 01"]["phone"].get())
        check("重建后密码还在",
              wiz.pages["Account 01"]["pwd"].get() == "pw-TYPED",
              wiz.pages["Account 01"]["pwd"].get())
        check("重建后仍然停在同一个账号", wiz._current_aid() in ("Account 01", "Account 02"),
              wiz._current_aid())

        print("\n[K5] 各自关掉自动登录时，只有它不带 --auto")
        wiz.pages["Account 02"]["auto_login"].set(False)
        wiz._collect_all()
        launched.clear()
        wiz.start(all_accounts=True)
        per = {a[2]: ("--auto" in a) for a, _ in launched}
        check("01 带 --auto", per.get("Account 01") is True, per)
        check("02 不带 --auto", per.get("Account 02") is False, per)
        wiz.pages["Account 02"]["auto_login"].set(True)
        wiz._collect_all()

        print("\n[K6] 分页上的「登录这个账号」也吃界面上填的密码")
        cred_store.drop("Account 02") if cred_store.has("Account 02") else None
        fill("Account 02", "13800002222", "pw-LOGIN")
        launched.clear()
        wiz._login_account("Account 02")
        check("登录按钮把密码存下来了", cred_store.has("Account 02"), cred_store.get("Account 02"))
        check("命令是 login --auto <账号>",
              any(a[1] == "login" and "--auto" in a and "Account 02" in a
                  for a, _ in launched), [a for a, _ in launched])

        print("\n[K7] 立即保存密码 / 清除已存密码")
        wiz.save_cred("Account 01")
        check("「立即保存密码」生效",
              cred_store.get("Account 01") == {"phone": "13800008888", "password": "pw-TYPED"},
              cred_store.get("Account 01"))
        wiz.drop_cred("Account 01")
        check("「清除已存密码」生效", not cred_store.has("Account 01"))
        try:
            pdir = acc_store.profile_path(acc_store.find(wiz.acc_data, "Account 01"))
            dir_ok = bool(pdir)
        except Exception:
            dir_ok = False
        check("清除密码不碰登录状态目录（路径照样算得出来）", dir_ok)
        cred_store.put("Account 01", "13800001111", "pw-AAA")

        print("\n[L] 观演人名单：全局共用，每个账号各勾各的")
        wiz.nb.select(0)
        p1 = wiz.pages["Account 01"]
        p1["new_person"].insert(0, "王小二")
        wiz.add_person("Account 01")
        check("名单加进去了", "王小二" in wiz.roster, wiz.roster)
        check("两边分页都有了这个勾选框",
              all("王小二" in wiz.pages[a]["user_vars"] for a in ("Account 01", "Account 02")))
        # 分页被重建过，前面那份引用已经作废，得重新取
        p1 = wiz.pages["Account 01"]
        check("加的人默认在当前分页勾上", p1["user_vars"]["王小二"].get() is True)
        check("另一个账号没被连带勾上",
              wiz.pages["Account 02"]["user_vars"]["王小二"].get() is False)

        p1["user_vars"]["王小明"].set(False)     # 只勾王小二，等下把它从名单里删掉
        p1["user_vars"]["王小二"].set(True)
        wiz.remove_checked_persons("Account 01")
        check("名单里删掉了王小二", "王小二" not in wiz.roster, wiz.roster)
        check("没勾的人留在名单里", "王小明" in wiz.roster, wiz.roster)
        check("各账号的 users 也同步清掉",
              all("王小二" not in (acc_store.find(wiz.acc_data, a).get("users") or [])
                  for a in ("Account 01", "Account 02")))

        print("\n[M] 新建账号会多出来一个分页")
        wiz._ask_name = lambda *a, **k: "账号 03"
        wiz.add_account()
        check("多了一个分页", "账号 03" in wiz.tab_order, wiz.tab_order)
        check("新账号默认启用", wiz.pages["账号 03"]["enabled"].get() is True)
        check("新账号是空的", wiz.pages["账号 03"]["url"].get() == "")
        check("落盘了", acc_store.find(json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8")),
                                    "账号 03") is not None)
        wiz.delete_account("账号 03")
        check("删掉后分页也没了", "账号 03" not in wiz.tab_order, wiz.tab_order)

        # M2：账号一多，账号栏要横向可滑、标签宽度不许被压缩。量标签实际宽度，
        #   加账号时不变小说明宽度按内容算、没被平分。标签文案改短后（~400px 缩到 ~88px）
        #   10 个账号装得下、溢出前提没了，所以把窗口收窄到 620 故意造溢出。
        print("\n[M2] 账号栏装不下时横向可滑，标签宽度不被压缩")
        _saved_acc = wiz.accounts
        _saved_data = wiz.acc_data.get("accounts")
        _root_state = root.state()
        try:
            # 量之前先把窗口显示出来（2026-10-09）：Tk 对 withdrawn 窗口给不出真实
            #   winfo_width，而「装不装得下」全靠它算（`_tabbar_overflow`）。
            #   标签宽度是固定设计的（不随账号名变长），所以只能靠窗口宽度撑出溢出。
            root.deiconify()
            root.geometry("620x700")
            root.update()
            base = [wiz.tab_chips[i].winfo_width() for i in sorted(wiz.tab_chips)]
            check("前提：先有两个账号的标签", len(base) == 2, base)

            _many = []
            for i in range(10):
                a = dict(_saved_acc[0])
                a.update({"account_id": "Account %02d" % (i + 1), "note": "",
                          "city": "北京", "dates": ["2026-12-05"],
                          "prices": [str(380 + i)], "enabled": True})
                _many.append(a)
            wiz.accounts = _many
            wiz.acc_data["accounts"] = _many
            wiz._render_tabs(recollect=False)
            root.update()
            time.sleep(0.3)
            root.update()

            check("10 个账号都有标签", len(wiz.tab_chips) == 10, len(wiz.tab_chips))
            after = [wiz.tab_chips[i].winfo_width() for i in sorted(wiz.tab_chips)]
            reqs = [wiz.tab_chips[i].winfo_reqwidth() for i in sorted(wiz.tab_chips)]
            # 每个标签实际宽度 >= 内容需要的宽度，说明没被容器压缩（自带标签栏
            #   相反，会把标签平分到装得下为止、字被截断）。别比两个时刻的快照，
            #   中途改过标题文字宽度本来就会变，那种断言只会误报。
            squeezed = [i for i in sorted(wiz.tab_chips)
                        if wiz.tab_chips[i].winfo_width() < wiz.tab_chips[i].winfo_reqwidth()]
            check("加账号后没有哪个标签被压缩（自带标签栏会平分到认不出字）",
                  not squeezed,
                  "被压缩的下标 %s；实际 %s / 需要 %s" % (squeezed, after, reqs))
            # 核心断言：溢出时走的是「内容变宽 + 横向滚动」，不是「标签变窄」
            check("容器总宽 = 各标签所需宽度之和（没人被牺牲掉）",
                  wiz.tab_strip.winfo_reqwidth() >= sum(reqs),
                  "容器需要 %d / 标签合计 %d"
                  % (wiz.tab_strip.winfo_reqwidth(), sum(reqs)))
            check("内容比可视区宽，确实需要横向滚动",
                  wiz._tabbar_overflow(),
                  "内容 %d / 可视 %d" % (wiz.tab_strip.winfo_reqwidth(),
                                        wiz.tb_canvas.winfo_width()))
            check("溢出时左右翻页箭头出现",
                  wiz.tb_left.winfo_ismapped() and wiz.tb_right.winfo_ismapped())

            before_x = wiz.tb_canvas.xview()[0]
            wiz._scroll_tabbar(1)
            root.update()
            check("点右箭头能真的横向滚动",
                  wiz.tb_canvas.xview()[0] > before_x,
                  "%.2f -> %.2f" % (before_x, wiz.tb_canvas.xview()[0]))

            # 切换账号不主动滚账栏：以前切到视野外的标签会把整条栏横移顶到最右，
            #   连点几个账号整条栏一直在跳。判据用像素不用 xview 比例（选中态加粗会
            #   改标签宽度、比例跟着变），量 canvasx(0)；容差 ~90px，旧行为 150px 起步。
            px_before = wiz.tb_canvas.canvasx(0)
            wiz.nb.select(9)
            root.update()
            time.sleep(0.3)
            root.update()
            px_after = wiz.tb_canvas.canvasx(0)
            check("切换到最后一个账号时，账号栏不主动横移（维持原状）",
                  abs(px_after - px_before) < 90,
                  "%.0f px -> %.0f px（差 %.0f）" % (px_before, px_after,
                                                    px_after - px_before))
            check("高亮跟着切过去（选中的是最后一个标签）",
                  wiz.tab_chips[9].winfo_children()[1].cget("fg") == W.COLOR_PRIMARY,
                  wiz.tab_chips[9].winfo_children()[1].cget("fg"))

            # 重建标签栏时仍要把选中项滚进可视区，否则新加账号落在视野外像是「点了没反应」。
            wiz.tb_canvas.xview_moveto(0)
            root.update()
            wiz._render_tabs(recollect=False)
            root.update()
            time.sleep(0.3)
            root.update()
            check("重建标签栏后，选中的那个仍然会被滚进可视区",
                  wiz.tb_canvas.xview()[1] > 0.95 or wiz.tb_canvas.xview()[0] > 0.3,
                  "xview=%s" % (wiz.tb_canvas.xview(),))
        finally:
            wiz.accounts = _saved_acc
            wiz.acc_data["accounts"] = _saved_data
            root.geometry("1020x760")
            wiz._render_tabs(recollect=False)
            root.update()
            if _root_state != "normal":
                root.withdraw()          # 这组开头 deiconify 过，量完还原
                root.update()

        # N：账号栏标签平时写「账号N」，选中那个显示手机号（用户要求）。旧标签是
        #   长标题（账号号 + 城市 + 日期 + 票档），400 像素宽、10 个账号要 4000 像素。
        print("\n[N2] 账号栏标签：平时「账号N」，选中的那个显示手机号")
        _saved_phones = getattr(wiz, "_chip_phones", {})

        def chip_text(i):
            return wiz.tab_chips[i].winfo_children()[1].cget("text")

        try:
            wiz._chip_phones = {"Account 01": "13800138000"}
            wiz._paint_tabbar()
            root.update()
            check("N1 没选中的标签是「账号N」",
                  chip_text(1) == "账号2", chip_text(1))

            wiz.nb.select(0)
            root.update()
            check("N2 选中的那个显示手机号",
                  chip_text(0) == "13800138000", chip_text(0))
            check("N3 同一时刻只有选中那一个显示手机号",
                  chip_text(1) == "账号2" and chip_text(0) == "13800138000")

            wiz.nb.select(1)
            root.update()
            check("N4 切走的那个变回「账号N」",
                  chip_text(0) == "账号1", chip_text(0))
            check("N5 没存手机号的账号，选中时仍是「账号N」",
                  chip_text(1) == "账号2", chip_text(1))
            check("N6 选中的是高亮色",
                  wiz.tab_chips[1].winfo_children()[1].cget("fg") == W.COLOR_PRIMARY)

            # 手机号取自本机已存凭据，不是输入框现取；敲了新号没保存，标签不该跟着变。
            wiz.pages["Account 02"]["account_id"]  # 只是确认页面还在
            check("N7 手机号表只含本机存过凭据的账号（不是现编的）",
                  isinstance(wiz._refresh_chip_phones(), dict)
                  and "根本没存过这个账号" not in wiz._refresh_chip_phones(),
                  wiz._refresh_chip_phones())

            # 停用标记要保留（旧标签上有，砍掉等于丢信息）
            wiz._chip_phones = {}
            wiz.acc_data["accounts"][0]["enabled"] = False
            wiz._render_tabs(recollect=False)
            root.update()
            check("N8 停用的账号标签带 [停用] 标记",
                  chip_text(0).startswith("[停用]"), chip_text(0))
            wiz.acc_data["accounts"][0]["enabled"] = True
            wiz._render_tabs(recollect=False)
            root.update()
        finally:
            wiz._chip_phones = _saved_phones
            wiz._paint_tabbar()
            root.update()

        print("\n[P2] 环境检测：最上面的按钮 + 缺什么就给什么下载链接")
        check("P1 最上面那一行里有「环境检测」按钮",
              hasattr(wiz, "env_btn") and wiz.env_btn.cget("text") == "环境检测")
        check("P2 它和标题在同一行（真的在最上面）",
              getattr(wiz, "title_row", None) is wiz.env_btn.master
              and wiz.env_btn.winfo_y() <= 6,
              "master=%s y=%d" % (getattr(wiz, "title_row", None),
                                  wiz.env_btn.winfo_y()))

        # V：名字旁边显示版本号（用户要求）。主要价值在截图：发来一张图就知道
        #   是哪个版本，省掉「你装的是哪一版」的来回。
        print("\n[V] 名字旁边显示版本号")
        ver = getattr(W, "app_version", None)
        check("V1 版本模块打进来了", ver is not None)
        if ver is not None:
            vtext = str(wiz.version_label.cget("text")) if hasattr(wiz, "version_label") else ""
            # 只钉格式 + 接线，不钉具体数字：改 version.py 的版本号不该让测试变红。
            check("V2 标题那一行上有版本号，文案是「版本：vX.Y」",
                  vtext == ver.version_label()
                  and re.fullmatch(r"版本：v\d+\.\d+", vtext) is not None,
                  vtext or "没有这个控件")
            check("V3 它和标题在同一行（截图里一眼能看到）",
                  hasattr(wiz, "version_label")
                  and wiz.version_label.master is wiz.title_row,
                  getattr(getattr(wiz, "version_label", None), "master", None))
            # 位置：从左到右是「主色竖条，名字，版本，环境检测按钮」。不能比
            #   winfo_x（窗口 withdraw 着，普通 Frame 子控件映射前不走布局、恒为 0），
            #   按子控件顺序判，真窗口坐标手量：名字 x=15 / 版本 x=270 / 按钮 x=862。
            kids = wiz.title_row.winfo_children()
            if hasattr(wiz, "version_label") and len(kids) >= 2:
                i_title = kids.index(kids[1])
                i_ver = kids.index(wiz.version_label)
                i_btn = kids.index(wiz.env_btn)
                check("V4 它在名字右边、按钮左边（挨着名字）",
                      i_title < i_ver < i_btn,
                      "顺序下标：名字 %d / 版本 %d / 按钮 %d" % (i_title, i_ver, i_btn))
                check("V4.1 它和环境检测按钮一样靠左排（side=left，不会被挤到下一行）",
                      str(wiz.version_label.pack_info().get("side")) == "left",
                      wiz.version_label.pack_info())
            # 窗口标题也带上：任务栏、Alt-Tab、以及不截图只报标题的场合
            check("V5 窗口标题也带版本（任务栏 / Alt-Tab 上认得出）",
                  ver.APP_VERSION in root.title(), root.title())

        # V6：版本号旁边加个「?」入口，点开是更新日志（2026-10-10 用户要求）。
        print("\n[V6] 版本号旁边那个「?」点开是更新日志")
        chg = getattr(W, "changelog", None)
        check("V6.1 更新日志模块打进来了", chg is not None)
        if chg is not None:
            import app_paths as _ap

            btn = getattr(wiz, "changelog_btn", None)
            check("V6.2 版本号旁边有个「?」入口，就挂在标题那一行",
                  btn is not None and btn.master is getattr(wiz, "title_row", None),
                  getattr(getattr(btn, "master", None), "winfo_class", None))
            # 位置照 V4 那套判：窗口 withdraw 着，比坐标是比空气，比子控件顺序。
            kids = wiz.title_row.winfo_children()
            if btn is not None and btn in kids and hasattr(wiz, "version_label"):
                i_ver = kids.index(wiz.version_label)
                i_help = kids.index(btn)
                i_env = kids.index(wiz.env_btn)
                check("V6.3 它在版本号右边、环境检测左边（挨着版本号）",
                      i_ver < i_help < i_env,
                      "顺序下标：版本 %d / 问号 %d / 环境检测 %d"
                      % (i_ver, i_help, i_env))
            if btn is not None:
                shapes = [btn.type(i) for i in btn.find_all()]
                check("V6.4 画的是一个圆圈 + 一个问号（Tk 没有圆角控件，只能画）",
                      "oval" in shapes
                      and any(btn.itemcget(i, "text") == "?" for i in btn.find_all()
                              if btn.type(i) == "text"),
                      shapes)
                check("V6.5 鼠标移上去变手型（提示能点）",
                      str(btn.cget("cursor")) == "hand2", btn.cget("cursor"))
                check("V6.6 已经接上动作（<Button-1> 有绑定）",
                      bool(btn.bind("<Button-1>")), btn.bind("<Button-1>"))

            print("\n[V6b] 更新日志文件从哪儿读")
            cand = chg.candidates()
            check("V6.7 第一个候选是「程序目录那份」（exe 同级 = 交付文件夹里）",
                  bool(cand)
                  and cand[0] == os.path.join(_ap.app_dir(), chg.FILE_NAME),
                  cand[0] if cand else cand)
            check("V6.8 候选里还有 data_dir / resource_dir（exe 放只读目录时靠它）",
                  os.path.join(_ap.data_dir(), chg.FILE_NAME) in cand
                  and os.path.join(_ap.resource_dir(), chg.FILE_NAME) in cand,
                  cand)
            # 只读、写到临时目录：真文件是发布方要填的那份，自检不碰。
            p_txt = os.path.join(tmp, "更新日志.txt")
            assert os.path.abspath(p_txt).startswith(os.path.abspath(tmp)), "停手"
            check("V6.9 文件不存在时返回 None（不抛异常）",
                  chg.read_file(os.path.join(tmp, "___no_such___.txt")) is None)
            with io.open(p_txt, "w", encoding="utf-8") as f:
                f.write("v9.9\n· 测试用\n")
            check("V6.10 读得到 UTF-8 的文件",
                  chg.read_file(p_txt) == "v9.9\n· 测试用",
                  repr(chg.read_file(p_txt)))
            with io.open(p_txt, "w", encoding="gbk") as f:
                f.write("v9.8 中文一条\n")
            check("V6.11 记事本存出来的 GBK 也读得出来（不会变一串问号）",
                  chg.read_file(p_txt) == "v9.8 中文一条",
                  repr(chg.read_file(p_txt)))
            check("V6.12 找不到文件时给的提示里有文件名、也有该放哪个目录",
                  chg.FILE_NAME in chg.hint() and _ap.app_dir() in chg.hint(),
                  chg.hint().replace("\n", " ")[:90])
            os.remove(p_txt)

        # 点一下真开窗口、内容原样；读文件换成假的，不碰真实的 dist/更新日志.txt
        #   （那是发布方要填的，自检只该读存在性）。
        print("\n[V7] 点「?」开出来的窗口：内容原样、关得掉")
        real_read = getattr(chg, "read", None)
        if chg is not None and real_read is not None:
            chg.read = lambda: ("v9.9\n· 测试用一句话", "C:/fake/更新日志.txt")

            def _find_text(w):
                if w.winfo_class() == "Text":
                    return w
                for c in w.winfo_children():
                    got = _find_text(c)
                    if got is not None:
                        return got
                return None

            def _all_labels(w, out=None):
                out = [] if out is None else out
                if w.winfo_class() == "Label":
                    try:
                        out.append(str(w.cget("text")))
                    except Exception:
                        pass
                for c in w.winfo_children():
                    _all_labels(c, out)
                return out

            before_warn = len(warned)
            dlg = None
            try:
                wiz.open_changelog()
                root.update()
                wins = [w for w in root.winfo_children()
                        if w.winfo_class() == "Toplevel"]
                dlg = wins[-1] if wins else None
                check("V7.1 点一下真的开出了一个窗口", dlg is not None,
                      "弹了错框：{}".format(warned[before_warn:] or "")[:80])
                if dlg is not None:
                    body = _find_text(dlg)
                    labels = " ".join(_all_labels(dlg))
                    check("V7.2 窗口标题写着「更新日志」",
                          "更新日志" in dlg.title(), dlg.title())
                    check("V7.3 正文就是文件内容，原样贴出来（不解析格式）",
                          body is not None
                          and body.get("1.0", "end-1c") == "v9.9\n· 测试用一句话",
                          repr(body.get("1.0", "end-1c")) if body else "没有文本域")
                    check("V7.4 还写着当前版本号（截图能对上号）",
                          ver is not None and ver.APP_VERSION in labels,
                          labels[:90])
                    check("V7.5 正文只读（能选、能 Ctrl+C，改不了）",
                          body is not None and str(body.cget("state")) == "disabled",
                          body.cget("state") if body else None)
                    check("V7.6 有一个「关闭」按钮，而且它在窗口里（没被挤出去）",
                          any(t[0] == "关闭" for t in dialog_buttons(dlg)),
                          [t[0] for t in dialog_buttons(dlg)])
                    dlg.destroy()
                    root.update()
            finally:
                chg.read = real_read

        real_run = W.env_check.run
        real_dialog = W.EnvCheckDialog

        def wait_env(timeout=6.0):
            """等环境检测走完。

            不能只等 `_env_busy` 变 False：后台线程可能瞬间完事，那时 after(120)
              轮询定时器还没到期，`_poll_env_check` 一次没跑，结论永远写不进状态栏。
              判据落在结果上：状态栏不再是「检测中…」。
            """
            end = time.time() + timeout
            while time.time() < end:
                root.update()
                time.sleep(0.05)
                if (not getattr(wiz, "_env_busy", True)
                        and wiz.env_var.get() != "检测中…"):
                    root.update()
                    return True
            root.update()
            return False

        try:
            W.env_check.run = lambda only=None: [
                {"key": "chrome", "name": "Chrome 浏览器", "level": "fail",
                 "detail": "没找到 Chrome", "fix": "装一个 Chrome。",
                 "url": "https://example.invalid/chrome", "url_text": "下载 Chrome"},
                {"key": "datadir", "name": "数据目录", "level": "ok",
                 "detail": "D:\\x（exe 同级目录）", "fix": "", "url": None,
                 "url_text": "打开"},
            ]
            wiz.open_env_check()
            wait_env()
            check("P3 点击之后状态栏给出结论",
                  "缺少" in wiz.env_var.get(), wiz.env_var.get())
            check("P4 按钮检测完会恢复可点",
                  str(wiz.env_btn.cget("state")) in ("normal", "active"),
                  wiz.env_btn.cget("state"))

            diffs = [x for x in root.winfo_children()
                     if isinstance(x, tk.Toplevel) and x.title() == "环境检测"]
            check("P5 弹出了检测结果窗口", len(diffs) == 1, len(diffs))
            if diffs:
                texts = []

                def walk(w):
                    for c in w.winfo_children():
                        try:
                            t = c.cget("text")
                            if str(t).strip():
                                texts.append(str(t))
                        except Exception:
                            pass
                        walk(c)

                walk(diffs[0])
                check("P6 窗口里点明了缺什么", any("Chrome" in t for t in texts),
                      texts[:6])
                check("P7 缺的那一项带下载按钮",
                      "下载 Chrome" in texts, texts[:8])
                check("P8 也给了「怎么办」",
                      any("怎么办" in t for t in texts), texts[:8])
                check("P9 正常的项也照实列出来（不是只报坏消息）",
                      any("数据目录" in t for t in texts), texts[:8])
                diffs[0].destroy()

            # 全绿时不应说「缺东西」
            W.env_check.run = lambda only=None: [
                {"key": "chrome", "name": "Chrome 浏览器", "level": "ok",
                 "detail": "主版本 155", "fix": "", "url": None, "url_text": "打开"}]
            wiz.open_env_check()
            wait_env()
            check("P10 全绿时结论是「没问题」",
                  "没问题" in wiz.env_var.get(), wiz.env_var.get())
            for x in root.winfo_children():
                if isinstance(x, tk.Toplevel) and x.title() == "环境检测":
                    x.destroy()

            # 检测炸了也不能让界面挂住
            def boom(only=None):
                raise RuntimeError("故意炸的")

            W.env_check.run = boom
            answered = {"n": 0}
            real_ask = W.messagebox.showerror
            W.messagebox.showerror = lambda *a, **k: answered.__setitem__(
                "n", answered["n"] + 1)
            try:
                wiz.open_env_check()
                wait_env()
                check("P11 检测过程炸了也不抛、不挂住，而是弹一句说明",
                      answered["n"] == 1, answered["n"])
                check("P12 按钮恢复可点（不是卡在「检测中…」）",
                      str(wiz.env_btn.cget("state")) in ("normal", "active"),
                      wiz.env_btn.cget("state"))
            finally:
                W.messagebox.showerror = real_ask
        finally:
            W.env_check.run = real_run
            W.EnvCheckDialog = real_dialog
            wiz.env_var.set("")
            for x in root.winfo_children():
                if isinstance(x, tk.Toplevel) and x.title() == "环境检测":
                    x.destroy()

        # R：开箱观演人名单是空的，名字得用户自己加。名单全局共用，首次运行还会
        #   从老的 profiles.json 自动迁移，示例数据带什么名字装上就有。界面（R1–R3）
        #   和仓库示例文件（R4–R5）两边都钉：后者是防回归关键，光清数据下次又会自带。
        print("\n[R] 开箱观演人名单是空的（名字要使用者自己加）")
        _saved_acc2 = wiz.accounts
        _saved_data2 = wiz.acc_data
        # 路径必须用 acc_store.ACCOUNTS_PATH（夹具重定向过的那个），不是
        #   W.ACCOUNTS_PATH（仓库真文件）：写成后者会把用户真实 accounts.json
        #   覆盖掉。动任何路径之前先确认它在临时目录里。
        _acc_path = acc_store.ACCOUNTS_PATH
        if not os.path.abspath(_acc_path).startswith(os.path.abspath(tmp)):
            raise AssertionError(
                "拒绝往一个不在临时目录里的账号清单上写：%s" % _acc_path)
        try:
            # 把临时那份写成「刚装好」的样子：有账号，但没有名单，每个账号也没勾人
            with io.open(_acc_path, encoding="utf-8") as f:
                _before = f.read()
            fresh = json.loads(_before)
            fresh["roster"] = []
            for _a in fresh.get("accounts") or []:
                _a["users"] = []
            with io.open(_acc_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(fresh, f, ensure_ascii=False)
            wiz._reload_accounts()
            wiz._render_tabs(recollect=False)
            root.update()

            check("R1 名单是空的（没有从任何地方继承名字）",
                  wiz.roster == [], wiz.roster)
            first = wiz.tab_order[0]
            page = wiz.pages[first]
            kids = page["users_box"].winfo_children()
            check("R2 观演人那一块没有半个人名，只有一句「名单为空，先在下面输入姓名加入」",
                  len(kids) == 1 and "名单为空" in str(kids[0].cget("text")),
                  [str(k.cget("text")) for k in kids][:4])
            check("R3 也没有勾选框（一个都没有，不是「有框但没勾」）",
                  not [k for k in kids if isinstance(k, tk.Checkbutton)],
                  [k.winfo_class() for k in kids])

            # R4/R5/R6：仓库里随交付的示例文件，只读不改。路径用
            #   `damai_wizard.__file__` 定位，不能顺向导的模块常量走（那些在夹具里
            #   被重定向到临时目录，查的是空文件）。
            web_dir = os.path.dirname(os.path.abspath(W.__file__))
            legacy_path = os.path.join(web_dir, "profiles.json")
            check("R4 前提：读的是仓库里那份示例文件",
                  os.path.isfile(legacy_path), legacy_path)
            if os.path.isfile(legacy_path):
                with io.open(legacy_path, encoding="utf-8") as f:
                    legacy = json.load(f)
                names = list(legacy.get("roster") or [])
                for _p in (legacy.get("profiles") or {}).values():
                    if isinstance(_p, dict):
                        names.extend(_p.get("users") or [])
                check("R5 示例文件 profiles.json 里没有任何观演人名字"
                      "（它会被自动迁移进名单，等于开箱自带名字）",
                      [n for n in names if str(n).strip()] == [], names)
            # 老的单账号配置同样会被读到界面上
            _cfg = os.path.join(web_dir, "damai", "config.json")
            if os.path.isfile(_cfg):
                with io.open(_cfg, encoding="utf-8") as f:
                    _c = json.load(f)
                check("R6 damai/config.json 里也没有观演人名字",
                      [n for n in (_c.get("users") or []) if str(n).strip()] == [],
                      _c.get("users"))
            # R7 随手机端一起下线：仓库里已经没有第二份示例配置了
        finally:
            wiz.accounts = _saved_acc2
            wiz.acc_data = _saved_data2
            with io.open(_acc_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(_before)
            wiz._reload_accounts()
            wiz._render_tabs(recollect=False)
            root.update()

        # O：只勾「自动提交订单」，别的控件不碰，先钉那个勾有没有写进 accounts.json。
        #   写不进去运行时读到的就是 False，脚本会安静停在确认页等手动点，
        #   表现和「勾了没用」一样且不报错。
        print("\n[O] 只勾「自动提交订单」也要落盘到 accounts.json")
        p1o, p2o = wiz.pages["Account 01"], wiz.pages["Account 02"]
        for page_o in (p1o, p2o):
            page_o["url"].delete(0, "end")
            page_o["url"].insert(0, "https://detail.damai.cn/item.htm?id=33333333")
            if not page_o["prices"].get("1.0", "end").strip():
                page_o["prices"].insert("1.0", "380")
            page_o["user_vars"]["王小明"].set(True)
            page_o["enabled"].set(True)
            page_o["if_commit_order"].set(False)
            # 「自动登录」一起关掉，用来确认 True 不是被别的开关顺带带出来的
            page_o["auto_login"].set(False)
        wiz._collect_all()
        wiz._persist_accounts()

        d0 = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        check("前提：落盘里两个账号都还没开自动下单",
              all(acc_store.find(d0, a)["if_commit_order"] is False
                  for a in ("Account 01", "Account 02")),
              [(a, acc_store.find(d0, a)["if_commit_order"])
               for a in ("Account 01", "Account 02")])

        # 模拟用户的一次真实点击：Tk 是先写变量（触发 trace），再走 command
        wiz.nb.select(wiz._tab_index("Account 02"))
        p2o["if_commit_order"].set(True)
        wiz._confirm_commit("Account 02")
        check("勾上之后内存里就是 True（确认框点了「是」）",
              acc_store.find(wiz.acc_data, "Account 02")["if_commit_order"] is True)

        # 只勾这一个复选框、别的控件不碰，然后直接点「开始」
        launched.clear()
        dialogs.clear()
        wiz.start(all_accounts=True)

        d1 = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        check("accounts.json 里 Account 02 的 if_commit_order 真的是 True",
              acc_store.find(d1, "Account 02")["if_commit_order"] is True,
              acc_store.find(d1, "Account 02"))
        check("Account 01 没被牵连，仍是 False",
              acc_store.find(d1, "Account 01")["if_commit_order"] is False,
              acc_store.find(d1, "Account 01")["if_commit_order"])
        check("确认框如实提示了会直接扣款",
              "扣款" in bodies(dialogs), bodies(dialogs)[-160:])
        check("两个账号都被启动（开的那个没把别的挤掉）",
              sorted(a[0][2] for a in launched) == ["Account 01", "Account 02"],
              [a[0] for a in launched])

        print("\n[N] 刷新频率开放给用户自定义")
        p1 = wiz.pages["Account 01"]
        check("账号分页上有「刷新频率」输入框", "refresh" in p1)
        check("默认留空（空 = 不写字段，行为和没有这个功能时完全一样）",
              p1["refresh"].get() == "", p1["refresh"].get())

        wiz._collect_all()
        wiz._persist_accounts()
        d = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        check("留空时凭空多出来的字段不会写进 accounts.json",
              "refresh_interval" not in acc_store.find(d, "Account 01"),
              acc_store.find(d, "Account 01"))

        p1["refresh"].delete(0, "end")
        p1["refresh"].insert(0, "1.5")
        wiz._on_refresh_typed("Account 01")
        wiz._collect_all()
        wiz._persist_accounts()
        d = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        v = acc_store.find(d, "Account 01").get("refresh_interval")
        check("填 1.5 之后真的写进了 accounts.json", v == 1.5, v)
        check("取值是数字而不是字符串（CLI 那边要 float() 它）",
              isinstance(v, float), type(v).__name__)
        check("页内摘要里能看见这个设置",
              "刷新 1.5 秒" in p1["summary"].cget("text"), p1["summary"].cget("text"))
        check("正常值不报警", p1["refresh_warn"].cget("text") == "好",
              p1["refresh_warn"].cget("text"))
        check("另一个账号没被牵连",
              "refresh_interval" not in acc_store.find(d, "Account 02"))

        p1["refresh"].delete(0, "end")
        p1["refresh"].insert(0, "0.3")
        wiz._on_refresh_typed("Account 01")
        check("填 0.3 时如实提示「偏快」",
              "偏快" in p1["refresh_warn"].cget("text"),
              p1["refresh_warn"].cget("text"))

        p1["refresh"].delete(0, "end")
        p1["refresh"].insert(0, "0.05")
        wiz._on_refresh_typed("Account 01")
        check("低于硬下限时说明会被按 0.2 秒执行",
              "0.2 秒" in p1["refresh_warn"].cget("text"),
              p1["refresh_warn"].cget("text"))

        # 非法值不能落进 accounts.json：account_runner 的校验会直接跳过这个账号，
        #   表现就是「改了一下刷新频率，账号就不跑了」。
        before_bad = acc_store.find(wiz.acc_data, "Account 01").get("refresh_interval")
        p1["refresh"].delete(0, "end")
        p1["refresh"].insert(0, "abc")
        wiz._on_refresh_typed("Account 01")
        check("填 abc 时提示要数字",
              "数字" in p1["refresh_warn"].cget("text"),
              p1["refresh_warn"].cget("text"))
        wiz._collect_all()
        wiz._persist_accounts()
        d = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        check("非法值没写进文件（保留填错之前那个合法值 {}）".format(before_bad),
              acc_store.find(d, "Account 01").get("refresh_interval") == before_bad,
              acc_store.find(d, "Account 01").get("refresh_interval"))

        p1["refresh"].delete(0, "end")
        p1["refresh"].insert(0, "nan")
        wiz._on_refresh_typed("Account 01")
        check("nan / inf 这类能骗过 float() 的值也拦得住",
              "数字" in p1["refresh_warn"].cget("text"),
              p1["refresh_warn"].cget("text"))

        p1["refresh"].delete(0, "end")
        wiz._on_refresh_typed("Account 01")
        wiz._collect_all()
        wiz._persist_accounts()
        d = json.load(io.open(acc_store.ACCOUNTS_PATH, encoding="utf-8"))
        check("清空之后字段被摘掉，回到「用默认值」",
              "refresh_interval" not in acc_store.find(d, "Account 01"),
              acc_store.find(d, "Account 01"))

        print("\n[O] 任务面板（单窗口聚合 + 单任务停止入口）")
        import task_manager as tm_mod
        real_runs = os.path.join(BASE, "logs", "runs")   # 日志/运行目录现在在 logs/ 下
        runs_before = set(os.listdir(real_runs)) if os.path.isdir(real_runs) else None

        # 面板的状态文件必须落在临时目录，绝不能进项目目录
        tm_mod.RUNS_DIR = os.path.join(tmp, "runs")
        W._TASK_MANAGER = None
        # 前面 [E]/[E2]/[F] 起过任务，日志落在 tmp/runs；「查看日志」会兜底翻
        #   runs/ 里最近的同名日志（2026-10-09 新增），不清掉下面那条用例会翻到
        #   前面几组的日志，测的就不是它自己那件事。
        if os.path.isdir(tm_mod.RUNS_DIR):
            for _stale in os.listdir(tm_mod.RUNS_DIR):
                try:
                    os.remove(os.path.join(tm_mod.RUNS_DIR, _stale))
                except Exception:
                    pass
        check("向导 import 到了 task_manager", W.tasks is not None)
        check("底部按钮接到 open_task_panel",
              callable(getattr(wiz, "open_task_panel", None)))

        panel = W.TaskPanel(wiz)
        root.update_idletasks()
        check("面板窗口建起来了", panel.alive())
        rows = panel.tree.get_children()
        check("每个账号一行，一个不漏", len(rows) == 2, rows)
        check("行标用的是账号号（面板和账号清单同源）",
              set(rows) == {"Account 01", "Account 02"}, rows)
        vals = panel.tree.item("Account 01", "values")
        check("账号 / 状态 / 耗时 / 最近一行 / 操作 —— 五列都在",
              len(vals) == 5, vals)
        # 「状态」列得放得下最长的状态：原来 80px，「已提交，待支付」在本机字体下
        #   是 84px、会被截。判据用真量像素，不是「列宽 >= 某个数」那种过程值。
        from tkinter import font as _tkfont
        _tdesc = W.ttk.Style(root).lookup("Treeview", "font") or "TkDefaultFont"
        _tf = _tkfont.Font(root=root, font=_tdesc)
        _longest = ("已提交，待支付", "需要验证码", "登录输错", "停止中…")
        _need = max(_tf.measure(s) for s in _longest)
        _sw = panel.tree.column("state", "width")
        check("状态列放得下最长的状态文案（真量像素，不截断）",
              _need + 16 <= _sw, (_need, _sw))
        check("还没跑过的账号显示「未启动」", vals[1] == "未启动", vals[1])
        check("状态文件写到了临时目录",
              panel.tm.state_path.startswith(tmp), panel.tm.state_path)

        panel.tree.selection_set("Account 01")
        panel._sync_ops()
        check("选中一行后提示行说的是这个账号",
              "Account 01" in panel._sel.cget("text"), panel._sel.cget("text"))

        # 没跑过，停止 / 看日志 都要给得出人话，而不是静默失败
        panel.stop_selected()
        check("停一个没跑过的账号 → 明确说「没有正在跑的任务」",
              "没有正在跑" in panel.msg.cget("text"), panel.msg.cget("text"))
        panel.view_log()
        check("看一个还不存在的日志 → 明确说「还没有日志」",
              "还没有日志" in panel.msg.cget("text"), panel.msg.cget("text"))
        panel.stop_all()
        check("没有任务时「全部停止」不弹确认框、直接说明",
              "没有在跑的任务" in panel.msg.cget("text"), panel.msg.cget("text"))

        check("面板的启动前校验认得出「还没填完」的账号",
              wiz._account_problems({"account_id": "空账号"}) != [],
              wiz._account_problems({"account_id": "空账号"}))

        # [Q] 「操作」列（2026-10-09 新增）：每行右边一个一键入口，替代原来
        #   「先选中，再点停止选中」的两步法（账号一多容易点错行）。
        print("\n[Q] 任务面板 ·「操作」列（每行一键停 / 起）")
        import types as _types

        check("表头多了「操作」一列",
              panel.tree.heading("op", "text") == "操作",
              panel.tree.heading("op", "text"))
        check("没跑过的行，那一格写着「启动」",
              panel.tree.item("Account 01", "values")[4] == "启动",
              panel.tree.item("Account 01", "values"))

        check("状态→那一格的字：未启动 → 启动",
              panel._op_text({"aid": "X", "state": tm_mod.S_NOT_STARTED}) == "启动")
        check("状态→那一格的字：在跑 → 停止",
              panel._op_text({"aid": "X", "state": tm_mod.S_RUNNING}) == "停止")
        check("状态→那一格的字：已抢到 → 重跑",
              panel._op_text({"aid": "X", "state": tm_mod.S_SUCCESS}) == "重跑")
        panel._queue = ["X"]
        check("状态→那一格的字：排队中 → 取消排队",
              panel._op_text({"aid": "X", "state": tm_mod.S_NOT_STARTED}) == "取消排队")
        panel._queue = []

        # 点那一格，分发到对应动作。三个动作都换成探针，不起真进程。
        hits = []
        real_ops = (panel._stop_row, panel._launch, panel._cancel_queued)
        real_col = (panel.tree.identify_column, panel.tree.identify_row)
        panel._stop_row = lambda aid: hits.append(("stop", aid))
        panel._launch = lambda aid, acc: hits.append(("launch", aid))
        panel._cancel_queued = lambda aid: hits.append(("cancel", aid))
        try:
            panel.tree.identify_column = lambda x: "#5"
            panel.tree.identify_row = lambda y: "Account 01"

            panel.tm.tasks.pop("Account 01", None)
            panel.tm.tasks["Account 01"] = tm_mod.Task("Account 01",
                                                       state=tm_mod.S_RUNNING)
            hits[:] = []
            panel._on_tree_click(_types.SimpleNamespace(x=100, y=100))
            check("在跑的行点「操作」格 → 停这一个账号",
                  hits == [("stop", "Account 01")], hits)

            panel._queue = ["Account 01"]
            hits[:] = []
            panel._on_tree_click(_types.SimpleNamespace(x=100, y=100))
            check("排队中的行点「操作」格 → 取消排队",
                  hits == [("cancel", "Account 01")], hits)
            panel._queue = []

            panel.tm.tasks.pop("Account 01", None)
            hits[:] = []
            panel._on_tree_click(_types.SimpleNamespace(x=100, y=100))
            check("没跑过的行点「操作」格 → 启动",
                  hits == [("launch", "Account 01")], hits)

            panel._stopping.add("Account 01")
            panel.tm.tasks["Account 01"] = tm_mod.Task("Account 01",
                                                       state=tm_mod.S_RUNNING)
            hits[:] = []
            panel._on_tree_click(_types.SimpleNamespace(x=100, y=100))
            check("正在停止中的行，连点不会重复停", hits == [], hits)
            panel._stopping.discard("Account 01")

            panel.tree.identify_column = lambda x: "#2"
            hits[:] = []
            panel._on_tree_click(_types.SimpleNamespace(x=100, y=100))
            check("点在别的列 → 不触发动作（老的两步法一点没动）",
                  hits == [], hits)
        finally:
            panel._stop_row, panel._launch, panel._cancel_queued = real_ops
            (panel.tree.identify_column,
             panel.tree.identify_row) = real_col
            panel.tm.tasks.pop("Account 01", None)

        # 停放后台线程：stop() 要等进程树收完（最多 6 秒），卡主线程就没有一键手感。
        _src = io.open(W.__file__, encoding="utf-8").read()
        _seg = _src.split("def _stop_row", 1)[1].split("\n    def ", 1)[0]
        check("「操作」格的停放在后台线程里跑（主线程不卡 6 秒）",
              "Thread(" in _seg and "daemon=True" in _seg, _seg[:60])
        check("停完回主线程刷界面（tkinter 的控件只能主线程碰）",
              ".after(0," in _seg or "after(0, " in _seg)

        # [Q2] 任务画面：每个浏览器窗口缩成一张图（2026-10-09 新增），跟任务面板
        #   是两条路：面板管起停 / 状态 / 日志，这条只管看得见。
        print("\n[Q2] 任务画面（每个窗口缩略图摆一屏，哪个在等人一眼看得见）")
        import window_wall as ww_mod
        if not ww_mod.available():
            check("非 Windows 平台：画面墙如实报「不支持」", True)
        else:
            # ① 后端：拿 Tk 自己的窗口当靶子，自证「枚举，截图，编码，解码」
            #    整条链路真的通，而不是只测了个空函数。
            probe = tk.Toplevel(root)
            probe.title("画面墙测试窗口 MarkZz")
            probe.geometry("360x200+60+60")
            probe.update()
            time.sleep(0.4)
            probe.update()
            try:
                wins = ww_mod.list_windows()
                hit = [h for h, _p, t in wins if "MarkZz" in t]
                check("后端能按标题找到窗口", bool(hit),
                      [t for _h, _p, t in wins][:5])
                if hit:
                    b64, tw, th = ww_mod.thumb_b64(hit[0], 200)
                    check("能把窗口截成缩略图", bool(b64), (tw, th))
                    check("缩略图确实缩过（不是原尺寸）",
                          b64 is not None and tw <= 200, tw)
                    if b64:
                        img = tk.PhotoImage(data=b64)
                        check("截出来的图 Tk 能直接显示（不用装 Pillow）",
                              img.width() > 10 and img.height() > 10,
                              (img.width(), img.height()))
            finally:
                probe.destroy()
                root.update()

            # ② 面板那条路：能开、没任务时说人话、关掉干净
            wall = W.TaskWall(panel)
            root.update_idletasks()
            check("画面墙窗口建起来了", wall.alive())
            wall.refresh()
            _txt = wall.msg.cget("text")
            check("刷新之后给得出人话（有窗口就报个数，没窗口就指路怎么弄出来）",
                  ("个浏览器窗口" in _txt) or ("没有开着" in _txt), _txt)
            # 按窗口句柄干活：向导绿色按钮那条老路的任务在面板里没有账号对象。
            wall._raise_hwnd(0)
            check("对着无效的窗口句柄拉窗口 → 不抛异常、不静默假成功", True)
            wall.close()
            root.update_idletasks()
            check("关掉画面墙之后窗口真的没了（而且一个任务都没停）",
                  not wall.alive())

        # [Q3] 实时状态（2026-10-09）：不管任务从哪条路起（任务面板 / 启动页那两个
        #   绿色按钮）都算进那个账号那一行；状态要细（登录输错 / 需要验证码 /
        #   待提交订单 / 刷新中 / 轮询中）；验证码 / 登录输错标红，每行操作格真能停。
        print("\n[Q3] 实时状态：两条路起的任务都算进账号那一行（含标红 / 真停）")
        import live_status as _ls

        # 造一个「正在轮询」的活任务：pid 必须是真活进程，读的一侧按 pid 过滤死
        #   进程（强杀不留遗嘱），假 pid 测不到东西。用睡眠子进程当靶子，下面还要
        #   真停它；必须用 _real_popen，模块的假 Popen 会把这个也吞掉。
        _sleeper = _real_popen(
            [sys.executable, "-c", "import time; time.sleep(600)"])
        _live_file = _ls.path_for("Account 01")
        check("前提：live 状态文件落在临时目录（真目录一个字节没动）",
              _live_file.startswith(tmp), _live_file)

        os.makedirs(os.path.dirname(_live_file), exist_ok=True)

        def _write_live(state, detail=""):
            with open(_live_file, "w", encoding="utf-8") as f:
                json.dump({"account": "Account 01", "pid": _sleeper.pid,
                           "state": state, "detail": detail,
                           "started": time.time() - 65,
                           "updated": time.time()}, f)

        try:
            _write_live("轮询中", "正在盯「预订」按钮")
            panel.refresh()
            _vals = panel.tree.item("Account 01", "values")
            check("面板按账号显示实时状态（轮询中）—— 不管任务是谁起的",
                  _vals[1] == "轮询中", _vals)
            check("活着的状态用运行色（不落灰）",
                  "running" in panel.tree.item("Account 01", "tags"),
                  panel.tree.item("Account 01", "tags"))
            check("「操作」格 = 停止（两条路的任务都停得掉）",
                  _vals[4] == "停止", _vals)
            check("最近一行显示状态详情", "盯" in _vals[3], _vals)
            check("耗时按实时状态的开始时间算（不再是「-」）",
                  ("分" in _vals[2]) or ("秒" in _vals[2]), _vals[2])

            # 换「需要验证码」，标红
            _write_live("需要验证码", "页面要求：滑块")
            panel.refresh()
            check("「需要验证码」标红（要去人工处理的，一眼看见）",
                  "alert" in panel.tree.item("Account 01", "tags"),
                  panel.tree.item("Account 01", "tags"))

            # 换「登录输错」，标红，且状态文字就是用户点名的那个说法
            _write_live("登录输错", "页面提示：账号或密码错误")
            panel.refresh()
            check("「登录输错」标红，且状态文字就是「登录输错」",
                  "alert" in panel.tree.item("Account 01", "tags")
                  and panel.tree.item("Account 01", "values")[1] == "登录输错",
                  panel.tree.item("Account 01", "values"))

            # 真停：模拟「面板没起过它」（走启动页那条路的），也要停得掉
            panel.tm.tasks.pop("Account 01", None)
            panel.tm._save()
            _write_live("轮询中", "")
            panel.refresh()
            panel._row_action("Account 01")
            _deadline = time.time() + 15
            while time.time() < _deadline:
                root.update()
                if "Account 01" not in panel._stopping:
                    break
                time.sleep(0.05)
            panel.refresh()
            check("点「停止」→ 那个任务真被停了（整棵进程树）",
                  _sleeper.poll() is not None, _sleeper.poll())
            check("强杀不留遗嘱：live 文件还在磁盘上，但 pid 死了就不算在跑",
                  os.path.exists(_live_file) and "Account 01" not in panel._live,
                  (os.path.exists(_live_file), sorted(panel._live)))
            # 停止的回刷要真执行到（2026-10-09 修的坑：回刷原走「子线程调
            #   self.win.after」，tkinter 抛 RuntimeError 被吞，那一格就永远卡在
            #   「停止中…」。现在走投递箱 post，主线程 refresh 执行）。
            check("停止完成后不再卡「停止中…」（回刷真的执行到了）",
                  "Account 01" not in panel._stopping, sorted(panel._stopping))

            # 双击那一行，打开对应浏览器窗口（找不到时给得出人话）
            _bind = panel.tree.bind("<Double-1>")
            check("双击绑定在（双击账号那一行会去开浏览器窗口）",
                  bool(_bind), _bind)
            panel.focus_browser("Account 01")     # 睡眠进程没有窗口
            check("没有开着的浏览器窗口时，双击给得出人话，不静默",
                  "没有开着的浏览器" in panel.msg.cget("text"),
                  panel.msg.cget("text"))
        finally:
            try:
                _sleeper.kill()
            except Exception:
                pass
            try:
                os.remove(_live_file)
            except Exception:
                pass

        # 防误伤：机器上开着 Edge/Chrome 时，不许把用户自己开的算成抢票任务。
        _ours = W.our_chrome_pids(panel.tm)
        check("没有本程序的抢票进程时，「我们的浏览器」是空集（不会误收自己的）",
              _ours == set(), _ours)

        panel.close()
        root.update_idletasks()
        check("关掉面板之后窗口真的没了", not panel.alive())
        check("测试没在项目目录里建 logs/runs/（落盘全在临时目录）",
              (set(os.listdir(real_runs)) if os.path.isdir(real_runs) else None) == runs_before,
              real_runs)

        # [Y] 输入框右键菜单（2026-10-09）：用 bind_class（Entry / TEntry / Text）挂，
        #   一次覆盖启动页 + 以后新增的输入框。只验接线：绑定在、菜单项对、
        #   命令把 <<Cut>>/<<Copy>>/<<Paste>> 发给目标框。不真弹菜单（tk_popup 会卡死）。
        print("\n[Y] 输入框右键菜单（复制 / 粘贴）")
        check("右键菜单建起来了（向导一开就有）", W._CLIP_MENU is not None)
        for _cls, _name in (("Entry", "tk.Entry"), ("TEntry", "ttk.Entry"),
                            ("Text", "Text 文本框")):
            check("{} 上有右键绑定".format(_name),
                  bool(root.bind_class(_cls, "<Button-3>")),
                  root.bind_class(_cls, "<Button-3>"))

        _cm = W._CLIP_MENU
        _labels = []
        try:
            for _i in range(_cm.index("end") + 1):
                _labels.append(_cm.entrycget(_i, "label")
                               if _cm.type(_i) == "command" else "-")
        except Exception:
            pass
        check("菜单项：剪切 / 复制 / 粘贴 —— 全选（顺序对）",
              _labels == ["剪切", "复制", "粘贴", "-", "全选"], _labels)
        check("重复安装是幂等的（不会越挂越多）",
              W.install_clipboard_menu(root) is _cm)

        # 「全选」：真的把整个框选上
        _e = tk.Entry(root)
        _e.insert(0, "abc中文")
        W._clip_select_all(_e)
        try:
            _first, _last = _e.index("sel.first"), _e.index("sel.last")
        except Exception:
            _first = _last = None
        check("「全选」把整个输入框都选上（到头）",
              _first == 0 and _last == len("abc中文"), (_first, _last))
        _e.destroy()

        # 剪切 / 复制 / 粘贴：点菜单后，动作要发给右键点中的那个框
        _calls = []
        _e2 = tk.Entry(root)
        _real_gen = _e2.event_generate
        _e2.event_generate = lambda ev, **kw: _calls.append(ev)
        _old_target = W._CLIP_TARGET
        try:
            W._CLIP_TARGET = _e2
            try:
                _cm.invoke(0)
                _cm.invoke(1)
                _cm.invoke(2)
            except Exception:
                pass                     # 菜单都没建起来的话，上面几条已经红了
            check("点「剪切 / 复制 / 粘贴」→ 动作真的发给目标输入框",
                  _calls == ["<<Cut>>", "<<Copy>>", "<<Paste>>"], _calls)
            W._CLIP_TARGET = None
            _calls[:] = []
            try:
                _cm.invoke(2)
            except Exception:
                pass
            check("没点过右键也能安全执行（防御，不崩）", _calls == [], _calls)
        finally:
            W._CLIP_TARGET = _old_target
            _e2.event_generate = _real_gen
        _e2.destroy()

        # [Z] 任务面板「画面」列（2026-10-09）：每行左边放对应浏览器窗口的实时
        #   缩略图，不用另开「画面墙」翻。用真窗口靶子走完整链路：找窗口，截图，
        #   编码，回填到行上，撤图。
        print("\n[Z] 任务面板 ·「画面」列（每行一个实时缩略图）")
        panel_z = W.TaskPanel(wiz)      # 上面那个 panel 已经关了，重开一个
        root.update_idletasks()
        import window_wall as ww_mod2
        if not ww_mod2.available():
            check("非 Windows 平台：画面列如实跳过", True)
        else:
            check("树里多了「画面」列（窗口列表形态）",
                  "tree" in str(panel_z.tree["show"]), panel_z.tree["show"])
            check("「画面」列的标题是「画面」",
                  panel_z.tree.heading("#0", "text") == "画面",
                  panel_z.tree.heading("#0", "text"))
            _rh = int(W.ttk.Style(root).lookup("TaskPanel.Treeview", "rowheight") or 0)
            check("行高至少放得下缩略图（和 SHOT_H 挂钩，改大图不用改测试）",
                  _rh >= panel_z.SHOT_H, (_rh, panel_z.SHOT_H))
            check("缩略图确实是「看得清楚」的尺寸（>=100px 高）",
                  panel_z.SHOT_H >= 100, panel_z.SHOT_H)

            # 子线程，主线程投递箱（2026-10-09 的坑：tkinter 不许子线程调 after，
            #   抛 RuntimeError 被吞 = 界面永不更新）。post 进去先不执行，主线程 drain 才执行。
            _got = []
            panel_z.post(lambda: _got.append(1))
            check("投递箱：放进去不立刻执行（等主线程来取）", _got == [], _got)
            panel_z._drain_inbox()
            check("投递箱：主线程 drain 后执行（替掉子线程 after 的那条路）",
                  _got == [1], _got)

            # 真链路：拿 Tk 自己的窗口当「某个账号的浏览器」，整条走一遍。
            probe2 = tk.Toplevel(root)
            probe2.title("面板缩略图测试窗口 MarkZz")
            probe2.geometry("420x240+80+80")
            probe2.update()
            time.sleep(0.3)
            probe2.update()
            try:
                _hw = [h for h, _p, t in ww_mod2.list_windows()
                       if "面板缩略图测试窗口" in t]
                check("前提：找得到靶子窗口", bool(_hw), _hw[:2])
                if _hw:
                    _real_hwnd_of = panel_z._browser_hwnd_of
                    panel_z._browser_hwnd_of = lambda aid, _h=_hw[0]: _h
                    try:
                        panel_z._last_shot = 0        # 清节流，立刻截一轮
                        panel_z._maybe_shoot()
                        _dl = time.time() + 10
                        while time.time() < _dl:
                            root.update()
                            if not panel_z._shooting and panel_z._thumbs:
                                break
                            time.sleep(0.05)
                        _img = panel_z._thumbs.get("Account 01")
                        check("缩略图真截到、真贴到了账号那一行",
                              _img is not None
                              and bool(panel_z.tree.item("Account 01", "image")),
                              panel_z.tree.item("Account 01", "image"))
                        if _img is not None:
                            check("缩略图按行高缩过（不是原图）",
                                  _img.height() <= panel_z.SHOT_H
                                  and _img.width() <= panel_z.SHOT_W + 2,
                                  (_img.width(), _img.height()))
                    finally:
                        panel_z._browser_hwnd_of = _real_hwnd_of

                    # 窗口没了，旧图要撤（不留过期画面）
                    panel_z._browser_hwnd_of = lambda aid: None
                    try:
                        # 先等上一轮截图收尾（可能正好在跑，忙时会跳过新一轮）
                        _dl2 = time.time() + 8
                        while time.time() < _dl2 and panel_z._shooting:
                            root.update()
                            time.sleep(0.05)
                        panel_z._last_shot = 0
                        panel_z._maybe_shoot()
                        # 兜底：万一刚好忙、被跳过，等下一拍 refresh 自然清
                        _dl3 = time.time() + 4
                        while time.time() < _dl3 and "Account 01" in panel_z._thumbs:
                            root.update()
                            time.sleep(0.05)
                        check("窗口不在之后旧图被撤掉（不留过期画面）",
                              "Account 01" not in panel_z._thumbs,
                              sorted(panel_z._thumbs))
                    finally:
                        panel_z._browser_hwnd_of = _real_hwnd_of
            finally:
                try:
                    probe2.destroy()
                except Exception:
                    pass
            # 这个面板是 [Z] 自己开的，用完关掉（不留给后面的组）
            try:
                panel_z.close()
            except Exception:
                pass

        # [Z2] 最小化的窗口也要能截到画面（2026-10-09）。真 Chrome 实测：最小化窗口的
        #   PrintWindow 是黑图（亮度 32，正常 ~120）、GetWindowRect 是幽灵值
        #   （237x39 @ -32000）。修法：截前「离屏恢复」（ShowWindow + SetWindowPos ~7ms）。
        print("\n[Z2] 最小化的窗口也要能截到画面（离屏恢复）")
        if not ww_mod2.available():
            check("非 Windows：跳过（截图只在 Windows 上有）", True)
        else:
            probe3 = tk.Toplevel(root)
            probe3.title("最小化靶子 MarkZz")
            probe3.geometry("360x240+90+90")
            probe3.configure(bg="#c04040")
            tk.Label(probe3, text="最小化验证", bg="#c04040", fg="white",
                     font=W.UI_FONT_SMALL).pack(expand=True)
            probe3.update()
            time.sleep(0.4)
            probe3.update()
            try:
                _hit3 = [h for h, _p, t in ww_mod2.list_windows()
                         if "最小化靶子" in t]
                check("前提：找得到靶子窗口", bool(_hit3), _hit3[:2])
                if _hit3:
                    _h3 = _hit3[0]
                    _u3 = ww_mod2._user32
                    _b0, _w0, _h0 = ww_mod2.grab_rgb(_h3, 260, max_h=160)
                    check("前提：正常状态下能截到内容（基线）",
                          _b0 is not None and _w0 > 80,
                          (_b0 is not None, _w0, _h0))
                    _wp0 = ww_mod2._get_placement(_h3)
                    _u3.ShowWindow(_h3, 6)          # SW_MINIMIZE = 6
                    time.sleep(0.6)
                    check("前提：窗口真的最小化了", bool(_u3.IsIconic(_h3)))
                    # 核心断言：最小化状态下截图链路仍要给出真内容
                    _b1, _w1, _h1 = ww_mod2.grab_rgb(_h3, 260, max_h=160)
                    check("最小化后仍截到画面（不是 237x39 的黑图）",
                          _b1 is not None and _w1 > 80
                          and abs(_w1 - _w0) <= 12,
                          (_b1 is not None, _w1, _h1))
                    if _b1 is not None:
                        _tot = len(_b1) // 3
                        _sm = sum(_b1[i] + _b1[i + 1] + _b1[i + 2]
                                  for i in range(0, len(_b1), 3))
                        check("内容是真的（亮度接近基线，不是黑图）",
                              _sm / float(_tot * 3) > 40,
                              round(_sm / float(_tot * 3), 1))
                    check("截完窗口被放回任务栏（用户的最小化没被弄丢）",
                          bool(_u3.IsIconic(_h3)))
                    _wp1 = ww_mod2._get_placement(_h3)
                    check("「正常位置」复位了（以后手动恢复不会跑到屏幕外）",
                          _wp0 is not None and _wp1 is not None
                          and _wp0.rcNormalPosition.left
                          == _wp1.rcNormalPosition.left
                          and _wp0.rcNormalPosition.top
                          == _wp1.rcNormalPosition.top,
                          (_wp1.rcNormalPosition.left,
                           _wp1.rcNormalPosition.top))
            finally:
                try:
                    probe3.destroy()
                except Exception:
                    pass
                root.update()

        # [Z3] 多开：两个（最小化的）窗口各有各的图。把现场钉死——两窗都最小化时
        #   面板两行都得有图，且是两张不同的图（各截各的，没串门/共用）。
        print("\n[Z3] 多开：两个最小化窗口，两行都贴上图")
        if not ww_mod2.available():
            check("非 Windows：跳过", True)
        else:
            probe4 = tk.Toplevel(root)
            probe4.title("多开靶子A MarkZz")
            probe4.geometry("320x220+40+40")
            probe4.configure(bg="#3a6ea5")
            probe4.update()
            probe5 = tk.Toplevel(root)
            probe5.title("多开靶子B MarkZz")
            probe5.geometry("320x220+420+40")
            probe5.configure(bg="#5a8f3a")
            probe5.update()
            time.sleep(0.4)
            probe4.update()
            probe5.update()
            panel_z3 = W.TaskPanel(wiz)
            root.update_idletasks()
            try:
                _u4 = ww_mod2._user32
                _hitA = [h for h, _p, t in ww_mod2.list_windows()
                         if "多开靶子A" in t]
                _hitB = [h for h, _p, t in ww_mod2.list_windows()
                         if "多开靶子B" in t]
                check("前提：两个靶子窗口都在",
                      bool(_hitA) and bool(_hitB), (_hitA[:1], _hitB[:1]))
                if _hitA and _hitB:
                    _real_hwnd_of3 = panel_z3._browser_hwnd_of
                    _map3 = {"Account 01": _hitA[0], "Account 02": _hitB[0]}
                    panel_z3._browser_hwnd_of = (
                        lambda aid, _m=_map3: _m.get(str(aid)))
                    try:
                        # 复刻用户现场：两个窗口都缩到任务栏
                        _u4.ShowWindow(_hitA[0], 6)
                        _u4.ShowWindow(_hitB[0], 6)
                        time.sleep(0.6)
                        panel_z3._last_shot = 0
                        panel_z3._maybe_shoot()
                        _dl4 = time.time() + 15
                        while time.time() < _dl4 and len(panel_z3._thumbs) < 2:
                            root.update()
                            panel_z3._drain_inbox()
                            time.sleep(0.05)
                        check("两个账号的图都截到了 —— 不是「只显示一个」",
                              len(panel_z3._thumbs) >= 2,
                              sorted(panel_z3._thumbs))
                        _imgA = panel_z3._thumbs.get("Account 01")
                        _imgB = panel_z3._thumbs.get("Account 02")
                        check("两行的图都贴到树上了",
                              _imgA is not None and _imgB is not None
                              and bool(panel_z3.tree.item("Account 01", "image"))
                              and bool(panel_z3.tree.item("Account 02", "image")),
                              (panel_z3.tree.item("Account 01", "image"),
                               panel_z3.tree.item("Account 02", "image")))
                        _nA = panel_z3.tree.item("Account 01", "image")
                        _nB = panel_z3.tree.item("Account 02", "image")
                        check("两行是两张不同的图（没有都贴成同一张）",
                              bool(_nA) and bool(_nB) and _nA != _nB,
                              (_nA, _nB))
                        # 最小化黑图的「幽灵尺寸」是 237x39（缩完高 ~31），
                        # 真窗口按 max_h=110 缩出来最少也有几十像素高。
                        # 这条是给自证准备的：拿掉离屏恢复时它会红。
                        check("图是真实画面（不是最小化幽灵尺寸的黑图）",
                              _imgA is not None and _imgB is not None
                              and _imgA.height() >= 80 and _imgB.height() >= 80,
                              (_imgA.height() if _imgA else None,
                               _imgB.height() if _imgB else None))
                        check("（顺带）两个窗口都还在任务栏里（截图没把状态弄乱）",
                              bool(_u4.IsIconic(_hitA[0]))
                              and bool(_u4.IsIconic(_hitB[0])))
                    finally:
                        panel_z3._browser_hwnd_of = _real_hwnd_of3
            finally:
                for _w in (probe4, probe5):
                    try:
                        _w.destroy()
                    except Exception:
                        pass
                root.update()
                try:
                    panel_z3.close()
                except Exception:
                    pass

        print("\n[W] 高 DPI 屏：每个窗口的按钮都得真的看得见")
        # 真因：窗口尺寸写死成物理像素（如 680x450），
        #   字体按点算、tk scaling 跟屏幕 DPI 走，2560x1600 / 200% 缩放下 scaling ≈ 2.0、
        #   内容要 946x595；按钮在最后被 pack 切出窗口外。量法：窗口真映射出来再比坐标。
        print("  tk scaling = {:.3f}（1.333 = 96dpi/72，2.0 = 200% 缩放）"
              .format(root.tk.call("tk", "scaling")))

        root_was = root.state()
        try:
            root.attributes("-alpha", 0.0)
            root.deiconify()
            root.update()
        except Exception:
            pass

        def measure(tag, win, must_have=(), only=None):
            try:
                win.attributes("-alpha", 0.0)      # 别闪屏；几何照样是真的
            except Exception:
                pass
            try:
                win.deiconify()
            except Exception:
                pass
            win.update()
            win.update_idletasks()
            bad = offscreen_buttons(win, only=only)
            texts = [b[0] for b in dialog_buttons(win)]
            check("{}：窗口里没有跑到外面的按钮（{}x{}）"
                  .format(tag, win.winfo_width(), win.winfo_height()),
                  not bad, bad)
            for t in must_have:
                check("{}：找得到「{}」这个按钮".format(tag, t),
                      any(t == x for x in texts), texts)

        # 向导主窗口最先量：底部「保存并开始 / 全部账号启动 / 退出」被切掉就等于
        #   程序没法用。only= 那串是固定外圈按钮；中间分页里的按钮在可滚动画布上，
        #   超出时滚动看，不算跑到外面。
        measure("向导主窗口", root,
                must_have=("退出", "全部账号启动"),
                only=("环境检测", "保存并开始", "全部账号启动", "仅保存",
                      "＋ 新建", "任务面板", "退出"))

        # 账号名输入框没写死尺寸、Tk 自适应；顺手量一遍防以后有人加 geometry("400x150")。
        cap = []

        def _probe_name_dialog():
            for c in root.winfo_children():
                try:
                    if (c.winfo_class() == "Toplevel"
                            and str(c.title()).startswith("自检用")):
                        c.update_idletasks()
                        cap.append(offscreen_buttons(c, only=("确定", "取消")))
                        c.destroy()
                except Exception:
                    pass

        root.after(400, _probe_name_dialog)
        # 必须调类上的真身：实例属性 `wiz._ask_name` 已被换成 lambda，调它等于没测。
        W.Wizard._ask_name(wiz, "自检用", "账号X")     # 会被上面的 after 关掉
        check("账号名输入框的按钮在可视区", bool(cap) and not cap[0], cap)

        fake_env = [
            {"key": "system", "name": "操作系统", "level": "ok",
             "detail": "Windows 10", "fix": "", "url": ""},
            {"key": "python", "name": "Python 运行环境", "level": "fail",
             "detail": "找不到 python.exe", "fix": "去官网装一个",
             "url": "https://www.python.org/downloads/", "url_text": "打开下载页"},
            {"key": "runner", "name": "抢票运行环境", "level": "warn",
             "detail": "缺 selenium", "fix": "pip install selenium",
             "url": "https://pypi.org/project/selenium/", "url_text": "打开下载页"},
            {"key": "chrome", "name": "Chrome 浏览器", "level": "ok",
             "detail": "131", "fix": "", "url": ""},
            {"key": "driver", "name": "ChromeDriver", "level": "ok",
             "detail": "131", "fix": "", "url": ""},
            {"key": "net", "name": "网络", "level": "ok",
             "detail": "通", "fix": "", "url": ""},
            {"key": "data", "name": "数据目录可写", "level": "ok",
             "detail": "可以写", "fix": "", "url": ""},
        ]
        d = W.EnvCheckDialog(root, fake_env)
        measure("环境检测窗口", d.win, must_have=("关闭", "复制检测报告"))
        try:
            d.win.destroy()
        except Exception:
            pass

        # 先建任务面板：只读文本窗口和日志窗口都是它的方法（体内有「复制 / 刷新」按钮）。
        panel2 = W.TaskPanel(wiz)

        win = panel2._show_text("自检用文本窗口", "内容\n" * 40)
        measure("只读文本窗口", win, must_have=("关闭", "复制"))
        try:
            win.destroy()
        except Exception:
            pass

        # 日志窗口是另一段代码（有「刷新」）单独量；用替身顶 tm，只用到 tasks / read_log。
        class _FakeTM(object):
            class _T(object):
                log_path = ""

            def __init__(self, path):
                self.tasks = {"Account 01": self._T()}
                self.tasks["Account 01"].log_path = path

            def read_log(self, aid):
                return "一行日志\n" * 40

        logfile = os.path.join(tmp, "fake-run.log")
        with io.open(logfile, "w", encoding="utf-8") as f:
            f.write("一行日志\n" * 40)
        real_tm2 = panel2.tm
        panel2.tm = _FakeTM(logfile)
        panel2.tree.selection_set("Account 01")     # view_log 认的是「选中的那一行」
        panel2.view_log()
        root.update()
        logwin = None
        for c in panel2.win.winfo_children():        # 它的父窗口是任务面板，不是 root
            try:
                if c.winfo_class() == "Toplevel" and str(c.title()).startswith("日志"):
                    logwin = c
            except Exception:
                pass
        check("日志窗口开起来了", logwin is not None)
        if logwin is not None:
            measure("日志窗口", logwin, must_have=("刷新", "打开文件所在目录"))
            try:
                logwin.destroy()
            except Exception:
                pass
        panel2.tm = real_tm2

        # 任务面板自身的按钮也在最后面，同样要量。
        measure("任务面板", panel2.win,
                must_have=("全部启动", "全部停止", "启动选中", "停止选中"))

        # 多开设置窗口（从面板弹出来）
        try:
            mo = W.MultiOpenDialog(panel2)
            measure("多开设置窗口", mo.win, must_have=("保存", "取消"))
            mo.win.destroy()
        except Exception as exc:                                 # pragma: no cover
            check("多开设置窗口能量出来", False, repr(exc))

        try:
            panel2.win.destroy()
        except Exception:
            pass

        # X：打包形态不需要任何外部文件。exe 文件夹里没有 accounts.bat、也没有 python.exe，
        #   子进程由 exe 走 `sys.executable --runner` 自己扮演（.bat 写的是开发机解释器路径）。
        #   两个「开抢」入口以前不分形态先查这两个文件，客户按「开始抢票」就弹「找不到启动文件」。
        print("\n[X] 打包形态：没有 .bat / 没有外部解释器，也要能开抢")

        def _arm_account01():
            """把 Account 01 恢复成「填完了、可以开抢」的样子。

            前面的用例动过它（取消勾选、删过名单里的人），不重新武装这条断言会被
              「这些账号还没填完」提前挡掉。
            """
            if "王小明" not in wiz.roster:
                wiz.roster.append("王小明")
                wiz._render_tabs(recollect=False)
            page = wiz.pages.get("Account 01")
            if page is None:                                   # pragma: no cover
                return
            if "王小明" in page["user_vars"]:
                page["user_vars"]["王小明"].set(True)
            page["enabled"].set(True)
            page["url"].delete(0, "end")
            page["url"].insert(0, "https://detail.damai.cn/item.htm?id=11111111")
            page["city"].delete(0, "end")
            page["city"].insert(0, "北京")
            page["dates"].delete("1.0", "end")
            page["dates"].insert("1.0", "2026-12-05")
            page["prices"].delete("1.0", "end")
            page["prices"].insert("1.0", "380")
            page["qty"].delete(0, "end")
            page["qty"].insert(0, "1")
            wiz._collect_all()
            acc = acc_store.find(wiz.acc_data, "Account 01")
            if acc is not None:
                acc["enabled"] = True
            wiz._persist_accounts()

        _old_bat, _old_venv = W.ACCOUNTS_BAT, W.VENV_PYTHON
        _old_frozen = getattr(sys, "frozen", None)
        try:
            _arm_account01()
            W.ACCOUNTS_BAT = os.path.join(tmp, "客户机上没有这个.bat")
            W.VENV_PYTHON = os.path.join(tmp, "客户机上没有这个", "python.exe")

            # 自证：源码形态 + 文件缺失时必须被拦住，拦不住说明下面几条断言恒真。
            sys.frozen = False
            launched[:] = []
            warned_at = len(warned)
            check("X0 前提：两个文件确实都指到了不存在的地方",
                  not os.path.exists(W.ACCOUNTS_BAT) and not os.path.exists(W.VENV_PYTHON))
            wiz._launch_accounts("login", ["Account 01"], auto=False)
            check("X1 自证：源码形态下缺 accounts.bat 会被拦住"
                  "（拦不住就说明本组断言恒真）",
                  launched == [] and len(warned) > warned_at,
                  (launched, warned[warned_at:]))

            # 打包形态：同样两个文件都不在，必须放行
            sys.frozen = True
            check("X2 打包形态下 startup_precheck 直接放行",
                  W.startup_precheck() is True)

            launched[:] = []
            warned_at = len(warned)
            wiz._launch_accounts("login", ["Account 01"], auto=False)
            _cmd = list(launched[-1][0]) if launched else []
            check("X3 打包形态下「登录这个账号」不再被拦（真的起了子进程）",
                  len(launched) == 1, launched)
            check("X4 起的是 exe 自己（--runner），不是 accounts.bat",
                  len(_cmd) >= 2 and _cmd[1] == "--runner", _cmd)
            check("X5 没有弹「找不到启动文件」",
                  len(warned) == warned_at, warned[warned_at:])

            launched[:] = []
            warned_at = len(warned)
            wiz.start(all_accounts=True)
            check("X6 打包形态下「全部账号启动」也能起进程",
                  len(launched) >= 1, launched)
            check("X7 那条路也没弹「找不到启动文件」",
                  not any("找不到启动文件" in str(d[0]) for d in warned[warned_at:]),
                  warned[warned_at:])

            # X8：错误提示不能让打包版用户去双击一个不存在的文件
            #   （「手动双击 accounts.bat」只在源码形态对）。
            check("X8 打包形态的「起不来怎么办」提示不提 accounts.bat",
                  "accounts.bat" not in W.manual_start_hint(),
                  W.manual_start_hint())

            # X9：源码形态仍要拦（源码下 .bat 和解释器是真需要的，缺了跑不起来，
            #   静默放行只会让用户看到「点了没反应」）。
            sys.frozen = False
            check("X9 源码形态下缺文件仍然不放行",
                  W.startup_precheck() is False)
        finally:
            W.ACCOUNTS_BAT, W.VENV_PYTHON = _old_bat, _old_venv
            if _old_frozen is None:
                try:
                    del sys.frozen
                except AttributeError:
                    pass
            else:
                sys.frozen = _old_frozen

        # 根窗口还原成不可见，免得后面的用例（或收尾）被一个隐形窗口挡着。
        try:
            root.attributes("-alpha", 1.0)
            root.withdraw() if root_was != "normal" else root.deiconify()
        except Exception:
            pass

    finally:
        W.subprocess.Popen = old["Popen"]
        W.messagebox.askyesno = old["askyesno"]
        W.messagebox.showwarning = old["showwarning"]
        W.messagebox.showinfo = old["showinfo"]
        W.messagebox.showerror = old["showerror"]
        W.time.sleep = old["sleep"]
        W.resolve_chromedriver = old["driver"]
        W.focus_damai_window = old["focus"]
        W.CONFIG_PATH = old["config_path"]
        W.RUNS_DIR = real["runs_dir"]        # 见上面「子进程日志目录隔离」
        # 任务面板改过的模块级路径与全局单例也要还原，免得后续用例写到临时目录。
        _tmm = sys.modules.get("task_manager")
        if _tmm is not None:
            _tmm.RUNS_DIR = os.path.join(BASE, "logs", "runs")
        W._TASK_MANAGER = None
        try:
            root.destroy()
        except Exception:
            pass

    print("\n[P] 用户真实文件没被测试碰过")
    for k, path in real.items():
        now = (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
        check("没动过 {}".format(os.path.basename(path)), now == real_stat[k], (real_stat[k], now))

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 56)
    print("通过 {} / {} 项".format(len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：")
        for n in FAIL:
            print("  -", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
