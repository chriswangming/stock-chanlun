# -*- coding: utf-8 -*-
"""扫描 backend 下全部 .py，用 AST 抽取注释骨架，一次产出两份 Markdown：

1. docs/backend-notes.md      —— 极简版：只列模块 + 一句话职责（约 150 行）
2. docs/backend-notes-full.md —— 完整版：模块 + 类 + 每个函数的签名与说明

用法：
    cd backend && python docs/gen_notes.py
"""
import ast, logging, os, re

# 以本脚本所在目录（docs/）的上一级，即 backend/ 为根；脚本可整体搬走复用
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {'.venv', '__pycache__', 'node_modules', '.git', 'docs'}
BRIEF_OUT = os.path.join(ROOT, 'docs/backend-notes.md')
FULL_OUT = os.path.join(ROOT, 'docs/backend-notes-full.md')

log = logging.getLogger(__name__)

MAXDOC = 82       # 完整版：单条类/函数说明最大字符
MAXSIG = 64       # 完整版：签名最大字符
MAXMOD = 165      # 模块一句话职责最大字符

STOP = re.compile(r'^(Args?|Arguments|Returns?|Raises?|Yields?|Note[s]?|Example[s]?'
                  r'|Attributes|参数|返回|抛出|要点)\s*[:：]')

GROUP_DESC = {
    '（backend 根）': '启动与装配（main / config / deps / HTTP 补丁）',
    'chan': '自研缠论内核：K线 → 包含 → 分型 → 笔 → 线段 → 中枢 → 买卖点',
    'ai': '缠论之上的信号层：背驰 / 波浪 / 策略 / LLM 客户端与会话',
    'routers': 'HTTP 入口（FastAPI）',
    'services': '取数与业务：A 股 akshare、港股富途、缠论选股',
    'stores': '本地 JSON 持久化',
    'utils': '通用工具：缓存 / 限流 / 重试 / 代码归一 / 指标',
}


def esc(s):
    return s.replace('|', '\\|')


def brief(doc, maxlines=2, maxchars=MAXDOC):
    """docstring -> 一行说明：只取正文段，遇 Args:/Returns:/要点: 等段落标题即停。"""
    if not doc:
        return ''
    keep = []
    for l in doc.strip().split('\n'):
        t = l.strip()
        if STOP.match(t):
            break
        if t:
            keep.append(t)
    if not keep:
        return ''
    s = ' '.join(keep[:maxlines])
    if len(s) <= maxchars:
        return esc(s)
    cut = s[:maxchars - 1]
    for sep in ('。', '；', '，', ', ', '; '):          # 优先断在句读处，别切半句
        i = cut.rfind(sep)
        if i > maxchars * 0.45:
            cut = cut[:i + 1].rstrip('，,；; ')
            break
    return esc(cut + '…')


def psig(fn):
    """简化签名：只留参数名，默认值短的保留，去掉类型注解。"""
    a = fn.args
    names = []
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for p, d in zip(pos, defaults):
        if p.arg in ('self', 'cls'):
            continue
        s = p.arg
        if d is not None:
            v = ast.unparse(d)
            if len(v) <= 12:
                s += '=' + v
        names.append(s)
    if a.vararg:
        names.append('*' + a.vararg.arg)
    if a.kwarg:
        names.append('**' + a.kwarg.arg)
    head = ('async ' if isinstance(fn, ast.AsyncFunctionDef) else '') + fn.name
    out = head + '(' + ', '.join(names) + ')'
    return esc(out if len(out) <= MAXSIG else out[:MAXSIG - 2] + '…)')


def route(fn):
    """路由装饰器 -> '`GET /api/x/{code}` '；没有则返回空串。"""
    for x in getattr(fn, 'decorator_list', []):
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.args \
                and isinstance(x.args[0], ast.Constant) and isinstance(x.args[0].value, str):
            return '`%s %s` ' % (x.func.attr.upper(), x.args[0].value)
    return ''


