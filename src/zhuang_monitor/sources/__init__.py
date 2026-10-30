"""数据源连接器包（PRD §4.2）：iFinD MCP / 巨潮 / akshare / baostock / 腾讯。"""

from __future__ import annotations

import socket

# 全市场刷新依赖的外部源没有任何一个在我们的代码里显式设置“读取超时”：
# - baostock 走 TCP socket，卡住时 recv 永远挂起（2026-09-03 实测 40 分钟无进度，重启才恢复）；
# - akshare 内部 requests 同样不带 timeout，网络假死时会永远等待。
# 全局默认给所有新 socket 一个 20 秒读/写超时：正常请求 < 2 秒，20 秒只兜底假死。
# 显式传过 timeout 的调用（cninfo=30 / tencent=10 / iFind=60）不受影响。
socket.setdefaulttimeout(20)
