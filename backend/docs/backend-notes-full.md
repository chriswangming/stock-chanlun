# backend 注释骨架（完整版）

> 数据源：`backend` 下全部 Python 文件，AST 解析自动生成。
> **86 文件 / 18,777 行 / 86 类 / 651 函数 / 43 常量，docstring 覆盖率 100%**
> 每条说明是 docstring 首段浓缩（≤82 字符），完整版看源码；`L` 为定义行号。

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


### `config.py` · 66 行 — 运行时配置：全部从环境变量读取（须在任何 HTTP 补丁之前加载）。
- 常量 `CORS_ORIGINS_RAW` · `FINANCE_TLS_RELAXED` · `TRUST_X_FORWARDED_FOR` · `DEEPSEEK_MODEL_ID` · `SCREENING_WORKERS` · `PORT` · `LOG_LEVEL` · `LOG_FILE`
- `L15` `_truthy(name, default=False)` — 把环境变量当布尔读：空值走 default，其余只认 1/true/yes/on。
- `L52` `cors_allow_origins()` — 把 CORS_ORIGINS 按逗号拆成列表；配置为空时退回 ["*"]。
- `L61` `cors_allow_credentials()` — 是否允许浏览器带凭证：通配符源时浏览器会拒绝，所以恒为 False。

### `deps.py` · 82 行 — FastAPI 依赖：客户端 IP 识别与限流守卫。
- `L19` `client_ip(request)` — 取客户端 IP：仅在 TRUST_X_FORWARDED_FOR 打开时才信 XFF 头，否则直接用连接地址。
- `L33` `check_chanlun_rate_limits(ip, tokens=1)` — Global + per-IP limits for 缠论 / AI 策略 / 选股等重计算接口.
- `L45` `check_screening_rate_limits(ip)` — 选股接口：消耗更多令牌（与并发缠论分析成本对齐）。
- `L50` `check_kline_rate_limits(ip)` — Global + per-IP limits for K 线 / 导出等行情拉取接口.
- `L61` `check_light_api_rate_limits(ip)` — 搜索 / 热门 / 新闻 / 评论等轻量读接口。
- `L72` `check_ai_diagnosis_rate_limits(ip)` — AI 诊股接口限流：全局 + 每 IP 双重，超限抛 429。

### `http_adapter_patch.py` · 39 行 — 给行情域名猴子补丁 requests 的 HTTPAdapter.send：不走代理、补默认超时、可关 TLS 校验。 必须在任何会触发 requests 访问东财 / 腾讯行情的代码之前导入，否则补丁不生效。
- `L21` `_patched_send(adapter, request, stream=False, timeout=None, ve…)` — requests 的猴子补丁：给行情域名强制不走代理、默认补 12s 超时，按要求可关掉 TLS 校验。

### `main.py` · 96 行 — ChanStock — 缠论智能股票分析系统 FastAPI 后端入口（路由已拆至 routers/）。
- `L47` `async lifespan(_app)` — 启动时恢复 AI 模型并清理各缓存的过期项；yield 之后（关停）不做额外处理。

### `run_server.py` · 56 行 — 启动入口：清掉代理环境变量后，用子进程拉起 uvicorn（等价于 `python -m uvicorn main:app`）。
- _（仅包说明）_

## chan · 自研缠论内核：K线 → 包含 → 分型 → 笔 → 线段 → 中枢 → 买卖点


### `chan/Chan.py` · 640 行 — 缠论计算的顶层入口模块：对外只暴露 CChan 一个类。 链路：数据源 -> CKLine_Unit -> CKLine_List（包含处理 -> 分型 -> 笔 -> 线段 -> 中枢 -> 买卖点）。 - lv_list 必须从大级别排到小级别，高级别K线驱动次级别K线的消费与父子挂载。
- **CChan** — 缠论计算的顶层入口。 职责链路：拉数据源 -> 按级别组装 CKLine_Unit -> 逐级喂给 CKLine_List 增量算笔/线段/中枢，
  - `L39` `__init__(code, begin_time=None, end_time=None, data_src, lv_li…)` — 初始化一只标的的缠论计算并立刻算完（非回放模式下）。
  - `L133` `do_init()` — 为每个级别创建空的 CKLine_List 并重置 kl_datas。
  - `L144` `load_stock_data(stockapi_instance, lv)` — 把数据源吐出的原始K线包装成带 idx 和级别的 CKLine_Unit。
  - `L160` `get_load_stock_iter(stockapi_cls, lv)` — 实例化数据源并拿到某个级别的K线迭代器。
  - `L177` `add_lv_iter(lv_idx, iter)` — 登记某个级别的K线迭代器，同一级别可登记多个、按登记先后顺序消费。
  - `L190` `get_next_lv_klu(lv_idx)` — 取该级别的下一根K线；当前迭代器取完后自动切到同级别的下一个迭代器。
  - `L216` `step_load()` — 回放模式：每喂入一根最高级别K线就对外产出一个快照，便于回测逐步推进。
  - `L237` `trigger_load(inp)` — 外部喂K线入口：不经数据源自拉，直接把已在内存的K线灌进来并一次性算完。
  - `L270` `init_lv_klu_iter(stockapi_cls)` — 为每个级别创建取数迭代器，取不到数据的级别可按配置跳过。
  - `L301` `GetStockAPI()` — 根据 data_src 解析出对应的取数类。
  - `L329` `load(step=False)` — 主加载流程：初始化数据源 -> 登记各级迭代器 -> 逐级喂K线计算 -> 收尾算线段和中枢。
  - `L366` `set_klu_parent_relation(parent_klu, kline_unit, cur_lv, lv_idx)` — 建立父/子级别K线的双向引用，并在开启数据校验时先核对两者是否同一天。
  - `L384` `add_new_kl(cur_lv, kline_unit)` — 把一根K线喂给对应级别的 CKLine_List —— 包含处理、笔/线段的增量更新都在这里触发。
  - `L402` `try_set_klu_idx(lv_idx, kline_unit)` — 给缺失 idx 的K线补下标：已有 idx(>=0) 的原样保留，否则接在同级最后一根之后。
  - `L420` `load_iterator(lv_idx, parent_klu, step)` — 单级别的K线消费循环，也是整个计算的递归主体。 流程：取一根K线 -> 校验时间单调递增 -> 喂给本级 CKLine_List -> 递归驱动次级别
  - `L472` `check_kl_consitent(parent_klu, sub_klu)` — 校验父子级别K线是否落在同一天（年/月/日都要一致），不一致会累加记录。
  - `L492` `check_kl_align(kline_unit, lv_idx)` — 校验一根父级别K线是否在次级别找到了子K线（sub_kl_list 非空）。
  - `L531` `get_latest_bsp(idx=None, number=1)` — 取最近的若干个买卖点。
  - `L548` `chan_dump_pickle(file_path)` — 把整个计算结果序列化到 pickle 文件。
  - `L586` `chan_load_pickle(file_path)` — 从 pickle 文件反序列化一个 CChan，并自动恢复各级对象的 pre/next 链接。
  - `L602` `chan_pickle_restore()` — 重新串接各级对象的双向链表（K线、合并K线、笔、线段、线段段的 pre/next）。

### `chan/ChanConfig.py` · 309 行 — 缠论计算的**全局配置**模块：把用户传入的一个扁平 dict（通常来自命令行 -c 参数或策略脚本） 拆解、校验、分发到各个子模块的配置对象里。 配置分发的层次：
- **CChanConfig** — 缠论计算的全局配置对象：持有各子模块配置，并负责把扁平 dict 分发下去。
  - `L45` `__init__(conf=None)` — 用一个扁平 dict 初始化全部配置。
  - `L121` `GetMetricModel()` — 按配置创建**一根全新**的指标模型列表。
  - `L165` `set_bsp_config(conf)` — 解析买卖点相关配置，生成**笔级别**与**线段级别**两套买卖点配置，各分买侧/卖侧。
- **ConfigWithCheck** — 配置 dict 的**一次性消费包装**：读一个键就删一个键，最后 check() 时残留即非法。 目的是让"配错参数名"立刻暴露，而不是被默认值悄悄吞掉。
  - `L252` `__init__(conf)` — 包一层待校验的配置 dict。
  - `L261` `get(k, default_value=None)` — 取值并从 dict 中移除该键（无论键是否真的存在）。
  - `L280` `items()` — 产出剩余的 (键, 值)，消费完毕后把产出过的键全部删除。
  - `L299` `check()` — 确认配置已被全部消费；仍有残留说明存在无法识别的参数。

### `chan/__init__.py` · 2 行 — 自研缠论计算内核：K线 → 包含合并 → 分型 → 笔 → 线段 → 中枢 → 买卖点。
- _（仅包说明）_

### `chan/Bi/Bi.py` · 611 行 — 笔（Bi）模块：缠论计算链路中「分型 -> 笔」这一环。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 【本模块：笔】 -> 线段(Seg) -> 中枢(ZS) -> 买卖点
- **CBi** — 一根笔：由起点分型合并K线 begin_klc 与终点分型合并K线 end_klc 界定， 方向在 set() 里由起点分型推出（底分型 -> UP，顶分型 -> DOWN）。
  - `L44` `__init__(begin_klc, end_klc, idx, is_sure)`
  - `L79` `clean_cache()` — 清空 @make_cache 缓存：端点/区间被改写后必须调一次，否则会读到旧值。
  - `L85` `begin_klc()` — 笔起点的合并K线（在 set() 时才真正赋值）。
  - `L91` `end_klc()` — 笔终点的合并K线（update_new_end 会被改写成新的末端合并K线）。
  - `L97` `dir()` — 笔方向 BI_DIR；由两端合并K线的取值比较得出，set() 之前为 None。
  - `L103` `idx()` — 笔在所属级别里的序号（第几根笔）。
  - `L109` `type()` — 笔的类型 BI_TYPE（严格笔/非严格笔等），默认 STRICT。
  - `L115` `is_sure()` — 笔是否已确认：True 表示端点不会再被回撤。
  - `L121` `used_to_be_sure()` — 是否曾经确认过：端点被回撤时由 True 变 False，但这里仍留痕。
  - `L127` `is_used_to_be_sure()` — 现在已确认、或历史上曾经确认过（用于区分「从未确认」与「确认后被回撤」）。
  - `L133` `sure_end()` — 曾经被确认过的端点合并K线列表（append_sure_end 追加、restore 后清空）。
  - `L138` `klc_lst()` — 顺序产出本笔覆盖的合并K线（含首尾两根）。
  - `L154` `klc_lst_re()` — 逆序产出本笔覆盖的合并K线（沿 KLC 的 pre 链表从 end_klc 倒回 begin_klc）。
  - `L170` `seg_idx()` — 本笔所属线段的序号，由 set_seg_idx 写入，尚未归属线段时为 None。
  - `L174` `set_seg_idx(idx)` — 回写所属线段的下标（外部 KLine/KLine_List.py 的 cal_seg 每次重算线段时整刷一遍）。
  - `L182` `check()` — 自检笔的方向与首尾位置是否自洽，不自洽就抛异常。
  - `L200` `set(begin_klc, end_klc)` — 唯一允许改写端点的入口，内部顺序固定：写端点 -> 定方向 -> 自检 -> 清缓存。
  - `L223` `get_begin_val()` — 笔起点的价格值：向上笔取起点 KLC 的 low，向下笔取 high。
  - `L234` `get_end_val()` — 笔终点的价格值：向上笔取终点 KLC 的 high，向下笔取 low（与 get_begin_val() 严格对称）。
  - `L239` `get_begin_klu()` — 笔真正的起点原始K线：向上笔取起点 KLC 里打出最低价的那根，向下笔取打出最高价的那根。
  - `L253` `get_end_klu()` — 笔真正的终点原始K线：向上笔取终点 KLC 里打出最高价的那根，向下笔取打出最低价的那根。
  - `L267` `amp()` — 笔的振幅 \|get_end_val()-get_begin_val()\|，恒非负；要跨价位标的比较请用百分比口径 Cal_MACD_amp()。
  - `L272` `get_klu_cnt()` — 本笔覆盖的原始K线根数：首尾 KLU 的 idx 之差再 +1（首尾两根都算在内）。
  - `L277` `get_klc_cnt()` — 本笔覆盖的合并K线根数：首尾 KLC 的 idx 之差再 +1。
  - `L290` `_high()` — 笔区间的最高价：向上笔取 end_klc.high（顶在终点），向下笔取 begin_klc.high（顶在起点）。
  - `L302` `_low()` — 笔区间的最低价：向上笔取 begin_klc.low（底在起点），向下笔取 end_klc.low（底在终点）。
  - `L307` `_mid()` — 笔区间的中位价 (_high()+_low())/2，粗略代表这笔的"重心高度"。
  - `L312` `is_down()` — 是否是向下笔（dir == BI_DIR.DOWN）。
  - `L317` `is_up()` — 是否是向上笔（dir == BI_DIR.UP）。
  - `L321` `update_virtual_end(new_klc)` — 把尾部改写到 new_klc，同时把这笔降级为虚笔。
  - `L335` `restore_from_virtual_end(sure_end)` — 把虚笔还原成确认笔：尾部退回 sure_end（BiList 传的是 sure_end 栈里最早的那个）。
  - `L348` `append_sure_end(klc)` — 把一个"曾经成立过"的尾部 KLC 压入 sure_end 栈。
  - `L358` `update_new_end(new_klc)` — 单纯把尾部换成新的 KLC，不改变确认状态。
  - `L370` `cal_macd_metric(macd_algo, is_reverse)` — 按 macd_algo 指定的算法算出本笔的"力度"标量，用于和相邻的反向笔做背驰比较。
  - `L412` `Cal_Rsi()` — 笔区间的 RSI 极值力度：向上笔取区间最大 RSI，向下笔取 10000/(区间最小 RSI)。
  - `L427` `Cal_MACD_area()` — 笔区间内 MACD 同色柱的面积之和（对应 MACD_ALGO.FULL_AREA）： 向下笔只累加负柱、向上笔只累加正柱的 \|macd\|…
  - `L450` `Cal_MACD_peak()` — 笔区间内 MACD 同色柱的峰值（\|macd\| 的最大值，初值 1e-7）： 向下笔只认负柱、向上笔只认正柱。
  - `L469` `Cal_MACD_half(is_reverse)` — MACD 面积类（MACD_ALGO.AREA）的分派入口：is_reverse 为真走倒序版，否则走正序版。
  - `L483` `Cal_MACD_half_obverse()` — 从笔头开始顺着累加 MACD 面积，直到柱子变色为止（所谓的"半面积"）。
  - `L514` `Cal_MACD_half_reverse()` — 倒序版的半面积：从笔尾往前累加，遇到与尾端柱子异色的柱就停。
  - `L540` `Cal_MACD_diff()` — 笔区间内 MACD 柱最大值与最小值之差（对应 MACD_ALGO.DIFF）。
  - `L558` `Cal_MACD_slope()` — 笔的斜率：单位K线上的相对涨幅（注意与 MACD 无关）。
  - `L575` `Cal_MACD_amp()` — 笔的涨跌幅（百分比口径）。
  - `L590` `Cal_MACD_trade_metric(metric, cal_avg=False)` — 量能类力度：某个量能字段（成交额/成交量/换手率）在笔区间内的合计，cal_avg 为真时取均值。

### `chan/Bi/BiConfig.py` · 63 行 — 笔的配置模块：CBiConfig。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 笔(Bi，规则由本配置控制) -> 线段 -> 中枢 -> 买卖点
- **CBiConfig** — 笔的配置项集合。 六个开关分别控制：
  - `L30` `__init__(bi_algo='normal', is_strict=True, bi_fx_check='half',…)`

