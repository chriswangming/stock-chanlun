from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from deps import check_light_api_rate_limits, client_ip
from services.akshare_service import normalize_stock_code
from stores.local_json import (
    comments_add,
    comments_delete,
    comments_get,
    comments_update,
)

router = APIRouter()


class CommentBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)


def _get_comments_impl(stock_code: str):
    sym, _ = normalize_stock_code(stock_code)
    items = comments_get(sym)
    items = sorted(items, key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"comments": items, "total": len(items)}


@router.get("/api/comments/{stock_code}", tags=["评论"])
async def get_comments(stock_code: str, request: Request):
    check_light_api_rate_limits(client_ip(request))
    return await asyncio.to_thread(_get_comments_impl, stock_code)


def _add_comment_impl(stock_code: str, content: str):
    sym, _ = normalize_stock_code(stock_code)
    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="笔记内容不能为空")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    comment = {
        "id": str(uuid.uuid4()),
        "stockCode": sym,
        "content": content,
        "createdAt": now,
        "updatedAt": now,
    }
    comments_add(sym, comment)
    return {"comment": comment, "added": True}


@router.post("/api/comments/{stock_code}", tags=["评论"])
async def add_comment(stock_code: str, comment_in: CommentBody, request: Request):
    check_light_api_rate_limits(client_ip(request))
    return await asyncio.to_thread(_add_comment_impl, stock_code, comment_in.content)


def _update_comment_impl(stock_code: str, comment_id: str, content: str):
    sym, _ = normalize_stock_code(stock_code)
    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="笔记内容不能为空")

    updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not comments_update(sym, comment_id, content, updated):
        raise HTTPException(status_code=404, detail="笔记不存在")
    for c in comments_get(sym):
        if c.get("id") == comment_id:
            return {"comment": c, "updated": True}
    raise HTTPException(status_code=404, detail="笔记不存在")


@router.put("/api/comments/{stock_code}/{comment_id}", tags=["评论"])
async def update_comment(
    stock_code: str,
    comment_id: str,
    comment_in: CommentBody,
    request: Request,
):
    check_light_api_rate_limits(client_ip(request))
    return await asyncio.to_thread(
        _update_comment_impl, stock_code, comment_id, comment_in.content
    )


def _delete_comment_impl(stock_code: str, comment_id: str):
    sym, _ = normalize_stock_code(stock_code)
    if not comments_delete(sym, comment_id):
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"id": comment_id, "deleted": True}


@router.delete("/api/comments/{stock_code}/{comment_id}", tags=["评论"])
async def delete_comment(stock_code: str, comment_id: str, request: Request):
    check_light_api_rate_limits(client_ip(request))
    return await asyncio.to_thread(_delete_comment_impl, stock_code, comment_id)
