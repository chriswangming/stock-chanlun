"""统一 datetime / 日期字符串格式化。

存在的原因是数据源与模型层的日期类型不统一：pandas 给的是 Timestamp，
缠论模型里可能是 datetime / date / 纯字符串，甚至带时区后缀。
统一走本模块格式化，避免各处在 f-string 里各写一套 strftime，
也保证喂给 LLM 的上下文是干净的 YYYY-MM-DD（不带时分秒，省 token）。
"""
from __future__ import annotations

from datetime import date, datetime


def format_date_short(value: object) -> str:
    """把任意日期类型/字符串格式化为 YYYY-MM-DD，供 LLM 上下文与日志使用。

    Args:
        value: datetime / date / 字符串 / None / 其它对象。

    Returns:
        "YYYY-MM-DD"；无法解析或为空时返回空串（不抛异常）。

    Note:
        datetime 是 date 的子类，所以必须先判 datetime 再判 date，
        否则 datetime 会被 isoformat() 带上 "T00:00:00" 的时间部分。
    """
    # 兜底：None / 空串都返回空串，让调用方可以直接拼接而不必判空
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    # 字符串兜底：兼容 "2024-01-02 10:00:00"、"2024-01-02T10:00:00+08:00"
    # 等写法，先统一分隔符再取第一段并截 10 位，丢掉时间与时区尾巴
    return text.replace("T", " ").split(" ")[0][:10]
