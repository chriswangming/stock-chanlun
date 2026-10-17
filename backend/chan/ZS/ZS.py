"""
中枢（ZS）模块：缠论链路中「线段 -> 中枢 -> 买卖点」的中间一环。

链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段
-> 【本模块：中枢（笔中枢 / 线段中枢）】 -> 买卖点

要点：
- 中枢就是若干（至少三根，开启一笔中枢时一根）连续走势的价格区间的重叠部分 [low, high]：
  low = 各元素低点的最大值（"最高低点"），high = 各元素高点的最小值（"最低高点"）。
- low <= high 说明它们确实有重叠，构造才算成立（见 ZSList.try_construct_zs）。
- 本模块负责单个中枢自身的计算：区间维护（update_zs_range / update_zs_end / try_add_to_end）、
  延伸与合并（try_add_to_end / combine / do_combine）、
  以及为买卖点提供背驰判断（is_divergence）与进出中枢的笔（bi_in / bi_out）。
- "哪些笔该构成中枢"由 ZS/ZSList.py 决定。
"""
from typing import Generic, List, Optional, TypeVar

from ..Bi.Bi import CBi
from ..BuySellPoint.BSPointConfig import CPointConfig
from ..Common.ChanException import CChanException, ErrCode
from ..Common.func_util import has_overlap
from ..KLine.KLine_Unit import CKLine_Unit
from ..Seg.Seg import CSeg

LINE_TYPE = TypeVar('LINE_TYPE', CBi, "CSeg")


class CZS(Generic[LINE_TYPE]):
    """
    一个中枢。

    内层元素可以是笔（构成笔中枢），也可以是线段（构成线段中枢），
    由泛型 LINE_TYPE 决定，两个层级共用同一个实现。

    核心状态：
        begin/end          ：中枢的首尾，永远记在原始K线（KLU）粒度上
        begin_bi/end_bi    ：首尾两根内层元素（笔或线段）
        low/high/mid       ：中枢区间与中位价
        peak_low/peak_high ：涉及到的所有元素的实际极值（比 low/high 更宽）
        bi_in/bi_out/bi_lst：进出中枢的笔与中枢内部元素，由 KLine_List.update_zs_in_seg 回填
        sub_zs_lst         ：被合并进来的子中枢
    """

    def __init__(self, lst: Optional[List[LINE_TYPE]], is_sure=True):
        # begin/end：永远指向 klu
        # low/high: 中枢的范围
        # peak_low/peak_high: 中枢所涉及到的笔的最大值，最小值
        """
        要点：
        - lst 为 None 时是"空壳模式"：只留 is_sure 与子中枢列表，其余字段稍后由 init_from_zs 填
          （make_copy / 合并子中枢时走这条路径）。
        - is_sure 表示是否由确认笔构成（含未确认笔的中枢也只是未确认中枢）。
        - 中枢区间先按参与构造的元素算出来，极值区间再逐个 update_zs_end 撑开。
        """
        self.__is_sure = is_sure
        self.__sub_zs_lst: List[CZS] = []

