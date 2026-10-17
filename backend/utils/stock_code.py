"""
股票代码归一化：把前端 / 命令行传进来的各种写法统一成各数据源要的格式。

同一个标的在项目里有好几种写法在流转::

    002384        前端搜索框、自选股、URL path 里最常见（裸 6 位）
    2384          老数据 / Excel 导出，省掉了前导零
    sz.002384     baostock（证券宝）格式：小写市场 + 点 + 代码
    sz002384      部分行情源 / 爬虫返回
    002384.SZ     新浪 / 雪球 / 聚宽风格：代码在前，市场在后缀

``chan`` 里的 ``CBaoStock`` **原样透传** ``self.code`` 给 baostock，不做任何归一化。
所以只要传进去的不是 9 位格式，就会在 ``query_stock_basic`` 阶段报::

    CChanException: baostock 查询002384基本信息失败: 股票代码应为9位，请检查。格式示例：sh.600000

本模块就是这层「入口适配」，在 **路由层** 把用户传的任意写法转成 baostock 格式，
归一化因此统一收在本模块与路由层完成，不写进 ``chan`` 的数据源内部。

设计取舍:

    - **幂等**：已经是 baostock 格式的串进去再出来不变，可以重复调用；
    - **宽容**：认不出形态时按「纯数字左补零」兜底 + 按代码段猜市场，不抛异常；
    - **只有一个函数需要对外暴露**：:func:`to_baostock_code`。

市场判定规则见 :func:`detect_market`。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

#: 项目支持的市场标识（小写）
MARKETS: Tuple[str, ...] = ("sh", "sz", "bj")

#: baostock 不支持北交所（实测 bj.430047 返回 error_code='0' 但结果为空），
#: 见 to_baostock_code 的异常说明。
_BAOSTOCK_MARKETS: Tuple[str, ...] = ("sh", "sz")

#: 形态1：市场在前 —— sh600000 / sh.600000 / SH_600000
_RE_MKT_PREFIX = re.compile(r"^(?P<mkt>sh|sz|bj)[.\-_\s]?(?P<num>\d{1,6})$", re.I)

#: 形态2：市场在后 —— 600000.sh / 600000-SH
_RE_MKT_SUFFIX = re.compile(r"^(?P<num>\d{1,6})[.\-_\s]?(?P<mkt>sh|sz|bj)$", re.I)

#: 形态3：纯数字（可能不足 6 位）
_RE_PURE_NUM = re.compile(r"^(?P<num>\d{1,6})$")


def split_code(code: str) -> Tuple[str, Optional[str]]:
    """
    把任意写法的代码拆成 ``(纯 6 位数字, 市场标识或 None)``。

    Args:
        code: 用户输入，如 ``"002384"`` / ``"sz.002384"`` / ``"sz002384"``
            / ``"002384.SZ"`` / ``"2384"``

    Returns:
        Tuple[str, Optional[str]]: ``(symbol, market)``。
        ``symbol`` 恒为 6 位数字串；``market`` 为 ``"sh"/"sz"/"bj"``，
        输入里没带市场信息时为 ``None``。

    Note:
        大小写与 ``.``/``-``/``_``/空格 分隔符都兼容；不足 6 位时左补零
        （``"2384"`` -> ``"002384"``）。认不出任何已知形态时原样返回
        ``(code, None)``，由调用方决定怎么处理 —— 这里刻意不抛异常，
        因为路由层还要拿原始值去打日志和拼缓存键。
    """
    s = str(code or "").strip()

    m = _RE_MKT_PREFIX.match(s)
    if m:
        return m.group("num").zfill(6), m.group("mkt").lower()

    m = _RE_MKT_SUFFIX.match(s)
    if m:
        return m.group("num").zfill(6), m.group("mkt").lower()

    m = _RE_PURE_NUM.match(s)
    if m:
        return m.group("num").zfill(6), None

    # 认不出形态：返回 (原串, None)。刻意不抛异常，让调用方决定降级方式；
    # to_baostock_code() 会在这里兜住并给出「无法识别」的明确报错。
    return s, None


def is_valid_symbol(symbol: str) -> bool:
    """
    判断是否为合法的 6 位 A 股数字代码。

    Args:
        symbol: :func:`split_code` 产出的 symbol

    Returns:
        bool: 恰好 6 位数字时为 True

    Note:
        用于拦截 ``"abc"`` / ``"sz.abc"`` 这类脏输入：``split_code`` 对它们
        会原样返回，若不二次校验就会拼成 ``"sz.abc"`` 打到数据源，
        报出来的还是「股票代码应为9位」这种看不出根因的错。
    """
    return bool(symbol) and len(symbol) == 6 and symbol.isdigit()


def detect_market(symbol: str, default: str = "sz") -> str:
    """
    按 A 股代码段判定所属市场。

    Args:
        symbol: 纯 6 位数字代码
        default: 命中不了任何规则时的兜底市场

    Returns:
        str: ``"sh"``（上海）/ ``"sz"``（深圳）/ ``"bj"``（北交所）

    Note:
        规则按「先特例后兜底」排列，**顺序有意义**::

            60 / 68          沪市主板 / 科创板
            9                沪市 B 股（900xxx）
            5                沪市基金 / ETF（50xxxx、51xxxx、58xxxx）
            11               沪市可转债（11xxxx）
            8 / 4            北交所（43/83/87/92xxxx）
            其余（00/30/...）  深市

        ``000001`` 是有名的歧义码：平安银行在深市、上证综指在沪市。
        项目里缠论分析的对象是**个股**，所以这里一律判深市
        （与 ``services/akshare_service.normalize_stock_code`` 口径一致）。
        要分析上证指数请显式传 ``sh.000001``。
    """
    s = str(symbol or "").strip()
    if s.startswith(("60", "68", "9", "5", "11")):
        return "sh"
    if s.startswith(("8", "4")):
        return "bj"
    return default if default in MARKETS else "sz"


def to_baostock_code(code: str) -> str:
    """
    把任意写法的代码转成 **baostock 格式** ``"市场.代码"``（如 ``"sz.002384"``）。

    Args:
        code: 股票代码，支持裸 6 位 / ``sz.002384`` / ``sz002384`` / ``002384.SZ``
            / 不足 6 位的数字串

    Returns:
        str: baostock 格式代码，如 ``"sz.002384"`` / ``"sh.600519"``

    Raises:
        ValueError: 输入为空，或判定为北交所（baostock 数据源不收录北交所标的）

    Note:
        - **幂等**：再调一次结果不变，所以调用方可以无条件对每个入参都过一遍；
        - 输入自带市场标识时**尊重原值**，不按代码段重判 —— 否则
          ``sh.000001``（上证指数）会被误改成 ``sz.000001``（平安银行）；
        - 北交所实测：``bs.query_stock_basic(code="bj.430047")`` 返回
          ``error_code='0'`` 但结果为空，再往下走会变成难以定位的空数据异常，
          所以这里直接拦掉并给出明确提示。

    Examples:
        >>> to_baostock_code("002384")
        'sz.002384'
        >>> to_baostock_code("600519")
        'sh.600519'
        >>> to_baostock_code("sz.000001")
        'sz.000001'
        >>> to_baostock_code("sh.000001")
        'sh.000001'
    """
    symbol, market = split_code(code)
    if not symbol:
        raise ValueError("股票代码不能为空")
    if not is_valid_symbol(symbol):
        raise ValueError(f"无法识别的股票代码（应为 6 位数字）: {code!r}")
    # 带市场标识就原样用；只有纯数字才按代码段猜
    mkt = market or detect_market(symbol)
    if mkt not in _BAOSTOCK_MARKETS:
        raise ValueError(f"baostock 数据源暂不支持{mkt.upper()}市场（北交所标的请换用其它数据源）: {code}")
    return f"{mkt}.{symbol}"
