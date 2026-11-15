# 大麦抢票（电脑端）

基于 Selenium 的大麦网抢票工具。多账号并行、图形向导、登录态管理、卡点重试，
付款永远留给人来做。

## 目录

| 目录 | 内容 |
|------|------|
| `web/` | 抢票本体：`damai/`（核心流程）、`account_runner.py`（多账号调度）、`damai_wizard.py`（图形向导）、`task_manager.py`、`notify.py` |
| `tools/` | 回归与自检脚本，每条「不该出现什么」都写成断言 |
| `packaging/` | 打包 exe、产物自检 |
| `doc/` | 使用说明与流程图 |
| `tests/` | pytest |

## 环境

- Python 3.9+
- Chrome 最新版（驱动按需自动下载）
- Windows / macOS / Linux

## 安装

```bash
git clone <repository-url>
cd damai-ticket-purchase
pip install -r web/damai/requirements.txt
```

或用 Poetry：

```bash
poetry install
```

## 运行

进 `web/` 目录，按需要选一个入口：

```bash
cd web
```

- `wizard.bat` / `python damai_wizard.py`：图形向导，填场次和票档，多账号一屏管完
- `accounts.bat` / `python account_runner.py`：命令行多账号
- `python damai/damai.py`：单个账号直接跑（先用 `python account_runner.py login` 登一次）

**第一次用先登录一次**，登录态保存在该账号自己的浏览器 Profile 里，之后重跑不用再登。
想免手输就把手机号和密码填进向导的「手机号 / 密码」一栏，程序存到 `web/credentials.json`
（明文，不写进 `accounts.json`），下次自动填表。这个文件不要外传。

## 配置

`web/damai/config.json`：

```json
{
  "index_url": "https://www.damai.cn/",
  "login_url": "https://passport.damai.cn/login",
  "target_url": "https://detail.damai.cn/item.htm?id=xxx",
  "users": [],
  "city": "北京",
  "dates": ["2026-12-05"],
  "prices": ["380"],
  "if_commit_order": false
}
```

| 字段 | 说明 |
|------|------|
| `target_url` | 目标演出详情页 |
| `users` | 观演人，**留空**自己填；`quantity` 不能超过人数 |
| `city` | 巡演多城市时用来选城市 |
| `dates` | 场次日期，可写多个 |
| `prices` | 票档，按优先级排；写票价数字或直接粘贴票价页原文都能认 |
| `if_commit_order` | `false` = 停在提交前，付款始终手动 |

程序第一次启动会自己生成缺的配置文件，不用手动造。

## 已知限制

- 平台有风控。刷新节奏调太快会被拦，当前取值是踩过坑之后定的（整页刷新 3.5 秒上下），
  不建议为了「快一点」去压它。
- 验证码、滑块、短信验证都要人来过，脚本遇到会停下等人。
- 付款环节不碰。

## 免责

仅供学习研究。使用前请自行确认符合大麦网的用户协议与当地法律法规。
