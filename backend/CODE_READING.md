# backend 全部 Python 文件 · 阅读笔记

> 统计口径：19,077 行 / 76 个 .py。其中 `chan/`（**本项目自研**的缠论实现）10,652 行，
> 其余第一方代码 8,425 行，`services/` 两个数据源大文件 3,427 行。
> 读的时候按「入口 → 基础设施 → 领域层 → 数据源」四层组织。
>
> 注：`chan/` 是本仓库自己写的缠论内核，**不是第三方 vendored 库**，可自由修改；
> 代码里残留的 "newchan" 只是历史文案（实际包名就是 `chan`）。

## 0. 分层与数据流

```
HTTP 请求
  └─ routers/          限流 + 参数校验 + asyncio.to_thread 包装
      ├─ services/     取数（A股 baostock+东财腾讯新浪 / 港股 富途 OpenD）
      ├─ chan/         缠论计算（K线→笔→线段→中枢→买卖点）→ Plot 序列化
      ├─ ai/           背驰 / 走势分类 / 策略引擎 / LLM
      └─ utils/ stores/ 缓存·限流·代码归一·指标 / JSON 持久化
```

一次个股分析的主链路（当前唯一活着的重链路）：

```
/api/chanlun/{code}
 → to_baostock_code 归一 → normalize_date_param(730天)
 → CChan(code, DATA_SRC.BAO_STOCK, lv=[K_DAY], QFQ)   ← 构造即联网查基本信息
 → chan/Plot.ChanPicture.serialize_chanlun_analysis → JSON
```

---

## 1. 进程入口与基础设施

| 文件 | 职责 | 关键逻辑 |
|---|---|---|
| `main.py` (78) | FastAPI 应用 | `lifespan` 里 `apply_startup_ai_model()` + 8 个缓存 `purge_expired()`；CORS 通配时自动关 credentials。**只挂了 4 个 router**：chanlun / watchlist / comments / system；`stocks`、`diagnosis` 是注释状态 |
| `config.py` (53) | 环境变量 | `CORS_ORIGINS`、`FINANCE_TLS_RELAXED`、`TRUST_X_FORWARDED_FOR`（默认关，防 XFF 伪造绕过限流）、`DEEPSEEK_MODEL_ID`（默认 deepseek-v4-pro）、`SCREENING_WORKERS`(12, clamp 1–32)、`PORT`(8010) |
| `http_adapter_patch.py` (35) | requests monkey-patch | 替换 `HTTPAdapter.send`：东财/腾讯/新浪/同花顺主机强制无代理、默认 timeout 12s、可选关 TLS 校验。**必须在任何行情请求前 import**，main.py 把它排在 `config` 之后 |
| `run_server.py` (36) | 启动器 | 清空 6 个代理环境变量 + `NO_PROXY=*`，再 `subprocess` 起 `python -m uvicorn main:app` |
| `deps.py` (77) | 依赖与限流 | `client_ip()`（XFF 需开关）；`check_chanlun_rate_limits`(tokens clamp 1–8)、`check_screening_rate_limits`(=3)、`check_kline_rate_limits`、`check_light_api_rate_limits`、`check_ai_diagnosis_rate_limits`，全部 429 |
| `stores/local_json.py` (165) | JSON 持久化 | watchlist / comments / settings 三个文件；每类一把 `RLock`；`_atomic_write_json` = 临时文件 + `os.replace`；读失败只 warn 回落空；模块导入即 `init_stores()`；`apply_startup_ai_model()` 把设置里的模型写回 LLM 单例 |

---

## 2. utils（通用件）

- **`__init__.py` (265)**：`LRUCache`（OrderedDict + RLock + TTL，`delete_prefix` / `purge_expired` / `stats`）；`with_retry`（指数退避+抖动）；`RateLimiter`（滑动窗口计数，key>64 字符 sha256 截断，>1500 key 自动 prune）。
  预建实例：7 个缓存（chanlun 300s / multi 90s / ai_rule 90s / ai_llm 300s / watchlist 15s / market 60s / sector·news 120s）、6 个限流器（chanlun 全局 500、每 IP 90；kline 1000/200；ai 诊股 120/30；light 2000/300，窗口均 60s）。
  `__init__` 只重导出 `stock_code` 的五个函数，其余子模块按路径导入。
