# backend 模块速览

> 数据源：`backend` 下全部 Python 文件，AST 解析自动生成。
> **86 文件 / 18,777 行 / 86 类 / 651 函数，docstring 覆盖率 100%**
> 只列模块与一句话职责；类/函数级骨架见 `backend-notes-full.md`。
> 重跑：`cd backend && python docs/gen_notes.py`

## 总览

| 分组 | 文件 | 行数 | 职责 |
|---|---:|---:|---|
| **（backend 根）** | 5 | 339 | 启动与装配（main / config / deps / HTTP 补丁） |
| **chan** | 54 | 9,980 | 自研缠论内核：K线 → 包含 → 分型 → 笔 → 线段 → 中枢 → 买卖点 |
| **ai** | 8 | 1,391 | 缠论之上的信号层：背驰 / 波浪 / 策略 / LLM 客户端与会话 |
| **routers** | 7 | 1,823 | HTTP 入口（FastAPI） |
| **services** | 4 | 4,100 | 取数与业务：A 股 akshare、港股富途、缠论选股 |
| **stores** | 2 | 192 | 本地 JSON 持久化 |
| **utils** | 6 | 952 | 通用工具：缓存 / 限流 / 重试 / 代码归一 / 指标 |

**整文件处于注释（停用）状态**：`routers/diagnosis.py` `routers/stocks.py`

**分层**：`chan/` 自研缠论内核 → `ai/` 在其上做信号与 LLM → `services/` 取数 → `routers/` 出 HTTP → `utils/`+`stores/` 兜底。

## （backend 根） · 启动与装配（main / config / deps / HTTP 补丁）

- `config.py` · 66 行 — 运行时配置：全部从环境变量读取（须在任何 HTTP 补丁之前加载）。
- `deps.py` · 82 行 — FastAPI 依赖：客户端 IP 识别与限流守卫。
- `http_adapter_patch.py` · 39 行 — 给行情域名猴子补丁 requests 的 HTTPAdapter.send：不走代理、补默认超时、可关 TLS 校验。 必须在任何会触发 requests 访问东财 / 腾讯行情的代码之前导入，否则补丁不生效。
- `main.py` · 96 行 — ChanStock — 缠论智能股票分析系统 FastAPI 后端入口（路由已拆至 routers/）。
- `run_server.py` · 56 行 — 启动入口：清掉代理环境变量后，用子进程拉起 uvicorn（等价于 `python -m uvicorn main:app`）。

## chan · 自研缠论内核：K线 → 包含 → 分型 → 笔 → 线段 → 中枢 → 买卖点