### `chan/Bi/BiList.py` · 427 行 — 笔列表（BiList）模块：缠论链路中"哪些K线能成笔、旧笔要不要回收"的判定者。 链路位置：原始K线 -> 包含合并(CKLine) -> 分型 -> 【Bi/Bi.py 单根笔】 + 【BiList 成笔规则】 -> 线段(Seg) -> 中枢(ZS) -> 买卖点
- **CBiList** — 一个级别的笔序列容器 + 成笔状态机。 核心状态：
  - `L44` `__init__(bi_conf=CBiConfig())` — bi_conf 是笔的配置 CBiConfig（成笔跨度、分型检查强度、是否把缺口当K线等）。
  - `L78` `try_create_first_bi(klc)` — 尝试画出整个序列的第一笔。
  - `L100` `update_bi(klc, last_klc, cal_virtual)` — 推进笔的主入口：先更新确认笔，再按需追加虚笔；返回笔的形态是否发生了变化。
  - `L121` `can_update_peak(klc)` — 判断能否把"最后一笔"整个撤掉、把倒数第二笔延长到 klc（次高点/次低点处理）。
  - `L151` `update_peak(klc, for_virtual=False)` — 执行"撤掉最后一笔 + 把倒数第二笔延长到 klc"，返回是否执行成功。
  - `L171` `update_bi_sure(klc)` — 更新确认笔：先清掉尾部虚笔，再按 klc 的分型决定是新开一笔还是延伸最后一笔。
  - `L208` `delete_virtual_bi()` — 删除尾部的虚笔，并把虚笔此前确认过的尾部逐一还原成确认笔。
  - `L235` `try_add_virtual_bi(klc, need_del_end=False)` — 尝试把笔延伸到最新走势上，形成（或更新）尾部虚笔。
  - `L272` `add_new_bi(pre_klc, cur_klc, is_sure=True)` — 追加一根新笔（is_sure 为 False 时是虚笔）并串好 pre/next 链表。
  - `L286` `satisfy_bi_span(klc, last_end)` — 判断两个分型之间的跨度够不够成笔。
  - `L313` `get_klc_span(klc, last_end)` — 计算两个 KLC 之间的跨度（KLC 数量，配置允许时中间每处跳空各再记 1）。
  - `L335` `can_make_bi(klc, last_end, for_virtual=False)` — 成笔判定：三关全过，这两点才可以连成一笔。
  - `L354` `try_update_end(klc, for_virtual=False)` — 尝试把最后一笔的尾巴延伸到 klc（最后一笔在新方向上创了新高/新低时）。
  - `L390` `get_last_klu_of_last_bi()` — 取最后一笔终点的原始K线 idx（用于比对"删虚笔前后状态是否变化"），没有笔时为 None。
- `L395` `end_is_peak(last_end, cur_end)` — 判断从 last_end 到 cur_end 这一段里，cur_end 是不是真正的区间极值。

### `chan/Bi/__init__.py` · 2 行 — 笔（CBi / CBiList / CBiConfig）模块。
- _（仅包说明）_

### `chan/BuySellPoint/BSPointConfig.py` · 186 行 — 买卖点配置（BSPointConfig）模块：缠论链路中「中枢 -> 买卖点」这一环的算法参数。 CPointConfig 是"单侧"的一份配置（买侧或卖侧各一份），CBSPointConfig 把这两份打包在一起， 因为同一个标的的买点判据和卖点判据常常要分别调（例如涨得猛的票卖点条件要放宽）。
- **CBSPointConfig** — 买卖点配置的容器：同时持有买侧（b_conf）与卖侧（s_conf）两套 CPointConfig。
  - `L34` `__init__(**args)` — 按同一份入参造出买/卖两套配置。
  - `L44` `GetBSConfig(is_buy)` — 按买卖方向取对应的那份配置（True 取买侧 b_conf，False 取卖侧 s_conf）。
- **CPointConfig** — 单侧（买或卖）的买卖点算法参数集合。 各项含义（默认值见 ChanConfig.set_bsp_config）：
  - `L81` `__init__(divergence_rate, min_zs_cnt, bsp1_only_multibi_zs, ma…)` — 保存单侧的买卖点参数，各项取值的含义见类文档。
  - `L126` `parse_target_type()` — 把 bs_type（逗号分隔字符串或字符串列表）解析成 BSP_TYPE 枚举列表，写入 target_types。
  - `L143` `SetMacdAlgo(macd_algo)` — 把字符串形式的力度口径翻译成 MACD_ALGO 枚举，写进 self.macd_algo。
  - `L171` `set(k, v)` — 改写单个参数（配置装载 / 回测调参的入口）。

### `chan/BuySellPoint/BSPointList.py` · 723 行 — 买卖点列表（BSPointList）模块：缠论计算链路的最后一环。 CBSPointList 是"某个级别上所有买卖点"的容器兼计算器：它拿着上一环算好的笔/线段序列与中枢， 逐条线段判定各类买卖点，按类型分桶存起来，并负责"行情尾部被重算时把失效买卖点撤掉"的去重与更新。
- 常量 `LINE_TYPE` · `LINE_LIST_TYPE`
- **CBSPointList** — 某个级别上全部买卖点的容器 + 计算器。 存储结构：
  - `L62` `__init__(bs_point_config)` — 建一个空的买卖点容器，配置决定后续判定与分桶行为。
  - `L79` `store_add_bsp(bsp_type, bsp)` — 把一个买卖点登记进"按类型分桶"的存储（一个买卖点只登记一次，取它首次命中的类型）。
  - `L95` `add_bsp1(bsp)` — 单独登记一个1类买卖点（T1/T1P）。
  - `L107` `clear_store_end()` — 撤销"尾部失效"的买卖点（分桶存储那一侧）。
  - `L127` `clear_bsp1_end()` — 撤销"尾部失效"的1类买卖点（bsp1_list 那一侧），规则与 clear_store_end 完全一致。
  - `L140` `bsp_iter()` — 按"类型桶"顺序遍历全部买卖点（每个桶内先买后卖，各桶按插入顺序）。
  - `L151` `bsp_iter_v2()` — 按笔 idx 从新到旧归并遍历全部买卖点（多桶 k 路归并），第一个产出的就是最新的买卖点。
  - `L195` `cal(bi_list, seg_list)` — 计算入口：把买卖点重算到最新（幂等，可反复调用）。
  - `L212` `update_last_pos(seg_list)` — 更新定稿游标：从后往前找最后一个"确认线段"，把它的位置记下来。
  - `L231` `seg_need_cal(seg)` — 判断这条线段是否还需要重算买卖点（线段的结束K线还在定稿线之后则为 True）。
  - `L235` `add_bs(bs_type, bi, relate_bsp1, is_target_bsp=True, feature_d…)` — 登记（或合并）一个买卖点 —— 全模块唯一的"去重 + 入库"入口。 要点（每一步都可能提前 return）：
  - `L284` `cal_seg_bs1point(seg_list, bi_list)` — 逐条线段算1类买卖点（T1 趋势背驰 / T1P 盘整背驰）。
  - `L296` `cal_single_bs1point(seg, bi_list)` — 算单条线段上的1类买卖点：先决定"够不够格"，再分派到趋势背驰或盘整背驰。
  - `L322` `treat_bsp1(seg, BSP_CONF, is_target_bsp)` — 趋势背驰1类买卖点（T1）：末笔突破线段最后一个中枢，且相对"进中枢那笔"背驰。
  - `L345` `treat_pz_bsp1(seg, BSP_CONF, bi_list, is_target_bsp)` — 盘整背驰1类买卖点（T1P）：中枢不够格时，改用"末笔 vs 前一同向笔"直接比力度。
  - `L382` `cal_seg_bs2point(seg_list, bi_list)` — 逐条线段算2类买卖点（T2）与类二买卖点（T2S）。
  - `L397` `treat_bsp2(seg, seg_list, bi_list)` — 2类买卖点（T2）：1类之后的第一根回撤笔，回撤幅度不超过 max_bs2_rate。
  - `L444` `treat_bsp2s(seg_list, bi_list, bsp2_bi, break_bi, real_bsp1, B…)` — 类二买卖点（T2S）：2类之后，仍在同一线段内、且区间与2类那笔有重叠的后续同向笔。
  - `L500` `cal_seg_bs3point(seg_list, bi_list)` — 逐条线段算3类买卖点：T3A（中枢在1类之后）+ T3B（中枢在1类之前）。
  - `L536` `treat_bsp3_after(seg_list, next_seg, BSP_CONF, bi_list, real_b…)` — 3A 类买卖点（T3A）：中枢在1类"之后" —— 下一段里的中枢被突破后，回抽不回中枢的那一笔。
  - `L600` `treat_bsp3_before(seg_list, seg, next_seg, bsp1_bi, BSP_CONF, …)` — 3B 类买卖点（T3B）：中枢在1类"之前" —— 本段最后一个中枢之后的回抽笔。
  - `L648` `getSortedBspList()` — 取全部买卖点，按时间（笔 idx 升序）排好序。
  - `L656` `get_latest_bsp(number)` — 取最近的若干个买卖点（从最新到最旧，最多 number 个；number=0 表示取全部）。
- `L666` `bsp2s_break_bsp1(bsp2s_bi, bsp2_break_bi)` — 判断类二那笔是否已经"跌破/涨破"了突破笔的端点：是则说明走成新一轮趋势，应停止继续找类二。
- `L676` `bsp3_back2zs(bsp3_bi, zs)` — 判断回抽笔是否又回到了中枢区间里：是则该笔不构成3类买卖点（3类的核心否决条件）。
- `L687` `bsp3_break_zspeak(bsp3_bi, zs)` — 判断是否突破了中枢的"极值区间"（peak_low/peak_high，比中枢区间 [low, high] 更宽）。
- `L698` `cal_bsp3_bi_end_idx(seg)` — 算 T3B 回抽笔的考察上界（笔 idx，含），返回 int 或 float("inf")。

### `chan/BuySellPoint/BS_Point.py` · 106 行 — 买卖点对象（BS_Point）模块：缠论链路中「中枢 -> 买卖点」这一环的产物定义。 CBS_Point 就是"一个买卖点"：它挂在一根笔（笔级买卖点）或一条线段（线段级买卖点）的尾部， 记录这个位置是买点还是卖点、属于哪几类（1/1p/2/2s/3a/3b）、它对应的1类买卖点是谁，
- 常量 `LINE_TYPE`
- **CBS_Point** — 一个买卖点。 它绑定在一根笔（或一条线段）的尾部，表示"这根笔走完的位置出现了买/卖信号"。
  - `L46` `__init__(bi, is_buy, bs_type, relate_bsp1, feature_dict=None)` — 造一个买卖点并挂到所在笔/线段上。
  - `L69` `add_type(bs_type)` — 追加一个买卖点类型：同一根笔可同时命中多类，只并类型不新建对象。
  - `L73` `type2str()` — 把类型列表拼成逗号分隔字符串（如 "1,2"），供打印/绘图标注，顺序与 type 一致。
  - `L77` `add_another_bsp_prop(bs_type, relate_bsp1)` — 叠加一个新类型，并把该类型对应的1类买卖点合并进来。
  - `L91` `add_feat(inp1, inp2=None)` — 往特征表里写一条（传特征名+值）或一批（传 dict / 另一个 CFeatures）特征。
  - `L100` `init_common_feature()` — 补上与买卖点类型无关的通用特征（目前只有所在笔振幅 bsp_bi_amp），构造时自动调一次。

### `chan/BuySellPoint/__init__.py` · 2 行 — 买卖点（CBS_Point / CBSPointList / 配置）模块。
- _（仅包说明）_

### `chan/ChanModel/Features.py` · 38 行 — 买卖点的**特征（feature）容器**模块。 买卖点在判定过程中会顺带算出一些便于后续筛选/建模的附属量（例如背驰力度比、中枢个数、 是否为线段级别买卖点等），本模块提供一个轻量的键值对容器把它们挂在 CBS_Point 上，
- **CFeatures** — 买卖点特征的轻量字典包装：只暴露读（items / []）与写入（add_feat），不支持删除已写入的键。
  - `L15` `__init__(initFeat=None)` — 用一份初始特征字典建容器（浅拷贝一份，避免外部 dict 被后续 add_feat 污染），None 表示从空开始。
  - `L19` `items()` — 遍历全部 (特征名, 特征值)。
  - `L27` `add_feat(inp1, inp2=None)` — 写入特征：传 (名字, 值) 写一条，只传一个 dict / CFeatures 则批量合并。

### `chan/ChanModel/__init__.py` · 2 行 — 买卖点特征（CFeatures）模块。
- _（仅包说明）_

### `chan/Combiner/Combine_Item.py` · 53 行 — CCombine_Item —— 「包含关系判定用的统一取点适配器」。 缠论的包含处理和笔/线段算法需要反复比较两个元素的 high/low 与时间， 但参与比较的元素可能是三种完全不同的类型（原始K线 / 笔 / 线段），
- **CCombine_Item** — 把 CKLine_Unit / CBi / CSeg 适配成统一的 时间+高低点 视图。
  - `L19` `__init__(item)`

### `chan/Combiner/KLine_Combiner.py` · 360 行 — K线包含处理（缠论最基础的一步）。 本类是整个缠论算法的公共底座，被两个场景复用（泛型参数 T 不同）： - `KLine/KLine.py` 的 CKLine(CKLine_Combiner[CKLine_Unit])：原始K线按包含关系合并成"合并K线(KLC)"；
- 常量 `T=TypeVar('T')`
- **CKLine_Combiner** — 一根"合并K线"的通用实现，T 是本级别被合并的元素类型（CKLine_Unit 或 CBi）。 对外暴露只读属性 time_begin/time_end/high/low/lst/dir/fx，
  - `L41` `__init__(kl_unit, _dir)` — 用第一个元素开出一根新的合并K线。
  - `L68` `clean_cache()` — 清空 @make_cache 的缓存 —— 任何会改动本对象状态的路径都必须调它。
  - `L75` `time_begin()` — 本合并K线的起始时间（取所含第一根原始K线的时间）。
  - `L81` `time_end()` — 本合并K线的结束时间（取所含最后一根原始K线的时间）。
  - `L87` `high()` — 合并区间的最高价（含关系处理时向上取到的最高点）。
  - `L93` `low()` — 合并区间的最低价（含关系处理时向下取到的最低点）。
  - `L99` `lst()` — 被合并进来的原始K线列表（按时间顺序）。
  - `L105` `dir()` — 与上一根合并K线的包含方向（UP/DOWN），用于判定包含关系该怎么合。
  - `L111` `fx()` — 本合并K线的分型类型（顶/底/未知），由后续分型识别写入。
  - `L117` `pre()` — 上一根合并K线；首根没有前驱，取用会直接断言失败。
  - `L124` `next()` — 下一根合并K线；行情末尾那根为 None。
  - `L128` `get_next()` — 取后一根合并K线，并断言它确实存在。
  - `L133` `test_combine(item, exclude_included=False, allow_top_equal=None)` — 判断「新元素 item 与当前 KLC」的位置关系，也就是能不能合并。
  - `L178` `set_fx(fx)` — 直接写分型值，仅供 deepcopy 复原时使用（正常运行路径请走 update_fx）。
  - `L183` `try_add(unit_kl, exclude_included=False, allow_top_equal=None,…)` — 尝试把新元素并入当前 KLC；能合就合，不能合就原样返回方向让调用方新开一根。
  - `L228` `get_peak_klu(is_high)` — 取"真正造出本 KLC 极值"的那一根元素。
  - `L242` `get_high_peak_klu()` — 找最高价所在元素。
  - `L260` `get_low_peak_klu()` — 找最低价所在元素，同样从后往前取最近一根。
  - `L272` `update_fx(_pre, _next, exclude_included=False, allow_top_equal…)` — 用左右两根邻居判定**自身**的分型，并顺便把前后链表接上。
  - `L346` `set_pre(_pre)` — 设置前驱（允许 None，用于链表首尾/序列化前后断开重连）。
  - `L356` `set_next(_next)` — 设置后继（允许 None），同样只改自己的指针。

### `chan/Combiner/__init__.py` · 2 行 — K线包含关系处理（CKLine_Combiner）模块。
- _（仅包说明）_

### `chan/Common/CEnum.py` · 271 行 — 全局枚举与常量定义模块。 集中定义缠论计算全流程共用的枚举类型：数据源（DATA_SRC）、K线级别（KL_TYPE）、 K线方向与分型、笔方向与笔种类、买卖点类型、复权方式、均线/通道类型、线段语义、
- 常量 `BSP_MAIN_TYPE` · `TRADE_INFO_LST`
- **DATA_SRC** — K线数据源类型；CChan.GetStockAPI 依此决定实例化哪个取数类。
  - `L20` ``BAO_STOCK``
  - `L22` ``CCXT``
  - `L24` ``CSV``
  - `L26` ``AKSHARE``
  - `L28` ``FUTU``