- **`numbers.py` (35)**：`finite_float` —— 脏值→有限浮点，拦 NaN/inf（JSON 不支持这两个字面量）。
- **`datetime_fmt.py` (97)**：`normalize_date_param(value, default_days)` 认 `YYYY-MM-DD/YYYYMMDD/斜杠` 并校验月日越界，失败按窗口回退（None=不限）；`format_date_short` 输出 `YYYY-MM-DD`（先判 datetime 再判 date，否则多出 `T00:00:00`）。
- **`stock_code.py` (181)**：`split_code`（前缀/后缀/纯数字三种形态，兼容 `. - _ 空格`，不足 6 位左补零，认不出原样返回不抛错）→ `is_valid_symbol` → `detect_market`（60/68/9/5/11→sh，8/4→bj，其余 sz；`000001` 按个股口径判 sz）→ `to_baostock_code`（**幂等**、带市场标识时尊重原值不重判、北交所直接 ValueError）。
- **`indicators.py` (278)**：前后端共用的唯一指标口径。list 版（`ema_series` / `calc_macd_lists` / `calc_skdj_lists` 通达信 SMA 写法 / 两个 `*_gold_cross_indices` / `_valid_dual_cross_at` / `find_latest_dual_cross_bar`）供选股；`calculate_macd_df`（`bar=(dif-dea)*2`）供背驰。**<30 根样本直接放弃双金叉**（EMA/SKDJ 未收敛）。

---

## 3. chan —— 自研缠论内核（本项目自己实现，勿误当作第三方库）

计算链路：`原始K线 → 包含合并(KLC) → 分型 → 笔 → 线段 → 中枢 → 买卖点`，每级都有对应 Config。

- **`Chan.py` (624) `CChan`**：顶层入口。`load()` = 建数据源 → 各级迭代器 → 逐级喂 K 线 → 收尾 `cal_seg_and_zs`。
  `load_iterator` 是递归主体（取一根→校验时间递增→喂本级→驱动次级别→校验级别对齐；越过父 K 线则缓存）。
  `trigger_load()` 支持外部灌数据；`step_load()` 回放逐步产出快照；`get_latest_bsp()`；`chan_dump_pickle/chan_load_pickle/chan_pickle_restore`（存盘前断开双向链表以免爆栈）。
