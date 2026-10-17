"""
Monkey-patch requests HTTPAdapter.send for finance hosts: no proxy, optional TLS relax.
Must be imported before any code that triggers requests to eastmoney / tencent quote, etc.
"""
from __future__ import annotations

import logging

import requests as _req_mod

from config import FINANCE_TLS_RELAXED

log = logging.getLogger(__name__)

_NO_PROXY = {"http": None, "https": None}
_EM_HOSTS = ("eastmoney", "qt.gtimg", "sinajs", "ifzq", "10jqka")
_orig_send = _req_mod.adapters.HTTPAdapter.send


def _patched_send(adapter, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
    """requests 的猴子补丁：给行情域名强制不走代理、默认补 12s 超时，按要求可关掉 TLS 校验。
    """
    url = getattr(request, "url", "") or ""
    if timeout is None and url:
        timeout = 12.0
    if proxies is None and url and any(h in url for h in _EM_HOSTS):
        proxies = _NO_PROXY
        if FINANCE_TLS_RELAXED:
            verify = False
    return _orig_send(adapter, request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)


_req_mod.adapters.HTTPAdapter.send = _patched_send
log.info(
    "requests patch: timeout=12s, no proxy for finance hosts; FINANCE_TLS_RELAXED=%s",
    FINANCE_TLS_RELAXED,
)
