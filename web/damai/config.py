# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "配置类"
__Created__ = 2023/10/11 18:01
"""


class Config:

    def __init__(self, index_url, login_url, target_url, users, city, dates, prices, if_listen, if_commit_order, max_retries=1000, fast_mode=True, page_load_delay=2, no_seat=True, random_seat=True, dismiss_mask=True, quantity=None, refresh_interval=None, chrome_args=None, calendar_date=None):
        self.index_url = index_url
        self.login_url = login_url
        self.target_url = target_url
        self.users = users
        self.city = city
        self.dates = dates
        # 日历日期（选填，单值，例 "2026-12-26"）。它和 dates 是两件事，2026-10-10 起故意拆开：dates = 详情页「场次」那一栏的
        # 原文（例：12月26日 周六 19:30 下本），calendar_date = 页面月历格子要点哪一天。以前一个框逼一个值干两件事：写得能匹配
        # 场次，日历认不出；写得是纯日期，场次又匹配不上。填了才点日历；留空 = 这页没有月历格子，日历那步整个跳过。
        self.calendar_date = calendar_date
        self.prices = prices
        self.if_listen = if_listen
        self.if_commit_order = if_commit_order
        self.max_retries = max_retries
        self.fast_mode = fast_mode  # 快速模式：减少等待时间和调试输出
        self.page_load_delay = page_load_delay  # 订单确认页加载等待时间（秒）
        # 要买几张。选座页要按它决定「选几个座位」，而选座发生在下单之前，
        # 所以不能用「观演人已经勾上几个」去推。没传就按 users 的人数（见 _h5_ticket_count）。
        self.quantity = quantity
        # 「还没开票」时整页刷新一次等多久（秒）。None = 跟 fast_mode 走（快速 0.3 / 常规 1.0）。这是整套流程里唯一真的把整页
        # 重拉一遍的动作，也是风控最在意的一类（大麦未公开规则，但社区实测「开票前反复刷新」是被盾的头号原因）。所以别把它压到
        # 0.5 秒以下：刷新快不等于更抢得到，反而更容易被移出排队队列；该压的是「已在页面上」的动作（勾人、提交）。实际等待会乘一个 0.8~1.25 的随机抖动，见 Concert._refresh_wait()。
        self.refresh_interval = refresh_interval
        # 多开减负：启动 Chrome 时额外加的参数，由 web/chrome_flags.py 决定。None / 空列表 = 一个都不加，
        # 行为跟改造前完全一致。放在 Config 上而不是让 concert.py 直接 import，是因为 concert.py 是上游
        # 原始文件、改动越小越好（只加一个 for 循环），而「加不加、加什么」是本项目策略，该跟其它策略待在一起。
        self.chrome_args = chrome_args
        # 下面三个对应 PC 端的「扫码弹窗挡按钮 / 要不要选座」
        self.no_seat = no_seat            # 优先点「不选座购票」，跳过选座直接进订单确认页
        self.random_seat = random_seat    # 真落到选座页时，随机点一个座位并确认
        self.dismiss_mask = dismiss_mask  # 自动关掉「手机扫码购买更便捷」这类遮挡浮层
