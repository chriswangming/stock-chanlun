"""
特征序列（CEigenFX）模块：缠论特征序列法判定线段的"引擎"。

一个 CEigenFX 就是一个方向上的特征序列：
    上升线段的内部笔是"向下笔"，它们合并成若干元素（CEigen），
    当这串元素出现顶分型，就说明这条上升线段结束了；下降线段完全对称。
它对外提供的核心能力有三个：
    add(bi)      ：喂进一根笔，攒元素，攒满三个元素时判一次分型；
    can_be_end   ：算出线段该在哪根笔上结束，以及这条线段算不算确认；
    reset()      ：三个元素没凑成分型时，扔掉最旧的一个元素重来。

链路位置：
    原始K线 -> 包含合并 -> 分型 -> 笔 -> 特征序列元素(CEigen)
    -> 【本模块：特征序列分型】 -> 线段 -> 中枢 -> 买卖点

Note:
    本模块只被 Seg/SegListChan.py（seg_algo='chan'）使用。
"""
from typing import List, Optional

from ..Bi.Bi import CBi
from ..Bi.BiList import CBiList
from ..Common.CEnum import BI_DIR, FX_TYPE, KLINE_DIR, SEG_TYPE
from ..Common.ChanException import CChanException, ErrCode
from ..Common.func_util import revert_bi_dir

from .Eigen import CEigen


