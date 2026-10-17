"""
买卖点的**特征（feature）容器**模块。

买卖点在判定过程中会顺带算出一些便于后续筛选/建模的附属量（例如背驰力度比、中枢个数、
是否为线段级别买卖点等），本模块提供一个轻量的键值对容器把它们挂在 CBS_Point 上，
供策略层（Debug/ 目录下的策略示例、机器学习建模等）读取。
"""

from typing import Optional


class CFeatures:
    """买卖点特征的轻量字典包装：只暴露读（items / []）与写入（add_feat），不支持删除已写入的键。"""

    def __init__(self, initFeat=None):
        """用一份初始特征字典建容器（浅拷贝一份，避免外部 dict 被后续 add_feat 污染），None 表示从空开始。"""
        self.__features = {} if initFeat is None else dict(initFeat)

    def items(self):
        """遍历全部 (特征名, 特征值)。"""
        yield from self.__features.items()

    def __getitem__(self, k):
        """按名字取特征值，特征不存在时抛 KeyError（没有默认值，需调用方自行保证）。"""
        return self.__features[k]

    def add_feat(self, inp1, inp2: Optional[float] = None):
        """写入特征：传 (名字, 值) 写一条，只传一个 dict / CFeatures 则批量合并。

        要点：
        - 写入用的是 dict.update：同名特征会被覆盖，且无法删除已写入的键
        - 特征名恰好叫 "inp2" 且值传 None 时会走错分支（歧义），实际未触发
        """
        if inp2 is None:
            self.__features.update(inp1)
        else:
            self.__features.update({inp1: inp2})
