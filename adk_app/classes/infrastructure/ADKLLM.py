# ADKLLM.py
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from contextlib import asynccontextmanager, AsyncExitStack, aclosing
from typing import Any, Dict, List, Optional, AsyncIterator

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService  # noqa: F401 (kept for external imports)
from google.genai import types
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda


class _Pipe:
    """Simple pipe to support `llm | other` and `other | llm`."""
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def invoke(self, x):
        y = self.left.invoke(x)
        return self.right.invoke(y)

    async def ainvoke(self, x):
        y = await self.left.ainvoke(x)
        return await self.right.ainvoke(y)


class _LoopThread:
    """Background event loop living on a dedicated thread for sync calls from async contexts."""
    def __init__(self, default_timeout: Optional[float] = None):
        self.default_timeout = default_timeout
        self.loop = asyncio.new_event_loop()
        self._stopped = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self._stopped.set()

    def run_sync(self, coro, timeout: Optional[float] = None):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        to = self.default_timeout if timeout is None else timeout
        try:
            return fut.result(timeout=to)
        except concurrent.futures.TimeoutError as e:
            fut.cancel()
            try:
                fut.result(timeout=1.0)
            except Exception:
                pass
            raise TimeoutError("Background event-loop call timed out") from e

    def close(self):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.t.join(timeout=5.0)
        if not self.loop.is_closed():
            # Nudge pending callbacks to finish tearing down
            self.loop.call_soon_threadsafe(lambda: None)
        try:
            self.loop.close()
        except Exception:
            pass


@asynccontextmanager
async def _runner_stream(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    content: types.Content,
) -> AsyncIterator[AsyncIterator]:
    """One-stop CM that ensures both Runner and its event stream are always closed."""
    async with AsyncExitStack() as stack:
        # Enter runner context if it supports async context mgmt
        try:
            await stack.enter_async_context(runner)  # type: ignore[misc]
        except Exception:
            # Fallback: best-effort close/aclose on exit
            async def _close_runner():
                close = getattr(runner, "aclose", None) or getattr(runner, "close", None)
                if close:
                    res = close()
                    if inspect.isawaitable(res):
                        await res
            stack.push_async_callback(_close_runner)

        agen = runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        )

        # Normalize coroutine → async-iterator
        if inspect.isawaitable(agen):
            agen = await agen  # type: ignore[assignment]

        # If aclose exists, wrap with aclosing for guaranteed finalization
        agen_has_aclose = hasattr(agen, "aclose")
        if agen_has_aclose:
            await stack.enter_async_context(aclosing(agen))  # type: ignore[arg-type]

        try:
            yield agen  # type: ignore[misc]
        finally:
            # If no aclose, try close() best-effort
            if not agen_has_aclose:
                maybe_close = getattr(agen, "close", None)
                if callable(maybe_close):
                    res = maybe_close()
                    if inspect.isawaitable(res):
                        try:
                            await res
                        except Exception:
                            pass

        # Defensive: try to close obvious session attrs if Runner didn't
        async def _safe_close_possible_sessions(r):
            for attr in ("client", "_client", "session", "_session"):
                obj = getattr(r, attr, None)
                if obj is None:
                    continue
                for meth in ("aclose", "close"):
                    fn = getattr(obj, meth, None)
                    if callable(fn):
                        res = fn()
                        if inspect.isawaitable(res):
                            try:
                                await res
                            except Exception:
                                pass
        try:
            await _safe_close_possible_sessions(runner)
        except Exception:
            pass