- **KL_TYPE** — K线级别。 成员值从 1 递增到 19，值越大级别越高；秒/分钟级别属于「天级别以下」，
  - `L40` ``K_1S``
  - `L42` ``K_3S``
  - `L44` ``K_5S``
  - `L46` ``K_10S``
  - `L48` ``K_15S``
  - `L50` ``K_20S``
  - `L52` ``K_30S``
  - `L54` ``K_1M``
  - `L56` ``K_3M``
  - `L58` ``K_5M``
  - `L60` ``K_10M``
  - `L62` ``K_15M``
  - `L64` ``K_30M``
  - `L66` ``K_60M``
  - `L68` ``K_DAY``
  - `L70` ``K_WEEK``
  - `L72` ``K_MON``
  - `L74` ``K_QUARTER``
  - `L76` ``K_YEAR``
- **KLINE_DIR** — 相邻K线的相对位置关系，用于K线包含处理（KLine_Combiner.test_combine）。
  - `L83` ``UP``
  - `L85` ``DOWN``
  - `L87` ``COMBINE``
  - `L89` ``INCLUDED``
- **FX_TYPE** — 分型类型（顶/底）。
  - `L96` ``BOTTOM``
  - `L98` ``TOP``
  - `L100` ``UNKNOWN``
- **BI_DIR** — 笔的方向。
  - `L107` ``UP``
  - `L109` ``DOWN``
- **BI_TYPE** — 笔的分类标签。当前实现只在 CBi 构造时固定置为 STRICT，其余成员为预留/说明性定义。
  - `L116` ``UNKNOWN``
  - `L118` ``STRICT``
  - `L119` ``SUB_VALUE``
  - `L121` ``TIAOKONG_THRED``
  - `L123` ``DAHENG``
  - `L125` ``TUIBI``
  - `L127` ``UNSTRICT``
  - `L129` ``TIAOKONG_VALUE``
- **BSP_TYPE** — 买卖点的细分类型，值字符串的首字符即主类型（1/2/3 类）。
  - `L140` ``T1``
  - `L142` ``T1P``
  - `L144` ``T2``
  - `L146` ``T2S``
  - `L147` ``T3A``
  - `L148` ``T3B``
  - `L150` `main_type()` — 取买卖点主类型。
- **AUTYPE** — 复权方式，原样透传给数据源的取数接口。
  - `L164` ``QFQ``
  - `L166` ``HFQ``
  - `L168` ``NONE``
- **TREND_TYPE** — 均线 / 通道类型，供 CKLine_Unit 计算 trend 特征及绘图使用。
  - `L175` ``MEAN``
  - `L177` ``MAX``
  - `L179` ``MIN``
- **TREND_LINE_SIDE** — 趋势线（CTrendLine）的取点侧，决定用笔的起点还是终点去拟合直线。
  - `L186` ``INSIDE``
  - `L188` ``OUTSIDE``
- **LEFT_SEG_METHOD** — 线段列表收尾时，剩余的、无法归入已确定线段的笔如何处理（seg.left_method）。
  - `L195` ``ALL``
  - `L197` ``PEAK``
- **FX_CHECK_METHOD** — 笔的顶底分型是否成立的检查强度（BiConfig.bi_fx_check，字符串默认 "strict"）。
  - `L204` ``STRICT``
  - `L206` ``LOSS``
  - `L208` ``HALF``
  - `L210` ``TOTALLY``
- **SEG_TYPE** — 线段所在的层级语义，决定该序列是「由笔构段」还是「由线段构段」。
  - `L217` ``BI``
  - `L219` ``SEG``
- **MACD_ALGO** — 买卖点背驰判断所用的力度指标算法（ChanConfig 的 macd_algo）。
  - `L226` ``AREA``
  - `L228` ``PEAK``
  - `L230` ``FULL_AREA``
  - `L232` ``DIFF``
  - `L234` ``SLOPE``
  - `L236` ``AMP``
  - `L238` ``VOLUMN``
  - `L240` ``AMOUNT``
  - `L242` ``VOLUMN_AVG``
  - `L244` ``AMOUNT_AVG``
  - `L246` ``TURNRATE_AVG``
  - `L248` ``RSI``
- **DATA_FIELD** — 数据源返回的K线字段名常量，统一在这里约定取数用的键名。
  - `L255` ``FIELD_TIME``
  - `L257` ``FIELD_OPEN``
  - `L259` ``FIELD_HIGH``
  - `L261` ``FIELD_LOW``
  - `L263` ``FIELD_CLOSE``
  - `L264` ``FIELD_VOLUME``
  - `L265` ``FIELD_TURNOVER``
  - `L266` ``FIELD_TURNRATE``

### `chan/Common/CTime.py` · 134 行 — 自定义时间类型模块（CTime）。 缠论里K线时间有两类语义，不能用 datetime 直接替代： 1. 天级别以下（分钟/秒线）：时间戳描述的是这根K线的**结束时间**，
- **CTime** — 缠论专用的K线时间：支持「天级别结束时间」语义，且只按 ts 比较大小。
  - `L26` `__init__(year, month, day, hour, minute, second=0, auto=True)`
  - `L58` `to_str()` — 与 __str__ 同格式的字符串输出，供需要显式调用（如日志、图例）的场景使用。
  - `L70` `toDateStr(splt='')` — 只取日期部分拼接成字符串，常用于拼数据源的日期参数或文件名。
  - `L82` `toDate()` — 退化成「纯日期」的 CTime。
  - `L95` `set_timestamp()` — 根据当前字段重算 self.ts（浮点时间戳），构造后每次改字段都需重新调用。

### `chan/Common/ChanException.py` · 185 行 — 错误码与自定义异常模块。 ErrCode 用 IntEnum 按业务域分段编号：缠论计算错误 0~99、交易错误 100~199、 K线数据错误 200~299；段首段尾的 _XXX_BEGIN / _XXX_END 是哨兵位，不参与实际抛错，
- **ErrCode** — 全局错误码，按 0~99 / 100~199 / 200~299 三段划分业务域。
  - `L29` ``COMMON_ERROR``
  - `L31` ``SRC_DATA_NOT_FOUND``
  - `L33` ``SRC_DATA_TYPE_ERR``
  - `L35` ``PARA_ERROR``
  - `L37` ``EXTRA_KLU_ERR``
  - `L39` ``SEG_END_VALUE_ERR``
  - `L41` ``SEG_EIGEN_ERR``
  - `L43` ``BI_ERR``
  - `L45` ``COMBINER_ERR``
  - `L47` ``PLOT_ERR``
  - `L49` ``MODEL_ERROR``
  - `L51` ``SEG_LEN_ERR``
  - `L53` ``ENV_CONF_ERR``
  - `L55` ``UNKNOWN_DB_TYPE``
  - `L57` ``FEATURE_ERROR``
  - `L59` ``CONFIG_ERROR``
  - `L61` ``SRC_DATA_FORMAT_ERROR``
  - `L70` ``SIGNAL_EXISTED``
  - `L72` ``RECORD_NOT_EXIST``
  - `L74` ``RECORD_ALREADY_OPENED``
  - `L76` ``QUOTA_NOT_ENOUGH``
  - `L78` ``RECORD_NOT_OPENED``
  - `L80` ``TRADE_UNLOCK_FAIL``
  - `L82` ``PLACE_ORDER_FAIL``
  - `L84` ``LIST_ORDER_FAIL``
  - `L86` ``CANDEL_ORDER_FAIL``
  - `L88` ``GET_FUTU_PRICE_FAIL``
  - `L90` ``GET_FUTU_LOT_SIZE_FAIL``
  - `L92` ``OPEN_RECORD_NOT_WATCHING``
  - `L94` ``GET_HOLDING_QTY_FAIL``
  - `L96` ``RECORD_CLOSED``
  - `L98` ``REQUEST_TRADING_DAYS_FAIL``
  - `L100` ``COVER_ORDER_ID_NOT_UNIQUE``
  - `L102` ``SIGNAL_TRADED``
  - `L111` ``PRICE_BELOW_ZERO``
  - `L113` ``KL_DATA_NOT_ALIGN``
  - `L115` ``KL_DATA_INVALID``
  - `L117` ``KL_TIME_INCONSISTENT``
  - `L119` ``TRADEINFO_TOO_MUCH_ZERO``
  - `L121` ``KL_NOT_MONOTONOUS``
  - `L123` ``SNAPSHOT_ERR``
  - `L124` ``SUSPENSION``
  - `L126` ``STOCK_IPO_TOO_LATE``
  - `L128` ``NO_DATA``
  - `L130` ``STOCK_NOT_ACTIVE``
  - `L132` ``STOCK_PRICE_NOT_ACTIVE``
- **CChanException** — 缠论库统一异常；在 message 之外额外携带 ErrCode 便于调用方分类处理。
  - `L140` `__init__(message, code)`
  - `L150` `is_kldata_err()` — 判断是否为K线数据类错误。
  - `L159` `is_chan_err()` — 判断是否为缠论计算类错误。

### `chan/Common/__init__.py` · 2 行 — 公共基础：枚举 CEnum、时间 CTime、缓存、异常等。
- _（仅包说明）_

### `chan/Common/cache.py` · 92 行 — 实例级方法结果缓存（memoize）模块。 提供一个装饰器类 make_cache：被装饰的方法在**同一个实例**上第一次调用后结果被存下来， 之后直接返回缓存值。缠论里笔/合并K线的端点、涨跌幅、K线根数等属性会被反复读取
- **make_cache** — 装饰器（描述符）类：把方法的返回值按实例缓存到 instance._memoize_cache 上。
  - `L28` `__init__(func)`

### `chan/Common/func_util.py` · 134 行 — 通用小工具函数模块。 放一些跨模块复用、又不值得单独成类的纯函数：K线级别的大小与顺序判定、 笔方向取反、区间是否重叠、字符串转浮点，以及把 inf 转成可写回源码字面量的小工具。
- `L14` `kltype_lt_day(_type)` — 判断级别是否严格小于日线（即秒/分钟级别）。
- `L27` `kltype_lte_day(_type)` — 判断级别是否小于等于日线。
- `L44` `check_kltype_order(type_list)` — 校验级别列表是否严格从大级别排到小级别。
- `L60` `revert_bi_dir(dir)` — 笔方向取反。
- `L73` `has_overlap(l1, h1, l2, h2, equal=False)` — 判断两个价格区间 [l1, h1] 与 [l2, h2] 是否有交集，用于中枢重叠、笔的重叠等判定。
- `L94` `str2float(s)` — 把字符串安全转成 float，失败时退化成 0.0（数据源里常见空串/占位符）。
- `L114` `_parse_inf(v)` — 把正负无穷转成可写回源码的字面量字符串，供配置导出/代码生成场景使用。

### `chan/DataAPI/BaoStockAPI.py` · 343 行 — A股数据源模块（baostock / 证券宝）。 baostock 是一个免费、免注册的A股历史行情库，核心接口是 `query_history_k_data_plus`（按 标的+级别+日期区间 一次返回全部K线）和
- **CBaoStock** — 证券宝（baostock）A股数据源，实现 CCommonStockApi 的四条契约。
  - `L181` `__init__(code, k_type, begin_date=None, end_date=None, autype=…)`
  - `L196` `get_kl_data()` — 取数主入口：向 baostock 查历史K线，按时间从旧到新逐根产出。
  - `L256` `SetBasciInfo()` — 查标的基本信息，填 self.name 与 self.is_stock。
  - `L283` `do_init()` — 建立 baostock 会话（CChan.load 取数前调用一次，靠 is_connect 做幂等）。
  - `L303` `do_close()` — 登出 baostock（挂在 CChan.load 的 finally 里，必定执行到）。
- `L62` `create_item_dict(data, column_name)` — 把 baostock 的一行原始字符串转成 CKLine_Unit 需要的字段字典。
- `L82` `parse_time_column(inp)` — 把 baostock 的时间字符串解析成 CTime。 按长度区分三种形态：
- `L137` `GetColumnNameFromFieldList(fileds)` — 把 "date,open,high,..." 这样的 baostock 字段名串翻译成 DATA_FIELD 列表。

### `chan/DataAPI/CommonStockAPI.py` · 130 行 — 数据源抽象基类模块（CCommonStockApi）。 这里定义了本缠论内核与所有行情数据源之间的唯一契约层： CChan 只认这个基类，完全不关心底层是 baostock / ccxt / 本地 CSV / 富途，
- **CCommonStockApi** — 行情数据源抽象基类：单个标的单个级别的取数契约。 代表「一只代码 + 一个 KL_TYPE 级别 + 一段日期区间」的一次取数；
  - `L50` `__init__(code, k_type, begin_date, end_date, autype)`
  - `L75` `get_kl_data()` — 取数主入口（抽象方法，子类必须重写）。
  - `L92` `SetBasciInfo()` — 查询并填充标的基本信息（抽象方法，子类必须重写）。
  - `L107` `do_init()` — 整体取数前的初始化钩子（抽象方法，子类必须重写）。
  - `L120` `do_close()` — 取数结束后的清理钩子（抽象方法，子类必须重写）。

### `chan/DataAPI/__init__.py` · 2 行 — 数据源适配层（统一接口 + baostock 等实现）。
- _（仅包说明）_

### `chan/KLine/KLine.py` · 203 行 — 合并后的K线 CKLine（简称 KLC）模块。 链路位置：CKLine_Unit（单根原始K线） -> CKLine（做包含关系处理，把若干根同方向 KLU 并成一根） -> CKLine_List（某级别整段序列）。 它承上启下：
- **CKLine** — 包含处理后的单根合并K线，是判分型、判笔的基本单位。 它自己只负责"序号 / 级别 / 展开成子级别KLC / 校验两点能否连成笔"这几件事，
  - `L29` `__init__(kl_unit, idx, _dir=KLINE_DIR.UP)` — 以一根原始K线为起点，开出本级别的一根新合并K线。
  - `L61` `GetSubKLC()` — 生成本 KLC 在次级别对应的那些合并K线（去相邻重复）。
  - `L82` `get_klu_max_high()` — 返回本 KLC 内所有原始K线的最高价的最大值。
  - `L96` `get_klu_min_low()` — 返回本 KLC 内所有原始K线的最低价的最小值（理由同 get_klu_max_high）。
  - `L100` `has_gap_with_next()` — 判断本 KLC 与后一根 KLC 之间是否存在跳空（价格区间完全没有交集）。
  - `L117` `check_fx_valid(item2, method, for_virtual=False)` — 判断"self 和 item2 这一对（顶/底）分型之间是否够格连成一笔"。

### `chan/KLine/KLine_List.py` · 417 行 — 某一级别的完整K线序列 CKLine_List：合并K线列表 + 笔/线段/线段段 + 各类中枢 + 买卖点容器。 链路位置：CKLine_Unit -> CKLine -> CKLine_List…
- **CKLine_List** — 一个级别的全部计算产物容器，也是"增量推进"的执行者。 它同时持有四个层级的元素序列和三类派生容器：
  - `L82` `__init__(kl_type, conf)` — 建出一个级别的空容器（含笔/线段/中枢/买卖点各层与指标算子），不算任何东西。
  - `L201` `cal_seg_and_zs()` — 把"线段及其上的中枢、买卖点"结算到最新，是重活，分两级同构做两遍。 流程（第一遍，笔 -> 线段 -> 笔中枢）：
  - `L236` `need_cal_step_by_step()` — 判断本序列是否需要在每根新K线到来时就重算线段/中枢。
  - `L249` `add_single_klu(klu)` — 喂入一根新的原始K线，把 lst / 分型 / 笔推进一格，必要时顺带结算线段与中枢。
  - `L289` `klu_iter(klc_begin_idx=0)` — 把合并K线序列展开成扁平的原始K线流，用于只关心单根K线的场景。
