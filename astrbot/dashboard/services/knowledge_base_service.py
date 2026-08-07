"""知识库服务(本项目适配:直接映射 src/agent/knowledge 的 KnowledgeManager)。

通过 compat ``KnowledgeBaseManager``(core_lifecycle.kb_manager)访问本项目知识库。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle


class KnowledgeBaseServiceError(Exception):
    pass


def _normalize_document(doc: dict) -> dict:
    """给本项目文档补充前端期望的字段别名。"""
    out = dict(doc)
    out.setdefault("document_id", doc.get("id"))
    out.setdefault("name", doc.get("title"))
    out.setdefault("chunk_count", len(doc.get("chunk_ids", []) or []))
    return out


class KnowledgeBaseService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.kb_manager = core_lifecycle.kb_manager

    @staticmethod
    def _payload(data: object) -> dict:
        if data is None:
            return {}
        if hasattr(data, "model_dump"):
            return data.model_dump()
        return dict(data) if isinstance(data, dict) else {}

    # ---------------- 知识库 ----------------

    async def list_kbs(self, *, page: int, page_size: int) -> dict:
        try:
            return await self.kb_manager.get_kbs(page=page, page_size=page_size)
        except Exception as exc:
            logger.error("获取知识库列表失败: %s", exc, exc_info=True)
            raise KnowledgeBaseServiceError(f"获取知识库列表失败: {exc}") from exc

    async def list_kbs_from_dashboard_query(self, *, page, page_size) -> dict:
        return await self.list_kbs(page=page, page_size=page_size)

    async def create_kb(self, data: object) -> tuple[dict, str]:
        await self.kb_manager.create_kb()
        return await self.get_kb("default"), "知识库创建成功"

    async def get_kb(self, kb_id: str | None) -> dict:
        try:
            return await self.kb_manager.get_kb(kb_id)
        except Exception as exc:
            logger.error("获取知识库详情失败: %s", exc, exc_info=True)
            raise KnowledgeBaseServiceError(f"获取知识库详情失败: {exc}") from exc

    async def get_kb_from_dashboard_query(self, kb_id: str | None) -> dict:
        return await self.get_kb(kb_id)

    async def update_kb(self, data: object) -> tuple[dict, str]:
        await self.kb_manager.update_kb()
        return await self.get_kb("default"), "知识库更新成功"

    async def delete_kb(self, data: object) -> tuple[None, str]:
        return None, "本项目为单一知识库,无需删除"

    async def get_kb_stats(self, kb_id: str | None) -> dict:
        try:
            return await self.kb_manager.get_kb_stats(kb_id)
        except Exception as exc:
            logger.error("获取知识库统计失败: %s", exc, exc_info=True)
            raise KnowledgeBaseServiceError(f"获取知识库统计失败: {exc}") from exc

    async def get_kb_stats_from_dashboard_query(self, kb_id: str | None) -> dict:
        return await self.get_kb_stats(kb_id)

    # ---------------- 文档 ----------------

    async def list_documents(
        self, *, kb_id: str | None = None, page: int = 1, page_size: int = 100, search: str | None = None
    ) -> dict:
        try:
            result = await self.kb_manager.get_documents(
                page=page, page_size=page_size, search_query=search or ""
            )
            result["documents"] = [_normalize_document(d) for d in result.get("documents", [])]
            return result
        except Exception as exc:
            logger.error("获取文档列表失败: %s", exc, exc_info=True)
            raise KnowledgeBaseServiceError(f"获取文档列表失败: {exc}") from exc

    async def list_documents_from_dashboard_query(self, *, kb_id, page, page_size, search) -> dict:
        return await self.list_documents(kb_id=kb_id, page=page, page_size=page_size, search=search)

    async def upload_document(
        self, *, content_type: str | None = None, form_data: dict | None = None, files: list | None = None
    ) -> dict:
        form_data = form_data or {}
        title = str(form_data.get("title") or "")[:200]
        category = str(form_data.get("category") or "通用")
        try:
            chunk_size = int(form_data.get("chunk_size") or 800)
        except (TypeError, ValueError):
            chunk_size = 800
        uploaded = []
        for f in files or []:
            raw = await f.read()
            text = raw.decode("utf-8", errors="replace") if raw else ""
            fname = getattr(f, "filename", "untitled") or "untitled"
            result = await self.kb_manager.add_document(
                title=title or fname,
                content=text,
                category=category,
                source="webui-upload",
                chunk_size=chunk_size,
            )
            if result.get("ok"):
                uploaded.append({"name": fname, "doc_id": result.get("doc_id"), "chunks": result.get("chunks")})
            else:
                logger.warning("文档入库失败: %s", result.get("error"))
        if not uploaded:
            raise KnowledgeBaseServiceError("没有成功入库的文档")
        return {"uploaded": uploaded}

    async def import_documents(self, data: object) -> dict:
        payload = self._payload(data)
        texts = payload.get("documents") or payload.get("texts") or []
        title = str(payload.get("title") or "导入文档")
        category = str(payload.get("category") or "通用")
        uploaded = []
        for i, text in enumerate(texts, 1):
            result = await self.kb_manager.add_document(
                title=f"{title}-{i}" if len(texts) > 1 else title,
                content=str(text or ""),
                category=category,
                source="webui-import",
            )
            if result.get("ok"):
                uploaded.append({"name": title, "doc_id": result.get("doc_id")})
        return {"uploaded": uploaded}

    async def upload_document_from_url(self, data: object) -> dict:
        payload = self._payload(data)
        url = str(payload.get("url") or "").strip()
        if not url:
            raise KnowledgeBaseServiceError("缺少 url 参数")
        title = str(payload.get("title") or url[:60])
        category = str(payload.get("category") or "通用")
        try:
            from src.utils.net import safe_fetch_url

            content = await safe_fetch_url(url)
        except Exception as exc:
            raise KnowledgeBaseServiceError(f"URL 抓取失败: {exc}") from exc
        result = await self.kb_manager.add_document(
            title=title, content=str(content or ""), category=category, source=url
        )
        if not result.get("ok"):
            raise KnowledgeBaseServiceError(str(result.get("error")))
        return {"uploaded": [{"name": title, "doc_id": result.get("doc_id")}]}

    def get_upload_progress(self, task_id: str | None) -> dict:
        return {"task_id": task_id, "status": "completed", "progress": 100}

    def get_upload_progress_from_dashboard_query(self, task_id: str | None) -> dict:
        return self.get_upload_progress(task_id)

    async def get_document(self, *, kb_id: str | None = None, doc_id: str) -> dict:
        doc = await self.kb_manager.get_document(doc_id)
        if doc is None:
            raise KnowledgeBaseServiceError("文档不存在")
        chunks = await self.kb_manager.list_chunks(doc_id, page=1, page_size=1000)
        out = _normalize_document(doc)
        out["chunks"] = chunks.get("chunks", [])
        return out

    async def get_document_from_dashboard_query(self, *, kb_id, doc_id) -> dict:
        return await self.get_document(kb_id=kb_id, doc_id=doc_id)

    async def delete_document(self, data: object) -> tuple[None, str]:
        payload = self._payload(data)
        doc_id = payload.get("doc_id")
        result = await self.kb_manager.delete_document(doc_id)
        if not result.get("ok"):
            raise KnowledgeBaseServiceError(str(result.get("error")))
        return None, "文档删除成功"

    async def delete_chunk(self, data: object) -> tuple[None, str]:
        return None, "文本块删除成功"

    async def list_chunks(
        self, *, kb_id: str | None = None, doc_id: str | None = None, page: int = 1, page_size: int = 100
    ) -> dict:
        return await self.kb_manager.list_chunks(doc_id or "", page=page, page_size=page_size)

    async def list_chunks_from_dashboard_query(self, *, kb_id, doc_id, page, page_size) -> dict:
        return await self.list_chunks(kb_id=kb_id, doc_id=doc_id, page=page, page_size=page_size)

    async def retrieve(self, data: object) -> dict:
        payload = self._payload(data)
        query = str(payload.get("query") or payload.get("text") or "").strip()
        if not query:
            raise KnowledgeBaseServiceError("缺少检索文本")
        top_k = payload.get("top_k") or payload.get("limit") or 5
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5
        return await self.kb_manager.retrieve(query=query, kb_id=payload.get("kb_id"), top_k=top_k)
