"""AI 诊股路由：把缠论数据喂给 LLM，以 SSE 流式返回回答。

协议约定（三帧）：

    1. `: stream-open\n\n`        注释帧，先让浏览器/网关建立连接
    2. `data: {"token": "..."}\n\n`  逐 token 正文帧
    3. `data: {"done": true, "full": "..."}\n\n` 结束帧（含完整文本）

出错时发 `data: {"error": "..."}` 帧，而不是断开连接，前端可原地提示。

两个模型的差异：

    - DeepSeek：用共享的 httpx.AsyncClient 原生流式读 SSE；
    - Gemini：官方客户端是同步的，只能 asyncio.to_thread 丢线程池，
      拿到全文后再切成 48 字符的小块模拟流式。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai.chat_sessions import get_or_create_session
from config import DEEPSEEK_MODEL_ID
from core.chanlun_analysis import run_analysis
from core.datetime_fmt import format_date_short
from deps import check_ai_diagnosis_rate_limits, client_ip
from services.akshare_service import get_stock_info, normalize_stock_code

router = APIRouter()
log = logging.getLogger(__name__)

# 进程内共享的异步 HTTP 客户端：复用连接池，避免每个诊断请求重建 TLS 握手
_diagnosis_async_client: httpx.AsyncClient | None = None


def _get_diagnosis_async_client() -> httpx.AsyncClient:
    """惰性创建并返回共享的 httpx 异步客户端（进程内单例）。

    Returns:
        httpx.AsyncClient，超时 120s（LLM 长回答需要），连接池上限 20。
    """
    global _diagnosis_async_client
    if _diagnosis_async_client is None:
        _diagnosis_async_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _diagnosis_async_client


class DiagnosisBody(BaseModel):
    """AI 诊股 POST 请求体（推荐入口，长问题走 body 而不是 URL）。"""

    code: str = Field(..., min_length=4, max_length=16, description="股票代码")
    question: str = Field(..., min_length=1, max_length=8000, description="用户问题")
    session_id: str = Field("default", max_length=128, description="会话 ID")
    model: str = Field("deepseek", description="AI 模型：deepseek / gemini")


def _normalize_model(model: str) -> str:
    """把模型名收敛到白名单内的取值，非法值一律回落 deepseek。

    Args:
        model: 用户传入的模型名。

    Returns:
        "deepseek" 或 "gemini"。
    """
    return model if model in ("deepseek", "gemini") else "deepseek"


async def _diagnosis_event_stream(
    code: str,
    question: str,
    session_id: str,
    model: str,
) -> AsyncIterator[str]:
    """AI 诊股的 SSE 事件流生成器。

    流程：发开流帧 → 并发取「股票信息 + 日线缠论结果」→ 拼装成 LLM 系统提示 →
    按模型分别流式生成 → 结束时把助手文本写入会话历史 → 发 done 帧。

    Args:
        code: 用户输入的股票代码，内部会归一化。
        question: 用户问题。
        session_id: 会话 ID，用于多轮上下文。
        model: "deepseek" 或 "gemini"。

    Yields:
        SSE 帧字符串（已带 `data: ` 前缀与双换行）。

    Note:
        整体包在 try/except 里，未捕获异常也会以 error 帧结束，不会让连接崩掉。
        但注意 session.add("assistant", full_text) 在异常分支同样会执行，
        失败时可能把空字符串写进历史。
    """
    try:
        # 注释帧：先发一帧占位，让浏览器/反向代理立刻建立 SSE 连接
        yield ": stream-open\n\n"

        sym, _exchange = normalize_stock_code(code)

        async def _load_info() -> tuple[str, str]:
            """并发任务一：取股票名称；失败时静默回落到代码本身。"""
            name = sym
            try:
                info = await asyncio.to_thread(get_stock_info, sym)
                name = str(info.get("名称", sym))
            except Exception:
                pass
            return sym, name

        async def _load_analysis():
            """并发任务二：跑日线缠论分析（走 core 层缓存）。"""
            return await asyncio.to_thread(run_analysis, sym, "daily")

        # 两条取数互不依赖，并发跑；return_exceptions=True 表示任一失败不中断另一条
        info_res, analysis_res = await asyncio.gather(
            _load_info(),
            _load_analysis(),
            return_exceptions=True,
        )

        # 降级：拿不到名称就用代码顶上，不影响后续诊断
        if isinstance(info_res, Exception):
            stock_name = sym
        else:
            _, stock_name = info_res

        analysis_context = ""
        # 缠论拿不到时不报错，把失败原因写进上下文，让 LLM 自己说明数据缺失
        if isinstance(analysis_res, Exception):
            analysis_context = f"[缠论数据获取失败: {analysis_res}]"
        else:
            result = analysis_res
            try:
                # 只喂最近 20 根 K 线 + 最近几笔/中枢/买卖点：控制 token，也避免超窗
                recent_kl = result.klines[-20:] if len(result.klines) > 20 else result.klines
                kl_lines = "\n".join(
                    f"{format_date_short(k.date)}  开:{k.open:.2f} 高:{k.high:.2f} 低:{k.low:.2f} 收:{k.close:.2f}"
                    for k in recent_kl
                )
                bi_lines = "\n".join(
                    f"[{format_date_short(b.start)}] {b.direction} 高:{b.high:.2f} 低:{b.low:.2f}"
                    for b in result.bis[-5:]
                )
                zs_lines = "\n".join(
                    f"[{format_date_short(z.start)}] 中枢 高:{z.range_high:.2f} 低:{z.range_low:.2f}"
                    for z in result.zhongshus[-3:]
                )
                sig_lines = "\n".join(
                    f"{format_date_short(s.datetime)} {s.type}@{s.price:.2f} 置信:{s.confidence}"
                    for s in result.signals[-5:]
                )

                analysis_context = f"""