# ---------------- 解析 ----------------
Group, stat = {}, dict(files=0, lines=0, cls=0, fn=0, consts=0, withdoc=0, nodes=0, dead=[])

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
    for f in sorted(filenames):
        if not f.endswith('.py'):
            continue
        p = os.path.join(dirpath, f)
        rel = os.path.relpath(p, ROOT)
        src = open(p, encoding='utf-8').read()
        nlines = len(src.split('\n'))
        stat['files'] += 1
        stat['lines'] += nlines
        group = rel.split(os.sep)[0] if os.sep in rel else '（backend 根）'
        try:
            tree = ast.parse(src)
        except Exception as e:
            Group.setdefault(group, []).append((rel, dict(err=str(e), lines=nlines)))
            continue

        mod_doc = ast.get_docstring(tree)
        consts, classes, funcs = [], [], []

        def collect(node):
            for c in node.body:
                if isinstance(c, (ast.Assign, ast.AnnAssign)):
                    tgts = c.targets if isinstance(c, ast.Assign) else [c.target]
                    for t in tgts:
                        if isinstance(t, ast.Name) and re.match(r'^[A-Z][A-Z0-9_]*$', t.id):
                            v = ast.unparse(c.value) if c.value is not None else ''
                            consts.append((t.id, v[:40] + ('…' if len(v) > 40 else '')))
                elif isinstance(c, ast.ClassDef):
                    stat['cls'] += 1
                    stat['nodes'] += 1
                    stat['withdoc'] += 1 if ast.get_docstring(c) else 0
                    members = []
                    for m in c.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if m.name.startswith('__') and m.name != '__init__':
                                continue
                            stat['fn'] += 1
                            stat['nodes'] += 1
                            stat['withdoc'] += 1 if ast.get_docstring(m) else 0
                            members.append((m.lineno, psig(m), brief(ast.get_docstring(m)), route(m)))
                        elif isinstance(m, ast.Assign):
                            for t in m.targets:
                                if isinstance(t, ast.Name) and re.match(r'^[A-Z][A-Z0-9_]*$', t.id):
                                    members.append((m.lineno, '`' + t.id + '`', '', ''))
                    classes.append((c.name, brief(ast.get_docstring(c), 2, 110), members))
                elif isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if c.name.startswith('__') and c.name != '__init__':
                        continue
                    stat['fn'] += 1
                    stat['nodes'] += 1
                    stat['withdoc'] += 1 if ast.get_docstring(c) else 0
                    funcs.append((c.lineno, psig(c), brief(ast.get_docstring(c)), route(c)))
        collect(tree)

        stat['consts'] += len(consts)
        if nlines > 60 and not classes and not funcs and not consts:
            stat['dead'].append(rel)
        Group.setdefault(group, []).append(
            (rel, dict(lines=nlines, mod=mod_doc, consts=consts, classes=classes, funcs=funcs)))

ORDER = [g for g in ['（backend 根）', 'chan', 'ai', 'routers', 'services', 'stores', 'utils'] if g in Group]
ORDER += [g for g in sorted(Group) if g not in ORDER]
COV = stat['withdoc'] * 100.0 / stat['nodes'] if stat['nodes'] else 0


def mod_desc(info):
    """模块一句话职责：优先 docstring 首段，没有则用「主要定义」兜底。"""
    d = brief(info['mod'], 3, MAXMOD) if info.get('mod') else ''
    if d:
        return d
    names = [c[0] for c in info['classes']] + [f[1].split('(')[0] for f in info['funcs']]
    return '定义：' + '、'.join(names[:4]) if names else '（空模块）'