- **`ChanConfig.py` (307)**：把扁平 dict 分发到 bi/seg/zs/bsp 子配置。`ConfigWithCheck` 是「读一个删一个」的一次性包装，**残留键即非法参数**直接报错（防止配错参数名被默认值吞掉）。`GetMetricModel()` 每次造**全新**指标模型（MACD/均线/趋势/BOLL/Demark/RSI/KDJ，都是有状态的流式算子）。
- **`KLine/`**：`CKLine_Unit`（单根 K 线，`autofix=True` 可修 `open<low` 这类畸形数据；指标属性**懒创建**，没配就是没有）、`CKLine`（合并 K 线，含跳空判定 `has_gap_with_next`）、`CKLine_List`（某级别全部产物容器；`add_single_klu` 增量推进笔，`cal_seg_and_zs()` 批量结算线段/中枢/买卖点）、`TradeInfo`（成交字段，缺补 None）。
- **`Combiner/`**：`CKLine_Combiner[T]` 是被两处复用的公共底座——T=KLU 得合并 K 线，T=CBi 得特征序列元素。`test_combine`/`try_add`/`update_fx` 三件套，`get_peak_klu` 从后往前找造出极值的那根。
- **`Bi/` (Bi 732 + BiList 518 + BiConfig)**：`CBi` 端点由 `begin_klc.fx` 定方向；`get_begin_klu/get_end_klu` 是精确端点 KLU（**不是 KLC**，极值未必在首根）；力度算法 `cal_macd_metric` 支持 AREA/PEAK/FULL_AREA/DIFF/SLOPE/AMP/RSI/量能。`CBiList.can_make_bi` 三关：跨度 → 分型配对(`FX_CHECK_METHOD`) → 终点是否极值；虚笔（unsure）机制 + `sure_end` 栈用于尾部改写与还原。
- **`Seg/`**：三种切段算法共基类 `CSegListComm`（`seg_algo`: chan 特征序列法默认 / 1+1 都业华 / break 线段破坏；`left_method`: peak / all 补收尾）。`Eigen`+`EigenFX` 是特征序列法引擎（元素攒到 3 个判一次分型，`actual_break` 防假突破，`can_be_end` 三态 True/None/False）。`CSeg` 线段本身，笔数<3 永不为确认段。
- **`ZS/` (441+337+35)**：`CZS` 中枢 = 参与元素的区间交集（low 取各元素低点最大、high 取各元素高点最小）。`CZSList` 是构造状态机：先试延伸 → 攒 free list → 够 2~3 个试构造 → 新中枢再按 `zs_combine_mode`(zs/peak) 合并。`is_divergence(config, out_bi)` 给出背驰布尔 + 力度比。
- **`BuySellPoint/`**：`CBS_Point`（`type` 是 **BSP_TYPE 列表**，一根笔可同时是多类；`is_buy` 是方向权威；`klu` 是尾端极值 K 线；`features` 特征袋）。`CBSPointList` 分桶存储 + **尾部失效撤销**（每来新 K 线先撤掉未定稿的旧买卖点）+ 1/2/3 类判定（`bsp_iter()` 按类型桶、`bsp_iter_v2()` 按 idx 从新到旧归并、`getSortedBspList()` 时间序）。
- **`Plot/ChanPicture.py` (871)** —— **整个库的数据出口**，把对象图摊平成前端 JSON。三个公开入口：`GetPlotMeta(chan)` / `get_plot_lv_data(meta,…)` / `serialize_chanlun_analysis(chan,…)`。内含合 K 线/分型/笔/线段/中枢/信号的序列化、`_bsp_confidence`（纯启发式：1类0.75 / 2类0.70 / 3类0.65，线段级 +0.05）、`_serialize_support_resistance`、`_detect_trend`（无段→未知；中枢>1→盘整；否则近 3 段投票）。
- **`Math/`**：MACD / RSI(Wilder) / KDJ / BOLL / Demark(TD Sequential) / 趋势模型 / 趋势线拟合（供线段支撑压力线）。
- **`Common/`**：`CEnum`（KL_TYPE 值越大级别越高；BSP_TYPE 首字符即主类型）、`CTime`（**天级别 auto=True 时落到 23:59**，保证日线时间戳大于同日分钟线）、`ChanException`（ErrCode 分 0-99/100-199/200-299 三段）、`cache.make_cache`（实例级 memoize）、`func_util`（级别比较、区间重叠 `has_overlap`、安全 float）。
- **`DataAPI/`**：`CCommonStockApi` 四条契约（`get_kl_data` 生成器 / `SetBasciInfo` / `do_init` / `do_close`，后两个是 **classmethod，只能操作类属性**）。`CBaoStock` 用类变量 `is_connect` 做登录幂等，**code 原样透传不做归一化**——这是外层必须自己归一化的根因。

---

## 4. ai —— 策略层（基于自研 chan 包的对象）

