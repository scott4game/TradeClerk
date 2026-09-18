from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .tools import execute_tool, registered_tools

logger = logging.getLogger("tradeclerk")

MAX_STEPS = 8
SYSTEM_PROMPT = """你是宁波 Apex Lighting 的外贸销售 AI Agent，由 TradeClerk 驱动，服务海外采购商。只处理当前买家的询盘与出货。

规则：
1. 问产品、规格、MOQ、交期、认证、HS 编码时，必须先 search_catalog，禁止编造 SKU 或价格。
2. 买家要报价（数量、Incoterm、目的港）时，必须调用 create_quotation，只能依据工具返回的单价和总价回答，并报出报价单号。
3. 涉及付款、样品、质保、交期、Incoterms 时，必须先 search_trade_terms，引用条款 ID。
4. 问出货、柜号、提单、ETA 时，必须先 get_shipment。
5. 无法当场办结、需要业务员确认、或买家留下公司/国家时，调用 create_lead。
6. 工具返回 ok=false 时说明原因；FORBIDDEN / NOT_FOUND / BELOW_MOQ 时给出可执行建议或转人工。
7. 用买家所用语言回复（英文询盘用简洁商务英语，中文询盘用中文）。不要承诺工具中未出现的折扣、交期或认证。"""

SKU_RE = re.compile(r"LED-[A-Z0-9-]+")
SHIPMENT_RE = re.compile(r"(?:SHP-\d+|EGLV\d+|COSU\d+|TCLU\d+)")
INCOTERM_RE = re.compile(r"(?i)\b(EXW|FOB|CFR|CIF)\b")


@dataclass
class ToolTrace:
    name: str
    arguments: str
    result: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "arguments": self.arguments, "result": self.result}


@dataclass
class AgentResult:
    reply: str
    traces: list[ToolTrace] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)


def run_agent(client: OpenAI, model: str, messages: list[dict[str, Any]]) -> AgentResult:
    traces: list[ToolTrace] = []
    working = list(messages)
    tools = registered_tools()

    for step in range(1, MAX_STEPS + 1):
        completion = client.chat.completions.create(
            model=model,
            messages=working,
            tools=tools,
        )
        msg = completion.choices[0].message
        if not msg.tool_calls:
            reply = (msg.content or "").strip() or "模型没有给出回复，请转人工。"
            working.append({"role": "assistant", "content": msg.content or ""})
            return AgentResult(reply=reply, traces=traces, messages=working)

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in msg.tool_calls
            ],
        }
        working.append(assistant_msg)

        for call in msg.tool_calls:
            name = call.function.name
            args = call.function.arguments or "{}"
            logger.info("tool call step=%s name=%s arguments=%s", step, name, args)
            result = execute_tool(name, args)
            logger.info("tool result step=%s name=%s result=%s", step, name, result)
            traces.append(ToolTrace(name=name, arguments=args, result=result))
            working.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return AgentResult(reply="超过最大步数，已转人工。", traces=traces, messages=working)


def mock_agent(message: str) -> AgentResult:
    lower = message.lower()
    sku = "LED-HB-150"
    catalog_query = sku
    found_sku = SKU_RE.search(message)
    if found_sku:
        sku = found_sku.group(0)
        catalog_query = sku
    elif "street" in lower or "路灯" in lower:
        catalog_query, sku = "street light", "LED-ST-100"
    elif "panel" in lower or "面板" in lower:
        catalog_query, sku = "panel", "LED-PNL-40"
    elif "t8" in lower or "tube" in lower or "灯管" in lower:
        catalog_query, sku = "t8", "LED-T8-18"
    elif "high bay" in lower or "工矿" in lower:
        catalog_query, sku = "high bay", "LED-HB-150"

    incoterm = "FOB"
    found_term = INCOTERM_RE.search(message)
    if found_term:
        incoterm = found_term.group(1).upper()

    dest = ""
    if "los angeles" in lower or "洛杉矶" in lower:
        dest = "Los Angeles"
    elif "hamburg" in lower or "汉堡" in lower:
        dest = "Hamburg"
    elif incoterm in {"CFR", "CIF"}:
        dest = "Los Angeles"

    traces: list[ToolTrace] = []

    def call(name: str, args: dict[str, Any]) -> None:
        raw = json.dumps(args, ensure_ascii=False)
        traces.append(ToolTrace(name=name, arguments=raw, result=execute_tool(name, raw)))

    asks_product = any(
        token in lower
        for token in ("产品", "sku", "moq", "high bay", "工矿", "路灯", "panel", "street", "certificate", "认证")
    ) or bool(SKU_RE.search(message))
    asks_quote = any(token in lower for token in ("报价", "quote", "price", "cif", "fob"))
    asks_terms = any(
        token in lower for token in ("付款", "payment", "样品", "sample", "质保", "warranty", "信用证", "l/c")
    )
    asks_ship = any(token in lower for token in ("出货", "shipment", "提单", "柜")) or bool(
        SHIPMENT_RE.search(message)
    )
    asks_lead = any(
        token in lower for token in ("跟进", "lead", "业务员", "联系", "follow", "salesman", "sales ")
    )

    if asks_product or asks_quote:
        call("search_catalog", {"query": catalog_query})
    if asks_quote:
        call(
            "create_quotation",
            {"sku": sku, "qty": 500, "incoterm": incoterm, "dest_port": dest},
        )
    if asks_terms:
        call("search_trade_terms", {"query": "payment T/T sample"})
    if asks_ship:
        ref = "SHP-9001"
        found_ref = SHIPMENT_RE.search(message)
        if found_ref:
            ref = found_ref.group(0)
        call("get_shipment", {"ref": ref})
    if asks_lead:
        call("create_lead", {"company": "", "country": "", "need": message})

    reply = (
        "【mock 模式，未调用大模型】工具已执行完毕。报价以 create_quotation 返回为准；"
        "条款请引用 POL-PAY-TT 等 ID。需要业务员跟进可说「帮我建个 lead」。"
    )
    if not traces:
        reply = "【mock 模式】我是 TradeClerk。可以问 150W 工矿灯 500 件 CIF 洛杉矶报价，或查询 SHP-9001。"
    return AgentResult(reply=reply, traces=traces)
