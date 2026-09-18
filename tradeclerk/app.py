from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from openai import OpenAI
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, context
from .agent import SYSTEM_PROMPT, mock_agent, run_agent
from .auth import configured_api_key, reject_unauthorized
from .tools import list_leads, list_quotes

logger = logging.getLogger("tradeclerk")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent
WEB_INDEX = ROOT / "web" / "index.html"
SESSION_NOT_FOUND = "session_id 不存在，请留空以创建新会话"


@dataclass
class Session:
    id: str
    customer_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
        self.mock = not api_key
        self.client: OpenAI | None = None
        if api_key:
            kwargs: dict[str, Any] = {"api_key": api_key}
            base_url = os.getenv("OPENAI_BASE_URL", "").strip()
            if base_url:
                kwargs["base_url"] = base_url
            self.client = OpenAI(**kwargs)
        else:
            self.model = "mock"

    def get_or_create(self, session_id: str, customer_id: str) -> Session:
        with self.lock:
            if session_id:
                sess = self.sessions.get(session_id)
                if sess is None:
                    raise KeyError(SESSION_NOT_FOUND)
                return sess
            new_id = "sess_" + secrets.token_hex(8)
            sess = Session(
                id=new_id,
                customer_id=customer_id,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}],
            )
            self.sessions[new_id] = sess
            return sess


store = Store()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if store.mock:
        logger.warning("未设置 OPENAI_API_KEY，使用 mock 模式（不调用模型，只演示 HTTP 接口）")
    if not configured_api_key():
        logger.warning("未设置 TRADECLERK_API_KEY，/v1 接口将返回 503")
    logger.info(
        "TradeClerk 服务已启动 version=%s model=%s mock=%s",
        __version__,
        store.model,
        store.mock,
    )
    yield


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        denied = reject_unauthorized(request)
        if denied is not None:
            logger.info("http method=%s path=%s status=%s", request.method, request.url.path, denied.status_code)
            return denied
        start = time.perf_counter()
        response = await call_next(request)
        took = time.perf_counter() - start
        logger.info("http method=%s path=%s took=%.3fms", request.method, request.url.path, took * 1000)
        return response


app = FastAPI(title="TradeClerk", version=__version__, lifespan=lifespan)
app.add_middleware(AccessLogMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "mock" if store.mock else "openai",
        "model": store.model,
    }


@app.get("/")
def index():
    if not WEB_INDEX.is_file():
        return JSONResponse({"error": "index not found"}, status_code=500)
    return FileResponse(WEB_INDEX, media_type="text/html; charset=utf-8")


@app.post("/v1/chat")
async def chat(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON 无法解析"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "JSON 无法解析"}, status_code=400)

    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message 不能为空"}, status_code=400)
    customer_id = str(payload.get("customer_id") or "").strip() or "buyer_001"
    session_id = str(payload.get("session_id") or "").strip()

    try:
        sess = store.get_or_create(session_id, customer_id)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    with sess.lock:
        sess.messages.append({"role": "user", "content": message})
        token = context.buyer_id.set(sess.customer_id)
        try:
            if store.mock:
                result = mock_agent(message)
                mode = "mock"
            else:
                assert store.client is not None
                try:
                    result = run_agent(store.client, store.model, sess.messages)
                except Exception as exc:
                    logger.exception("agent 失败 session_id=%s", sess.id)
                    return JSONResponse({"error": str(exc)}, status_code=502)
                sess.messages = result.messages
                mode = "openai"
        finally:
            context.buyer_id.reset(token)

    return {
        "session_id": sess.id,
        "customer_id": sess.customer_id,
        "reply": result.reply,
        "traces": [t.as_dict() for t in result.traces],
        "mode": mode,
    }


@app.get("/v1/quotes")
def quotes():
    return list_quotes()


@app.get("/v1/leads")
def leads():
    return list_leads()


def parse_addr(addr: str) -> tuple[str, int]:
    raw = addr.strip() or ":8080"
    if raw.startswith(":"):
        return "0.0.0.0", int(raw[1:])
    host, _, port = raw.rpartition(":")
    return host or "0.0.0.0", int(port)


def run() -> None:
    import uvicorn

    host, port = parse_addr(os.getenv("ADDR", ":8080"))
    uvicorn.run("tradeclerk.app:app", host=host, port=port, factory=False)


if __name__ == "__main__":
    run()