- **`divergence.py` (479)**：`DivergenceDetector(df, idx_offset=0)`。核心改动：笔区间改用 `get_begin_klu()/get_end_klu()` 的 **KLU idx 闭区间**切片（旧版按日期过滤易错位）；**指标优先取 KLU 上引擎算好的值**（全历史递推，比截断窗口临时重算准），取不到才退 pandas 列。判据：同向柱面积比 < 0.85 且价格创新高/新低 → `prob = min(1, 1-ratio+0.5)`，RSI/KDJ 同向背离各 +0.07。喂 `df.tail(200)` 必须传 offset。
- **`wave_classifier.py` (106)**：`classify(segments, zhongshus, current_price)` —— 无中枢看近 3 段方向投票；有中枢按价格在中枢上/中/下判定，+0.2 置信。`multi_level_resonance` 需 ≥2 个同向级别。**入参必须同级配对**（线段配线段中枢）。
- **`strategy_engine.py` (327)**：5 个 `CBS_Point` 适配函数（`_bsp_labels` 返回**列表**、`_bsp_idx` 用 KLU idx 而非 CTime、**CTime 无比较运算符**、`_bsp_price` 买取 low/卖取 high、`_bsp_confidence` 回落 0.6）。`generate_signal()` 取最近买/卖点 → 置信度（背驰 +0.15 / 中枢>2 个 ×0.85 / 趋势同向 +0.1）→ 风险 → 止损止盈（按 ±3% / ±5% 估算，因为 `CBS_Point` 不带止损位）→ 描述。
- **`elements.py` (52)**：只剩 `AiSignal`（pydantic，`signals: list[CBS_Point]`，需 `arbitrary_types_allowed=True`）。
- **`analysis_agent.py` (148)**：`build_analysis_prompt`（最近 30 根 K 线 + 5 笔 + 3 中枢 + 背驰 + 5 买卖点）+ `parse_llm_response`（先试 ```json 块，再试首尾花括号，失败 **返回 None 而不是兜“观望/0.0”**，避免污染规则结果）。
- **`llm_client.py` (151)**：DeepSeek Chat Completions / Gemini generateContent；密钥走请求头（Gemini `x-goog-api-key`，避免泄进 URL 与日志）；全局单例 + `set_llm_model`。
- **`chat_sessions.py` (67)**：进程内会话，max 10 轮（20 条），TTL 6h，上限 200，LRU 淘汰。

---

## 5. services —— 取数与选股

### `akshare_service.py` (1,836) —— A 股
- **K 线走 baostock**：全局单会话设计，`_bs_lock`(RLock) 串行化 + `_bs_logged_in` 幂等登录；`_bs_query` 会话失效时清登录态重试一次；`_BS_DAYS_PER_BAR` 把 limit 反推成日期区间（baostock 没有 limit 参数）；volume 股→手（`_BS_VOLUME_TO_LOTS`）；日线才有 `tradestatus`，用它 + 空 volume 过滤停牌日。分钟线 baostock 取不到时降级新浪。缓存 key 含 `code:period:adjust:limit`（日线 300s / 分钟 30s）。
- **非 K 线走 HTTP**：实时报价（腾讯，150 只/批）、指数、涨跌家数（东财 clist 优先，新浪兜底）、行业/概念板块、板块成分股、东财五档盘口、个股资料/新闻、新浪搜索、人气榜（东财→同花顺→新浪三级降级）。东财接口是 GBK，手动解码；多 push2 域名轮换。
- `_cache` 是 512 条上限的独立 LRU；`warm_hot_cache()` 是 5 分钟后台预热线程，**当前无人调用**。

### `futu_hk_service.py` (1,593) —— 港股
与 akshare **同签名的镜像**（`get_kline_hist` / `get_realtime_quote` / `get_stock_info` / `get_daily_hot_stocks` / `get_board_constituents_em` / `get_market_overview_bundle` …），意图是上层换数据源不用改代码。
- 健壮性设计值得记：`opend_alive()` 裸 socket 探活（**OpenD 没起时 futu SDK 是无限阻塞不是报错**）；`_create_ctx` 把 SDK 线程改 daemon（否则进程退出卡在 `threading._shutdown`）；`_call` 用 daemon 线程 + `join(CALL_TIMEOUT=15)` 看门狗，`BoundedSemaphore(MAX_CONCURRENT_CALLS=8)` 限并发；`_retry` **只有超时才作废连接重建**（普通错误不动，否则丢掉已订阅的连接）。
- 业务限制：快照 400 只/批（SNAPSHOT_BATCH）、K 线单页 1000 根需翻页（MAX_PAGE=60）、`get_stock_basicinfo` 的 `suspension` 恒为 `'N/A'` 无参考价值（要判停牌看快照 `sec_status`）。

### `screening_service.py` (630) —— 选股
- **刻意不构造 `CChan(BAO_STOCK)`**（`CBaoStock.do_init/do_close` 是类属性 + 全局 login/logout，线程池并发会互踢，且 `do_close` 的 logout 会注销 akshare 已建会话）。改为：`get_kline_hist(limit=200)` → `_build_kl_list`（手工 `CKLine_Unit(autofix=True)` + `set_idx(i)` + `kl_type`，逐个 `add_single_klu`，最后统一 `cal_seg_and_zs()`）→ `_latest_bsp`（`bsp_iter_v2()` 取第一个，从新到旧）→ `_dual_cross_info`（复用 utils.indicators）→ 走势分类。
- `screen_stocks_stream` 是生成器：人气榜取池 → 用热门行情做涨跌幅/量预过滤 → 按需并发拉行业/PE（30 并发）→ `SCREENING_WORKERS` 线程池算缠论 → yield `progress`/`result`/`done`，达 `max_results` 就 cancel 剩余。`screen_stocks` 是等全部算完的兼容版。
- 级别映射注意：`"1min"` 折到 5 分钟（akshare 没有 1 分钟线），`_LEVEL_TO_KLTYPE` 按**数据真实周期**标注。

---

## 6. routers —— HTTP 层

- **`chanlun_routes.py` (468)**：**只有 `/api/chanlun/{code}` 是活的**。入参先 `to_baostock_code`（失败 400）、`start_date` 走 `normalize_date_param(default_days=730)`；`CChan(...)` 构造必须包 try（构造即联网查基本信息，代码不存在/退市在构造阶段就抛 `CChanException`）；`CChanException`→400、其它→500；回写 `result["stock_code"] = code`（保留前端传入原值，不带 `sz.` 前缀）。
  `/multi-level` 与 `/ai` 两个路由 + `build_ai_signal_response` 全部注释（依赖已删除的 `core.chanlun_analysis`）。
- **`watchlist.py` (77)** / **`comments.py` (101)** / **`system.py` (67)**：均生效。watchlist 用 15s 缓存按代码组合聚合批量报价；comments 走 `asyncio.to_thread` + light 限流；system 暴露 `/health`（各缓存 stats + 限流余量 + 会话数）、`/api/settings`(GET/PUT 切 LLM 模型)。
- **`stocks.py` (668)**、**`diagnosis.py` (353)**：**整文件被注释掉**（`main.py` 里也没 include）。stocks 里原本有 K 线/搜索/盘口/新闻/板块/大盘概览/选股与 SSE 选股；diagnosis 是 AI 诊股 SSE（三帧协议：stream-open → token → done）。**这意味着 `services/` 里大量函数当前没有调用方**（见下）。

---

## 7. 当前生效 vs 沉睡代码

**生效的接口**：`/api/chanlun/{code}`、`/api/watchlist*`、`/api/comments*`、`/api/settings`、`/health`。

**沉睡（已实现但无路由可达）**：
- `routers/stocks.py`、`routers/diagnosis.py` 两个整文件；
- `chanlun_routes` 里的 multi-level 与 AI 信号；
- `services/akshare_service` 与 `futu_hk_service` 的大部分函数（指数、涨跌家数、板块、盘口、新闻、搜索、市场概览 bundle 等）只有 `system.py` 的 `*_cache_stats` 被用到；
- `akshare_service.warm_hot_cache()` 无人调用；
- `screening_service.screen_stocks*` 只有被注释掉的 `/screen*` 路由调用；
- `futu_hk_service` 整体只在 `system.py` 里被引用一次（`futu_cache_stats`），实际不提供任何港股接口。

**实际被真正调用的数据源函数**只有：`normalize_stock_code`、`get_realtime_quote`、`get_kline_hist`、`get_daily_hot_stocks`、`get_stock_info`、`get_stock_boards_em`、`akshare_cache_stats`、`futu_cache_stats`。

---

## 8. 值得记住的坑（注释里已写明，改动前务必回看）

1. `CChan` 构造即联网；`CBaoStock` 不做代码归一化 → 归一化只能在路由层做。
2. baostock 参数非法时返回 `None`（不是带 `error_code` 的对象），真实原因会被盖成 `AttributeError`。
3. baostock 没有 limit 参数，`start_date=None` 会拉全历史（实测 2862 根 / 1.3MB / 8~46s）→ 必须显式给窗口。
4. `CBS_Point.type` 是列表，`CTime` 没有比较运算符 → 时间比较一律用 `klu.idx`。
5. 线段/中枢/买卖点必须 `cal_seg_and_zs()` 统一结算；手工喂 K 线时要自己 `set_idx`。
6. `KLU` 上的指标属性是懒创建的（没配就没有），读取前必须 `hasattr` / 判 None。
7. `utils/indicators` 与前端 `stockIndicators` 是同一口径，改一处必须同步另一处。
8. 分钟级别 `CTime` 必须带真实时分，填 0 会被当成天级别。
9. baostock 不收录北交所；`get_kline_hist` 对北交所返回空 → 选股时会被静默跳过（曾表现为「选股 0 条」）。