class CEigenFX:
    """
    一个方向上的特征序列。

    核心状态：
        dir      ：这条特征序列服务于哪个方向的线段（UP -> 序列装向下笔）
        ele      ：三个元素的滑动窗口 [元素1, 元素2, 元素3]，全为 None 时表示还没开始
        lst      ：所有喂进来过的笔（用于重置时重放）
        exclude_included：被包含时是否不合并（标准特征序列做法为 True）
        kl_dir   ：元素做包含合并时的方向
        last_evidence_bi / *_is_sure：判定这条线段成立所依据的最后一根笔，用于判断线段是否确认

    Note:
        ele 三个槽位同时最多只能有两个非空（第三个元素一旦被创建就会立刻判分型，
        判完若不成则 reset 扔掉最旧的那个）。
    """

    def __init__(self, _dir: BI_DIR, exclude_included=True, lv=SEG_TYPE.BI):
        """构造一个方向上的特征序列。

        要点：
        - _dir 是本序列服务的线段方向：UP 表示序列装的是向下笔
        - exclude_included 为标准特征序列口径（被包含的笔不参与合并）
        - lv 表示本层序列由笔构成还是由线段构成
        """
        self.lv = lv
        self.dir = _dir  # 线段方向
        self.ele: List[Optional[CEigen]] = [None, None, None]
        self.lst: List[CBi] = []
        self.exclude_included = exclude_included
        self.kl_dir = KLINE_DIR.UP if _dir == BI_DIR.UP else KLINE_DIR.DOWN
        self.last_evidence_bi: Optional[CBi] = None
        self.last_evidence_bi_is_sure: bool = False
        self.actual_break_flag = True

    def treat_first_ele(self, bi: CBi) -> bool:
        """用这根笔开出第一个元素；一个元素不可能判分型，恒返回 False。"""
        self.ele[0] = CEigen(bi, self.kl_dir)
        return False

    def treat_second_ele(self, bi: CBi) -> bool:
        """处理第二个元素：先试着并入第一个元素，并不了才新开一个。

        要点：
        - 前两个元素若能合并成一个，说明走势还很连贯，不存在分型
        - 新开元素后还要查位置：上升线段里第二元素比第一元素更低（或下降线段里更高），
          说明这两元素越来越极端，中间那个永远不可能成为顶/底，直接 reset
        - 返回 True 表示要走 reset
        """
        assert self.ele[0] is not None
        combine_dir = self.ele[0].try_add(bi, exclude_included=self.exclude_included)
        if combine_dir != KLINE_DIR.COMBINE:  # 不能合并
            self.ele[1] = CEigen(bi, self.kl_dir)
            if (self.is_up() and self.ele[1].high < self.ele[0].high) or \
               (self.is_down() and self.ele[1].low > self.ele[0].low):  # 前两元素不可能成为分形
                return self.reset()
        return False

    def treat_third_ele(self, bi: CBi) -> bool:
        """处理第三个元素：新开第三元素（或被第二元素合并掉）之后，给第二元素判分型。

        要点：
        - 先把这根笔记成判定依据（last_evidence_bi），后面判断线段是否确认时要看它
        - 能并入第二元素就直接返回 False（分型还没成型）
        - 否则新开第三元素，先过 actual_break 这一关，再给第二元素判分型
        - 判出来的分型方向必须与本序列方向匹配（上升线段要顶分型），否则 reset
        - 返回 True 表示第二元素已是一个可用的顶/底分型
        """
        assert self.ele[0] is not None
        assert self.ele[1] is not None
        self.last_evidence_bi = bi
        self.last_evidence_bi_is_sure = bi.is_used_to_be_sure
        # 顶/底相等时的合并口径：向下笔的元素用 1（顶相同却创了新低 -> 不合并），
        # 向上笔的元素用 -1（底相同却创了新高 -> 不合并）；普通口径下不需要它
        allow_top_equal = (1 if bi.is_down() else -1) if self.exclude_included else None
        combine_dir = self.ele[1].try_add(bi, allow_top_equal=allow_top_equal)
        if combine_dir == KLINE_DIR.COMBINE:
            return False
        self.ele[2] = CEigen(bi, combine_dir)
        if not self.actual_break():
            return self.reset()
        self.ele[1].update_fx(self.ele[0], self.ele[2], exclude_included=self.exclude_included, allow_top_equal=allow_top_equal)  # type: ignore
        fx = self.ele[1].fx
        is_fx = (self.is_up() and fx == FX_TYPE.TOP) or (self.is_down() and fx == FX_TYPE.BOTTOM)
        return True if is_fx else self.reset()

    def add(self, bi: CBi) -> bool:  # 返回是否出现分形
        """
        喂进一根笔（方向必须与本序列服务的线段方向相反），攒到三个元素时判一次分型。

        要点：
        - 笔会先记进 lst，reset 时要靠它重放
        - 三个元素都找齐了却还没处理属于异常，抛 SEG_EIGEN_ERR
        """
        assert bi.dir != self.dir
        self.lst.append(bi)
        if self.ele[0] is None:  # 第一元素
            return self.treat_first_ele(bi)
        elif self.ele[1] is None:  # 第二元素
            return self.treat_second_ele(bi)
        elif self.ele[2] is None:  # 第三元素
            return self.treat_third_ele(bi)
        else:
            raise CChanException(f"特征序列3个都找齐了还没处理!! 当前笔:{bi.idx},当前:{str(self)}", ErrCode.SEG_EIGEN_ERR)

    def reset(self):
        """三个元素凑不出分型时，扔掉最旧的那个元素再来，返回重放后是否已判出分型。

        要点：
        - 标准口径（exclude_included 为真）：整窗清空，把除最旧一笔以外的笔重新喂一遍
        - 非标准口径：只把元素窗口整体前移一格（旧元素1丢掉、元素2变成元素1），
          并把 lst 裁到旧元素2的起点，让笔列表与窗口对齐
        """
        bi_tmp_list = list(self.lst[1:])
        if self.exclude_included:
            self.clear()
            for bi in bi_tmp_list:
                if self.add(bi):
                    return True
        else:
            assert self.ele[1] is not None
            # 非标准口径下只挪动元素窗口，笔列表也要裁到与窗口对齐
            ele2_begin_idx = self.ele[1].lst[0].idx
            self.ele[0], self.ele[1], self.ele[2] = self.ele[1], self.ele[2], None
            self.lst = [bi for bi in bi_tmp_list if bi.idx >= ele2_begin_idx]  # 从第二元素开始

        return False

    def can_be_end(self, bi_lst: CBiList):
        """判断这个分型能不能真的终结一条线段，以及这条线段算不算确认。

        要点：
        - 返回三态：True 分型成立、线段确认；None 分型成立但还没走出反向分型，只能算未确认线段；
          False 分型不算数，调用方要丢掉它重新算
        - 有缺口时要更严格：标准做法要求缺口后面还得再出现一个反向分型才算真的被终结，
          所以这里回去找那个反向分型（find_revert_fx）
        - 没缺口时只要 actual_break_flag 还立着就可以；否则说明分型只是"看起来成立"，
          实际并没有突破，返回 None
        """
        assert self.ele[1] is not None
        if self.ele[1].gap:
            assert self.ele[0] is not None
            end_bi_idx = self.GetPeakBiIdx()
            thred_value = bi_lst[end_bi_idx].get_end_val()
            break_thred = self.ele[0].low if self.is_up() else self.ele[0].high
            # 注意：thred_value / break_thred 算出来了，但 find_revert_fx 里目前没用到它们
            return self.find_revert_fx(bi_lst, end_bi_idx+2, thred_value, break_thred)
        else:
            # 没有实际突破（分型是被合并出来的假象）-> 分型不作数，只当作未确认
            if not self.actual_break_flag:
                return None
            return True

    def is_down(self):
        """本序列服务于下降线段（即装的是向上笔）。"""
        return self.dir == BI_DIR.DOWN

    def is_up(self):
        """本序列服务于上升线段（即装的是向下笔）。"""
        return self.dir == BI_DIR.UP

    def GetPeakBiIdx(self):
        """取线段终点笔的下标（由中间那个元素说了算）。"""
        assert self.ele[1] is not None
        return self.ele[1].GetPeakBiIdx()

    def all_bi_is_sure(self):
        """构成这条线段的笔是否都已确认（全部是确认笔或曾经确认过才为 True）。

        要点：
        - 除了窗口内的笔，还要看"判定依据"那根笔：它是分型成立后才出现的笔，
          它没确认，线段就不能算确认
        """
        assert self.last_evidence_bi is not None
        return next((False for bi in self.lst if not bi.is_used_to_be_sure), True) and self.last_evidence_bi_is_sure

    def clear(self):
        """清空整个序列（元素窗口与笔列表一起清空）。"""
        self.ele = [None, None, None]
        self.lst = []

    def __str__(self):
        """调试输出：三个元素各自的笔 idx，用 " | " 分隔。"""
        _t = [f"{[] if ele is None else ','.join([str(b.idx) for b in ele.lst])}" for ele in self.ele]
        return " | ".join(_t)

    def actual_break(self):
        """判断第二个元素是否"真的"被第三个元素突破了（防止合并造成的假分型）。

        要点：
        - 元素做过包含合并，端点可能被抬高/压低，于是"看起来第三元素高过第二元素"、
          实际并没有真正突破，所以要拿合并前的原始笔比对：
          上升线段要求后面的笔确实创了新低，下降线段对称
        - 后面笔数不够、实在判断不了时，把 actual_break_flag 置 False 并放行，
          让上层把它当未确认线段处理
        - 返回 False 表示分型不可用，要走 reset
        """
        if not self.exclude_included:
            return True
        assert self.ele[2] and self.ele[1]
        if (self.is_up() and self.ele[2].low < self.ele[1][-1]._low()) or \
           (self.is_down() and self.ele[2].high > self.ele[1][-1]._high()):  # 防止第二元素因为合并导致后面没有实际突破
            return True
        assert len(self.ele[2]) == 1
        ele2_bi = self.ele[2][0]
        if ele2_bi.next and ele2_bi.next.next:
            # 往后看两根笔，才能确定第三元素之后的走势确实延续（或确实没有延续）
            if ele2_bi.is_down():
                if ele2_bi.next.next._low() < ele2_bi._low():
                    self.last_evidence_bi = ele2_bi.next.next
                    self.last_evidence_bi_is_sure = ele2_bi.next.next.is_used_to_be_sure
                    return True
                else:
                    if not ele2_bi.next.next.is_used_to_be_sure or ele2_bi.next.next.next is None:
                        self.actual_break_flag = False
                        return True
            elif ele2_bi.is_up():
                if ele2_bi.next.next._high() > ele2_bi._high():
                    self.last_evidence_bi = ele2_bi.next.next
                    self.last_evidence_bi_is_sure = ele2_bi.next.next.is_used_to_be_sure
                    return True
                else:
                    if not ele2_bi.next.next.is_used_to_be_sure or ele2_bi.next.next.next is None:
                        self.actual_break_flag = False
                        return True
        else:
            if ele2_bi.next:
                if self.is_up() and ele2_bi.next._high() > self.ele[1].high:
                    ...
                elif self.is_down() and ele2_bi.next._low() < self.ele[1].low:
                    ...
                else:
                    self.actual_break_flag = False
                    return True
            else:
                self.actual_break_flag = False
                return True
        return False

    def find_revert_fx(self, bi_list: CBiList, begin_idx: int, thred_value: float, break_thred: float):
        """在有缺口的分型之后，去找那个能证明线段真正终结的"反向分型"。

        要点：
        - 返回三态：True 找到了反向分型、线段确认；None 找到最后也没找到，只能算未确认；
          False 找到的分型不算数
        - 找法：造一个反向的 CEigenFX（本序列是上升就造一个下降的），
          隔一根喂一笔（bi_list[begin_idx::2]），让它自己判分型
        - 反向分型看起来成型后还要再确认它自己也成立，缺口后面没走完的只能算未确认
        - thred_value / break_thred 目前没参与判断（相关逻辑删除的原因见 #272）
        """
        COMMON_COMBINE = False  # 是否用普通分形合并规则处理
        # 如果返回None，表示找到最后了
        first_bi_dir = bi_list[begin_idx].dir  # down则是要找顶分型
        egien_fx = CEigenFX(revert_bi_dir(first_bi_dir), exclude_included=not COMMON_COMBINE, lv=self.lv)  # 顶分型的话要找上升线段
        for bi in bi_list[begin_idx::2]:
            # 反向的分型成型了：再确认它是不是也成立（缺口后面没走完就只能算未确认）
            if egien_fx.add(bi):
                if COMMON_COMBINE:
                    return True

                while True:
                    _test = egien_fx.can_be_end(bi_list)
                    if not egien_fx.actual_break_flag:
                        _test = None
                    if _test in [True, None]:
                        assert egien_fx.ele[2]
                        self.last_evidence_bi = egien_fx.ele[2].lst[-1]
                        if _test is True:
                            self.last_evidence_bi_is_sure = self.last_evidence_bi.is_used_to_be_sure and egien_fx.last_evidence_bi_is_sure
                        return _test
                    elif not egien_fx.reset():
                        break
            # if (bi.is_down() and bi._low() < thred_value) or (bi.is_up() and bi._high() > thred_value):
            # 这段逻辑删除的原因参看#272，如果有其他badcase，再看怎么统一修复
            #     return False
        return None
