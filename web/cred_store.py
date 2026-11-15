# -*- coding: utf-8 -*-
"""账号密码的本地存储（明文 JSON）

为什么要单独一个文件
--------------------
`accounts.json` 是要备份、可能整包发给别人的，密码不能混在里面。所以密码单独存
`credentials.json`，一个账号一条记录。

存储形态
--------
明文。没有任何加密：文件里直接就是 `{"phone": ..., "password": ...}`。
好处是跨平台、换机器拷过去照样能用、出问题直接打开就能看；代价是谁拿到这个文件
谁就能读到密码。所以它只应该躺在自己的程序目录里，别拷来拷去、别放进网盘同步。

边界
----
  * 本模块只负责"存起来 / 取出来"，不碰浏览器，也不导入 selenium。
  * 自动填表登录时，一旦页面出现验证码 / 滑块 / 短信验证，调用方必须立刻
    停手交给用户，本模块不提供、也不会提供任何绕过验证的能力。
"""

import json
import os
import time

# 路径口径见 accounts.py 里的同款说明：源码形态与改造前一致，exe 形态落在 exe 同级。
try:
    import app_paths
    BASE_DIR = app_paths.data_dir()
    LOGS_DIR = app_paths.logs_dir()
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
STORE_PATH = os.path.join(BASE_DIR, "credentials.json")
LOG_PATH = os.path.join(LOGS_DIR, "accounts.log")      # 跟 accounts.py 同一份日志

# 新版本号用于将来换存储格式
_FORMAT = 2


def _log(line):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def supported():
    """能不能保存凭据。明文存储不依赖任何系统组件，永远可用。"""
    return True


def unsupported_reason():
    return ""


def _read_all():
    """读整个凭据表。文件不存在 = 空表；文件坏了 = 抛异常（不静默当空表）。"""
    if not os.path.exists(STORE_PATH):
        return {"format": _FORMAT, "creds": {}}

    with open(STORE_PATH, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return {"format": _FORMAT, "creds": {}}

    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError("credentials.json 不是合法内容：{}".format(e))

    if not isinstance(data, dict) or not isinstance(data.get("creds"), dict):
        raise RuntimeError("credentials.json 结构不对，应该是一个带 creds 的对象。")
    return data


def _write_all(data):
    plain = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(plain)
    os.replace(tmp, STORE_PATH)
    try:
        os.chmod(STORE_PATH, 0o600)     # 只让当前用户读写
    except Exception:
        pass                            # NTFS 上可能不支持，忽略即可


def _key(account_id):
    return str(account_id or "").strip()


def has(account_id):
    """这个账号有没有存过凭据。读不出来就当没有，不抛异常（列表展示用）。"""
    if not _key(account_id):
        return False
    try:
        return _key(account_id) in _read_all()["creds"]
    except Exception:
        return False


def ids():
    try:
        return sorted(_read_all()["creds"].keys())
    except Exception:
        return []


def get(account_id):
    """取出 {'phone': ..., 'password': ...}；没有则 None。"""
    k = _key(account_id)
    if not k:
        return None
    rec = _read_all()["creds"].get(k)
    if not isinstance(rec, dict):
        return None
    phone = str(rec.get("phone") or "")
    pwd = str(rec.get("password") or "")
    if not phone or not pwd:
        return None
    return {"phone": phone, "password": pwd}


def put(account_id, phone, password):
    """保存一个账号的凭据。返回存进去的账号号。"""
    k = _key(account_id)
    if not k:
        raise ValueError("account_id 不能为空")
    phone = str(phone or "").strip()
    password = str(password or "")
    if not phone:
        raise ValueError("手机号 / 账号不能为空")
    if not password:
        raise ValueError("密码不能为空")

    data = _read_all()
    data["format"] = _FORMAT
    data["creds"][k] = {"phone": phone, "password": password}
    _write_all(data)
    _log("凭据已保存 | account={} phone={}".format(k, mask_phone(phone)))
    return k


def drop(account_id):
    """删掉一个账号的凭据。返回是否真的删掉了。"""
    k = _key(account_id)
    data = _read_all()
    if k not in data["creds"]:
        return False
    del data["creds"][k]
    _write_all(data)
    _log("凭据已删除 | account={}".format(k))
    return True


def mask_phone(phone):
    """日志里用的脱敏：只保留前三后四位，中间打码。"""
    s = str(phone or "")
    if len(s) < 7:
        return "*" * len(s)
    return s[:3] + "****" + s[-4:]


def describe():
    """给界面用的一句话状态。"""
    n = len(ids())
    if n == 0:
        return "还没保存过密码"
    return "已保存 {} 个账号的密码".format(n)
