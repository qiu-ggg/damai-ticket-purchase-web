# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "大麦抢票脚本"
__Created__ = 2023/10/10 17:12
"""
import json
import os
import sys
import time

from concert import Concert
from config import Config


def check_config_file():
    """检查配置文件是否存在和有效"""
    config_file = 'config.json'

    if not os.path.exists(config_file):
        print("=" * 50)
        print("✗ 错误: 未找到配置文件 config.json")
        print("=" * 50)
        print(f"\n请先创建 {config_file} 配置文件，包含以下内容:")
        print("""
{
    "index_url": "https://www.damai.cn/",
    "login_url": "https://passport.damai.cn/login",
    "target_url": "目标演出页面URL",
    "users": ["观众1姓名", "观众2姓名"],
    "city": "城市名称（可选）",
    "dates": ["场次日期1", "场次日期2"],
    "calendar_date": "",
    "prices": ["188", "288"],
    "if_listen": true,
    "if_commit_order": true,
    "max_retries": 1000
}
        """)
        print("=" * 50)
        sys.exit(1)

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        required_fields = ['index_url', 'login_url', 'target_url', 'users']
        missing_fields = [field for field in required_fields if field not in config]

        if missing_fields:
            print(f"✗ 配置文件缺少必需字段: {', '.join(missing_fields)}")
            sys.exit(1)

        if not config['users']:
            print("✗ 配置文件中 users 字段不能为空")
            sys.exit(1)

        print(f"✓ 配置文件加载成功")
        print(f"  - 目标URL: {config['target_url']}")
        print(f"  - 观众人数: {len(config['users'])} 人")
        print(f"  - 最大重试次数: {config.get('max_retries', 1000)} 次")
        print()

    except json.JSONDecodeError as e:
        print(f"✗ 配置文件格式错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ 读取配置文件失败: {e}")
        sys.exit(1)


def load_config():
    with open('config.json', 'r', encoding='utf-8') as config_file:
        config = json.load(config_file)
    cfg = Config(
        config['index_url'],
        config['login_url'],
        config['target_url'],
        config['users'],
        config.get('city'),
        config.get('dates'),
        config.get('prices'),
        config['if_listen'],
        config['if_commit_order'],
        config.get('max_retries', 1000),
        config.get('fast_mode', True),  # 默认启用快速模式
        config.get('page_load_delay', 2)  # 默认2秒
    )
    # PC 端下单相关开关：不写进 config.json 也按「开」处理
    cfg.no_seat = config.get('no_seat', True)
    cfg.random_seat = config.get('random_seat', True)
    cfg.dismiss_mask = config.get('dismiss_mask', True)
    # 日历日期（选填）：页面日期是月历格子时才填（例 2026-12-26），留空 = 没有日历格、跳过这一步。与 dates（场次）是两件事。
    cfg.calendar_date = (str(config.get('calendar_date') or '').strip() or None)
    return cfg


def grab():
    print("\n" + "=" * 50)
    print("大麦网抢票脚本启动")
    print("=" * 50)
    print()

    check_config_file()

    config = load_config()

    # 初始化（会自动检查 Chrome 环境）
    con = Concert(config)
    try:
        con.enter_concert()
        con.choose_ticket()
        print("\n✓ 抢票流程完成，页面将保持5分钟...")
        time.sleep(300)
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断程序")
        con.finish()
    except Exception as e:
        print(f"\n✗ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        con.finish()


if __name__ == "__main__":
    grab()