- `L32` `get_seglist_instance(seg_config, lv)` — 按配置里的线段算法名，造出对应实现的"线段列表"对象。
- `L303` `cal_seg(bi_list, seg_list, last_sure_seg_start_bi_idx)` — 由下层元素序列重算上层序列，并给下层每个元素回写"我属于上层第几段"。 两个层级共用这一个函数：(bi_list=笔列表, seg_list=线段列表) 算线段，
- `L363` `update_zs_in_seg(bi_list, seg_list, zs_list)` — 双向绑定上层线段与中枢：把中枢挂到所属线段上，并给每个中枢回填入/出笔和笔列表。

### `chan/KLine/KLine_Unit.py` · 326 行 — 单根（未经包含关系处理的）K线单元 CKLine_Unit（简称 KLU），整条缠论计算链路的最底层。 链路位置：数据源 -> CKLine_Unit -> CKLine（若干根 KLU 合并成一根 KLC）-> CKLine_List（某级别整段序列，含笔/线段/中枢容器）。 KLU 之间靠三组指针表达关系：
- **CKLine_Unit** — 单根K线单元：时间、开高低收 + 成交明细 + 各类指标，并维系本级别链表和跨级别父子关系。 它的 high/low 是被保证过的（low <= open/close <= high），
  - `L35` `__init__(kl_dict, autofix=False)` — 用数据源给的一行行情 dict 造出一根K线。
  - `L126` `klc()` — 反向取到自己所属的那根合并K线 CKLine。
  - `L136` `set_klc(klc)` — 写入反向引用：标记"我这根原始K线被合并进了哪根 CKLine"。
  - `L151` `idx()` — 本K线在本级别里的顺序下标：从 0 开始递增，-1 表示尚未分配。
  - `L155` `set_idx(idx)` — 设置/改写本K线的下标。
  - `L174` `check(autofix=False)` — 校验开高低收是否自洽：low 必须是四价最小值，high 必须是四价最大值。
  - `L203` `add_children(child)` — 把一根次级别K线登记为自己的子K线。
  - `L216` `set_parent(parent)` — 把一根父级别K线登记为自己的上级（反向：子 -> 父）。
  - `L228` `get_children()` — 依次产出本K线对应的所有次级别K线（按加入顺序）。
  - `L232` `_low()` — 返回最低价；与 CBi/CSeg 的 _low 同签名，供中枢等逻辑统一取值。
  - `L236` `_high()` — 返回最高价；与 CBi/CSeg 的 _high 同签名，供中枢等逻辑统一取值。
  - `L240` `set_metric(metric_model_lst)` — 按顺序把各个指标漏斗喂一根收盘价（或高低收），并把结果写回自身属性。
  - `L270` `get_parent_klc()` — 取父级别K线所属的合并K线。
  - `L287` `include_sub_lv_time(sub_lv_t)` — 判断某个次级别时间是否落在本K线（及其子孙K线）覆盖的时间范围内。
  - `L310` `set_pre_klu(pre_klu)` — 把本K线接到某根前驱K线之后，建立双向链表。

### `chan/KLine/TradeInfo.py` · 44 行 — 单根K线的附属交易信息（成交量、成交额、换手率等）容器。 链路位置：CKLine_Unit -> CTradeInfo。 它是"只读旁路"数据：由数据源给的原始 dict 抽取而来，挂在 klu.trade_info 上，
- **CTradeInfo** — 一根K线的成交类指标集合。 键值固定为 TRADE_INFO_LST 里枚举的那些字段，缺哪个就存 None，
  - `L21` `__init__(info)` — 从原始K线 dict 中抽取成交类字段，缺失的补 None。

### `chan/KLine/__init__.py` · 2 行 — K线对象：单根 CKLine_Unit、列表 CKLine_List、合并K线 CKLine。
- _（仅包说明）_

### `chan/Math/BOLL.py` · 69 行 — 布林线（BOLL）指标。 MID  = N 个样本的均值 UP   = MID + 2 * 标准差
- **BOLL_Metric** — 单根K线的布林三轨值。
  - `L23` `__init__(ma, theta)`
- **BollModel** — 布林线计算器：流式喂值，内部只保留最近 N 个样本。
  - `L39` `__init__(N=20)`
  - `L48` `add(value)` — 喂入一个样本并返回当前窗口的布林轨。
- `L15` `_truncate(x)` — 把 0 替换成 1e-7（规避除零）；非 0 原样返回。

### `chan/Math/Demark.py` · 381 行 — Demark（TD Sequential，德马克序列）指标。 用来在一个线段/趋势的尾部数"衰竭计数"，帮助判断趋势是否接近尽头。分两个阶段： Setup（准备阶段，默认 9 计数）
- 常量 `T_DEMARK_TYPE`
- **C_KL** — Demark 计算用的K线最小快照（只保留要参与比较的字段）。
  - `L44` `v(is_close, _dir)` — 取参与比较的价格。
- **T_DEMARK_INDEX** — 一条 Demark 计数记录：哪个方向、哪个阶段、数到几。
  - _（无公开成员）_
- **CDemarkIndex** — 在某一根K线上产生的 Demark 计数集合（可能同时来自多个 series）。
  - `L75` `__init__()` — 初始化一个空的计数集合（data 里每条记录是一条 Setup/Countdown 计数）。
  - `L79` `add(_dir, _type, idx, series)` — 追加一条计数记录。
  - `L91` `get_setup()` — 取出所有 Setup 阶段的计数记录。
  - `L95` `get_countdown()` — 取出所有 Countdown 阶段的计数记录。
  - `L99` `update(demark_index)` — 合并另一个索引对象里的全部记录（直接 extend，不查重）。
- **CDemarkCountdown** — 倒计时阶段：Setup 完成后继续数 13 个数，或中途被反向突破作废。
  - `L112` `__init__(_dir, kl_list, TDST_peak)`
  - `L125` `update(kl)` — 喂入一根K线，推进倒计时。
- **CDemarkSetup** — 准备阶段：连续数 Setup 计数，数满后创建并驱动 CDemarkCountdown。
  - `L166` `__init__(_dir, kl_list, pre_kl)`
  - `L184` `update(kl)` — 喂入一根K线，推进 Setup / Countdown，并返回这一根上产生的计数。
  - `L216` `add_setup()` — Setup 计数 +1，并记录一条 setup 记录。
  - `L221` `cal_TDST_peak()` — 计算 TDST 极值线（Setup 区间的高/低点，用于判断 Countdown 是否作废）。
- **CDemarkEngine** — Demark 驱动器：逐根K线喂入，内部维护方向相反的多个 CDemarkSetup 并存。 注意所有配置都写在**类属性**上（构造时直接赋值给 CDemarkEngine.XXX），
  - `L256` ``DEMARK_LEN``
  - `L257` ``SETUP_BIAS``
  - `L258` ``COUNTDOWN_BIAS``
  - `L259` ``MAX_COUNTDOWN``
  - `L260` ``TIAOKONG_ST``
  - `L261` ``SETUP_CMP2CLOSE``
  - `L262` ``COUNTDOWN_CMP2CLOSE``
  - `L264` `__init__(demark_len=9, setup_bias=4, countdown_bias=2, max_cou…)`
  - `L299` `update(idx, close, high, low)` — 喂入一根K线，推进所有 series 并返回这一根上产生的计数。
  - `L342` `cal_result()` — 汇总所有 series 在这一根K线上产生的计数。
  - `L354` `clear()` — 清掉两类失效 series：Setup 已终止且没进倒计时的、倒计时已结束的。
  - `L363` `clean_series_from_setup_finish()` — 让每个 series 都吃掉最新这根K线，并在 Setup 数满时"只保留这一个 series"。

### `chan/Math/KDJ.py` · 77 行 — KDJ（随机指标）。 RSV = (close - 窗口内最低) / (窗口内最高 - 窗口内最低) * 100 K   = 2/3 * 前一根K + 1/3 * RSV
- **KDJ_Item** — 单根K线的 K/D/J 三项值。
  - `L17` `__init__(k, d, j)`
- **KDJ** — KDJ 计算器：流式喂入每根K线的最高/最低/收盘。
  - `L32` `__init__(period=9)`
  - `L43` `add(high, low, close)` — 喂入一根K线的最高/最低/收盘并返回该根K线的 KDJ。

### `chan/Math/MACD.py` · 76 行 — MACD 指标（指数平滑异同移动平均线）。 口径与常见行情软件一致（国内习惯乘 2）： DIF  = EMA(close, fastperiod) - EMA(close, slowperiod)   # 快慢均线之差
- **CMACD_item** — 单根K线算完的 MACD 快照，供背驰比较和绘图直接读取。
  - `L22` `__init__(fast_ema, slow_ema, DIF, DEA)`
- **CMACD** — MACD 计算器：流式累加，每根K线调一次 add()。
  - `L41` `__init__(fastperiod=12, slowperiod=26, signalperiod=9)`
  - `L53` `add(value)` — 喂入当前K线的价格（缠论里用收盘价），返回这根K线的 MACD 快照。

### `chan/Math/RSI.py` · 77 行 — RSI（相对强弱指标），Wilder 口径。 diff  = close_t - close_{t-1} 上涨力度 up   = diff 中为正的部分
- **RSI** — RSI 计算器：流式喂入收盘价。
  - `L19` `__init__(period=14)`
  - `L31` `add(close)` — 喂入一根K线的收盘价并返回当前 RSI。

### `chan/Math/TrendLine.py` · 172 行 — 趋势线拟合（用于线段的支撑线/压力线）。 调用点只有一处：`Seg/Seg.py:update_bi_list()`，当线段内的笔数 >= 3 时算两条线： support_trend_line    = CTrendLine(bi_list, TREND_LINE_SIDE.INSIDE)   取笔的「起点」
- **Point** — 拟合用的二维点：x 取合并K线的 idx（相对序号），y 取笔的端点价。
  - `L34` `cal_slope(p)` — 计算 self -> p 的斜率。
- **Line** — 一条候选趋势线：过点 p、斜率为 slope。
  - `L54` `cal_dis(p)` — 计算点 p 到本直线的距离（点到直线距离公式，直线写作 slope*x - y + c = 0）。
- **CTrendLine** — 趋势线拟合器：构造时直接完成计算，结果放在 `self.line`。
  - `L70` `__init__(lst, side)`
  - `L80` `cal(lst)` — 搜索评分最优的趋势线并写入 self.line。
- `L114` `init_peak_slope(_dir, side)` — 给斜率比较设一个"尽可能宽松"的初值，使得第一个通过的候选一定能被选中。
- `L134` `cal_tl(c_p, _dir, side)` — 以 c_p[0] 为锚点，在其余点里挑一个斜率最合适的，返回直线与最优点下标。

### `chan/Math/TrendModel.py` · 53 行 — 趋势模型：对某一数值序列做「固定窗口滑动统计」，可选用均值/最大值/最小值。 用途：给 BI 等对象挂一组多周期统计值（如 N 日均线、近 T 根K线的区间高/低点）， 供买卖点判定与绘图读取。配置入口是 CChanConfig 的 mean_metrics / trend_metrics。
- **CTrendModel** — 单个周期的滑动统计器，窗口长度固定为 T。
  - `L15` `__init__(trend_type, T)`
  - `L25` `add(value)` — 喂入一个样本并返回当前窗口的统计值。

### `chan/Math/__init__.py` · 2 行 — 技术指标与形态计算：MACD / RSI / KDJ / BOLL / Demark / 趋势线。
- _（仅包说明）_

### `chan/Plot/ChanPicture.py` · 873 行 — chan（本缠论内核）的**数据出口**：把缠论计算结果摊平成前端可直接消费的 JSON 结构。 ``CChan`` 算完之后，内部是一张互相引用的对象图:: 原始K线 KLU ──包含合并──> 合并K线 KLC ──> 笔 Bi ──> 线段 Seg ──┬─> 中枢 ZS
- `L72` `_json_num(v, round_digits=None)` — 把数值转成 JSON 友好的 float。
- `L99` `_json_enum_name(v)` — 枚举 → 可读名字（``FX_TYPE.TOP`` -> ``"TOP"``）；非枚举退化成 ``str(v)``。
- `L106` `_time_to_str(t)` — 把缠论时间统一成 ``'YYYY-MM-DD HH:mm:ss'``。
- `L130` `_klu_volume(klu)` — 取单根原始K线的成交量，缺字段时兜 0。
- `L150` `_kl_type_to_level(kl_type)` — :class:`KL_TYPE` → 前端 level 标签（``daily`` / ``30min`` / …）。
- `L173` `_serialize_klines(meta, rd, with_indicators)` — 摊平原始K线（KLU）序列，并顺手把每根K线上挂的指标抽成等长数组。
- `L255` `_klc_dir_fx(klc)` — 把合并K线的 ``type`` 拆成前端要的 ``dir`` 与 ``fx``。…
- `L285` `_serialize_merged_klines(meta, rd)` — 摊平包含处理后的合并K线（KLC）序列 —— 前端主图用它画「合并区间」框。
- `L325` `_serialize_fenxings(meta, rd)` — 从合并K线序列里抽出分型（顶 / 底）。
- `L350` `_serialize_bis(meta, rd, idx2date)` — 摊平笔序列。
- `L380` `_serialize_xians(meta, rd, idx2date)` — 摊平线段（笔的上一级）与线段的线段（segseg）序列。
- `L424` `_serialize_zhongshus(meta, rd, idx2date)` — 摊平中枢序列。
- `L452` `_compact_all_none(arr)` — 整列全是 None 时折成 None，避免往响应里塞一大片无意义的 null。
- `L466` `_bsp_type_cn(bsp_type, is_buy)` — 把 chan 的买卖点类型串翻成前端展示用的中文标签。
- `L486` `_bsp_confidence(bsp_meta)` — 估算买卖点置信度（**纯启发式，不是校准过的概率**）。 ``CBS_Point`` 本身不带置信度，只有一堆特征；而前端
- `L505` `_serialize_signals(meta, rd, idx2date, level)` — 摊平买卖点序列（笔级 + 线段级，按时间升序）。
- `L545` `_serialize_support_resistance(meta, rd, idx2date, signals)` — 汇总支撑位 / 阻力位。 来源与权重（沿用旧引擎 ``chanlun/signal_detector.detect_support_resistance`` 的口…
- `L660` `_detect_trend(meta)` — 判断当前走势：上涨 / 下跌 / 盘整 / 未知。
- `L690` `_make_summary(trend, signals, zhongshus)` — 生成一段简短的自然语言总结，供前端直接展示。
- `L723` `GetPlotMeta(chan)` — 为每个级别各造一份画图元数据。
- `L736` `get_plot_lv_data(meta, round_digits=None)` — 把**单一级别**的全部缠论画图数据摊平成可直接 ``json.dump`` 的字典。…
- `L839` `serialize_chanlun_analysis(chan, round_digits=None)` — 把 ``CChan`` 的**最高级别**结果序列化成前端响应字典（路由层入口）。

### `chan/Plot/PlotMeta.py` · 241 行 — 缠论绘图**元数据**模块：把缠论对象（合并K线/笔/线段/中枢/买卖点…）压扁成一批只含 绘图所需数值的轻量 meta 对象，供 Plot/ChanPicture.py 直接序列化给前端。 设计要点：
- **Cklc_meta** — 合并K线（合并后的一根）的绘图元数据：起止 idx、高低点、以及类型。
  - `L29` `__init__(klc)` — 从合并K线取绘图字段；type 优先用分型类型，没分型时退化成合并方向。
- **CBi_meta** — 笔的绘图元数据：首尾 KLU 的 idx 与价格、方向、笔类型、是否已确定。
  - `L43` `__init__(bi)` — 从 CBi 取绘图字段；横坐标为笔两端 KLU 的 idx，纵坐标为笔端点值。
- **CSeg_meta** — 线段的绘图元数据：起止点 + 方向 + 确定标记 + 支撑/压力趋势线。
  - `L58` `__init__(seg)` — 从 CSeg 取绘图字段。
  - `L87` `format_tl(tl)` — 把趋势线按本线段的起止价格裁成一段 (x0,y0,x1,y1)，供前端在本线段范围内画线。