- `chan/Chan.py` · 640 行 — 缠论计算的顶层入口模块：对外只暴露 CChan 一个类。 链路：数据源 -> CKLine_Unit -> CKLine_List（包含处理 -> 分型 -> 笔 -> 线段 -> 中枢 -> 买卖点）。 - lv_list 必须从大级别排到小级别，高级别K线驱动次级别K线的消费与父子挂载。
- `chan/ChanConfig.py` · 309 行 — 缠论计算的**全局配置**模块：把用户传入的一个扁平 dict（通常来自命令行 -c 参数或策略脚本） 拆解、校验、分发到各个子模块的配置对象里。 配置分发的层次：
- `chan/__init__.py` · 2 行 — 自研缠论计算内核：K线 → 包含合并 → 分型 → 笔 → 线段 → 中枢 → 买卖点。
- `chan/Bi/Bi.py` · 611 行 — 笔（Bi）模块：缠论计算链路中「分型 -> 笔」这一环。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 【本模块：笔】 -> 线段(Seg) -> 中枢(ZS) -> 买卖点
- `chan/Bi/BiConfig.py` · 63 行 — 笔的配置模块：CBiConfig。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 笔(Bi，规则由本配置控制) -> 线段 -> 中枢 -> 买卖点
- `chan/Bi/BiList.py` · 427 行 — 笔列表（BiList）模块：缠论链路中"哪些K线能成笔、旧笔要不要回收"的判定者。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 【Bi/Bi.py 单根笔】 + 【BiList 成笔规则】 -> 线段(Seg) -> 中枢(ZS) -> 买卖点
- `chan/Bi/__init__.py` · 2 行 — 笔（CBi / CBiList / CBiConfig）模块。
- `chan/BuySellPoint/BSPointConfig.py` · 186 行 — 买卖点配置（BSPointConfig）模块：缠论链路中「中枢 -> 买卖点」这一环的算法参数。 CPointConfig 是"单侧"的一份配置（买侧或卖侧各一份），CBSPointConfig 把这两份打包在一起， 因为同一个标的的买点判据和卖点判据常常要分别调（例如涨得猛的票卖点条件要放宽）。
- `chan/BuySellPoint/BSPointList.py` · 723 行 — 买卖点列表（BSPointList）模块：缠论计算链路的最后一环。 CBSPointList 是"某个级别上所有买卖点"的容器兼计算器：它拿着上一环算好的笔/线段序列与中枢， 逐条线段判定各类买卖点，按类型分桶存起来，并负责"行情尾部被重算时把失效买卖点撤掉"的去重与更新。
- `chan/BuySellPoint/BS_Point.py` · 106 行 — 买卖点对象（BS_Point）模块：缠论链路中「中枢 -> 买卖点」这一环的产物定义。 CBS_Point 就是"一个买卖点"：它挂在一根笔（笔级买卖点）或一条线段（线段级买卖点）的尾部， 记录这个位置是买点还是卖点、属于哪几类（1/1p/2/2s/3a/3b）、它对应的1类买卖点是谁，
- `chan/BuySellPoint/__init__.py` · 2 行 — 买卖点（CBS_Point / CBSPointList / 配置）模块。
- `chan/ChanModel/Features.py` · 38 行 — 买卖点的**特征（feature）容器**模块。 买卖点在判定过程中会顺带算出一些便于后续筛选/建模的附属量（例如背驰力度比、中枢个数、 是否为线段级别买卖点等），本模块提供一个轻量的键值对容器把它们挂在 CBS_Point 上，
- `chan/ChanModel/__init__.py` · 2 行 — 买卖点特征（CFeatures）模块。
- `chan/Combiner/Combine_Item.py` · 53 行 — CCombine_Item —— 「包含关系判定用的统一取点适配器」。 缠论的包含处理和笔/线段算法需要反复比较两个元素的 high/low 与时间， 但参与比较的元素可能是三种完全不同的类型（原始K线 / 笔 / 线段），
- `chan/Combiner/KLine_Combiner.py` · 360 行 — K线包含处理（缠论最基础的一步）。 本类是整个缠论算法的公共底座，被两个场景复用（泛型参数 T 不同）： - `KLine/KLine.py` 的 CKLine(CKLine_Combiner[CKLine_Unit])：原始K线按包含关系合并成"合并K线(KLC)"；
- `chan/Combiner/__init__.py` · 2 行 — K线包含关系处理（CKLine_Combiner）模块。
- `chan/Common/CEnum.py` · 271 行 — 全局枚举与常量定义模块。 集中定义缠论计算全流程共用的枚举类型：数据源（DATA_SRC）、K线级别（KL_TYPE）、 K线方向与分型、笔方向与笔种类、买卖点类型、复权方式、均线/通道类型、线段语义、
- `chan/Common/CTime.py` · 134 行 — 自定义时间类型模块（CTime）。 缠论里K线时间有两类语义，不能用 datetime 直接替代： 1. 天级别以下（分钟/秒线）：时间戳描述的是这根K线的**结束时间**，
- `chan/Common/ChanException.py` · 185 行 — 错误码与自定义异常模块。 ErrCode 用 IntEnum 按业务域分段编号：缠论计算错误 0~99、交易错误 100~199、 K线数据错误 200~299；段首段尾的 _XXX_BEGIN / _XXX_END 是哨兵位，不参与实际抛错，
- `chan/Common/__init__.py` · 2 行 — 公共基础：枚举 CEnum、时间 CTime、缓存、异常等。
- `chan/Common/cache.py` · 92 行 — 实例级方法结果缓存（memoize）模块。 提供一个装饰器类 make_cache：被装饰的方法在**同一个实例**上第一次调用后结果被存下来， 之后直接返回缓存值。缠论里笔/合并K线的端点、涨跌幅、K线根数等属性会被反复读取
- `chan/Common/func_util.py` · 134 行 — 通用小工具函数模块。 放一些跨模块复用、又不值得单独成类的纯函数：K线级别的大小与顺序判定、 笔方向取反、区间是否重叠、字符串转浮点，以及把 inf 转成可写回源码字面量的小工具。
- `chan/DataAPI/BaoStockAPI.py` · 343 行 — A股数据源模块（baostock / 证券宝）。 baostock 是一个免费、免注册的A股历史行情库，核心接口是 `query_history_k_data_plus`（按 标的+级别+日期区间 一次返回全部K线）和
- `chan/DataAPI/CommonStockAPI.py` · 130 行 — 数据源抽象基类模块（CCommonStockApi）。 这里定义了本缠论内核与所有行情数据源之间的唯一契约层： CChan 只认这个基类，完全不关心底层是 baostock / ccxt / 本地 CSV / 富途，
- `chan/DataAPI/__init__.py` · 2 行 — 数据源适配层（统一接口 + baostock 等实现）。
- `chan/KLine/KLine.py` · 203 行 — 合并后的K线 CKLine（简称 KLC）模块。 链路位置：CKLine_Unit（单根原始K线） -> CKLine（做包含关系处理，把若干根同方向 KLU 并成一根） -> CKLine_List（某级别整段序列）。 它承上启下：
- `chan/KLine/KLine_List.py` · 417 行 — 某一级别的完整K线序列 CKLine_List：合并K线列表 + 笔/线段/线段段 + 各类中枢 + 买卖点容器。 链路位置：CKLine_Unit -> CKLine -> CKLine_List…
- `chan/KLine/KLine_Unit.py` · 326 行 — 单根（未经包含关系处理的）K线单元 CKLine_Unit（简称 KLU），整条缠论计算链路的最底层。 链路位置：数据源 -> CKLine_Unit -> CKLine（若干根 KLU 合并成一根 KLC）-> CKLine_List（某级别整段序列，含笔/线段/中枢容器）。 KLU 之间靠三组指针表达关系：
- `chan/KLine/TradeInfo.py` · 44 行 — 单根K线的附属交易信息（成交量、成交额、换手率等）容器。 链路位置：CKLine_Unit -> CTradeInfo。 它是"只读旁路"数据：由数据源给的原始 dict 抽取而来，挂在 klu.trade_info 上，
- `chan/KLine/__init__.py` · 2 行 — K线对象：单根 CKLine_Unit、列表 CKLine_List、合并K线 CKLine。
- `chan/Math/BOLL.py` · 69 行 — 布林线（BOLL）指标。 MID  = N 个样本的均值 UP   = MID + 2 * 标准差
- `chan/Math/Demark.py` · 381 行 — Demark（TD Sequential，德马克序列）指标。 用来在一个线段/趋势的尾部数"衰竭计数"，帮助判断趋势是否接近尽头。分两个阶段： Setup（准备阶段，默认 9 计数）
- `chan/Math/KDJ.py` · 77 行 — KDJ（随机指标）。 RSV = (close - 窗口内最低) / (窗口内最高 - 窗口内最低) * 100 K   = 2/3 * 前一根K + 1/3 * RSV
- `chan/Math/MACD.py` · 76 行 — MACD 指标（指数平滑异同移动平均线）。 口径与常见行情软件一致（国内习惯乘 2）： DIF  = EMA(close, fastperiod) - EMA(close, slowperiod)   # 快慢均线之差
- `chan/Math/RSI.py` · 77 行 — RSI（相对强弱指标），Wilder 口径。 diff  = close_t - close_{t-1} 上涨力度 up   = diff 中为正的部分
- `chan/Math/TrendLine.py` · 172 行 — 趋势线拟合（用于线段的支撑线/压力线）。 调用点只有一处：`Seg/Seg.py:update_bi_list()`，当线段内的笔数 >= 3 时算两条线： support_trend_line    = CTrendLine(bi_list, TREND_LINE_SIDE.INSIDE)   取笔的「起点」
- `chan/Math/TrendModel.py` · 53 行 — 趋势模型：对某一数值序列做「固定窗口滑动统计」，可选用均值/最大值/最小值。 用途：给 BI 等对象挂一组多周期统计值（如 N 日均线、近 T 根K线的区间高/低点）， 供买卖点判定与绘图读取。配置入口是 CChanConfig 的 mean_metrics / trend_metrics。
- `chan/Math/__init__.py` · 2 行 — 技术指标与形态计算：MACD / RSI / KDJ / BOLL / Demark / 趋势线。
- `chan/Plot/ChanPicture.py` · 873 行 — chan（本缠论内核）的**数据出口**：把缠论计算结果摊平成前端可直接消费的 JSON 结构。 ``CChan`` 算完之后，内部是一张互相引用的对象图:: 原始K线 KLU ──包含合并──> 合并K线 KLC ──> 笔 Bi ──> 线段 Seg ──┬─> 中枢 ZS
- `chan/Plot/PlotMeta.py` · 241 行 — 缠论绘图**元数据**模块：把缠论对象（合并K线/笔/线段/中枢/买卖点…）压扁成一批只含 绘图所需数值的轻量 meta 对象，供 Plot/ChanPicture.py 直接序列化给前端。 设计要点：
- `chan/Plot/__init__.py` · 2 行 — 绘图层：元数据 PlotMeta 与序列化出口 ChanPicture。
- `chan/Seg/Eigen.py` · 77 行 — 特征序列元素（CEigen）模块。 CEigen 就是"由若干根笔合并而成的特征序列元素"： 它复用 Combiner/KLine_Combiner.py 的包含合并逻辑，只是被合并的元素从原始K线换成了笔。
- `chan/Seg/EigenFX.py` · 306 行 — 特征序列（CEigenFX）模块：缠论特征序列法判定线段的"引擎"。 一个 CEigenFX 就是一个方向上的特征序列： 上升线段的内部笔是"向下笔"，它们合并成若干元素（CEigen），
- `chan/Seg/Seg.py` · 274 行 — 线段（Seg）模块：缠论链路中「笔 -> 线段」这一环。 一条线段 CSeg 由若干根笔（LINE_TYPE = CBi）构成，反过来它自己也可以被 更高一层的"线段的线段"（LINE_TYPE = CSeg）当成元素 —— 这两层共用同一个类，
- `chan/Seg/SegConfig.py` · 40 行 — 线段的配置模块：CSegConfig。 它只决定两件事：用哪种算法切线段（seg_algo）， 以及尾部凑不满线段的笔怎么收尾（left_method）。
- `chan/Seg/SegListChan.py` · 139 行 — 线段算法之一：缠论原生的「特征序列法」（seg_algo='chan'，也是默认算法）。 判定的核心是特征序列（见 Seg/EigenFX.py）： 上升线段的内部是那些向下笔，它们两两做包含处理后形成"特征序列元素"，
- `chan/Seg/SegListComm.py` · 297 行 — 线段列表的公共基类模块。 CSegListComm 是三种线段算法（chan 特征序列法 / 1+1 都业华法 / break 线段破坏法） 的共同基类，它自己不负责"怎么切线段"，只负责三件公共的事：
- `chan/Seg/SegListDYH.py` · 177 行 — 线段算法之一：都业华的「1+1 终结法」（seg_algo='1+1'，已不推荐使用）。 注意：本文件的实现目前整体处于注释停用状态（KLine_List 的线段工厂也不再导入它）， 只保留代码原文作为算法参考，新代码请用 Seg/SegListChan.py（特征序列法）。
- `chan/Seg/SegListDef.py` · 132 行 — 线段算法之一：「线段破坏法」（seg_algo='break'，已不推荐使用）。 注意：本文件的实现目前整体处于注释停用状态（KLine_List 的线段工厂也不再导入它）， 只保留代码原文作为算法参考，新代码请用 Seg/SegListChan.py（特征序列法）。
- `chan/Seg/__init__.py` · 2 行 — 线段（CSeg 及三种算法实现）模块。
- `chan/ZS/ZS.py` · 391 行 — 中枢（ZS）模块：缠论链路中「线段 -> 中枢 -> 买卖点」的中间一环。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 【本模块：中枢（笔中枢 / 线段中枢）】 -> 买卖点
- `chan/ZS/ZSConfig.py` · 28 行 — 中枢的配置模块：CZSConfig，决定中枢"怎么构造、要不要合并"。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 中枢（规则由本配置控制） -> 买卖点
- `chan/ZS/ZSList.py` · 281 行 — 中枢列表（ZSList）模块：决定"哪些笔/线段该构成一个中枢、中枢要不要合并"。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 【本模块：中枢列表】 -> 买卖点
- `chan/ZS/__init__.py` · 2 行 — 中枢（CZS / CZSList / CZSConfig）模块。

