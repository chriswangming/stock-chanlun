"""全局日志装配：给根 logger 挂上「控制台 + 按大小滚动的文件」两路输出。

用法（整个进程只生效一次，重复调用会被忽略）：

    from utils.log import setup_logging
    setup_logging()

各业务模块不要在函数里直接用 logging.info/warning 打日志，统一在模块顶部取
本模块自己的 logger，日志格式里的 filename 就能直接看出来源文件：

    import logging
    log = logging.getLogger(__name__)
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 已装配过就不再重复挂 handler（--reload 或多次 import 都会走到这里）
_CONFIGURED = False


def setup_logging(
    level=logging.DEBUG,
    log_file="backend.log",
    max_bytes=10 * 1024 * 1024,
    backup_count=5
):
    """
    配置根 logger：控制台 + 滚动文件。整个项目只调用一次。

    Args:
        level: 根 logger 级别，默认 INFO
        log_file: 日志文件路径，默认 app.log；传 None 则只输出到控制台
        max_bytes: 单个日志文件上限，默认 10MB
        backup_count: 保留的历史文件个数，默认 5

    Returns:
        bool: 本次是否真的执行了配置；已配置过则返回 False
    """
    global _CONFIGURED
    if _CONFIGURED:
        return False
    _CONFIGURED = True

    handlers = []

    # 控制台
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    handlers.append(stream_handler)

    # 文件（按大小滚动）
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True   # Python 3.8+，防止重复配置
    )

    # 可选：第三方库降噪（这些库在 INFO 下会刷屏）
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("baostock").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return True