- **CEigen_meta** — 特征序列元素的绘图元数据：起止 idx 与上下沿，外加宽高（供画矩形）。
  - `L108` `__init__(eigen)` — 从 CEigen 取绘图字段；w/h 是矩形宽高，前端画特征序列用。
- **CEigenFX_meta** — 特征序列分型的绘图元数据：三个元素 + 缺口标记 + 分型方向。
  - `L121` `__init__(eigenFX)` — 从 CEigenFX 取绘图字段。
- **CZS_meta** — 中枢的绘图元数据：上下沿、起止 idx、宽高、确定标记、子中枢列表。
  - `L138` `__init__(zs)` — 从 CZS 取绘图字段；子中枢递归转成 CZS_meta，因此子中枢可以层层展开。
- **CBS_Point_meta** — 买卖点的绘图元数据：位置 idx、标注价格、买/卖、类型串。
  - `L154` `__init__(bsp, is_seg)` — 从 CBS_Point 取绘图字段。
  - `L169` `desc()` — 生成标注文案，如 "b1,2"（1/2类买点）/"s3a"（3a类卖点）；线段级前面加 ※。
- **CChanPlotMeta** — 绘图中枢：一次把一个级别的全部缠论对象转成 meta，并保留原始 CKLine_List 以便回查。
  - `L178` `__init__(kl_list)` — 按级别装配全部绘图元数据。
  - `L214` `klu_iter()` — 按时间顺序摊平遍历全部原始K线（越过合并K线的分组）。
  - `L219` `sub_last_kseg_start_idx(seg_cnt)` — 倒数第 seg_cnt 条线段的起始子K线 idx，用于"只画最后 N 段"时定位起点；数量不够返回 0（即从头画）。
  - `L226` `sub_last_kbi_start_idx(bi_cnt)` — 倒数第 bi_cnt 根笔的起始子K线 idx，用于"只画最后 N 笔"时定位起点；数量不够返回 0。
  - `L233` `sub_range_start_idx(x_range)` — 倒数第 x_range 根原始K线对应的子K线 idx；找不到（x_range 超过总根数）返回 0。

### `chan/Plot/__init__.py` · 2 行 — 绘图层：元数据 PlotMeta 与序列化出口 ChanPicture。
- _（仅包说明）_

### `chan/Seg/Eigen.py` · 77 行 — 特征序列元素（CEigen）模块。 CEigen 就是"由若干根笔合并而成的特征序列元素"： 它复用 Combiner/KLine_Combiner.py 的包含合并逻辑，只是被合并的元素从原始K线换成了笔。
- **CEigen** — 一个特征序列元素（若干根同方向笔按包含关系合并后的产物）。 继承来的 high/low/dir/fx/lst 含义与合并K线完全一致（只是元素是笔），
  - `L32` `__init__(bi, _dir)` — 用第一根笔开出一个特征序列元素。
  - `L42` `update_fx(_pre, _next, exclude_included=False, allow_top_equal…)` — 先按普通（或特征序列）口径判分型，再补判"与前一元素之间是否存在缺口"。
  - `L62` `GetPeakBiIdx()` — 取线段终点应该落在哪一根笔上（返回该笔的下标）。

### `chan/Seg/EigenFX.py` · 306 行 — 特征序列（CEigenFX）模块：缠论特征序列法判定线段的"引擎"。 一个 CEigenFX 就是一个方向上的特征序列： 上升线段的内部笔是"向下笔"，它们合并成若干元素（CEigen），
- **CEigenFX** — 一个方向上的特征序列。 核心状态：
  - `L47` `__init__(_dir, exclude_included=True, lv=SEG_TYPE.BI)` — 构造一个方向上的特征序列。
  - `L65` `treat_first_ele(bi)` — 用这根笔开出第一个元素；一个元素不可能判分型，恒返回 False。
  - `L70` `treat_second_ele(bi)` — 处理第二个元素：先试着并入第一个元素，并不了才新开一个。
  - `L88` `treat_third_ele(bi)` — 处理第三个元素：新开第三元素（或被第二元素合并掉）之后，给第二元素判分型。
  - `L116` `add(bi)` — 喂进一根笔（方向必须与本序列服务的线段方向相反），攒到三个元素时判一次分型。
  - `L135` `reset()` — 三个元素凑不出分型时，扔掉最旧的那个元素再来，返回重放后是否已判出分型。
  - `L158` `can_be_end(bi_lst)` — 判断这个分型能不能真的终结一条线段，以及这条线段算不算确认。
  - `L183` `is_down()` — 本序列服务于下降线段（即装的是向上笔）。
  - `L187` `is_up()` — 本序列服务于上升线段（即装的是向下笔）。
  - `L191` `GetPeakBiIdx()` — 取线段终点笔的下标（由中间那个元素说了算）。
  - `L196` `all_bi_is_sure()` — 构成这条线段的笔是否都已确认（全部是确认笔或曾经确认过才为 True）。
  - `L206` `clear()` — 清空整个序列（元素窗口与笔列表一起清空）。
  - `L216` `actual_break()` — 判断第二个元素是否"真的"被第三个元素突破了（防止合并造成的假分型）。
  - `L269` `find_revert_fx(bi_list, begin_idx, thred_value, break_thred)` — 在有缺口的分型之后，去找那个能证明线段真正终结的"反向分型"。

### `chan/Seg/Seg.py` · 274 行 — 线段（Seg）模块：缠论链路中「笔 -> 线段」这一环。 一条线段 CSeg 由若干根笔（LINE_TYPE = CBi）构成，反过来它自己也可以被 更高一层的"线段的线段"（LINE_TYPE = CSeg）当成元素 —— 这两层共用同一个类，
- 常量 `LINE_TYPE`
- **CSeg** — 一条线段（或一条"线段的线段"，取决于泛型 LINE_TYPE）。 核心状态：
  - `L50` `__init__(idx, start_bi, end_bi, is_sure=True, seg_dir=None, re…)` — 用首尾笔造出一条线段，登记方向、确认状态等核心状态。
  - `L92` `set_seg_idx(idx)` — 记录本线段在"线段的线段"里的序号（由上层 cal_seg 回写）。
  - `L96` `check()` — 自检线段的端点高低与长度是否自洽。
  - `L119` `add_zs(zs)` — 把一个落在本线段区间内的中枢挂到本线段上。
  - `L128` `cal_klu_slope()` — 线段的斜率：(终点值-起点值)/K线序号差/起点值。
  - `L137` `cal_amp()` — 线段的涨跌幅：端点价差 / 起点价。
  - `L141` `cal_bi_cnt()` — 本线段包含多少根笔。
  - `L145` `clear_zs_lst()` — 清空本线段的中枢列表（重复调用 update_zs_in_seg 时用来保证幂等）。
  - `L149` `_low()` — 线段区间的最低价：下降线段的低点在终点，上升线段的低点在起点（与 CBi._low 同理）。
  - `L153` `_high()` — 线段区间的最高价：上升线段的高点在终点，下降线段的高点在起点（与 CBi._high 同理）。
  - `L157` `is_down()` — 是否是下降线段。
  - `L161` `is_up()` — 是否是上升线段。
  - `L165` `get_end_val()` — 线段的终点价格值（终笔的终点值）。
  - `L169` `get_begin_val()` — 线段的起点价格值（首笔的起点值）。
  - `L175` `is_used_to_be_sure()` — 是否是（或曾经是）确认线段。
  - `L179` `amp()` — 线段的振幅（端点价差的绝对值）。
  - `L183` `get_end_klu()` — 线段的终点原始K线（终笔的终点 KLU）。
  - `L187` `get_begin_klu()` — 线段的起点原始K线（首笔的起点 KLU）。
  - `L191` `get_klu_cnt()` — 本线段覆盖了多少根原始K线（首尾都算）。
  - `L195` `cal_macd_metric(macd_algo, is_reverse)` — 算本线段的力度标量（用于线段级别的背驰比较）。
  - `L210` `Cal_MACD_slope()` — 线段的斜率（与 CBi.Cal_MACD_slope 同一口径）。
  - `L224` `Cal_MACD_amp()` — 线段的涨跌幅（与 CBi.Cal_MACD_amp 同一口径）。
  - `L238` `update_bi_list(bi_lst, idx1, idx2)` — 把 [idx1, idx2] 区间（含两端）内的笔登记为本线段成员，并给每根笔回写 parent_seg。
  - `L253` `get_first_multi_bi_zs()` — 取本线段内第一个非一笔中枢（由单根笔构成的中枢不算），没有则返回 None。
  - `L257` `get_multi_bi_zs_lst()` — 取本线段内所有非一笔中枢。
  - `L261` `get_final_multi_bi_zs()` — 取本线段内最后一个非一笔中枢（从后往前找），没有则返回 None。
  - `L271` `get_multi_bi_zs_cnt()` — 本线段内非一笔中枢的个数。

### `chan/Seg/SegConfig.py` · 40 行 — 线段的配置模块：CSegConfig。 它只决定两件事：用哪种算法切线段（seg_algo）， 以及尾部凑不满线段的笔怎么收尾（left_method）。
- **CSegConfig** — 线段的配置项集合。 seg_algo   ：'chan' 特征序列法（默认）、'1+1' 都业华法、'break' 线段破坏法
  - `L25` `__init__(seg_algo='chan', left_method='peak')` — 把配置字符串翻译成内部枚举并做合法性检查。

### `chan/Seg/SegListChan.py` · 139 行 — 线段算法之一：缠论原生的「特征序列法」（seg_algo='chan'，也是默认算法）。 判定的核心是特征序列（见 Seg/EigenFX.py）： 上升线段的内部是那些向下笔，它们两两做包含处理后形成"特征序列元素"，
- **CSegListChan** — 用特征序列切分线段的线段列表实现。
  - `L31` `__init__(seg_config=CSegConfig(), lv=SEG_TYPE.BI)` — 按线段配置与层级语义构造特征序列法的线段列表。
  - `L35` `do_init()` — 重算前的清理：把尾部未确认的线段撤掉，让它们有机会被重新算出来。
  - `L57` `update(bi_lst)` — 重算线段列表（特征序列法对基类 update 的实现）。
  - `L71` `cal_seg_sure(bi_lst, begin_idx)` — 用两个方向的特征序列容器并行推进，直到有线段被确定出来。
  - `L114` `treat_fx_eigen(fx_eigen, bi_lst)` — 处理一个已经成型的特征序列分型：据此切出一条线段。

### `chan/Seg/SegListComm.py` · 297 行 — 线段列表的公共基类模块。 CSegListComm 是三种线段算法（chan 特征序列法 / 1+1 都业华法 / break 线段破坏法） 的共同基类，它自己不负责"怎么切线段"，只负责三件公共的事：
- 常量 `SUB_LINE_TYPE`
- **CSegListComm** — 线段序列的公共基类（泛型：SUB_LINE_TYPE 是本层线段由什么元素构成，笔或线段）。 子类只要实现 update(bi_lst) 这一个抽象方法即可：
  - `L43` `__init__(seg_config=CSegConfig(), lv=SEG_TYPE.BI)` — 初始化线段序列容器。
  - `L55` `do_init()` — 清空线段列表（各子类会重写它，顺便把笔的 parent_seg 等反向引用清掉）。
  - `L79` `left_bi_break(bi_lst)` — 判断最后一条线段之后的笔，有没有突破该线段终点笔的高/低点（线段可能被破坏）。
  - `L98` `collect_first_seg(bi_lst)` — 还没有任何线段时的补收尾：把开头的笔收成第一条（未确认）线段。
  - `L129` `collect_left_seg_peak_method(last_seg_end_bi, bi_lst)` — PEAK 策略下的递归收尾：反复"在新的方向上找极值笔 -> 收成一段"，直到无可再收。
  - `L155` `collect_segs(bi_lst)` — 处理"已经有线段、但尾部还有剩余笔"的收尾。
  - `L186` `collect_left_seg(bi_lst)` — 补收尾的总入口：把还没被任何线段覆盖的尾部笔收成一条未确认线段。
  - `L205` `collect_left_as_seg(bi_lst)` — ALL 策略：把上一条线段之后剩余的笔，全部收成一条未确认线段。
  - `L221` `try_add_new_seg(bi_lst, end_bi_idx, is_sure=True, seg_dir=None…)` — 真正往 lst 里加一条线段（添加首段时可能顺带把首段拆成两段）。
  - `L251` `add_new_seg(bi_lst, end_bi_idx, is_sure=True, seg_dir=None, sp…)` — try_add_new_seg 的容错包装，参数与它完全一致，返回是否成功添加。
  - `L271` `update(bi_lst)` — 由三个算法子类实现：根据本层的元素序列重算线段列表。
  - `L275` `exist_sure_seg()` — 是否至少有一条已确认线段。
- `L280` `FindPeakBi(bi_lst, is_high)` — 在一堆笔里找"最极端的那一根"（最高点所在的向上笔 / 最低点所在的向下笔），没有则返回 None。

### `chan/Seg/SegListDYH.py` · 177 行 — 线段算法之一：都业华的「1+1 终结法」（seg_algo='1+1'，已不推荐使用）。 注意：本文件的实现目前整体处于注释停用状态（KLine_List 的线段工厂也不再导入它）， 只保留代码原文作为算法参考，新代码请用 Seg/SegListChan.py（特征序列法）。
- **CSegListDYH** — 用 1+1 终结法切分线段的线段列表实现（seg_algo='1+1'）。
  - `L64` `__init__(seg_config=CSegConfig(), lv=SEG_TYPE.BI)` — 按配置与层级语义构造 1+1 法的线段列表。
  - `L75` `update(bi_lst)` — 重算线段列表（1+1 法对基类 update 的实现）。
  - `L91` `cal_bi_sure(bi_lst)` — 按 1+1 形态扫描笔列表、切出确认线段。
  - `L127` `cal_bi_unsure(bi_lst)` — 在最后一条线段被突破的前提下，推算一条未确认线段。
  - `L152` `try_update_last_seg(bi_lst)` — 把最后一条（已确认）线段的终点往后挪到同方向笔的新极值处，并降级为未确认。
- `L22` `situation1(cur_bi, next_bi, pre_bi)` — 1+1 终结的形态一：中间那笔没创新低（趋势变弱），后面隔一根的笔整体把它包住。
- `L39` `situation2(cur_bi, next_bi, pre_bi)` — 1+1 终结的形态二：中间那笔创新低（趋势还在），但后面隔一根的笔直接越过了前面的端点。

### `chan/Seg/SegListDef.py` · 132 行 — 线段算法之一：「线段破坏法」（seg_algo='break'，已不推荐使用）。 注意：本文件的实现目前整体处于注释停用状态（KLine_List 的线段工厂也不再导入它）， 只保留代码原文作为算法参考，新代码请用 Seg/SegListChan.py（特征序列法）。
- **CSegListDef** — 用线段破坏法切分线段的线段列表实现（seg_algo='break'）。
  - `L49` `__init__(seg_config=CSegConfig(), lv=SEG_TYPE.BI)` — 按配置与层级语义构造线段破坏法的线段列表。
  - `L60` `update(bi_lst)` — 重算线段列表（线段破坏法对基类 update 的实现）。
  - `L72` `update_last_end(bi_lst, new_endbi_idx)` — 把最后一条线段的终点笔往后挪到 new_endbi_idx。
  - `L86` `cal_bi_sure(bi_lst)` — 扫描笔列表，边推进边在"被破坏"的位置切出线段。
- `L21` `is_up_seg(bi, pre_bi)` — 判断 bi 是否"破坏"了前面隔一根的笔 pre_bi 的高点。
- `L31` `is_down_seg(bi, pre_bi)` — 判断 bi 是否"破坏"了前面隔一根的笔 pre_bi 的低点。

### `chan/Seg/__init__.py` · 2 行 — 线段（CSeg 及三种算法实现）模块。
- _（仅包说明）_

