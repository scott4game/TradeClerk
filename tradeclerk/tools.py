from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import context

INCOTERM_FREIGHT_USD = {
    "EXW": -1.20,
    "FOB": 0.0,
    "CFR": 1.80,
    "CIF": 2.20,
}

QUOTE_LOCK = threading.Lock()
LEAD_LOCK = threading.Lock()
_quote_seq = 2000
_lead_seq = 3000
quotations: dict[str, dict[str, Any]] = {}
leads: dict[str, dict[str, Any]] = {}


def registered_tools() -> list[dict[str, Any]]:
    return [
        _fn(
            "search_catalog",
            "检索出口产品目录。询价、规格、MOQ、交期、认证、HS 编码前必须调用。可按关键词或 SKU 搜索。",
            {
                "query": {
                    "type": "string",
                    "description": "产品关键词或 SKU，例如 high bay、150W、LED-HB-150",
                }
            },
            ["query"],
        ),
        _fn(
            "create_quotation",
            "按 SKU、数量、贸易术语和目的港生成正式报价。回答单价或总价前必须调用，禁止用记忆编造价格。",
            {
                "sku": {"type": "string", "description": "产品 SKU，例如 LED-HB-150"},
                "qty": {"type": "integer", "description": "采购数量（件）"},
                "incoterm": {"type": "string", "description": "贸易术语：EXW、FOB、CFR、CIF"},
                "dest_port": {
                    "type": "string",
                    "description": "目的港，CFR/CIF 必填，例如 Los Angeles、Hamburg",
                },
            },
            ["sku", "qty", "incoterm"],
        ),
        _fn(
            "search_trade_terms",
            "检索付款、样品、交期、质保、Incoterms 等出口条款。回答贸易政策必须以本工具返回为准，并引用条款 ID。",
            {
                "query": {
                    "type": "string",
                    "description": "检索词，例如 T/T、L/C、sample、warranty、FOB Ningbo",
                }
            },
            ["query"],
        ),
        _fn(
            "get_shipment",
            "按出货单号或提单号查询当前买家的出运状态。问柜号、ETA、是否装船前必须调用。",
            {
                "ref": {
                    "type": "string",
                    "description": "出货单号或提单号，例如 SHP-9001、EGLV1234567890",
                }
            },
            ["ref"],
        ),
        _fn(
            "create_lead",
            "把询盘留给业务员跟进。无法当场报价、需要人工确认交期/认证、或买家留下公司与联系方式时使用。",
            {
                "company": {"type": "string", "description": "买家公司名，未知可留空"},
                "country": {"type": "string", "description": "买家国家或地区"},
                "need": {
                    "type": "string",
                    "description": "询盘要点：产品、数量、目的港、特殊要求",
                },
            },
            ["need"],
        ),
    ]


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def execute_tool(name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            return tool_error("BAD_ARGS", "参数必须是 JSON 对象")
    except json.JSONDecodeError as exc:
        return tool_error("BAD_ARGS", str(exc))

    if name == "search_catalog":
        return search_catalog(str(args.get("query") or ""))
    if name == "create_quotation":
        try:
            qty = int(args.get("qty"))
        except (TypeError, ValueError):
            return tool_error("BAD_ARGS", "qty 必须为正整数")
        return create_quotation(
            str(args.get("sku") or ""),
            qty,
            str(args.get("incoterm") or ""),
            str(args.get("dest_port") or ""),
        )
    if name == "search_trade_terms":
        return search_trade_terms(str(args.get("query") or ""))
    if name == "get_shipment":
        return get_shipment(str(args.get("ref") or ""))
    if name == "create_lead":
        return create_lead(
            str(args.get("company") or ""),
            str(args.get("country") or ""),
            str(args.get("need") or ""),
        )
    return tool_error("UNKNOWN_TOOL", "未注册的工具: " + name)


def search_catalog(query: str) -> str:
    q = query.strip().lower()
    if not q:
        return tool_error("BAD_ARGS", "query 不能为空")
    hits = [
        p.public_dict()
        for p in CATALOG
        if p.sku.lower() == query.strip().lower()
        or q in p.sku.lower()
        or q in p.name.lower()
        or q in p.category.lower()
        or contains_any(q, p.keywords)
    ]
    if not hits:
        return tool_error("PRODUCT_NOT_FOUND", "目录中没有匹配产品，禁止编造 SKU 或价格，请转人工")
    return tool_ok(hits)


def create_quotation(sku: str, qty: int, incoterm: str, dest_port: str) -> str:
    buyer_id = context.buyer_id.get("")
    sku = sku.strip().upper()
    incoterm = incoterm.strip().upper()
    dest_port = dest_port.strip()

    product = PRODUCTS_BY_SKU.get(sku)
    if product is None:
        return tool_error("PRODUCT_NOT_FOUND", "未知 SKU: " + sku)
    if qty <= 0:
        return tool_error("BAD_ARGS", "qty 必须为正整数")
    if qty < product.moq:
        return tool_error("BELOW_MOQ", f"数量低于 MOQ {product.moq} 件")
    if incoterm not in INCOTERM_FREIGHT_USD:
        return tool_error("BAD_INCOTERM", "仅支持 EXW、FOB、CFR、CIF")
    if incoterm in {"CFR", "CIF"} and not dest_port:
        return tool_error("DEST_REQUIRED", incoterm + " 必须提供 dest_port")

    discount = volume_discount(qty)
    freight = INCOTERM_FREIGHT_USD[incoterm]
    unit = round2(product.fob_ningbo_usd * (1 - discount) + freight)
    total = round2(unit * qty)
    now = datetime.now().astimezone()

    global _quote_seq
    with QUOTE_LOCK:
        _quote_seq += 1
        quote = {
            "id": f"QT-{_quote_seq:04d}",
            "buyer_id": buyer_id,
            "sku": product.sku,
            "product": product.name,
            "qty": qty,
            "incoterm": incoterm,
            "loading_port": "Ningbo, China",
            "currency": "USD",
            "unit_price": unit,
            "total": total,
            "discount_pct": discount * 100,
            "lead_days": product.lead_days,
            "valid_until": (now + timedelta(days=14)).strftime("%Y-%m-%d"),
            "payment_hint": "T/T 30% deposit, 70% before shipment. See POL-PAY-TT.",
            "notes": "Price excludes destination customs duties. Samples charged separately.",
            "created_at": now.isoformat(),
        }
        if dest_port:
            quote["dest_port"] = dest_port
        quotations[quote["id"]] = quote
        return tool_ok(quote)


def search_trade_terms(query: str) -> str:
    q = query.lower()
    hits = [
        asdict(doc)
        for doc in TRADE_TERMS
        if q in doc.title.lower() or q in doc.body.lower() or contains_any(q, doc.tags)
    ]
    if not hits:
        return tool_error("POLICY_NOT_FOUND", "知识库没有匹配条款，禁止编造，请转人工")
    return tool_ok(hits)


def get_shipment(ref: str) -> str:
    buyer_id = context.buyer_id.get("")
    key = ref.strip().upper()
    ship = SHIPMENTS.get(key)
    if ship is None:
        for item in SHIPMENTS.values():
            if item.bl_no.upper() == key or (item.container and item.container.upper() == key):
                ship = item
                break
    if ship is None:
        return tool_error("SHIPMENT_NOT_FOUND", "出货记录不存在: " + ref)
    if ship.buyer_id != buyer_id:
        return tool_error("FORBIDDEN", "无权查看该出货")
    return tool_ok(ship.public_dict())


def create_lead(company: str, country: str, need: str) -> str:
    buyer_id = context.buyer_id.get("")
    if not need.strip():
        return tool_error("BAD_ARGS", "need 不能为空")

    global _lead_seq
    with LEAD_LOCK:
        _lead_seq += 1
        lead = {
            "id": f"LD-{_lead_seq:04d}",
            "buyer_id": buyer_id,
            "need": need,
            "status": "new",
            "created_at": datetime.now().astimezone().isoformat(),
        }
        if company.strip():
            lead["company"] = company.strip()
        if country.strip():
            lead["country"] = country.strip()
        leads[lead["id"]] = lead
        return tool_ok(lead)


def list_quotes() -> list[dict[str, Any]]:
    with QUOTE_LOCK:
        return list(quotations.values())


def list_leads() -> list[dict[str, Any]]:
    with LEAD_LOCK:
        return list(leads.values())


def volume_discount(qty: int) -> float:
    if qty >= 2000:
        return 0.08
    if qty >= 1000:
        return 0.05
    if qty >= 500:
        return 0.03
    return 0.0


def round2(value: float) -> float:
    return round(value * 100) / 100


def tool_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def tool_error(code: str, message: str) -> str:
    return json.dumps({"ok": False, "code": code, "message": message}, ensure_ascii=False)


def contains_any(query: str, tags: list[str]) -> bool:
    return any(tag.lower() in query for tag in tags)


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    specs: str
    moq: int
    fob_ningbo_usd: float
    lead_days: int
    hs_code: str
    certs: list[str]
    keywords: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "specs": self.specs,
            "moq": self.moq,
            "fob_ningbo_usd": self.fob_ningbo_usd,
            "lead_days": self.lead_days,
            "hs_code": self.hs_code,
            "certs": list(self.certs),
        }


