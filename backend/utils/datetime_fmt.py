"""统一 datetime / 日期字符串格式化。

存在的原因是数据源与模型层的日期类型不统一：pandas 给的是 Timestamp，
缠论模型里可能是 datetime / date / 纯字符串，甚至带时区后缀。
统一走本模块格式化，避免各处在 f-string 里各写一套 strftime，
也保证喂给 LLM 的上下文是干净的 YYYY-MM-DD（不带时分秒，省 token）。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

#: 2026-05-06 / 2026/05/06 / 2026.05.06 / 20260506
_RE_DATE = re.compile(r"^(?P<y>\d{4})[-/.]?(?P<m>\d{2})[-/.]?(?P<d>\d{2})$")


def normalize_date_param(value: object, default_days: Optional[int] = None) -> Optional[str]:
    """把接口入参里的日期归一成 ``'YYYY-MM-DD'``；认不出来则回退。

    Args:
        value: 入参日期，可能是 ``"2026-05-06"`` / ``"2026/05/06"``
            / ``"20260506"`` / ``datetime`` / ``date`` / ``None`` / 任意垃圾串
        default_days: 解析失败时的回退窗口（``today - default_days``）。
            ``None`` 表示回退成 ``None``（= 不限制起点）。

    Returns:
        Optional[str]: ``'YYYY-MM-DD'``；解析失败时按 ``default_days`` 回退

    Note:
        存在的直接原因：``/api/chanlun/{code}`` 的 ``start_date`` 默认值曾误写成
        字面量 ``"start_date"``，原样透传给 baostock 后触发「日期格式不正确」，
        而 baostock 参数非法时返回的是 ``None`` 而不是带 ``error_code`` 的结果对象，
        于是真实原因被盖成 ``AttributeError: 'NoneType' object has no attribute
        'error_code'``。所以入参必须先在这里过一遍，
        **绝不能把认不出来的脏值往下传给数据源**。

        另一个隐含约束：baostock 的 ``query_history_k_data_plus`` 没有 limit 参数，
        只能靠 ``start_date`` 控制窗口。传 ``None`` 会拉全历史（实测某股 2862 根、
        响应 1.3MB、单次 8~46 秒），前端不传 start_date 时这会直接拖垮接口，
        因此调用方应显式传 ``default_days``。
    """
    if value is None:
        return _fallback_date(default_days)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return _fallback_date(default_days)
    # 先剥掉时间部分："2026-05-06 10:00:00" -> "2026-05-06"
    text = text.replace("T", " ").split(" ")[0]
    m = _RE_DATE.match(text)
    if not m:
        return _fallback_date(default_days)
    y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
    # 月/日越界（如 2026-13-45）同样降级，别交给数据源去报看不懂的错
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return _fallback_date(default_days)
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _fallback_date(default_days: Optional[int]) -> Optional[str]:
    """按回退窗口算出起始日；``default_days`` 为 None 时返回 None（不限）。"""
    if default_days is None:
        return None
    return (date.today() - timedelta(days=int(default_days))).isoformat()


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