# lst 为 None 时是「空壳模式」：只留 is_sure 与子中枢列表，其余字段稍后由 init_from_zs 填
        if lst is None:
            return

        # 中枢首尾都记在「原始K线」粒度上，方便和线段/笔的 KLU 直接比较
        self.__begin: CKLine_Unit = lst[0].get_begin_klu()
        self.__begin_bi: LINE_TYPE = lst[0]  # 中枢内部的笔

        # self.__low = None
        # self.__high = None
        # self.__mid = None
        # 先按参与构造的元素算出中枢区间 [low, high] 与中位价
        self.update_zs_range(lst)

        # self.__end: CKLine_Unit = None
        # self.__end_bi: CBi = None  # 中枢内部的笔
        # 极值区间初始化成「还没见过任何元素」，下面逐个 update_zs_end 时会撑开
        self.__peak_high = float("-inf")
        self.__peak_low = float("inf")
        for item in lst:
            self.update_zs_end(item)

        self.__bi_in: Optional[LINE_TYPE] = None  # 进中枢那一笔
        self.__bi_out: Optional[LINE_TYPE] = None  # 出中枢那一笔

        self.__bi_lst: List[LINE_TYPE] = []  # begin_bi~end_bi之间的笔，在update_zs_in_seg函数中更新

    def clean_cache(self):
        """清空缓存：区间/端点/进出笔被改动后都必须调一次。"""
        self._memoize_cache = {}

    @property
    def is_sure(self):
        """是否由确认笔构成（False 表示含未确认笔，随时可能被重算）。"""
        return self.__is_sure

    @property
    def sub_zs_lst(self):
        """被合并进本中枢的那些小中枢（combine 时逐个塞进来）。"""
        return self.__sub_zs_lst

    @property
    def begin(self):
        """中枢的起点原始K线。"""
        return self.__begin

    @property
    def begin_bi(self):
        """中枢内部的第一根元素（笔或线段）。"""
        return self.__begin_bi

    @property
    def low(self):
        """中枢区间下沿 = 各元素低点的最大值（「最高低点」）。"""
        return self.__low

    @property
    def high(self):
        """中枢区间上沿 = 各元素高点的最小值（「最低高点」）。"""
        return self.__high

    @property
    def mid(self):
        """中枢中位价 (low+high)/2。"""
        return self.__mid

    @property
    def end(self):
        """中枢的终点原始K线。"""
        return self.__end

    @property
    def end_bi(self):
        """中枢内部的最后一根元素（笔或线段）。"""
        return self.__end_bi

    @property
    def peak_high(self):
        """中枢涉及到的所有元素里最高的那个高点（比 high 更宽）。"""
        return self.__peak_high

    @property
    def peak_low(self):
        """中枢涉及到的所有元素里最低的那个低点（比 low 更低）。"""
        return self.__peak_low

    @property
    def bi_in(self):
        """进入中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。"""
        return self.__bi_in

    @property
    def bi_out(self):
        """离开中枢的那一笔（中枢还在行情尾部时为 None）。"""
        return self.__bi_out

    @property
    def bi_lst(self):
        """中枢内部的元素列表（由 KLine_List.update_zs_in_seg 回填）。"""
        return self.__bi_lst

    def update_zs_range(self, lst):
        """
        用参与构造的元素重算中枢区间。

        要点：
        - 取法就是"取交集"：下沿取各元素低点里最高的那个，上沿取各元素高点里最低的那个。
        - 若算出来 low > high，说明这批元素其实没有重叠，中枢不成立（由调用方负责判断）。
        """
        self.__low: float = max(bi._low() for bi in lst)
        self.__high: float = min(bi._high() for bi in lst)
        # 中位价：粗略代表中枢的「重心」，买卖点判定常用它做参考
        self.__mid: float = (self.__low + self.__high) / 2  # 中枢的中点
        self.clean_cache()

    def is_one_bi_zs(self):
        """
        是否是"一笔中枢"（整个中枢其实只包含一根笔，判据是 begin_bi 与 end_bi 同一根）。

        要点：
        - 一笔中枢由单根笔的区间构成，很多地方（如买卖点判定、中枢合并）都要求把它排除掉。
        """
        assert self.end_bi is not None
        return self.begin_bi.idx == self.end_bi.idx

    def update_zs_end(self, item):
        """
        把中枢的尾部推进到 item，并把极值区间（peak_low/peak_high）撑开。

        要点：
        - end/end_bi 每次都是"覆盖"而不是"比较取最值"：中枢按时间推进，
          最新并入的元素自然就是尾部。
        """
        self.__end: CKLine_Unit = item.get_end_klu()
        self.__end_bi: CBi = item
        if item._low() < self.peak_low:
            self.__peak_low = item._low()
        if item._high() > self.peak_high:
            self.__peak_high = item._high()
        self.clean_cache()

    def __str__(self):
        """调试输出：`首元素idx->尾元素idx(子中枢1,子中枢2...)`。"""
        _str = f"{self.begin_bi.idx}->{self.end_bi.idx}"
        if _str2 := ",".join([str(sub_zs) for sub_zs in self.sub_zs_lst]):
            return f"{_str}({_str2})"
        else:
            return _str

    def combine(self, zs2: 'CZS', combine_mode) -> bool:
        """
        尝试把时间上更晚的中枢 zs2 合并进本中枢。

        要点：
        - 前提：zs2 不是一笔中枢，且两个中枢落在同一条线段里（begin_bi.seg_idx 相同）。
        - combine_mode='zs' 按中枢区间重叠来判（equal=True，端点相接也算重叠）；
          'peak' 按两个中枢涉及的极值区间重叠来判（更宽松）；
          其它取值抛 CChanException(ErrCode.PARA_ERROR)。
        """
        if zs2.is_one_bi_zs():
            return False
        if self.begin_bi.seg_idx != zs2.begin_bi.seg_idx:
            return False
        if combine_mode == 'zs':
            if not has_overlap(self.low, self.high, zs2.low, zs2.high, equal=True):
                return False
            self.do_combine(zs2)
            return True
        elif combine_mode == 'peak':
            if has_overlap(self.peak_low, self.peak_high, zs2.peak_low, zs2.peak_high):
                self.do_combine(zs2)
                return True
            else:
                return False
        else:
            raise CChanException(f"{combine_mode} is unsupport zs conbine mode", ErrCode.PARA_ERROR)

    def do_combine(self, zs2: 'CZS'):
        """
        执行合并：把 zs2 并入本中枢，区间与极值都取并集，尾部（end/end_bi/bi_out）换成 zs2 的。

        要点：
        - 第一次被合并时，要先把"合并前的自己"存一份进 sub_zs_lst，
          否则原始的那个小中枢就丢失了（画图/回看需要它）。
        """
        if len(self.sub_zs_lst) == 0:
            self.__sub_zs_lst.append(self.make_copy())
        self.__sub_zs_lst.append(zs2)

        self.__low = min([self.low, zs2.low])
        self.__high = max([self.high, zs2.high])
        self.__peak_low = min([self.peak_low, zs2.peak_low])
        self.__peak_high = max([self.peak_high, zs2.peak_high])
        self.__end = zs2.end
        self.__bi_out = zs2.bi_out
        self.__end_bi = zs2.end_bi
        self.clean_cache()

    def try_add_to_end(self, item):
        """
        尝试把新元素并入本中枢（中枢延伸），并入成功返回 True。

        要点：
        - 并入的前提是它和中枢区间有重叠（in_range）。
        - 一笔中枢比较特殊：它的区间本来就是单根笔的区间，
          所以要先按"起点元素 + 新元素"重算区间，再把尾部推进。
        """
        if not self.in_range(item):
            return False
        if self.is_one_bi_zs():
            self.update_zs_range([self.begin_bi, item])
        self.update_zs_end(item)
        return True

    def in_range(self, item):
        """判断一个元素是否与中枢区间有重叠。"""
        return has_overlap(self.low, self.high, item._low(), item._high())

    def is_inside(self, seg: CSeg):
        """判断本中枢是否落在线段 seg 的笔区间内（只看起点元素是否在其中）。"""
        return seg.start_bi.idx <= self.begin_bi.idx <= seg.end_bi.idx

    def is_divergence(self, config: CPointConfig, out_bi=None):
        """
        判断"出中枢的那一笔"相对"进中枢的那一笔"是否背驰，返回 (是否背驰, 出笔力度/进笔力度)。

        要点：
        - 前置条件是"最后一笔必须突破中枢"（end_bi_break），没突破就返回 (False, None)。
        - 进笔力度用正序口径算、出笔力度用倒序口径算（out_bi 为 None 时取中枢自己记录的 bi_out）。
        - 判据是力度比值：out_metric/in_metric <= divergence_rate 即认为背驰；
          divergence_rate 配成 >100 时相当于"保送"，只要突破中枢就判背驰。
        """
        if not self.end_bi_break(out_bi):  # 最后一笔必须突破中枢
            return False, None
        in_metric = self.get_bi_in().cal_macd_metric(config.macd_algo, is_reverse=False)
        if out_bi is None:
            out_metric = self.get_bi_out().cal_macd_metric(config.macd_algo, is_reverse=True)
        else:
            out_metric = out_bi.cal_macd_metric(config.macd_algo, is_reverse=True)

        # divergence_rate 配成 >100 相当于跳过力度比较，只看有没有突破中枢
        if config.divergence_rate > 100:  # 保送
            return True, out_metric/in_metric
        else:
            return out_metric <= config.divergence_rate*in_metric, out_metric/in_metric

    def init_from_zs(self, zs: 'CZS'):
        """
        把另一个中枢的内容原样搬到自己身上（用于 make_copy）。

        要点：
        - 没有搬 bi_lst 与 mid，也没有搬子中枢列表，
          所以造出来的副本只是一份"中枢区间 + 极值 + 进出笔"的快照。
        """
        self.__begin = zs.begin
        self.__end = zs.end
        self.__low = zs.low
        self.__high = zs.high
        self.__peak_high = zs.peak_high
        self.__peak_low = zs.peak_low
        self.__begin_bi = zs.begin_bi
        self.__end_bi = zs.end_bi
        self.__bi_in = zs.bi_in
        self.__bi_out = zs.bi_out

    def make_copy(self) -> 'CZS':
        """造一份本中枢的副本（合并子中枢时用来保存"合并前的自己"）。"""
        copy = CZS(lst=None, is_sure=self.is_sure)
        copy.init_from_zs(zs=self)
        return copy

    def end_bi_break(self, end_bi=None) -> bool:
        """
        判断"离开中枢的那一笔"是否真的突破了中枢区间（end_bi 为 None 时取 bi_out）。

        要点：
        - 向下笔跌破中枢下沿、或向上笔涨破中枢上沿才算突破。
        """
        if end_bi is None:
            end_bi = self.get_bi_out()
        assert end_bi is not None
        return (end_bi.is_down() and end_bi._low() < self.low) or \
            (end_bi.is_up() and end_bi._high() > self.high)

    def out_bi_is_peak(self, end_bi_idx: int):
        """
        判断中枢内部、位于 end_bi_idx 之前的笔里，有没有比出中枢那笔更极端的。

        返回 (是否最低点，bi_out 与中枢里最接近它的那笔的差距比例)：
        没有更极端的笔时为 (True, 差距比例)，否则为 (False, None)，
        没有出中枢笔（bi_out 为 None）时同样是 (False, None)。

        要点：
        - 买卖点判定里用它来确认"出中枢的那一笔确实是这段行情的极值"。
        """
        assert len(self.bi_lst) > 0
        if self.bi_out is None:
            return False, None
        peak_rate = float("inf")
        for bi in self.bi_lst:
            if bi.idx > end_bi_idx:
                break
            if (self.bi_out.is_down() and bi._low() < self.bi_out._low()) or (self.bi_out.is_up() and bi._high() > self.bi_out._high()):
                return False, None
            r = abs(bi.get_end_val()-self.bi_out.get_end_val())/self.bi_out.get_end_val()
            if r < peak_rate:
                peak_rate = r
        return True, peak_rate

    def get_bi_in(self) -> LINE_TYPE:
        """取进入中枢的那一笔。"""
        assert self.bi_in is not None
        return self.bi_in

    def get_bi_out(self) -> LINE_TYPE:
        """取离开中枢的那一笔。"""
        assert self.__bi_out is not None
        return self.__bi_out

    def set_bi_in(self, bi):
        """记录进入中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。"""
        self.__bi_in = bi
        self.clean_cache()

    def set_bi_out(self, bi):
        """记录离开中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。"""
        self.__bi_out = bi
        self.clean_cache()

    def set_bi_lst(self, bi_lst):
        """记录中枢内部的元素列表（由 KLine_List.update_zs_in_seg 回填）。"""
        self.__bi_lst = bi_lst
        self.clean_cache()