@dataclass(frozen=True)
class PolicyDoc:
    id: str
    title: str
    body: str
    tags: list[str]


@dataclass(frozen=True)
class Shipment:
    id: str
    buyer_id: str
    sku: str
    qty: int
    status: str
    bl_no: str
    container: str
    etd: str
    eta: str
    vessel: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


CATALOG = [
    Product(
        sku="LED-PNL-40",
        name="40W LED Panel Light 600x600",
        category="indoor lighting",
        specs="40W, 4000K, 3600lm, AC100-277V, recessed 600x600mm",
        moq=200,
        fob_ningbo_usd=8.50,
        lead_days=15,
        hs_code="940511",
        certs=["CE", "RoHS"],
        keywords=["panel", "panel light", "40w", "室内", "面板灯"],
    ),
    Product(
        sku="LED-HB-150",
        name="150W UFO LED High Bay",
        category="industrial lighting",
        specs="150W, 5000K, 21000lm, IP65, 120° beam, meanwell driver",
        moq=50,
        fob_ningbo_usd=28.00,
        lead_days=20,
        hs_code="940542",
        certs=["CE", "RoHS", "UL"],
        keywords=["high bay", "ufo", "150w", "工矿灯", "warehouse"],
    ),
    Product(
        sku="LED-ST-100",
        name="100W LED Street Light",
        category="outdoor lighting",
        specs="100W, 5700K, 13000lm, IP66, IK08, photocell optional",
        moq=100,
        fob_ningbo_usd=42.00,
        lead_days=25,
        hs_code="940542",
        certs=["CE", "IEC"],
        keywords=["street", "street light", "100w", "路灯", "outdoor"],
    ),
    Product(
        sku="LED-T8-18",
        name="18W T8 LED Tube 1.2m",
        category="indoor lighting",
        specs="18W, 4000K, 1800lm, G13, single-end input",
        moq=1000,
        fob_ningbo_usd=1.80,
        lead_days=12,
        hs_code="853952",
        certs=["CE", "RoHS"],
        keywords=["t8", "tube", "18w", "灯管"],
    ),
]
PRODUCTS_BY_SKU = {p.sku: p for p in CATALOG}