### `chan/ZS/ZS.py` · 391 行 — 中枢（ZS）模块：缠论链路中「线段 -> 中枢 -> 买卖点」的中间一环。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 【本模块：中枢（笔中枢 / 线段中枢）】 -> 买卖点
- 常量 `LINE_TYPE`
- **CZS** — 一个中枢。 内层元素可以是笔（构成笔中枢），也可以是线段（构成线段中枢），
  - `L44` `__init__(lst, is_sure=True)`
  - `L85` `clean_cache()` — 清空缓存：区间/端点/进出笔被改动后都必须调一次。
  - `L90` `is_sure()` — 是否由确认笔构成（False 表示含未确认笔，随时可能被重算）。
  - `L95` `sub_zs_lst()` — 被合并进本中枢的那些小中枢（combine 时逐个塞进来）。
  - `L100` `begin()` — 中枢的起点原始K线。
  - `L105` `begin_bi()` — 中枢内部的第一根元素（笔或线段）。
  - `L110` `low()` — 中枢区间下沿 = 各元素低点的最大值（「最高低点」）。
  - `L115` `high()` — 中枢区间上沿 = 各元素高点的最小值（「最低高点」）。
  - `L120` `mid()` — 中枢中位价 (low+high)/2。
  - `L125` `end()` — 中枢的终点原始K线。
  - `L130` `end_bi()` — 中枢内部的最后一根元素（笔或线段）。
  - `L135` `peak_high()` — 中枢涉及到的所有元素里最高的那个高点（比 high 更宽）。
  - `L140` `peak_low()` — 中枢涉及到的所有元素里最低的那个低点（比 low 更低）。
  - `L145` `bi_in()` — 进入中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。
  - `L150` `bi_out()` — 离开中枢的那一笔（中枢还在行情尾部时为 None）。
  - `L155` `bi_lst()` — 中枢内部的元素列表（由 KLine_List.update_zs_in_seg 回填）。
  - `L159` `update_zs_range(lst)` — 用参与构造的元素重算中枢区间。
  - `L173` `is_one_bi_zs()` — 是否是"一笔中枢"（整个中枢其实只包含一根笔，判据是 begin_bi 与 end_bi 同一根）。
  - `L183` `update_zs_end(item)` — 把中枢的尾部推进到 item，并把极值区间（peak_low/peak_high）撑开。
  - `L207` `combine(zs2, combine_mode)` — 尝试把时间上更晚的中枢 zs2 合并进本中枢。
  - `L235` `do_combine(zs2)` — 执行合并：把 zs2 并入本中枢，区间与极值都取并集，尾部（end/end_bi/bi_out）换成 zs2 的。
  - `L256` `try_add_to_end(item)` — 尝试把新元素并入本中枢（中枢延伸），并入成功返回 True。
  - `L272` `in_range(item)` — 判断一个元素是否与中枢区间有重叠。
  - `L276` `is_inside(seg)` — 判断本中枢是否落在线段 seg 的笔区间内（只看起点元素是否在其中）。
  - `L280` `is_divergence(config, out_bi=None)` — 判断"出中枢的那一笔"相对"进中枢的那一笔"是否背驰，返回 (是否背驰, 出笔力度/进笔力度)。
  - `L304` `init_from_zs(zs)` — 把另一个中枢的内容原样搬到自己身上（用于 make_copy）。
  - `L323` `make_copy()` — 造一份本中枢的副本（合并子中枢时用来保存"合并前的自己"）。
  - `L329` `end_bi_break(end_bi=None)` — 判断"离开中枢的那一笔"是否真的突破了中枢区间（end_bi 为 None 时取 bi_out）。
  - `L342` `out_bi_is_peak(end_bi_idx)` — 判断中枢内部、位于 end_bi_idx 之前的笔里，有没有比出中枢那笔更极端的。 返回 (是否最低点，bi_out 与中枢里最接近它的那笔的差距比例)：
  - `L367` `get_bi_in()` — 取进入中枢的那一笔。
  - `L372` `get_bi_out()` — 取离开中枢的那一笔。
  - `L377` `set_bi_in(bi)` — 记录进入中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。
  - `L382` `set_bi_out(bi)` — 记录离开中枢的那一笔（由 KLine_List.update_zs_in_seg 回填）。
  - `L387` `set_bi_lst(bi_lst)` — 记录中枢内部的元素列表（由 KLine_List.update_zs_in_seg 回填）。

### `chan/ZS/ZSConfig.py` · 28 行 — 中枢的配置模块：CZSConfig，决定中枢"怎么构造、要不要合并"。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 中枢（规则由本配置控制） -> 买卖点
- **CZSConfig** — 中枢的配置项集合，四个开关原样保存： need_combine   ：新中枢成型后是否尝试与前一个中枢合并
  - `L22` `__init__(need_combine=True, zs_combine_mode='zs', one_bi_zs=Fa…)` — 依次是：是否开启中枢合并、合并口径（'zs'/'peak'）、是否允许一笔中枢、构造模式。

### `chan/ZS/ZSList.py` · 281 行 — 中枢列表（ZSList）模块：决定"哪些笔/线段该构成一个中枢、中枢要不要合并"。 链路位置：原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 【本模块：中枢列表】 -> 买卖点
- **CZSList** — 一个级别的中枢序列容器 + 构造状态机。 核心状态：
  - `L38` `__init__(zs_config=CZSConfig())` — zs_config 是中枢配置 CZSConfig（是否合并、合并口径、是否允许一笔中枢、构造模式）。
  - `L48` `update_last_pos(seg_list)` — 把增量游标推进到"最后一个确认线段"的位置。
  - `L67` `seg_need_cal(seg)` — 判断这条线段是否还需要参与本次重算（起点还在游标之后，含游标位置）。
  - `L71` `add_to_free_lst(item, is_sure, zs_algo)` — 把一个元素塞进"待构造"列表，够条件就构造一个新中枢并入列。
  - `L92` `clear_free_lst()` — 清空"待构造"列表（换一段线段时避免把上一段的笔带过去）。
  - `L96` `update(bi, is_sure=True)` — 喂进一个元素：先尝试中枢延伸，不行再攒着等构造新中枢。
  - `L112` `try_add_to_end(bi)` — 尝试让新元素直接并入最后一个中枢（中枢延伸）；还没有中枢时恒为 False。
  - `L116` `add_zs_from_bi_range(seg_bi_lst, seg_dir, seg_is_sure)` — 把一个线段区间内的元素逐个喂进来，构造该线段内的中枢。
  - `L138` `try_construct_zs(lst, is_sure, zs_algo)` — 用攒下来的元素试着构造一个中枢，重叠不成立时返回 None。
  - `L170` `cal_bi_zs(bi_lst, seg_lst)` — 重算中枢列表的总入口（bi_lst 是本层元素列表，seg_lst 是上层线段列表）。
  - `L226` `update_overseg_zs(bi)` — over_seg 模式下喂进一个元素。
  - `L268` `try_combine()` — 尝试把最后两个中枢合并（中枢的扩展/合并）。

### `chan/ZS/__init__.py` · 2 行 — 中枢（CZS / CZSList / CZSConfig）模块。
- _（仅包说明）_

## ai · 缠论之上的信号层：背驰 / 波浪 / 策略 / LLM 客户端与会话


### `ai/__init__.py` · 2 行 — AI 子包：LLM 客户端、诊股会话、背驰/波浪/策略等信号计算。
- _（仅包说明）_

### `ai/analysis_agent.py` · 163 行 — 缠论 + LLM 智能分析 — 将缠论分析结果交给大模型生成策略建议
- 常量 `SYSTEM_PROMPT`
- `L12` `_format_divergence_for_prompt(divergence)` — 将背驰检测结果格式化为模型可读的多行文本
- `L68` `build_analysis_prompt(code, level, klines, trend, divergence, …)` — 构造发送给 LLM 的分析 prompt
- `L132` `parse_llm_response(text)` — 从 LLM 回复里抠出 JSON 对象。

### `ai/chat_sessions.py` · 82 行 — 轻量 AI 诊股会话（进程内内存）。
- **ChatSession** — 一个 AI 诊股会话：消息列表 + 最近活跃时间，自带 RLock 保证并发安全。
  - `L11` `__init__(max_turns=10)` — max_turns 是保留的对话轮数，内部按「一轮两条」折算成 max_turns*2 条上限。
  - `L19` `add(role, content)` — 追加一条消息并刷新活跃时间；超过条数上限时从头部丢弃最旧的。
  - `L28` `history()` — 返回消息副本并刷新活跃时间（读取也算活跃，不会被 TTL 清掉）。
- `L42` `_prune_sessions(now)` — 清掉 TTL 过期的会话；若仍超过 _MAX_SESSIONS，按最久未活跃再淘汰到上限为止。
- `L64` `chat_session_stats()` — 返回当前会话数与上限，供健康检查/诊断接口观测。
- `L71` `get_or_create_session(session_id)` — 取（并刷新活跃时间）或新建会话；session_id 截断到 120 字符，空则归为 "default"。

### `ai/divergence.py` · 480 行 — 背驰自动判断 — 基于 chan 缠论笔对象 CBi 的 MACD 面积 + RSI/KDJ 背离检测。 改写说明（原实现基于 ``chanlun.elements.Bi``）： ``Bi`` 是纯数据模型，一根笔只有 ``start`` / ``end`` 两个日期，要取区间数据只能
- 常量 `MACD_WEAKEN_RATIO=0.85` · `MACD_AREA_EPS=1e-15` · `PRICE_EPS=1e-12` · `MIN_BIS_FOR_DIV=4` · `MIN_BARS_PER_SEGMENT=2` · `OSC_CONFIRM_BOOST=0.07`
- **DivergenceDetector** — 背驰检测: 1. MACD 同向柱面积对比（向上笔累计红柱、向下笔累计绿柱；过小则回退为绝对值面积）
  - `L178` `__init__(df, idx_offset=0)`
  - `L196` `check_divergence(bis)` — 检测背驰: 比较最近两个同向段的力度
  - `L240` `_rsi_confirms(seg_type, s1_df, s2_df)` — 顶: 价新高但段内 RSI 高点不及前一段；底: 价新低但 RSI 低点抬高
  - `L256` `_kdj_j_confirms(seg_type, s1_df, s2_df)` — 以 J 线为快速振荡器，逻辑同 RSI
  - `L272` `_klu_metric_series(bi, kind)` — 从笔自身的 KLU 上取由 chan 引擎算好的指标序列。
  - `L310` `_segment_series(bi, seg_df, kind)` — 取某笔区间上某个指标的序列：优先引擎侧值，取不到再退 pandas 自算的那一列。
  - `L335` `_augment_segment_df(bi, seg_df)` — 把引擎侧算好的 rsi / J 覆盖到切片里的同名列， 好让 ``_rsi_confirms`` / ``_kdj_j_confirms`` 能直接沿用原来的 D…
  - `L360` `_klu_range(bi)` — 这一笔的首尾 KLU idx（闭区间），用作 ``_get_segment_df`` 的入参。
  - `L364` `_check_segment_divergence(seg1, seg2, seg_type)` — 比较两段的力度差异
  - `L453` `_get_segment_df(start, end)` — 截取某笔区间的 K 线切片。
- `L48` `calculate_macd(df, fast=12, slow=26, signal=9)` — 计算 MACD 指标，返回追加 dif/dea/bar 列的 DataFrame。
- `L60` `calculate_rsi(df, period=14)` — 计算 RSI（Wilder 平滑，与常见行情软件一致）
- `L71` `calculate_kdj(df, n=9, m1=3, m2=3)` — 计算 KDJ 指标
- `L88` `money(v)` — 格式化金额显示
- `L93` `macd_area(bars)` — 计算 MACD 柱全绝对值面积（近似积分），忽略 NaN
- `L99` `macd_area_directional(bars, seg_type)` — 与走势同向的 MACD 柱累计面积（忽略反向柱），更符合「上涨笔比红柱、下跌笔比绿柱」的习惯。…
- `L112` `_nan_extreme(series)` — 段内 RSI/KDJ 极值；无有效数据时返回 nan
- `L124` `_bi_klu_list(bi)` — 取出这一笔真正覆盖的原始K线列表（按时间顺序）。
- `L149` `_bi_high(bi)` — 笔区间最高价（等价于旧 Bi.high；chan 里由 ``CBi._high()`` 给出）
- `L154` `_bi_low(bi)` — 笔区间最低价（等价于旧 Bi.low；chan 里由 ``CBi._low()`` 给出）
- `L159` `_bi_end_idx(bi)` — 笔终点 KLU 的下标，用作「谁更靠近当下」的比较键（等价旧 Bi.end 的时间先后）。

### `ai/elements.py` · 53 行 — AI 策略信号的输出契约。 本模块从 ``chanlun/elements.py`` 迁来。原文件曾定义缠论链路上所有的数据模型 （KLine / Bi / XianSegment / Zhongshu / BuySellPoint ……），但随着计算引擎换成
- **AiSignal** — AI策略信号 — 在缠论买卖点之上叠加方向、风险与持仓建议的复合输出。 属于「策略层」而非「结构层」：先由结构层产出买卖点（``CBS_Point``），
  - _（无公开成员）_

### `ai/llm_client.py` · 174 行 — 统一 LLM 客户端 — 支持 DeepSeek 和 Gemini，自动降级
- **LLMClient** — DeepSeek（https://platform.deepseek.com） / Gemini（https://ai.google.dev） 环境变量：
  - `L37` `__init__(model='deepseek')` — model 只是个名字，chat() 时按前缀 deepseek/gemini 决定走哪个厂商。
  - `L44` `_deepseek(messages, **kwargs)` — 调 DeepSeek chat/completions；没配 DEEPSEEK_API_KEY 直接抛错，不做静默降级。
  - `L71` `_gemini(messages, **kwargs)` — 调 Gemini generateContent。
  - `L119` `chat(prompt, system='', **kwargs)` — 发送对话，返回模型原始回复文本。
- `L19` `_get_llm_http_client()` — 全局共用一个 httpx.Client（60s 超时）以复用连接池；懒创建 + 加锁，线程安全。
- `L153` `get_llm_client(model=None)` — 取全局单例；传入的 model 与当前不同时才重建客户端，不传则沿用（首次默认 deepseek）。
- `L167` `set_llm_model(model)` — 切换全局模型：直接重建客户端，下次 get_llm_client() 拿到新的。

### `ai/strategy_engine.py` · 330 行 — 策略推荐引擎
- 常量 `DEFAULT_BSP_CONFIDENCE=0.6`
- **StrategyEngine** — 策略推荐: 1. 结合缠论信号 + 走势分类 + 背驰判断
  - `L116` `__init__(signals, trend, current_price, current_level, zhongsh…)` — 只做入参装配，不含计算：signals/zhongshus 是 chan 库的 CBS_Point/CZS 对象。
  - `L131` `generate_signal()` — 生成AI策略信号
  - `L188` `_calc_confidence(signal)` — 计算置信度
  - `L211` `_calc_risk(signal, direction)` — 风险评级
  - `L235` `_calc_levels(signal, direction)` — 计算入场价、止损价、止盈价。 一买 / 三买：入场价 = 信号价位（背驰点 / 回踩低点），止损在信号价下方。
  - `L285` `_calc_holding_period()` — 操作窗口
  - `L299` `_make_description(signal, direction, confidence)` — 生成自然语言描述
- `L20` `_bsp_labels(bsp)` — 把 CBS_Point 的类型列表翻译成中文标签，如 ``["一买"]`` / ``["二买", "三买"]``。
- `L44` `_bsp_has_main_type(bsp, main)` — 这笔买卖点是否命中某个主类型（'1'/'2'/'3'），二买就是 main='2'。
- `L49` `_bsp_idx(bsp)` — 买卖点的时间先后比较键。
- `L61` `_bsp_price(bsp)` — 买卖点的触发价位。
- `L72` `_bsp_confidence(bsp)` — 取买卖点置信度：features 里有 'confidence' 就用它，否则回落默认基准。
- `L83` `_bsp_description(bsp)` — 拼一句人类可读的买卖点说明。

### `ai/wave_classifier.py` · 107 行 — 走势自动分类 — 上涨/下跌/盘整
- **WaveClassifier** — 走势分类: - 上涨: 连续向上段 + 不创新低
  - `L22` `classify(segments, zhongshus, current_price)` — 返回分类结果及置信度
  - `L79` `multi_level_resonance(level_results)` — 多级别共振: 输入: [{'trend': '上涨', 'level': 'daily'}, ...]

