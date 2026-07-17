from contextvars import ContextVar

trace_id_context: ContextVar[str] = ContextVar("trace_id", default="unknown")


def get_trace_id() -> str:
    return trace_id_context.get()


def success(data: object) -> dict[str, object]:
    return {"data": data, "trace_id": get_trace_id()}