class ADKLLM(RunnableLambda):
    """LangChain Runnable that talks to a Google ADK Agent via Runner, with safe cleanup."""
    _shared_bg: Optional[_LoopThread] = None

    def __init__(
        self,
        agent,
        session_service,
        app_name: str,
        user_id: str,
        session_id: str,
        timeout_s: Optional[float] = None,
        use_shared_loop: bool = True,
        include_role_headers: bool = True,
        max_context_chars: Optional[int] = None,
    ):
        self.agent = agent
        self.session_service = session_service
        self.app_name = app_name
        self.user_id = user_id
        self.session_id = session_id
        self.timeout_s = timeout_s
        self.include_role_headers = include_role_headers
        self.max_context_chars = max_context_chars

        if use_shared_loop:
            if ADKLLM._shared_bg is None:
                ADKLLM._shared_bg = _LoopThread(default_timeout=timeout_s)
            self._bg = ADKLLM._shared_bg
            self._own_loop = False
        else:
            self._bg = _LoopThread(default_timeout=timeout_s)
            self._own_loop = True

    # ---------- lifecycle ----------
    @classmethod
    def close_shared(cls):
        if cls._shared_bg is not None:
            cls._shared_bg.close()
            cls._shared_bg = None

    def close(self):
        if self._own_loop and self._bg:
            self._bg.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------- composition ----------
    def __or__(self, other):
        return _Pipe(self, other)

    def __ror__(self, other):
        return _Pipe(other, self)

    # ---------- formatting ----------
    def _normalize_messages(self, messages_or_prompt: Any) -> List[Dict[str, str]]:
        if isinstance(messages_or_prompt, str):
            return [{"role": "user", "content": messages_or_prompt}]
        return list(messages_or_prompt or [])

    def _truncate(self, s: str) -> str:
        if self.max_context_chars is not None and len(s) > self.max_context_chars:
            return s[-self.max_context_chars :]
        return s

    def _rollup_as_single_user_turn(self, messages: List[Dict[str, str]]) -> str:
        sys_chunks: List[str] = []
        ctx_chunks: List[tuple[str, str]] = []
        last_user: Optional[str] = None

        for i, m in enumerate(messages):
            role = (m.get("role") or "user").lower()
            text = m.get("content", "")
            is_last = i == len(messages) - 1
            if is_last and role == "user":
                last_user = text
            else:
                if role == "system":
                    sys_chunks.append(text)
                elif role == "assistant":
                    ctx_chunks.append(("Assistant", text))
                elif role == "user":
                    ctx_chunks.append(("User", text))
                else:
                    ctx_chunks.append(("Context", text))

        parts: List[str] = []
        if self.include_role_headers:
            if sys_chunks:
                parts.append("System:\n" + "\n\n".join(sys_chunks))
            if ctx_chunks:
                blocks = [f"{tag}:\n{txt}" for tag, txt in ctx_chunks]
                parts.append("Context:\n" + "\n\n".join(blocks))
            if last_user is None:
                parts.append("User:\nPlease continue from where you left off.")
            else:
                parts.append("User:\n" + last_user)
        else:
            if sys_chunks:
                parts.append("\n\n".join(sys_chunks))
            if ctx_chunks:
                parts.append("\n\n".join(txt for _, txt in ctx_chunks))
            parts.append(last_user or "Please continue from where you left off.")

        stitched = "\n\n".join(parts).strip()
        return self._truncate(stitched)

    # ---------- core IO ----------
    async def _run_once(self, message_text: str) -> str:
        final_text = ""

        async def _do():
            nonlocal final_text
            content = types.Content(role="user", parts=[types.Part(text=message_text)])

            runner = Runner(agent=self.agent, app_name=self.app_name, session_service=self.session_service)

            async with _runner_stream(
                runner,
                user_id=self.user_id,
                session_id=self.session_id,
                content=content,
            ) as agen:
                async for ev in agen:
                    if ev.is_final_response() and ev.content and ev.content.parts:
                        part = ev.content.parts[0]
                        txt = getattr(part, "text", None)
                        if isinstance(txt, str):
                            final_text = txt

        if self.timeout_s:
            await asyncio.wait_for(_do(), timeout=self.timeout_s)
        else:
            await _do()
        return final_text

    async def _ainvoke_impl(self, messages_or_prompt: Any) -> AIMessage:
        messages = self._normalize_messages(messages_or_prompt)
        rolled = self._rollup_as_single_user_turn(messages)
        text = await self._run_once(rolled)
        return AIMessage(content=text or "")

    # Public API
    async def ainvoke(self, messages_or_prompt: Any) -> AIMessage:
        return await self._ainvoke_impl(messages_or_prompt)

    def invoke(self, messages_or_prompt: Any) -> AIMessage:
        """Sync facade:
        - If already inside an event loop, submit to the background loop.
        - Otherwise, start a temporary loop with asyncio.run.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop: safe to use asyncio.run
            return asyncio.run(self._ainvoke_impl(messages_or_prompt))
        else:
            # Running loop present: offload to background loop
            return self._bg.run_sync(
                self._ainvoke_impl(messages_or_prompt),
                timeout=self.timeout_s if self.timeout_s is not None else None,
            )

import atexit

def _close_shared_on_exit():
    try:
        ADKLLM.close_shared()
    except Exception:
        pass

atexit.register(_close_shared_on_exit)