## ai · 缠论之上的信号层：背驰 / 波浪 / 策略 / LLM 客户端与会话

- `ai/__init__.py` · 2 行 — AI 子包：LLM 客户端、诊股会话、背驰/波浪/策略等信号计算。
- `ai/analysis_agent.py` · 163 行 — 缠论 + LLM 智能分析 — 将缠论分析结果交给大模型生成策略建议
- `ai/chat_sessions.py` · 82 行 — 轻量 AI 诊股会话（进程内内存）。
- `ai/divergence.py` · 480 行 — 背驰自动判断 — 基于 chan 缠论笔对象 CBi 的 MACD 面积 + RSI/KDJ 背离检测。 改写说明（原实现基于 ``chanlun.elements.Bi``）： ``Bi`` 是纯数据模型，一根笔只有 ``start`` / ``end`` 两个日期，要取区间数据只能
- `ai/elements.py` · 53 行 — AI 策略信号的输出契约。 本模块从 ``chanlun/elements.py`` 迁来。原文件曾定义缠论链路上所有的数据模型 （KLine / Bi / XianSegment / Zhongshu / BuySellPoint ……），但随着计算引擎换成
- `ai/llm_client.py` · 174 行 — 统一 LLM 客户端 — 支持 DeepSeek 和 Gemini，自动降级
- `ai/strategy_engine.py` · 330 行 — 策略推荐引擎
- `ai/wave_classifier.py` · 107 行 — 走势自动分类 — 上涨/下跌/盘整