def render(brief_mode):
    L = []
    A = L.append
    cov = '%.0f%%' % COV
    if brief_mode:
        A('# backend 模块速览')
        A('')
        A('> 数据源：`backend` 下全部 Python 文件，AST 解析自动生成。')
        A('> **%d 文件 / %s 行 / %d 类 / %d 函数，docstring 覆盖率 %s**' % (
            stat['files'], format(stat['lines'], ','), stat['cls'], stat['fn'], cov))
        A('> 只列模块与一句话职责；类/函数级骨架见 `backend-notes-full.md`。')
        A('> 重跑：`cd backend && python docs/gen_notes.py`')
    else:
        A('# backend 注释骨架（完整版）')
        A('')
        A('> 数据源：`backend` 下全部 Python 文件，AST 解析自动生成。')
        A('> **%d 文件 / %s 行 / %d 类 / %d 函数 / %d 常量，docstring 覆盖率 %s**' % (
            stat['files'], format(stat['lines'], ','), stat['cls'], stat['fn'], stat['consts'], cov))
        A('> 每条说明是 docstring 首段浓缩（≤%d 字符），完整版看源码；`L` 为定义行号。' % MAXDOC)
    A('')
    A('## 总览')
    A('')
    A('| 分组 | 文件 | 行数 | 职责 |')
    A('|---|---:|---:|---|')
    for g in ORDER:
        items = Group[g]
        tot = sum(i[1].get('lines', 0) for i in items)
        A('| **%s** | %d | %s | %s |' % (g, len(items), format(tot, ','), GROUP_DESC.get(g, '')))
    A('')
    if stat['dead']:
        A('**整文件处于注释（停用）状态**：%s' % ' '.join('`%s`' % r for r in stat['dead']))
        A('')
    A('**分层**：`chan/` 自研缠论内核 → `ai/` 在其上做信号与 LLM → `services/` 取数 → '
      '`routers/` 出 HTTP → `utils/`+`stores/` 兜底。')
    A('')

    for g in ORDER:
        A('## %s · %s' % (g, GROUP_DESC.get(g, '')))
        A('')
        for rel, info in Group[g]:
            if brief_mode:
                if 'err' in info:
                    A('- `%s` · %d 行 — ⚠️ 语法解析失败：%s' % (rel, info['lines'], esc(info['err'])))
                else:
                    A('- `%s` · %d 行 — %s' % (rel, info['lines'], mod_desc(info)))
                continue
            head = '### `%s` · %d 行' % (rel, info.get('lines', 0))
            if 'err' in info:
                A('%s — ⚠️ 语法解析失败：%s' % (head, esc(info['err'])))
                continue
            A('')
            A(head + ' — ' + brief(info['mod'], 3, MAXMOD) if info.get('mod') else head)
            if info['consts']:
                A('- 常量 %s' % ' · '.join(
                    '`%s`' % (n + '=' + v if v and len(v) <= 18 else n) for n, v in info['consts']))
            if not info['classes'] and not info['funcs'] and not info['consts']:
                A('- _（%s）_' % ('整文件为注释，无可执行定义' if info.get('lines', 0) > 60 else '仅包说明'))
                continue
            for name, doc, members in info['classes']:
                A('- **%s** — %s' % (name, doc or '（无说明）'))
                if not members:
                    A('  - _（无公开成员）_')
                for ml, ms, mb, mr in members:
                    A('  - `L%d` %s`%s`%s' % (ml, mr, ms, (' — ' + mb) if mb else ''))
            for fl, fs, fb, fr in info['funcs']:
                A('- `L%d` %s`%s`%s' % (fl, fr, fs, (' — ' + fb) if fb else ''))
        A('')

    A('## 附：改动前必看的坑')
    A('')
    for t in [
        '`CChan` 构造即联网查股票基本信息，必须包 try/except。',
        '`CBS_Point.type` 是 `BSP_TYPE` **列表**（一根笔可同时命中多类买卖点），不是单值。',
        '`CTime` 无比较运算符，时间先后一律用 `klu.idx`。',
        'KLU 的 `macd/rsi/kdj` 懒创建：配置没开就没该属性，读取前要 `hasattr` / 判 None。',
        '手工喂 K 线给 `CKLine_List` 必须自己 `set_idx(i)` + `kl_type`，喂完统一 `cal_seg_and_zs()`。',
        'baostock 参数非法时返回 `None` 而非带 `error_code` 的对象；无 limit 参数，`start_date=None` 会拉全历史。',
        '`CBaoStock.do_init/do_close` 用类属性 + 全局 login/logout，线程池并发会互踢——这也是 `screening_service` 不直接构造 `CChan` 的原因。',
        '`utils/indicators` 口径与前端 `stockIndicators` 一致，改一处要同步前端。',
    ]:
        A('- ' + t)
    A('')
    A('## 附：重新生成')
    A('')
    A('```bash')
    A('cd backend && python docs/gen_notes.py')
    A('```')
    A('')
    for t in [
        '一次产出两份：`backend-notes.md`（极简，模块级）与 `backend-notes-full.md`（类/函数级）。',
        '跳过 `.venv/` / `__pycache__/` / `docs/` 自身；行数 > 60 且无任何定义的文件判定为「整文件注释停用」。',
        '说明只取 docstring 正文段，遇 `Args:`/`Returns:`/`要点:` 即停；签名去掉类型注解，超 %d 字符截断。' % MAXSIG,
        '要再短：调脚本顶部的 `MAXMOD`；要更长：改 `MAXDOC`。',
    ]:
        A('- ' + t)
    A('')
    return L


# 本脚本是独立命令行工具，root 没配过 handler 时才兜一个最简控制台输出
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format='%(levelname)-8s | %(message)s')

os.makedirs(os.path.dirname(BRIEF_OUT), exist_ok=True)
b = render(True)
f = render(False)
open(BRIEF_OUT, 'w', encoding='utf-8').write('\n'.join(b))
open(FULL_OUT, 'w', encoding='utf-8').write('\n'.join(f))
log.info('极简版 %s —— %d 行', BRIEF_OUT, len(b))
log.info('完整版 %s —— %d 行', FULL_OUT, len(f))
log.info('文件 %d  行 %d  类 %d  函数 %d  常量 %d  覆盖率 %.0f%%',
         stat['files'], stat['lines'], stat['cls'], stat['fn'], stat['consts'], COV)
