"""
缠论绘图**元数据**模块：把缠论对象（合并K线/笔/线段/中枢/买卖点…）压扁成一批只含
绘图所需数值的轻量 meta 对象，供 Plot/ChanPicture.py 直接序列化给前端。

设计要点：
    - meta 类只存"画得出来"的东西（idx、价格、方向、是否确定），不保留任何反向引用，
      因此可以被 JSON 化；真正的缠论对象图仍然留在 CKLine_List 上。
    - 所有横坐标统一用 KLU 的 idx（合并K线的起止也换算成 idx），纵坐标就是价格，
      前端拿到后不需要再做任何换算。
    - 一个 meta 类对应一种缠论对象：Cklc_meta/CBi_meta/CSeg_meta/CEigen_meta/
      CEigenFX_meta/CZS_meta/CBS_Point_meta，CChanPlotMeta 是总装入口。
"""
from typing import List

from ..Bi.Bi import CBi
from ..BuySellPoint.BS_Point import CBS_Point
from ..Common.CEnum import FX_TYPE
from ..KLine.KLine import CKLine
from ..KLine.KLine_List import CKLine_List
from ..Seg.Eigen import CEigen
from ..Seg.EigenFX import CEigenFX
from ..Seg.Seg import CSeg
from ..ZS.ZS import CZS


class Cklc_meta:
    """合并K线（合并后的一根）的绘图元数据：起止 idx、高低点、以及类型。"""

    def __init__(self, klc: CKLine):
        """从合并K线取绘图字段；type 优先用分型类型，没分型时退化成合并方向。"""
        self.high = klc.high
        self.low = klc.low
        self.begin_idx = klc.lst[0].idx
        self.end_idx = klc.lst[-1].idx
        self.type = klc.fx if klc.fx != FX_TYPE.UNKNOWN else klc.dir

        self.klu_list = list(klc.lst)


class CBi_meta:
    """笔的绘图元数据：首尾 KLU 的 idx 与价格、方向、笔类型、是否已确定。"""

    def __init__(self, bi: CBi):
        """从 CBi 取绘图字段；横坐标为笔两端 KLU 的 idx，纵坐标为笔端点值。"""
        self.idx = bi.idx
        self.dir = bi.dir
        self.type = bi.type
        self.begin_x = bi.get_begin_klu().idx
        self.end_x = bi.get_end_klu().idx
        self.begin_y = bi.get_begin_val()
        self.end_y = bi.get_end_val()
        self.is_sure = bi.is_sure


class CSeg_meta:
    """线段的绘图元数据：起止点 + 方向 + 确定标记 + 支撑/压力趋势线。"""

    def __init__(self, seg: CSeg):
        """从 CSeg 取绘图字段。

        要点：
        - start_bi 可能是 CBi（笔段）也可能是 CSeg（线段段，即"线段的线段"），
          后者要多剥一层才能拿到真正的 KLU
        - tl 字典只在趋势线真的算出来时才填，前端据此决定画不画支撑/压力线
        """
        if isinstance(seg.start_bi, CBi):
            self.begin_x = seg.start_bi.get_begin_klu().idx
            self.begin_y = seg.start_bi.get_begin_val()
            self.end_x = seg.end_bi.get_end_klu().idx
            self.end_y = seg.end_bi.get_end_val()
        else:
            assert isinstance(seg.start_bi, CSeg)
            self.begin_x = seg.start_bi.start_bi.get_begin_klu().idx
            self.begin_y = seg.start_bi.start_bi.get_begin_val()
            self.end_x = seg.end_bi.end_bi.get_end_klu().idx
            self.end_y = seg.end_bi.end_bi.get_end_val()
        self.dir = seg.dir
        self.is_sure = seg.is_sure
        self.idx = seg.idx

        self.tl = {}
        if seg.support_trend_line and seg.support_trend_line.line:
            self.tl["support"] = seg.support_trend_line
        if seg.resistance_trend_line and seg.resistance_trend_line.line:
            self.tl["resistance"] = seg.resistance_trend_line

    def format_tl(self, tl):
        """把趋势线按本线段的起止价格裁成一段 (x0,y0,x1,y1)，供前端在本线段范围内画线。

        要点：
        - 斜率加 1e-7 是为了避免水平线（slope=0）时后面的除法除零
        - 纵坐标直接取线段两端的 begin_y/end_y，即趋势线被画成贯穿整条线段
        """
        assert tl.line
        tl_slope = tl.line.slope + 1e-7
        tl_x = tl.line.p.x
        tl_y = tl.line.p.y
        tl_y0 = self.begin_y
        tl_y1 = self.end_y
        tl_x0 = (tl_y0-tl_y)/tl_slope + tl_x
        tl_x1 = (tl_y1-tl_y)/tl_slope + tl_x
        return tl_x0, tl_y0, tl_x1, tl_y1


class CEigen_meta:
    """特征序列元素的绘图元数据：起止 idx 与上下沿，外加宽高（供画矩形）。"""

    def __init__(self, eigen: CEigen):
        """从 CEigen 取绘图字段；w/h 是矩形宽高，前端画特征序列用。"""
        self.begin_x = eigen.lst[0].get_begin_klu().idx
        self.end_x = eigen.lst[-1].get_end_klu().idx
        self.begin_y = eigen.low
        self.end_y = eigen.high
        self.w = self.end_x - self.begin_x
        self.h = self.end_y - self.begin_y


class CEigenFX_meta:
    """特征序列分型的绘图元数据：三个元素 + 缺口标记 + 分型方向。"""

    def __init__(self, eigenFX: CEigenFX):
        """从 CEigenFX 取绘图字段。

        要点：
        - 过滤掉 None 元素后必须恰好剩 3 个（特征序列分型由三元素构成）
        - gap / fx 取的是中间那个元素的属性
        """
        self.ele = [CEigen_meta(ele) for ele in eigenFX.ele if ele is not None]
        assert len(self.ele) == 3
        assert eigenFX.ele[1] is not None
        self.gap = eigenFX.ele[1].gap
        self.fx = eigenFX.ele[1].fx


