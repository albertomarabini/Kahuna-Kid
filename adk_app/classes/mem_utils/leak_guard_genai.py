# classes/leak_guard_genai.py
from __future__ import annotations

import asyncio
import inspect
import threading
import weakref

_installed = False


def _run_coro_in_fresh_loop(coro):
    """Run a coroutine to completion on a fresh loop in a helper thread."""
    result_holder = {"exc": None}
    done = threading.Event()

    def _runner():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro)
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
        except Exception as e:
            result_holder["exc"] = e
        finally:
            done.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    done.wait(timeout=5.0)  # best-effort
    # don't re-raise at shutdown; this is a finalizer
    return


async def _aclose_api_client(api_client):
    """Best-effort async closer for google.genai._api_client.ApiClient."""
    # 1) Try public close/ aclose first
    close = getattr(api_client, "aclose", None) or getattr(api_client, "close", None)
    if callable(close):
        try:
            res = close()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass

    # 2) Force-close internal aiohttp session if still present
    sess = getattr(api_client, "_aiohttp_session", None)
    if sess is not None:
        try:
            res = sess.close()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass


def _finalize_api_client(api_client):
    """Sync finalizer that ensures the internal aiohttp session is closed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: safe to asyncio.run
        try:
            asyncio.run(_aclose_api_client(api_client))
        except Exception:
            pass
    else:
        # Already in a loop; run on a helper thread/loop
        try:
            _run_coro_in_fresh_loop(_aclose_api_client(api_client))
        except Exception:
            pass


def install():
    """Idempotent monkey-patch that attaches a finalizer to every ApiClient."""
    global _installed
    if _installed:
        return
    try:
        from google.genai import _api_client as gapi
    except Exception:
        # If google.genai isn't importable yet, caller can retry later.
        return

    ApiClient = getattr(gapi, "ApiClient", None)
    if ApiClient is None:
        return

    original_init = ApiClient.__init__

    def patched_init(self, *a, **kw):
        original_init(self, *a, **kw)
        # Ensure we always close the underlying aiohttp session at GC/exit
        weakref.finalize(self, _finalize_api_client, self)

    ApiClient.__init__ = patched_init
    _installed = True