## routers · HTTP 入口（FastAPI）


### `routers/__init__.py` · 2 行 — FastAPI 路由模块集合。
- _（仅包说明）_

### `routers/chanlun_routes.py` · 472 行 — 缠论 HTTP 路由：单级别完整分析（笔 / 线段 / 中枢 / 买卖点 / 支撑压力位）。 本层只做三件事： 1. 限流（check_chanlun_rate_limits）；
- 常量 `DEFAULT_HISTORY_DAYS=730`
- `L54` `GET /api/chanlun/{code}` `async analyze_chanlun(request, code, level, start_date)` — 单级别缠论完整分析：笔 / 线段 / 中枢 / 买卖点 / 支撑压力位。

### `routers/comments.py` · 126 行 — 笔记（/api/comments）路由：某只股票下的个人笔记增删改查。 实现函数带 _impl 后缀、全部走 asyncio.to_thread， 因为底层是同步 JSON 文件读写，不能直接在事件循环里做。
- **CommentBody** — 笔记请求体：content 限长 1~8000。
  - _（无公开成员）_
- `L34` `_get_comments_impl(stock_code)` — 归一化代码后取笔记，按 createdAt 倒序（最新在前）返回。
- `L44` `GET /api/comments/{stock_code}` `async get_comments(stock_code, request)` — GET 笔记列表：限速后转线程池执行，避免阻塞事件循环。
- `L51` `_add_comment_impl(stock_code, content)` — 造一条笔记（uuid + UTC 时间戳）并落盘；空内容抛 400。
- `L72` `POST /api/comments/{stock_code}` `async add_comment(stock_code, comment_in, request)` — POST 新增笔记。
- `L79` `_update_comment_impl(stock_code, comment_id, content)` — 改指定 id 的笔记内容；找不到抛 404。
- `L97` `PUT /api/comments/{stock_code}/{comment_id}` `async update_comment(stock_code, comment_id, comment_in, reque…)` — PUT 修改笔记。
- `L111` `_delete_comment_impl(stock_code, comment_id)` — 删除指定 id 的笔记；找不到抛 404。
- `L121` `DELETE /api/comments/{stock_code}/{comment_id}` `async delete_comment(stock_code, comment_id, request)` — DELETE 删除笔记。

### `routers/diagnosis.py` · 368 行 — 【已停用】AI 诊股路由 — 当前整文件为注释状态，main.py 中也没有挂载。 停用前的职责：把缠论数据喂给 LLM，以 SSE 流式返回回答。 SSE 协议（三帧）：
- _（整文件为注释，无可执行定义）_

### `routers/stocks.py` · 683 行 — 【已停用】行情与选股路由 — 当前整文件为注释状态，main.py 中也没有挂载。 停用前的职责：限流 → 参数校验 → asyncio.to_thread 包装 → 出参清洗， 取数交给 services/akshare_service.py 与 services/screening_service.py。
- _（整文件为注释，无可执行定义）_

### `routers/system.py` · 77 行 — 系统级路由：/health 健康检查、/api/settings 设置的读写。
- `L31` `GET /health` `health_check()` — 健康检查：顺带清理限流器的过期 key，并汇总各缓存/限流器/会话的当前状态。
- `L60` `GET /api/settings` `get_settings()` — GET 系统设置（目前只有 ai_model）。
- `L67` `PUT /api/settings` `update_settings(model)` — PUT 切换 AI 模型：即时生效（重建 LLM 客户端）并落盘持久化；只认 deepseek/gemini。

### `routers/watchlist.py` · 95 行 — 自选股（/api/watchlist）路由：列表（带行情缓存）与增删。
- `L17` `_get_watchlist_response()` — 取自选列表 + 实时行情。
- `L58` `GET /api/watchlist` `async get_watchlist()` — GET 自选列表：转线程池执行（底层是同步取行情）。
- `L64` `_invalidate_watchlist_cache()` — 增删自选后整体清空行情缓存（key 含全部代码，任一变动都会失效）。
- `L71` `POST /api/watchlist/{code}` `async add_watchlist(code)` — POST 添加自选：幂等，已存在时返回原 added_at 且 is_new=False。
- `L87` `DELETE /api/watchlist/{code}` `async remove_watchlist(code)` — DELETE 删除自选：原本不存在也返回 removed=True。

## services · 取数与业务：A 股 akshare、港股富途、缠论选股


### `services/__init__.py` · 2 行 — 取数与业务服务层：A 股行情（akshare）、港股（富途 OpenD）、缠论选股。
- _（仅包说明）_