class CZS_meta:
    """中枢的绘图元数据：上下沿、起止 idx、宽高、确定标记、子中枢列表。"""

    def __init__(self, zs: CZS):
        """从 CZS 取绘图字段；子中枢递归转成 CZS_meta，因此子中枢可以层层展开。"""
        self.low = zs.low
        self.high = zs.high
        self.begin = zs.begin.idx
        self.end = zs.end.idx
        self.w = self.end - self.begin
        self.h = self.high - self.low
        self.is_sure = zs.is_sure
        self.sub_zs_lst = [CZS_meta(t) for t in zs.sub_zs_lst]
        self.is_onebi_zs = zs.is_one_bi_zs()


class CBS_Point_meta:
    """买卖点的绘图元数据：位置 idx、标注价格、买/卖、类型串。"""

    def __init__(self, bsp: CBS_Point, is_seg):
        """从 CBS_Point 取绘图字段。

        要点：
        - 横坐标取 bsp.klu.idx（买卖点所在那根K线），纵坐标买点取最低价、卖点取最高价，
          这样标注会贴在该笔的极值点上
        - is_seg 区分笔级/线段级买卖点，只影响标注样式
        """
        self.is_buy = bsp.is_buy
        self.type = bsp.type2str()
        self.is_seg = is_seg

        self.x = bsp.klu.idx
        self.y = bsp.klu.low if self.is_buy else bsp.klu.high

    def desc(self):
        """生成标注文案，如 "b1,2"（1/2类买点）/"s3a"（3a类卖点）；线段级前面加 ※。"""
        is_seg_flag = "※" if self.is_seg else ""
        return f'{is_seg_flag}b{self.type}' if self.is_buy else f'{is_seg_flag}s{self.type}'


class CChanPlotMeta:
    """绘图中枢：一次把一个级别的全部缠论对象转成 meta，并保留原始 CKLine_List 以便回查。"""

    def __init__(self, kl_list: CKLine_List):
        """按级别装配全部绘图元数据。

        要点：
        - datetick 由 klu_iter 生成，与横坐标 idx 一一对应，前端用它画时间轴刻度
        - 线段与"线段的线段"分别装进 seg_list / segseg_list，各自再带一份特征序列分型
        - 买卖点分两批：笔级 bs_point_lst 与线段级 seg_bsp_lst
        """
        self.data = kl_list

        self.klc_list: List[Cklc_meta] = [Cklc_meta(klc) for klc in kl_list.lst]
        self.datetick = [klu.time.to_str() for klu in self.klu_iter()]
        self.klu_len = sum(len(klc.klu_list) for klc in self.klc_list)

        self.bi_list = [CBi_meta(bi) for bi in kl_list.bi_list]

        self.seg_list: List[CSeg_meta] = []
        self.eigenfx_lst: List[CEigenFX_meta] = []
        for seg in kl_list.seg_list:
            self.seg_list.append(CSeg_meta(seg))
            if seg.eigen_fx:
                self.eigenfx_lst.append(CEigenFX_meta(seg.eigen_fx))

        self.seg_eigenfx_lst: List[CEigenFX_meta] = []
        self.segseg_list: List[CSeg_meta] = []
        for segseg in kl_list.segseg_list:
            self.segseg_list.append(CSeg_meta(segseg))
            if segseg.eigen_fx:
                self.seg_eigenfx_lst.append(CEigenFX_meta(segseg.eigen_fx))

        self.zs_lst: List[CZS_meta] = [CZS_meta(zs) for zs in kl_list.zs_list]
        self.segzs_lst: List[CZS_meta] = [CZS_meta(segzs) for segzs in kl_list.segzs_list]

        self.bs_point_lst: List[CBS_Point_meta] = [CBS_Point_meta(bs_point, is_seg=False) for bs_point in kl_list.bs_point_lst.bsp_iter()]
        self.seg_bsp_lst: List[CBS_Point_meta] = [CBS_Point_meta(seg_bsp, is_seg=True) for seg_bsp in kl_list.seg_bs_point_lst.bsp_iter()]

    def klu_iter(self):
        """按时间顺序摊平遍历全部原始K线（越过合并K线的分组）。"""
        for klc in self.klc_list:
            yield from klc.klu_list

    def sub_last_kseg_start_idx(self, seg_cnt):
        """倒数第 seg_cnt 条线段的起始子K线 idx，用于"只画最后 N 段"时定位起点；数量不够返回 0（即从头画）。"""
        if seg_cnt is None or len(self.data.seg_list) <= seg_cnt:
            return 0
        else:
            return self.data.seg_list[-seg_cnt].get_begin_klu().sub_kl_list[0].idx

    def sub_last_kbi_start_idx(self, bi_cnt):
        """倒数第 bi_cnt 根笔的起始子K线 idx，用于"只画最后 N 笔"时定位起点；数量不够返回 0。"""
        if bi_cnt is None or len(self.data.bi_list) <= bi_cnt:
            return 0
        else:
            return self.data.bi_list[-bi_cnt].begin_klc.lst[0].sub_kl_list[0].idx

    def sub_range_start_idx(self, x_range):
        """倒数第 x_range 根原始K线对应的子K线 idx；找不到（x_range 超过总根数）返回 0。"""
        for klc in self.data[::-1]:
            for klu in klc[::-1]:
                x_range -= 1
                if x_range == 0:
                    return klu.sub_kl_list[0].idx
        return 0