TRADE_TERMS = [
    PolicyDoc(
        id="POL-PAY-TT",
        title="T/T payment",
        body="Standard payment is T/T: 30% deposit to start production, 70% balance before shipment. Photos and packing list provided before the balance is due.",
        tags=["tt", "t/t", "payment", "deposit", "付款", "定金"],
    ),
    PolicyDoc(
        id="POL-PAY-LC",
        title="Letter of credit",
        body="Irrevocable L/C at sight is accepted for orders above USD 20,000. L/C must be issued by a first-class bank and allow partial shipment.",
        tags=["lc", "l/c", "letter of credit", "信用证"],
    ),
    PolicyDoc(
        id="POL-SAMPLE",
        title="Sample policy",
        body="Samples are charged at 1.5x unit FOB plus express freight. Sample fee is deducted from the first bulk order of 500 pcs or more of the same SKU. Lead time for samples is 5–7 days.",
        tags=["sample", "样品", "courier"],
    ),
    PolicyDoc(
        id="POL-LEADTIME",
        title="Production lead time",
        body="Production starts after deposit clearance. Standard lead time is 15–25 days depending on SKU. Peak season (Aug–Oct) may add 7 days. Exact days are on each catalog item.",
        tags=["lead time", "交期", "production", "delivery"],
    ),
    PolicyDoc(
        id="POL-INCOTERMS",
        title="Incoterms",
        body="Available terms: EXW Ningbo factory, FOB Ningbo, CFR destination port, CIF destination port. FOB is default. Destination duties and inland trucking are always for the buyer.",
        tags=["incoterm", "fob", "cif", "cfr", "exw", "ningbo", "贸易术语"],
    ),
    PolicyDoc(
        id="POL-WARRANTY",
        title="Warranty",
        body="LED fixtures: 2 years. Meanwell / branded drivers: 3 years. Warranty covers manufacturing defects, not lightning, water ingress from improper install, or voltage surge.",
        tags=["warranty", "质保", "guarantee"],
    ),
]

SHIPMENTS = {
    "SHP-9001": Shipment(
        id="SHP-9001",
        buyer_id="buyer_001",
        sku="LED-HB-150",
        qty=500,
        status="on_the_water",
        bl_no="EGLV142600111111",
        container="TCLU8899001",
        etd="2026-09-05",
        eta="2026-09-26",
        vessel="EVER GIVEN V.142W",
    ),
    "SHP-9002": Shipment(
        id="SHP-9002",
        buyer_id="buyer_002",
        sku="LED-ST-100",
        qty=200,
        status="booked",
        bl_no="COSU1234987654",
        container="",
        etd="2026-09-22",
        eta="2026-10-18",
        vessel="CMA CGM MARCO POLO",
    ),
}