### `services/akshare_service.py` · 1854 行 — 股票数据服务。 K 线（日/周/月/分钟）走 baostock，其余行情（实时报价、板块、热门、新闻等）走 腾讯 / 东方财富 / 新浪的 HTTP 接口。
- `L30` `_get_client()` — 获取或创建 HTTP 客户端（禁用代理，直连，共享连接池）
- `L50` `get_stock_news(limit=10)` — 获取财经新闻列表，按优先级尝试：东方财富快讯 → 同花顺 → 东方财富资讯。 返回 [{ title, time, url, source, digest }]。
- `L85` `_fetch_em_breaking_news(limit)` — 东方财富实时快讯 API
- `L116` `_fetch_ths_news(limit)` — 同花顺财经快讯
- `L145` `_fetch_em_article_news(limit)` — 东方财富资讯列表（兜底）
- `L187` `_cache_get(key)` — 读缓存：命中且未过期则移到队尾并返回；过期则删掉返回 None。
- `L202` `_cache_set(key, data, ttl=60)` — 写缓存：已存在则刷新位置，超容量时淘汰最久未用的；ttl 是秒。
- `L213` `normalize_stock_code(code)` — 规范化股票代码，返回 (纯6位代码, 市场标识)。
- `L234` `_get_qq_market_code(code)` — 获取腾讯API的市场前缀（与 normalize_stock_code 同一套判定规则）
- `L239` `get_realtime_quote(codes)` — 获取实时行情，分批并发拉取（腾讯 API 单 URL 约 150 只为上限）。
- `L340` `_bs_login()` — 确保 baostock 已登录（幂等，失败可重入）。调用方需已持有 _bs_lock。
- `L362` `_bs_query(bs_code, fields, start, end, freq, adjustflag)` — 执行一次 baostock K 线查询，返回行列表（每行是字符串列表）；失败返回 []。…
- `L404` `_bs_date_window(period, limit, start_date, end_date)` — 把 limit 换算成 baostock 需要的 [start_date, end_date] 闭区间。…
- `L425` `_get_kline_baostock(sym, mkt, period, adjust, start_date, end_…)` — 用 baostock 取 A 股日/周/月/分钟 K 线，输出口径与旧腾讯实现保持一致。
- `L522` `get_kline_hist(code, period='daily', start_date=None, end_date…)` — 获取历史K线数据（数据源：baostock） period: daily/weekly/monthly/5/15/30/60 分钟
- `L577` `get_index_quote(index_code='000001')` — 获取指数实时行情
- `L621` `_normalize_index_row(index_code, display_name)` — 将 get_index_quote 结果转为前端统一字段
- `L639` `get_a_share_market_breadth()` — A 股涨跌家数： ① 东方财富 clist 分页统计（与涨幅榜同源，稳定）
- `L675` `_em_clist_request(params)` — 依次尝试各 push 域名请求 api/qt/clist/get，成功返回 JSON dict。…
- `L700` `_fetch_em_breadth_via_clist()` — 东方财富 clist 分页拉全 A，按 f3 涨跌幅统计涨跌平家数。
- `L760` `_fetch_sina_breadth_paginated()` — 新浪 A 股列表分页统计涨跌家数（兜底）。
- `L802` `get_all_industry_boards()` — 全部行业板块：① 东方财富行业板块（m:90+t:2） ② 兜底：东方财富概念板块（m:90+t:3）
- `L831` `_fetch_em_industry_boards()` — 东方财富行业板块（m:90+t:2）
- `L871` `_fetch_em_concept_boards()` — 东方财富概念板块（m:90+t:3）兜底
- `L911` `get_industry_board_movers(limit_each=5)` — 行业板块涨跌幅（兼容旧接口）：东方财富 push2 接口。 返回 top（涨幅前 N）与 bottom（跌幅前 N）。
- `L925` `_em_request(url, params)` — 东方财富 API 请求：多域名兜底，禁用代理，直连。 East Money 接口返回 GBK 编码，手动解码避免乱码。
- `L958` `_load_industry_name_map()` — 加载行业板块名称→代码映射（m:90+t:2），分页拉取直到取完全部。
- `L1003` `_load_concept_name_map()` — 加载概念板块名称→代码映射（m:90+t:3），全量分页拉取。
- `L1046` `_finite_num(v, default=0.0)` — 把任意值安全转 float：None / NaN / 不可转换都退回 default（默认 0.0）。
- `L1057` `_parse_em_board_cons_df(df)` — 东方财富行业/概念成分股 DataFrame → 统一结构。
- `L1085` `_parse_em_board_diff(diff)` — 解析东方财富板块成分 diff（字段 ID dict 格式）→ 统一结构。 关键字段：f12=代码, f14=名称, f2=最新价, f3=涨跌幅,
- `L1117` `get_board_constituents_em(board_name)` — 板块成分股（东方财富）：直接请求 East Money API，无需 akshare。 ① 先用行业板块映射查；② 兜底概念板块映射查。
- `L1216` `akshare_cache_stats()` — 内存行情缓存统计（供 /health）。
- `L1222` `get_market_overview_bundle()` — 聚合：主要指数 + 涨跌家数 + 全部行业板块（供 /api/market/overview）
- `L1266` `_parse_sina_suggest(text)` — 解析新浪搜索响应 两种格式：
- `L1305` `search_stocks(keyword)` — 搜索股票 - 使用新浪搜索API
- `L1338` `get_daily_hot_stocks(limit=20)` — 当日个股人气榜（东方财富），返回含代码、名称、排名等；缓存为空时直接获取实时数据。
- `L1348` `_fetch_and_cache_hot(limit=20)` — 按优先级尝试多个来源获取热门股票：东方财富涨幅榜 → 同花顺 → 新浪人气榜。
- `L1377` `_fetch_em_hot_stocks(limit)` — 东方财富涨幅榜（今日涨跌幅从高到低），并发分页，最多支持 1000 条。
- `L1449` `_fetch_ths_hot_stocks(limit)` — 同花顺热门股票
- `L1483` `_fetch_sina_hot_stocks(limit)` — 新浪人气榜（兜底）
- `L1531` `warm_hot_cache()` — 后台线程预热热门股票缓存（5 分钟刷新一次）。
- `L1545` `get_stock_info(code)` — 获取股票基本信息
- `L1590` `get_stock_depth_em(code)` — 东方财富五档盘口（买卖各五档）。 返回 {"asks": [{"price": float, "volume": int}, ...]…
- `L1663` `get_stock_boards_em(code)` — 个股资料中的行业等信息，直接请求东方财富接口（替代 akshare）。
- `L1746` `_fetch_em_symbol_news(code, limit)` — 东方财富个股新闻（直连 HTTP，避免 akshare 额外开销）。
- `L1784` `get_stock_symbol_news_em(code, limit=8)` — 东方财富个股相关新闻。
- `L1798` `get_minute_data(code, period='5')` — 获取分钟K线数据
- `L1812` `_get_minute_data_sina(code, market, period, adjust='qfq')` — 使用新浪API获取分钟K线数据 period: 5, 15, 30, 60 (分钟)

### `services/futu_hk_service.py` · 1609 行 — 港股数据服务 - 使用富途 OpenD（futu-api）获取港股行情与 K 线 与 akshare_service.py（A 股）平行存在。 接口签名与返回结构刻意与 akshare_service 对齐，上层路由可以直接换数据源：
- 常量 `FUTU_HOST` · `FUTU_PORT` · `PROBE_TIMEOUT=3.0` · `CALL_TIMEOUT=15.0` · `MAX_COUNT=1000` · `MAX_PAGE=60` · `RETRY_ATTEMPTS=2` · `MAX_CONCURRENT_CALLS=8` · `SNAPSHOT_BATCH=400` · `HOT_MIN_TURNOVER=0.0`
- **FutuError** — 富途取数失败（含未安装 SDK、OpenD 未启动、超时、接口报错）
  - _（无公开成员）_
- **FutuUnavailable** — SDK 未安装 / OpenD 没启动 —— 调用方通常直接降级返回空结果
  - _（无公开成员）_
- **FutuTimeout** — 调用超时（OpenD 卡住）
  - _（无公开成员）_
- `L100` `_futu()` — 返回 futu 模块；未安装时抛 FutuUnavailable
- `L127` `opend_alive(host=None, port=None, timeout)` — 探活 OpenD。 futu SDK 在 OpenD 没起时是阻塞重试而不是报错，所以先用裸 socket 探一下，
- `L151` `_create_ctx()` — 创建 OpenQuoteContext，并把 futu 自己的新线程设成 daemon。…
- `L181` `_get_ctx()` — 获取当前线程的 OpenQuoteContext（不存在则建）
- `L198` `_safe_close(ctx)` — 关连接：吞掉所有异常，避免关停阶段的噪声掩盖真正的错误。
- `L207` `_invalidate_ctx()` — 作废当前线程的连接（调用超时后那条连接可能已经卡死，不能再用）。 close() 本身也可能阻塞，所以丢到 daemon 线程里做，不拖住当前请求。
- `L222` `close_futu()` — 关闭当前线程的连接
- `L233` `close_all_futu()` — 关闭所有线程创建的连接（atexit 兜底）
- `L248` `_call(fn, timeout=CALL_TIMEOUT)` — 在 daemon 线程里执行 futu 调用 + join(timeout) 看门狗。 超时后作废当前线程的连接（下次调用会重建），并抛 FutuTimeout。
- `L282` `_retry(fn, attempts, timeout=CALL_TIMEOUT)` — 带一次重试的调用。 只有【超时】才作废连接重建 —— 真正的超时意味着那条连接可能已经卡死。
- `L309` `_unpack3(result)` — request_history_kline 新版返回 3 元组，老版 2 元组，这里统一成 3 个
- `L320` `normalize_stock_code(code)` — 规范化港股代码，返回 (code, market)，与 akshare_service 同签名。…
- `L342` `to_futu_code(code)` — 转成富途格式：'00700' -> 'HK.00700'
- `L348` `strip_market_prefix(futu_code)` — 'HK.00700' -> '00700'
- `L357` `to_query_code(code)` — 转成可直接喂给富途的查询代码。 '00700'        -> 'HK.00700'
- `L374` `is_hk_index(code)` — 港股指数的代码段以 8 开头（HK.800000 恒生指数）
- `L405` `_ktype_of(period)` — 把本项目周期串（如 "day"/"60m"）映射成富途 KLType；不支持的周期直接抛 FutuError。
- `L415` `_autype_of(adjust)` — 把复权参数映射成富途 AuType；不传或认不出时按前复权（QFQ）处理。
- `L423` `_norm_date(date_str, default)` — 把 20250101 / 2025-01-01 / 2025/01/01 统一成 'YYYY-MM-DD'
- `L441` `_default_start(kl_name, limit)` — 按周期和需要的根数反推起始日期，避免无脑从 1990 年拉导致多页翻页。 系数已经把周末 + 港股节假日算进去（一年约 245 个交易日）。
- `L459` `_to_float(x, default=0.0)` — futu 偶尔给 NaN / None / 空串，统一兜底成数字
- `L476` `_cache_get(key)` — 读缓存：命中且未过期则移到队尾并返回；过期则删掉返回 None。
- `L491` `_cache_set(key, data, ttl=60)` — 写缓存：已存在则刷新位置，超容量时淘汰最久未用的；ttl 是秒。
- `L502` `futu_cache_stats()` — 缓存状态（与 akshare_cache_stats 同用途，便于排查）
- `L509` `_request_kline_page(ctx, futu_code, ktype, autype, start, end,…)` — 单页K线请求。ctx 由调用方传入（必须在看门狗线程之外取，见 _get_ctx 说明）
- `L524` `_fetch_kline_df(futu_code, ktype, autype, start, end)` — 按 page_req_key 循环翻页取全量K线（futu 单页上限 1000，不翻页只拿最早那段）
- `L557` `get_kline_hist(code, period='daily', start_date=None, end_date…)` — 获取港股历史K线（富途 OpenD）。 period: 1min/5min/15min/30min/60min/daily/weekly/monthly（也认 "…
- `L622` `get_market_snapshot(codes)` — 批量取快照（自动按 futu 的 400 上限分批）。 返回富途原始列：code / name / last_price / open_price / high_…
- `L653` `get_realtime_quote(codes)` — 获取港股实时行情，列名与 akshare_service.get_realtime_quote 保持一致…
- `L701` `get_stock_info(code)` — 获取单只港股基本信息，键名与 akshare_service.get_stock_info 对齐…
- `L756` `get_hk_stock_list(sec_type='STOCK')` — 取港股标的清单（get_stock_basicinfo）。 sec_type: STOCK 正股 / IDX 指数 / ETF / WARRANT 窝轮 / BO…
- `L788` `_snapshot_rows(futu_codes)` — 批量取快照并转成统一行（内部自动按 400 分批）。 每行：code/name/price/change_pct/volume/amount/turnover_p…
- `L824` `_index_row(futu_code, display_name)` — 指数行：与 akshare_service._normalize_index_row 同结构
- `L849` `get_plate_list(plate_class='INDUSTRY')` — 取港股板块清单（get_plate_list）。 plate_class: INDUSTRY 行业 / CONCEPT 概念 / OTHER 其它 / REGIO…
- `L885` `_load_plate_name_map()` — 板块名 -> (板块代码, board_type)。行业优先，概念兜底。 与 akshare 版的 _EM_INDUSTRY_NAME_MAP / _EM_CON…
- `L917` `_resolve_plate(board_name)` — 板块名 -> (板块代码, board_type, 实际板块名)；先精确后模糊
- `L934` `get_all_industry_boards()` — 全部行业板块涨跌幅，按涨幅降序：[{name, change_pct}, ...]。…
- `L962` `_board_change_rows(plate_codes)` — 板块代码 -> [{name, change_pct}]（板块同样有 last_price/prev_close_price）
- `L980` `get_board_constituents_em(board_name)` — 板块成分股：与 akshare_service.get_board_constituents_em 同结构。…
- `L1061` `get_daily_hot_stocks(limit=20)` — 当日人气榜（港股领涨榜），与 akshare_service.get_daily_hot_stocks 同结构。…
- `L1131` `get_hk_market_breadth()` — 港股涨跌家数（get_rise_fall_distribution）。 返回 {"advancers", "decliners", "unchanged"}…
- `L1175` `get_market_overview_bundle()` — 聚合：港股主要指数 + 涨跌家数 + 全部行业板块（供 /api/market/overview）。…
- `L1209` `get_stock_boards_em(code)` — 个股所属行业与亮点（get_owner_plate + 快照），与 akshare_service 同结构。…
- `L1285` `_ensure_order_book_subscribed(futu_code)` — get_order_book 前必须先订阅，否则报"请先订阅OrderBook数据"。…
- `L1311` `get_stock_depth_em(code, num=5)` — 买卖档位盘口（get_order_book），与 akshare_service.get_stock_depth_em 同结构。…
- `L1369` `_parse_news_time(raw)` — 富途的 publish_time 只有 'MM/DD'（今年），跨年时要退到去年。 统一输出 'YYYY-MM-DD'；已经是完整时间的原样截断。
- `L1390` `_search_news_rows(keyword, max_count)` — 调富途 get_search_news 搜资讯；非 RET_OK 抛 FutuError，无结果返回空列表。
- `L1405` `get_stock_news(limit=10)` — 港股市场资讯，与 akshare_service.get_stock_news 同结构。…
- `L1451` `get_stock_symbol_news_em(code, limit=8)` — 个股相关资讯，与 akshare_service.get_stock_symbol_news_em 同结构。…
- `L1497` `search_stocks(keyword, limit=20)` — 按代码前缀或名称关键字搜索港股，返回 DataFrame[code, name]（与 A 股版同构）。…
- `L1547` `health()` — 数据源健康状态，供 /api/system 之类的接口展示。 {"available": bool, "host": str, "port": int…

### `services/screening_service.py` · 635 行 — 选股服务 — 按基础指标、缠论买卖点、MACD+SKDJ 双金叉筛选股票。 缠论部分基于 chan，但没有直接 ``new CChan(code, ..., DATA_SRC.BAO_STOCK)``， 而是拆成「取数」与「计算」两步：K 线仍走 services.akshare_service.get_kline_hist
- 常量 `SCREENING_KLINE_LIMIT=200` · `MIN_KLINE_NUM=20` · `DEFAULT_BSP_CONFIDENCE=0.6`
- `L80` `_level_to_period(level)` — 前端级别名 → akshare period；未知级别兜底 daily，宁可算日线也不传非法参数。
- `L85` `_level_to_kltype(level)` — 前端级别名 → chan KL_TYPE；未知级别兜底日线，规则同 _level_to_period。
- `L90` `_to_ctime(ts)` — pandas 时间戳 → chan 的 CTime。
- `L102` `_build_kl_list(df, kl_type)` — 把 K 线 DataFrame 喂给 chan，算出该级别的笔 / 线段 / 中枢 / 买卖点。
- `L153` `_latest_bsp(kl_list)` — 取该级别最新一个买卖点。
- `L167` `_bsp_labels(bsp)` — CBS_Point 的类型列表 → 中文标签，如 ``["一买"]`` / ``["二买", "三买"]``。
- `L185` `_bsp_date_str(bsp)` — 买卖点日期 → 'YYYY-MM-DD'。
- `L196` `_bsp_confidence(bsp)` — 买卖点置信度：features 里有 'confidence' 就用，否则回落默认基准。
- `L207` `_dual_cross_info(klines_df)` — 计算 MACD+SKDJ 双金叉共振，返回 (has_dual_cross, dual_cross_date_str)。
- `L228` `_analyze_stock(code, level)` — 对单只股票执行选股分析。 返回字典或 None（分析失败时跳过）。
- `L277` `_normalize_code(code)` — 统一转换为纯6位数字码，便于跨数据源匹配
- `L285` `_parse_pe_pb(info)` — 从股票信息里取 PE/PB；缺失、空串或 0 都算无值返回 None（0 不是有效估值）。
- `L301` `_quotes_from_hot(hot_list)` — 从热门池种子行情，避免重复拉全量实时报价。
- `L321` `_quote_map_from_seed(candidates, seed)` — 用种子行情（热门榜/预筛结果）补齐候选股的报价字段，缺失填 0，行业与 PE/PB 留空。
- `L341` `_get_quote_and_info(codes)` — 批量获取实时行情 + 基本面信息。 need_industry_name: 拉取行业（东方财富板块接口）
- `L428` `screen_stocks_stream(change_pct_min=None, change_pct_max=None,…)` — 选股主入口（生成器版）。yield 每条符合条件的结果，前端可边算边展示。 yield dict:
- `L600` `screen_stocks(change_pct_min=None, change_pct_max=None, volume…)` — 选股主入口（兼容版）。内部调用生成器，等全部算完返回列表。

## stores · 本地 JSON 持久化


### `stores/__init__.py` · 2 行 — 进程内持久化层：本地 JSON 文件，仅适用单节点。
- _（仅包说明）_

### `stores/local_json.py` · 190 行 — 自选、笔记、系统设置的 JSON 持久化。 单进程内用 RLock 保护读改写；写入使用临时文件 + os.replace 尽量原子化。 多 worker / 多机部署时各进程状态不一致——仅适合单机或文档化约束下的部署。
- `L32` `_atomic_write_json(path, data)` — 写 JSON：临时文件 + os.replace 原子替换；失败时清掉临时文件再抛出。
- `L49` `_load_watchlist_from_disk()` — 从磁盘读自选表；键统一补零成 6 位，文件缺失或解析失败返回空表。
- `L61` `_load_comments_from_disk()` — 从磁盘读笔记表；文件缺失或解析失败返回空表。
- `L72` `init_stores()` — 模块导入后初始化内存缓存（main 启动时也可再次调用）。
- `L81` `get_watchlist_map()` — 返回自选表快照（副本，避免外部直接改动内部状态）。
- `L88` `watchlist_add(sym)` — 加入自选：返回 (加入时间的 ISO 串, 本次是否新增)。
- `L100` `watchlist_remove(sym)` — 删除一条自选；原本不存在返回 False 且不落盘。
- `L111` `comments_get(sym)` — 取某只股票的笔记列表副本（保持写入顺序）。
- `L118` `comments_add(sym, comment)` — 追加一条笔记并落盘（不存在该股票时先建空列表）。
- `L128` `comments_update(sym, comment_id, new_content, updated_iso)` — 按 id 改内容与 updatedAt；找不到返回 False。
- `L142` `comments_delete(sym, comment_id)` — 按 id 删除笔记；不存在返回 False 且不落盘。
- `L156` `load_settings()` — 读设置；文件缺失或解析失败返回默认值 {"ai_model": "deepseek"}。
- `L168` `save_settings(s)` — 整体覆盖写设置文件（不做字段合并，调用方需先读出再改）。
- `L175` `apply_startup_ai_model()` — 启动时把设置里的 ai_model 恢复到 LLM 客户端；失败只记 debug 日志，不影响启动。

## utils · 通用工具：缓存 / 限流 / 重试 / 代码归一 / 指标


### `utils/__init__.py` · 281 行 — 轻量级工具集合：LRU 缓存（带 TTL）、HTTP 重试装饰器、请求限流。 除级的纯函数助手按模块单独提供，调用方直接按路径导入： - ``utils.datetime_fmt``：日期归一化 / 短日期格式化
- **LRUCache** — 线程安全的 LRU 缓存，支持 TTL。 超过 maxsize 时自动淘汰最久未使用的条目。
  - `L48` `__init__(maxsize=128, ttl=300.0)` — maxsize 是条目上限，ttl 是过期秒数；内部用 OrderedDict + RLock，线程安全。
  - `L56` `get(key)` — 取值：过期则删并返回 None，命中则移到队尾（表示最近用过）。
  - `L70` `set(key, value)` — 写入：已存在则刷新到队尾，超出 maxsize 时从队首淘汰最久未用的。
  - `L80` `clear()` — 清空全部条目。
  - `L86` `delete_prefix(prefix)` — 删除键名以 prefix 开头的条目，返回删除数量。
  - `L94` `purge_expired()` — 主动清理过期条目，返回清理数量。
  - `L106` `stats()` — 返回缓存统计信息。
- **RateLimiter** — 令牌桶限流器：限制每秒/每分钟最多 N 次调用。 线程安全，支持滑动窗口计数。
  - `L189` `__init__(max_calls=60, window_seconds=60.0)` — 滑动窗口限流：max_calls 次 / window_seconds 秒，按 key 分别计数。
  - `L197` `_make_key(identifier)` — 生成请求标识的哈希键，防止超长标识
  - `L203` `prune_stale_keys(max_keys=2000)` — 清理过期条目；键过多时丢弃最久未活动的标识。返回移除的键数量。
  - `L227` `try_acquire(identifier='global', tokens=1)` — 尝试获取令牌，返回是否允许请求
  - `L248` `get_remaining(identifier='global')` — 获取剩余可用次数
  - `L259` `reset(identifier='global')` — 清除指定标识的计数
  - `L265` `tracked_key_count()` — 当前被计数的 key 数量（用于观测限流器内存占用，需配合 prune_stale_keys 使用）。
- `L136` `with_retry(max_attempts=3, delay=1.0, jitter=0.2, exceptions)` — HTTP 请求重试装饰器。 用法:

### `utils/datetime_fmt.py` · 98 行 — 统一 datetime / 日期字符串格式化。 存在的原因是数据源与模型层的日期类型不统一：pandas 给的是 Timestamp， 缠论模型里可能是 datetime / date / 纯字符串，甚至带时区后缀。
- `L18` `normalize_date_param(value, default_days=None)` — 把接口入参里的日期归一成 ``'YYYY-MM-DD'``；认不出来则回退。
- `L65` `_fallback_date(default_days)` — 按回退窗口算出起始日；``default_days`` 为 None 时返回 None（不限）。
- `L72` `format_date_short(value)` — 把任意日期类型/字符串格式化为 YYYY-MM-DD，供 LLM 上下文与日志使用。

### `utils/indicators.py` · 279 行 — 共享技术指标计算（选股 / 背驰 / 前端算法对齐）。 本模块是「后端与前端、选股与个股分析」共用的唯一指标口径来源： - list 版（ema_series / calc_macd_lists / calc_skdj_lists / 双金叉判定）
- `L17` `ema_series(values, period)` — 指数移动平均（EMA）递推，返回与输入等长的序列。 递推式：EMA[i] = v[i] * k + EMA[i-1] * (1 - k)…
- `L45` `calc_macd_lists(closes, fast=12, slow=26, signal=9)` — MACD 的 DIF / DEA 两条线（list 版）。 DIF = EMA(fast) - EMA(slow)，DEA = EMA(signal, DIF)…
- `L70` `calc_skdj_lists(highs, lows, closes, n=9, smooth_n=3, smooth_m…)` — 通达信风格慢速随机指标 SKDJ，与前端 stockIndicators / 选股一致。 计算分三步：RSV → SK（第一段平滑）→ SD（第二段平滑）。
- `L123` `macd_gold_cross_indices(dif, dea)` — 列出所有 MACD 金叉位置：前一刻 DIF <= DEA，本刻 DIF > DEA。
- `L139` `skdj_gold_cross_indices(sk, sd)` — 列出所有 SKDJ 金叉位置：前一刻 SK <= SD，本刻 SK > SD。
- `L159` `_valid_dual_cross_at(hi, dif, dea, sk, sd)` — 校验 hi 这根 K 线能否作为「双金叉标记日」。 条件（与前端 computeDualMacdSkdjMarkerIndices 对齐）：
- `L202` `find_latest_dual_cross_bar(dif, dea, sk, sd, window=3)` — 最近一根有效 MACD+SKDJ 双金叉 K 线索引（bar 最大）。 与前端 computeDualMacdSkdjMarkerIndices / 选股逻辑对齐。
- `L252` `calculate_macd_df(df, fast=12, slow=26, signal=9)` — 向量化 MACD（DataFrame 版），供背驰检测等场景使用。

### `utils/log.py` · 76 行 — 全局日志装配：给根 logger 挂上「控制台 + 按大小滚动的文件」两路输出。 用法（整个进程只生效一次，重复调用会被忽略）： from utils.log import setup_logging
- 常量 `LOG_FORMAT` · `DATE_FORMAT`
- `L25` `setup_logging(level=logging.INFO, log_file='app.log', max_byte…)` — 配置根 logger：控制台 + 滚动文件。整个项目只调用一次。

### `utils/numbers.py` · 36 行 — 数值安全转换工具：把数据源的脏值统一转成 JSON 合法的有限浮点数。 存在的原因有两个： 1. 数据源（akshare / 东财）常返回 "--"、""、None 这类占位值，
- `L15` `finite_float(x, default=0.0)` — 把任意值转成有限浮点数，失败或非法时回落到 default。

### `utils/stock_code.py` · 182 行 — 股票代码归一化：把前端 / 命令行传进来的各种写法统一成各数据源要的格式。 同一个标的在项目里有好几种写法在流转:: 002384        前端搜索框、自选股、URL path 里最常见（裸 6 位）
- 常量 `MARKETS=('sh', 'sz', 'bj')`
- `L50` `split_code(code)` — 把任意写法的代码拆成 ``(纯 6 位数字, 市场标识或 None)``。
- `L88` `is_valid_symbol(symbol)` — 判断是否为合法的 6 位 A 股数字代码。
- `L106` `detect_market(symbol, default='sz')` — 按 A 股代码段判定所属市场。
- `L140` `to_baostock_code(code)` — 把任意写法的代码转成 **baostock 格式** ``"市场.代码"``（如 ``"sz.002384"``）。

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
