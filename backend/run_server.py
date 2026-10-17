"""
启动入口：清掉代理环境变量后，用子进程拉起 uvicorn（等价于 `python -m uvicorn main:app`）。

要点：
- 必须先清空 HTTP(S)_PROXY 并设 NO_PROXY=*，否则本机代理会挡掉行情源的请求
- 默认端口 8010（Windows 上 8000 常被占用），可用环境变量 PORT 覆盖
- 日志走 utils.log.setup_logging：控制台 + 滚动文件（LOG_FILE，默认 backend.log），
  级别取 LOG_LEVEL；uvicorn 子进程里由 main.py 再装配一次
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent

import config
from utils.log import setup_logging

setup_logging(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    log_file=config.LOG_FILE or None,
)

log = logging.getLogger(__name__)

for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ[k] = ""
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
os.environ.setdefault("PYTHONUNBUFFERED", "1")
log.info("代理环境变量已清空：HTTP(S)_PROXY 置空，NO_PROXY=*")

_port = os.environ.get("PORT", "8010").strip() or "8010"
log.info("启动 uvicorn：0.0.0.0:%s（等价 python -m uvicorn main:app，cwd=%s）", _port, _backend)

# 使用 CLI 子进程启动：与手动执行 `python -m uvicorn main:app` 一致。
# 默认端口 8010：部分 Windows 环境 8000 已被占用（甚至表现为 netstat 幽灵占用），
# 会导致本机访问异常；生产环境可设置环境变量 PORT=8000。
_code = subprocess.call(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        _port,
    ],
    cwd=str(_backend),
    env=os.environ.copy(),
)

if _code != 0:
    log.error("uvicorn 退出码 %s，请检查端口 %s 是否被占用或 PORT 配置有误", _code, _port)
else:
    log.info("uvicorn 已退出")

raise SystemExit(_code)
