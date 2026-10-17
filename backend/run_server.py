"""
启动入口：清掉代理环境变量后，用子进程拉起 uvicorn（等价于 `python -m uvicorn main:app`）。

要点：
- 必须先清空 HTTP(S)_PROXY 并设 NO_PROXY=*，否则本机代理会挡掉行情源的请求
- 默认端口 8010（Windows 上 8000 常被占用），可用环境变量 PORT 覆盖
"""
import os
import subprocess
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent

for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ[k] = ""
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

os.environ.setdefault("PYTHONUNBUFFERED", "1")

_port = os.environ.get("PORT", "8010").strip() or "8010"
print(f"[RUN] Starting uvicorn on 0.0.0.0:{_port} (python -m uvicorn)...", flush=True)

# 使用 CLI 子进程启动：与手动执行 `python -m uvicorn main:app` 一致。
# 默认端口 8010：部分 Windows 环境 8000 已被占用（甚至表现为 netstat 幽灵占用），
# 会导致本机访问异常；生产环境可设置环境变量 PORT=8000。
raise SystemExit(
    subprocess.call(
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
)
