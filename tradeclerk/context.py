from contextvars import ContextVar

buyer_id: ContextVar[str] = ContextVar("buyer_id", default="")
