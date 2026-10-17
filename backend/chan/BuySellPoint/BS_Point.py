"""
买卖点对象（BS_Point）模块：缠论链路中「中枢 -> 买卖点」这一环的产物定义。

CBS_Point 就是"一个买卖点"：它挂在一根笔（笔级买卖点）或一条线段（线段级买卖点）的尾部，
记录这个位置是买点还是卖点、属于哪几类（1/1p/2/2s/3a/3b）、它对应的1类买卖点是谁，
以及一批供回测/模型使用的特征（背驰力度比、中枢个数、回撤比例…）。

链路位置：
    原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 中枢 -> 【本模块：买卖点对象】

Note:
    - 买卖点对象由 BuySellPoint/BSPointList.py 负责创建与去重：同一根笔上只保留一个实例，
      多个类型通过 add_type 叠加在同一个实例上；本模块只描述"一个买卖点自身的数据"。
    - 买/卖不靠配置区分：向下笔的终点是低点，所以"向下笔结束"天然就是买点（is_buy=True）。
"""
from typing import Dict, Generic, List, Optional, TypeVar, Union

from ..Bi.Bi import CBi
from ..ChanModel.Features import CFeatures
from ..Common.CEnum import BSP_TYPE
from ..Seg.Seg import CSeg

LINE_TYPE = TypeVar('LINE_TYPE', CBi, CSeg)


class CBS_Point(Generic[LINE_TYPE]):
    """
    一个买卖点。

    它绑定在一根笔（或一条线段）的尾部，表示"这根笔走完的位置出现了买/卖信号"。

    核心字段：
        bi          ：买卖点所在的那根笔/线段（笔级买卖点是 CBi，线段级买卖点是 CSeg）
        klu         ：买卖点对应的原始K线，= bi 尾端造出极值的那根K线，画图/回测取价取它
        is_buy      ：True 是买点，False 是卖点；由所在笔的方向决定（向下笔结束即买点）
        type        ：买卖点类型列表，一根笔可能同时命中多类（如既是1买又是2买）
        relate_bsp1 ：本买卖点所对应的那个1类买卖点（2/3类才有，1类为 None）
        features    ：特征字典（CFeatures），供策略/模型消费
        is_segbsp   ：是否为线段级买卖点的标记，构造时恒为 False，由外部按需改写

    Note:
        构造时会把 self 反向写回 bi.bsp，因此"一根笔上至多一个买卖点实例"这条不变式
        由 BSPointList 保证（见 CBSPointList.add_bs 里对 bsp_store_flat_dict 的查重）。
    """

    def __init__(self, bi: LINE_TYPE, is_buy, bs_type: BSP_TYPE, relate_bsp1: Optional['CBS_Point'], feature_dict=None):
        """造一个买卖点并挂到所在笔/线段上。

        要点：
        - klu 取笔尾端造出极值的那根K线，买卖点的时间与价格都以它为准
        - type 是列表，构造时只装首个类型，后续类型由 add_type / add_another_bsp_prop 追加
        - 构造时会把 self 反向写回 bi.bsp，"一根笔至多一个买卖点实例"由 BSPointList.add_bs 保证
        - 末尾自动调 init_common_feature() 补通用特征，所以 feature_dict 只给本类专属的那部分即可
        """
        self.bi: LINE_TYPE = bi
        self.klu = bi.get_end_klu()
        self.is_buy = is_buy
        self.type: List[BSP_TYPE] = [bs_type]
        self.relate_bsp1 = relate_bsp1

        # 反向指针：让"从笔出发"的调用方（画图、策略）能直接找到尾部的买卖点
        self.bi.bsp = self  # type: ignore
        self.features = CFeatures(feature_dict)

        self.is_segbsp = False

        self.init_common_feature()

    def add_type(self, bs_type: BSP_TYPE):
        """追加一个买卖点类型：同一根笔可同时命中多类，只并类型不新建对象。"""
        self.type.append(bs_type)

    def type2str(self):
        """把类型列表拼成逗号分隔字符串（如 "1,2"），供打印/绘图标注，顺序与 type 一致。"""
        return ",".join([x.value for x in self.type])

    def add_another_bsp_prop(self, bs_type: BSP_TYPE, relate_bsp1):
        """叠加一个新类型，并把该类型对应的1类买卖点合并进来。

        要点：
        - relate_bsp1 只补不覆盖：自己没有就用对方的，两边都有则必须指向同一根K线
        - 两边都非 None 却不同K线，说明这个买卖点被两条不同的1类链条同时认领，属逻辑冲突
        - 不覆盖是因为先登记的类型更"根"，不应被后来的类型改写
        """
        self.add_type(bs_type)
        if self.relate_bsp1 is None:
            self.relate_bsp1 = relate_bsp1
        elif relate_bsp1 is not None:
            assert self.relate_bsp1.klu.idx == relate_bsp1.klu.idx

    def add_feat(self, inp1: Union[str, Dict[str, float], Dict[str, Optional[float]], 'CFeatures'], inp2: Optional[float] = None):
        """往特征表里写一条（传特征名+值）或一批（传 dict / 另一个 CFeatures）特征。

        要点：
        - 单条写法的第二个参数省略就按批量合并处理
        - 同名特征后写覆盖先写，所以"通用特征 + 本类专属特征"可以分两次写而不必担心键冲突
        """
        self.features.add_feat(inp1, inp2)

    def init_common_feature(self):
        """补上与买卖点类型无关的通用特征（目前只有所在笔振幅 bsp_bi_amp），构造时自动调一次。"""
        # 用于配置适用所有买卖点的特征
        self.add_feat({
            'bsp_bi_amp': self.bi.amp(),
        })
