"""轻量 AI 诊股会话（进程内内存）。"""
from __future__ import annotations

import threading
import time


class ChatSession:
    """一个 AI 诊股会话：消息列表 + 最近活跃时间，自带 RLock 保证并发安全。
    """
    def __init__(self, max_turns: int = 10):
        """max_turns 是保留的对话轮数，内部按「一轮两条」折算成 max_turns*2 条上限。
        """
        self.messages: list[dict] = []
        self.max_turns = max_turns
        self.updated_at = time.time()
        self._lock = threading.RLock()

    def add(self, role: str, content: str) -> None:
        """追加一条消息并刷新活跃时间；超过条数上限时从头部丢弃最旧的。
        """
        with self._lock:
            self.messages.append({"role": role, "content": content})
            self.updated_at = time.time()
            if len(self.messages) > self.max_turns * 2:
                self.messages = self.messages[-self.max_turns * 2 :]

    def history(self) -> list[dict]:
        """返回消息副本并刷新活跃时间（读取也算活跃，不会被 TTL 清掉）。
        """
        with self._lock:
            self.updated_at = time.time()
            return list(self.messages)


_chat_sessions: dict[str, ChatSession] = {}
_session_lock = threading.RLock()
_SESSION_TTL_SECONDS = 60 * 60 * 6
_MAX_SESSIONS = 200


def _prune_sessions(now: float) -> None:
    """清掉 TTL 过期的会话；若仍超过 _MAX_SESSIONS，按最久未活跃再淘汰到上限为止。
    """
    expired = [
        sid for sid, session in _chat_sessions.items()
        if now - session.updated_at > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        _chat_sessions.pop(sid, None)

    overflow = len(_chat_sessions) - _MAX_SESSIONS
    if overflow <= 0:
        return

    oldest = sorted(
        _chat_sessions.items(),
        key=lambda item: item[1].updated_at,
    )[:overflow]
    for sid, _session in oldest:
        _chat_sessions.pop(sid, None)


def chat_session_stats() -> dict:
    """返回当前会话数与上限，供健康检查/诊断接口观测。
    """
    with _session_lock:
        return {"active_sessions": len(_chat_sessions), "max_sessions": _MAX_SESSIONS}


def get_or_create_session(session_id: str) -> ChatSession:
    """取（并刷新活跃时间）或新建会话；session_id 截断到 120 字符，空则归为 "default"。
    """
    now = time.time()
    key = session_id.strip()[:120] or "default"
    with _session_lock:
        _prune_sessions(now)
        if key not in _chat_sessions:
            _chat_sessions[key] = ChatSession(max_turns=10)
        _chat_sessions[key].updated_at = now
        return _chat_sessions[key]
