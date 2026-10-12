"""
自选、笔记、系统设置的 JSON 持久化。
单进程内用 RLock 保护读改写；写入使用临时文件 + os.replace 尽量原子化。
多 worker / 多机部署时各进程状态不一致——仅适合单机或文档化约束下的部署。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

_WATCHLIST_FILE = _BACKEND_ROOT / "watchlist.json"
_COMMENTS_FILE = _BACKEND_ROOT / "comments.json"
_SETTINGS_FILE = _BACKEND_ROOT / "settings.json"

_watchlist_lock = threading.RLock()
_comments_lock = threading.RLock()
_settings_lock = threading.RLock()

_watchlist: dict[str, str] = {}
_comments: dict[str, list[dict]] = {}


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# 港股代码统一 5 位。历史上前端按 6 位补零（'700' -> '000700'）写入，
# 而读取时又 zfill(6)，写读口径不一致会产出富途不认的代码并让同一只股票撞键。
_WATCHLIST_DIGITS = 5


def _normalize_watchlist_code(code: object) -> str:
    """自选股代码归一化——写入与读取必须走这里，保证磁盘上的键唯一且与富途一致。

    '700' / '00700' / '000700' / 'HK.00700'  ->  '00700'
    """
    s = str(code or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return s
    if len(digits) > _WATCHLIST_DIGITS:
        stripped = digits.lstrip("0")
        if stripped:
            digits = stripped
    return digits.zfill(_WATCHLIST_DIGITS)


def _load_watchlist_from_disk() -> dict[str, str]:
    try:
        if _WATCHLIST_FILE.exists():
            data = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
            out: dict[str, str] = {}
            for k, v in data.items():
                key = _normalize_watchlist_code(k)
                if not key:
                    continue
                old = out.get(key)
                # 归一化后可能撞键（000700 与 00700 是同一只），保留更早的加入时间
                if old is None or str(v) < old:
                    out[key] = str(v)
            return out
    except Exception as e:
        log.warning("自选股加载失败: %s", e)
    return {}


def _load_comments_from_disk() -> dict[str, list[dict]]:
    try:
        if _COMMENTS_FILE.exists():
            return json.loads(_COMMENTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("评论加载失败: %s", e)
    return {}


def init_stores() -> None:
    """模块导入后初始化内存缓存（main 启动时也可再次调用）。"""
    global _watchlist, _comments
    with _watchlist_lock:
        _watchlist = _load_watchlist_from_disk()
        _migrate_watchlist_file()
    with _comments_lock:
        _comments = _load_comments_from_disk()


def _migrate_watchlist_file() -> None:
    """磁盘上若还留着旧口径（6 位补零 / 重复键）的代码，就地归一化并写回。"""
    if not _WATCHLIST_FILE.exists():
        return
    try:
        raw = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    dirty = len(raw) != len(_watchlist) or any(
        _normalize_watchlist_code(k) != str(k) for k in raw
    )
    if dirty:
        log.warning("自选股代码格式已归一化: %s -> %s", sorted(raw), sorted(_watchlist))
        _atomic_write_json(_WATCHLIST_FILE, _watchlist)


def get_watchlist_map() -> dict[str, str]:
    with _watchlist_lock:
        return dict(_watchlist)


def watchlist_add(sym: str) -> tuple[str, bool]:
    """返回 (iso_time, was_new)."""
    from datetime import datetime

    sym = _normalize_watchlist_code(sym)
    with _watchlist_lock:
        if sym in _watchlist:
            return _watchlist[sym], False
        _watchlist[sym] = datetime.now().isoformat()
        _atomic_write_json(_WATCHLIST_FILE, _watchlist)
        return _watchlist[sym], True


def watchlist_remove(sym: str) -> bool:
    sym = _normalize_watchlist_code(sym)
    with _watchlist_lock:
        if sym not in _watchlist:
            return False
        del _watchlist[sym]
        _atomic_write_json(_WATCHLIST_FILE, _watchlist)
        return True


def comments_get(sym: str) -> list[dict]:
    with _comments_lock:
        return list(_comments.get(sym, []))


def comments_add(sym: str, comment: dict) -> None:
    with _comments_lock:
        if sym not in _comments:
            _comments[sym] = []
        _comments[sym].append(comment)
        _atomic_write_json(_COMMENTS_FILE, _comments)


def comments_update(sym: str, comment_id: str, new_content: str, updated_iso: str) -> bool:
    with _comments_lock:
        items = _comments.get(sym, [])
        for c in items:
            if c.get("id") == comment_id:
                c["content"] = new_content
                c["updatedAt"] = updated_iso
                _atomic_write_json(_COMMENTS_FILE, _comments)
                return True
        return False


def comments_delete(sym: str, comment_id: str) -> bool:
    with _comments_lock:
        items = _comments.get(sym, [])
        n = len(items)
        items = [c for c in items if c.get("id") != comment_id]
        if len(items) == n:
            return False
        _comments[sym] = items
        _atomic_write_json(_COMMENTS_FILE, _comments)
        return True


def load_settings() -> dict:
    with _settings_lock:
        try:
            if _SETTINGS_FILE.exists():
                return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("设置加载失败: %s", e)
        return {"ai_model": "deepseek"}


def save_settings(s: dict) -> None:
    with _settings_lock:
        _atomic_write_json(_SETTINGS_FILE, s)


def apply_startup_ai_model() -> None:
    from ai.llm_client import set_llm_model

    try:
        settings = load_settings()
        model = settings.get("ai_model", "deepseek")
        set_llm_model(model)
        log.info("AI 模型已恢复: %s", model)
    except Exception:
        log.debug("启动时恢复 AI 模型失败", exc_info=True)


init_stores()
