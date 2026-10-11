"""
Optional Langfuse tracing.

Everything here is a no-op unless LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and
LANGFUSE_BASE_URL are all set, so the code runs unchanged (and makes no
network calls) without them. BASE_URL is required, not defaulted, because the
SDK would otherwise send to the EU region and silently fail auth for a project
created in another region.
"""

import os
from contextlib import contextmanager, nullcontext

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")


class _NullObservation:
    def update(self, **_) -> None:
        pass


def enabled() -> bool:
    return all(os.environ.get(name) for name in _REQUIRED_ENV)


def callbacks() -> list:
    """LangChain/LangGraph callbacks for config={"callbacks": ...}."""
    if not enabled():
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


@contextmanager
def trace(name: str, tags: list = None, input=None):
    """Root span for one run. Graph spans and gateway spans created inside it share one trace."""
    if not enabled():
        yield _NullObservation()
        return

    from langfuse import get_client, propagate_attributes

    with get_client().start_as_current_observation(name=name, as_type="span", input=input) as root:
        with propagate_attributes(trace_name=name, tags=tags or []):
            yield root


def observation(name: str, as_type: str = "span", **kwargs):
    """Child observation under the current span; yields an object with .update()."""
    if not enabled():
        return nullcontext(_NullObservation())

    from langfuse import get_client

    return get_client().start_as_current_observation(name=name, as_type=as_type, **kwargs)


def flush() -> None:
    if enabled():
        from langfuse import get_client

        get_client().flush()