## routers · HTTP 入口（FastAPI）

- `routers/__init__.py` · 2 行 — FastAPI 路由模块集合。
- `routers/chanlun_routes.py` · 472 行 — 缠论 HTTP 路由：单级别完整分析（笔 / 线段 / 中枢 / 买卖点 / 支撑压力位）。 本层只做三件事： 1. 限流（check_chanlun_rate_limits）；
- `routers/comments.py` · 126 行 — 笔记（/api/comments）路由：某只股票下的个人笔记增删改查。 实现函数带 _impl 后缀、全部走 asyncio.to_thread， 因为底层是同步 JSON 文件读写，不能直接在事件循环里做。
- `routers/diagnosis.py` · 368 行 — 【已停用】AI 诊股路由 — 当前整文件为注释状态，main.py 中也没有挂载。 停用前的职责：把缠论数据喂给 LLM，以 SSE 流式返回回答。 SSE 协议（三帧）：
- `routers/stocks.py` · 683 行 — 【已停用】行情与选股路由 — 当前整文件为注释状态，main.py 中也没有挂载。 停用前的职责：限流 → 参数校验 → asyncio.to_thread 包装 → 出参清洗， 取数交给 services/akshare_service.py 与 services/screening_service.py。
- `routers/system.py` · 77 行 — 系统级路由：/health 健康检查、/api/settings 设置的读写。
- `routers/watchlist.py` · 95 行 — 自选股（/api/watchlist）路由：列表（带行情缓存）与增删。