【{stock_name}({sym}) 日线数据】
最近K线：\n{kl_lines}
笔：\n{bi_lines or '暂无'}
中枢：\n{zs_lines or '暂无'}
买卖点：\n{sig_lines or '暂无'}
趋势：{result.trend}"""
            except Exception as e:
                analysis_context = f"[缠论数据解析失败: {e}]"

        system_prompt = f"""你是专业的缠论技术分析助手，名称"缠师"。

用户会向你询问股票诊断问题。请结合以下缠论数据，
用通俗易懂的语言给出诊断建议，避免过于专业的术语堆砌。

当前股票数据：
{analysis_context}

回答要求：
1. 先确认用户问的股票，分析当前走势和位置
2. 结合缠论关键点（笔、中枢、背驰、买卖点）
3. 给出明确操作建议（买入/卖出/观望）
4. 如需要，提示风险和止损位
5. 保持友好专业的语气，像一个老练的分析师在聊天

重要：如果没有股票数据，请直接说明并建议用户提供股票代码。"""

        session = get_or_create_session(session_id)
        # 先记用户问句再流式生成：即使后面出错，这一轮的问题也已留在历史里
        session.add("user", question)

        messages = [{"role": "system", "content": system_prompt}] + session.history()

        # full_text 在异常分支也要用，所以必须先初始化
        full_text = ""
        try:
            if model.startswith("deepseek"):
                key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
                if not key:
                    yield f"data: {json.dumps({'error': 'DEEPSEEK_API_KEY 未设置，请在 .env 中配置'}, ensure_ascii=False)}\n\n"
                    return

                log.info("AI诊断 DeepSeek 流式开始 session=%s", session_id)
                body = {
                    "model": DEEPSEEK_MODEL_ID,
                    "messages": messages,
                    "temperature": 0.4,
                    "stream": True,
                }
                client = _get_diagnosis_async_client()
                async with client.stream(
                        "POST",
                        "https://api.deepseek.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.strip() or not line.startswith("data:"):
                                continue
                            # 去掉 "data:" 前缀（5 个字符）后才是真正的 JSON 载荷
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                content = (
                                    chunk.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content", "")
                                )
                                if content:
                                    full_text += content
                                    yield f"data: {json.dumps({'token': content}, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue
                log.info(
                    "AI诊断 DeepSeek 完成 session=%s chars=%s",
                    session_id,
                    len(full_text),
                )

            elif model.startswith("gemini"):
                key = os.environ.get("GEMINI_API_KEY", "").strip()
                if not key:
                    yield f"data: {json.dumps({'error': 'GEMINI_API_KEY 未设置，请在 .env 中配置'}, ensure_ascii=False)}\n\n"
                    return

                from ai.llm_client import get_llm_client

                gemini_client = get_llm_client(model)
                # Gemini 走同步 httpx.Client，须丢到线程池，避免阻塞事件循环
                full_text = await asyncio.to_thread(
                    gemini_client.chat,
                    prompt=question,
                    system=system_prompt,
                    temperature=0.4,
                )
                # 同步客户端拿不到流式，只能把整段文本切块模拟流式，块大小影响手感
                chunk_size = 48
                for i in range(0, len(full_text), chunk_size):
                    piece = full_text[i : i + chunk_size]
                    yield f"data: {json.dumps({'token': piece}, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 生成阶段出错：发 error 帧而不是断开，前端可原地提示
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        # 无论成功失败都回填助手文本（异常时可能是空串），再发结束帧
        session.add("assistant", full_text)
        yield f"data: {json.dumps({'done': True, 'full': full_text}, ensure_ascii=False)}\n\n"

    except Exception as e:
        log.exception("AI诊断流未捕获异常 session=%s", session_id)
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"


def _sse_response(stream: AsyncIterator[str]) -> StreamingResponse:
    """把异步生成器包成 SSE 响应。

    Args:
        stream: 产出 SSE 帧字符串的异步生成器。

    Returns:
        StreamingResponse，media_type 为 text/event-stream。

    Note:
        X-Accel-Buffering: no 是给 Nginx 的开关，否则缓冲会让流式退化成一次性返回。
    """
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/ai/diagnosis", tags=["AI诊股"])
async def ai_diagnosis_get(
    request: Request,
    code: str = Query(..., description="股票代码"),
    question: str = Query(..., description="用户问题"),
    session_id: str = Query("default", description="会话ID"),
    model: str = Query("deepseek", description="AI模型"),
):
    """兼容旧版 GET 入口：问题放在 URL 查询参数（长文本不推荐，有长度限制）。

    Args:
        request: 用于取客户端 IP 做 AI 诊股限流。
        code: 股票代码。
        question: 用户问题。
        session_id: 会话 ID，默认 "default"。
        model: AI 模型，非法值会被 _normalize_model 回落为 deepseek。

    Returns:
        SSE 流式响应。
    """
    check_ai_diagnosis_rate_limits(client_ip(request))
    model = _normalize_model(model)
    log.info("AI诊断 GET code=%s session=%s", code, session_id)
    return _sse_response(_diagnosis_event_stream(code, question, session_id, model))


@router.post("/api/ai/diagnosis", tags=["AI诊股"])
async def ai_diagnosis_post(request: Request, body: DiagnosisBody):
    """推荐入口：JSON body 传参，避免长问题进 URL（也避开 URL 长度与转义问题）。

    Args:
        request: 用于取客户端 IP 做 AI 诊股限流。
        body: DiagnosisBody，字段长度由 pydantic 约束（问题最长 8000 字符）。

    Returns:
        SSE 流式响应。
    """
    check_ai_diagnosis_rate_limits(client_ip(request))
    model = _normalize_model(body.model)
    log.info("AI诊断 POST code=%s session=%s", body.code, body.session_id)
    return _sse_response(
        _diagnosis_event_stream(
            body.code.strip(),
            body.question.strip(),
            body.session_id.strip() or "default",
            model,
        )
    )
