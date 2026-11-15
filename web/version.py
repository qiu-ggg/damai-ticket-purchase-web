# -*- coding: utf-8 -*-
"""程序名和版本号。

至少两处要用：向导的标题栏（`damai_wizard`），用户报问题时截图里就能看出是哪个版本；
环境检测的报告（`env_check`），客户发过来的那份报告要能对应到版本。而 `env_check` 不能
import `damai_wizard` —— 那会把 tkinter 一起拖进来，偏偏它要能在「窗口都起不来」的机器上
跑出结果，所以放在这个零依赖的小模块里。

`APP_VERSION` 一处改，界面、窗口标题、报告全跟着变，不要在各处写死字符串。
"""

APP_NAME = "大麦抢票"
APP_VERSION = "v0.3"


def label():
    """带名字的完整写法，例如「大麦抢票 v0.3」。"""
    return "{} {}".format(APP_NAME, APP_VERSION)


def version_label():
    """界面上那行小字，例如「版本：v0.3」。"""
    return "版本：{}".format(APP_VERSION)