## services · 取数与业务：A 股 akshare、港股富途、缠论选股

- `services/__init__.py` · 2 行 — 取数与业务服务层：A 股行情（akshare）、港股（富途 OpenD）、缠论选股。
- `services/akshare_service.py` · 1854 行 — 股票数据服务。 K 线（日/周/月/分钟）走 baostock，其余行情（实时报价、板块、热门、新闻等）走 腾讯 / 东方财富 / 新浪的 HTTP 接口。
- `services/futu_hk_service.py` · 1609 行 — 港股数据服务 - 使用富途 OpenD（futu-api）获取港股行情与 K 线 与 akshare_service.py（A 股）平行存在。 接口签名与返回结构刻意与 akshare_service 对齐，上层路由可以直接换数据源：
- `services/screening_service.py` · 635 行 — 选股服务 — 按基础指标、缠论买卖点、MACD+SKDJ 双金叉筛选股票。 缠论部分基于 chan，但没有直接 ``new CChan(code, ..., DATA_SRC.BAO_STOCK)``， 而是拆成「取数」与「计算」两步：K 线仍走 services.akshare_service.get_kline_hist

## stores · 本地 JSON 持久化

- `stores/__init__.py` · 2 行 — 进程内持久化层：本地 JSON 文件，仅适用单节点。
- `stores/local_json.py` · 190 行 — 自选、笔记、系统设置的 JSON 持久化。 单进程内用 RLock 保护读改写；写入使用临时文件 + os.replace 尽量原子化。 多 worker / 多机部署时各进程状态不一致——仅适合单机或文档化约束下的部署。

## utils · 通用工具：缓存 / 限流 / 重试 / 代码归一 / 指标

- `utils/__init__.py` · 281 行 — 轻量级工具集合：LRU 缓存（带 TTL）、HTTP 重试装饰器、请求限流。 除级的纯函数助手按模块单独提供，调用方直接按路径导入： - ``utils.datetime_fmt``：日期归一化 / 短日期格式化
- `utils/datetime_fmt.py` · 98 行 — 统一 datetime / 日期字符串格式化。 存在的原因是数据源与模型层的日期类型不统一：pandas 给的是 Timestamp， 缠论模型里可能是 datetime / date / 纯字符串，甚至带时区后缀。
- `utils/indicators.py` · 279 行 — 共享技术指标计算（选股 / 背驰 / 前端算法对齐）。 本模块是「后端与前端、选股与个股分析」共用的唯一指标口径来源： - list 版（ema_series / calc_macd_lists / calc_skdj_lists / 双金叉判定）
- `utils/log.py` · 76 行 — 全局日志装配：给根 logger 挂上「控制台 + 按大小滚动的文件」两路输出。 用法（整个进程只生效一次，重复调用会被忽略）： from utils.log import setup_logging
- `utils/numbers.py` · 36 行 — 数值安全转换工具：把数据源的脏值统一转成 JSON 合法的有限浮点数。 存在的原因有两个： 1. 数据源（akshare / 东财）常返回 "--"、""、None 这类占位值，
- `utils/stock_code.py` · 182 行 — 股票代码归一化：把前端 / 命令行传进来的各种写法统一成各数据源要的格式。 同一个标的在项目里有好几种写法在流转:: 002384        前端搜索框、自选股、URL path 里最常见（裸 6 位）

## 附：改动前必看的坑

- `CChan` 构造即联网查股票基本信息，必须包 try/except。
- `CBS_Point.type` 是 `BSP_TYPE` **列表**（一根笔可同时命中多类买卖点），不是单值。
- `CTime` 无比较运算符，时间先后一律用 `klu.idx`。
- KLU 的 `macd/rsi/kdj` 懒创建：配置没开就没该属性，读取前要 `hasattr` / 判 None。
- 手工喂 K 线给 `CKLine_List` 必须自己 `set_idx(i)` + `kl_type`，喂完统一 `cal_seg_and_zs()`。
- baostock 参数非法时返回 `None` 而非带 `error_code` 的对象；无 limit 参数，`start_date=None` 会拉全历史。
- `CBaoStock.do_init/do_close` 用类属性 + 全局 login/logout，线程池并发会互踢——这也是 `screening_service` 不直接构造 `CChan` 的原因。
- `utils/indicators` 口径与前端 `stockIndicators` 一致，改一处要同步前端。

## 附：重新生成

```bash
cd backend && python docs/gen_notes.py
```

- 一次产出两份：`backend-notes.md`（极简，模块级）与 `backend-notes-full.md`（类/函数级）。
- 跳过 `.venv/` / `__pycache__/` / `docs/` 自身；行数 > 60 且无任何定义的文件判定为「整文件注释停用」。
- 说明只取 docstring 正文段，遇 `Args:`/`Returns:`/`要点:` 即停；签名去掉类型注解，超 64 字符截断。
- 要再短：调脚本顶部的 `MAXMOD`；要更长：改 `MAXDOC`。
