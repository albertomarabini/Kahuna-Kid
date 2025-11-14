# PromptOrchestratorAgent

import json
import traceback
from typing import Dict, List, MutableMapping, Optional, AsyncGenerator, Callable, Any, Sequence, Tuple, Type
import uuid
from typing_extensions import override
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService, BaseSessionService
import re
import asyncio
import inspect
import collections.abc as cabc

from classes.infrastructure.ADKLLM import ADKLLM
from classes.infrastructure.StructuredOutputChain import create_structured_output_chain
from classes.infrastructure.PromptOrchestratorSidekick import PromptOrchestratorSidekick

class PromptOrchestratorAgent(BaseAgent, PromptOrchestratorSidekick):
    text_agent: LlmAgent
    fixed_inputs: Dict[str, Any]
    timeout_s: int
    app_name: str
    user_id: str
    model_name: str
    status_notifier: Optional[Callable[[int, int, str], None]]
    logger: Any
    session_service: BaseSessionService
    model_config = {"arbitrary_types_allowed": True}
    concurrency : int

    def __init__(
        self,
        model_name: str,
        fixed_inputs: Optional[Dict[str, Any]] = None,
        status_notifier: Optional[Callable[[int, int, str], None]] = None,
        logger: Any = None,
        session_service: Optional[BaseSessionService] = None,
        timeout_s: int = 120,
        user_id: str = None,
        app_name: str = None,
        name: str = None,
        concurrency: int = 0
    ):
        if name is None:
            name = uuid.uuid4().hex[:8]
        base_name = self._sanitize_name(name)
        tkn = uuid.uuid4().hex[:8]
        text_agent = self._build_llm_agent(f"{base_name}_text_only", model_name)

        # default logger compatible with logger(tag, payload)
        if logger is None:
            def logger(tag: str, payload: dict):
                print(f"[{tag}] {payload}")

        super().__init__(
            model_name=model_name,
            name=base_name,
            text_agent=text_agent,
            sub_agents=[text_agent],
            status_notifier=status_notifier,
            logger=logger,
            fixed_inputs=fixed_inputs or {},
            session_service=session_service or InMemorySessionService(),
            timeout_s=timeout_s,
            user_id=user_id or f"user.{base_name}.{tkn}",
            app_name=app_name or f"app.{base_name}.{tkn}",
            concurrency=concurrency
        )

    def _sanitize_name(self, name: str) -> str:
        x = re.sub(r"\W+", "_", name)
        if not x or not re.match(r"[A-Za-z_]", x[0]):
            x = f"a_{x or 'agent'}"
        return x

    def _build_llm_agent(self, agent_name: str, model_name:str) -> LlmAgent:
        return LlmAgent(
            name=self._sanitize_name(agent_name),
            model= model_name,
            include_contents="none",
        )

    def set_status(self, code: int = None, max_code: int = None, message: str = None) -> None:
        if self.status_notifier:
            self.status_notifier(code, max_code, message)

    async def _fresh_local_ctx(self) -> InvocationContext:
        local_session_id = f"{self.name}.local.{uuid.uuid4().hex[:8]}"
        session = await self.session_service.create_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=local_session_id
        )
        return InvocationContext(session_service=self.session_service, session=session, agent=self, invocation_id=uuid.uuid4().hex)

    async def _build_llm_and_chain_for_ctx(self) -> Tuple[Any, Any]:
        ctx = await self._fresh_local_ctx()
        llm = ADKLLM(
            agent=self.text_agent,
            session_service=self.session_service,
            app_name=ctx.app_name,
            user_id=self.user_id,
            session_id=ctx.session.id,
            timeout_s=self.timeout_s,
        )
        chain = create_structured_output_chain(llm, logger_fn=self.logger)
        return llm, chain

    async def _invoke_once(self, prompt_text: str, chain: Any) -> str:
        result = await chain.ainvoke({"question": prompt_text})
        if hasattr(result, "content"):
            return getattr(result, "content", "") or ""
        if isinstance(result, str):
            return result
        return str(result)

    async def invoke(
        self,
        prompt: str,
        model: Optional[Type[Any]] = None,
        parser: Optional[Callable[[str], Any]] = None,
    ) -> Any:
        llm, chain = await self._build_llm_and_chain_for_ctx()
        try:
            self.logger("PROMPT", prompt)
            final_text = await self._invoke_once(prompt, chain)
            self.logger("RESPONSE", final_text)

            if parser is not None:
                return parser(final_text)
            if model is not None:
                # Provided by PromptOrchestratorSidekick
                return self.obnoxious_text_to_pydantic_list(final_text, model, backup_LLM=llm)
            return final_text
        finally:
            # Always clean up per-call resources
            try:
                if hasattr(llm, "close"):
                    llm.close()
            except Exception:
                pass
            # Do NOT call ADKLLM.close_shared() here; do it at process shutdown.

    async def invoke_many(
        self,
        prompts: Sequence[str],
        model: Optional[Type[Any]] = None,
        parser: Optional[Callable[[str], Any]] = None,
        concurrency: Optional[int] = 0,
    ) -> List[Any]:
        if concurrency is None:
            concurrency = self.concurrency
        sem = asyncio.Semaphore(concurrency) if concurrency and concurrency > 0 else None

        async def run_one(idx: int, prompt: str):
            try:
                if sem:
                    async with sem:
                        return idx, await self.invoke(prompt, model, parser)
                return idx, await self.invoke(prompt, model, parser)
            except Exception as e:
                return idx, e

        tasks = [run_one(i, p) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        results.sort(key=lambda x: x[0])

        # propagate exceptions in order
        out: List[Any] = []
        first_exc: Optional[BaseException] = None
        for _, r in results:
            if isinstance(r, Exception) and first_exc is None:
                first_exc = r
            out.append(r)
        if first_exc:
            # raise the first, but still return gathered order if you prefer
            raise first_exc
        return out

    async def _invoke_one_indexed(
        self,
        idx: int,
        prompt: str,
        model: Optional[Type[Any]],
        parser: Optional[Callable[[str], Any]],
    ):
        ret = await self.invoke(prompt, model, parser)
        return idx, ret

    async def _spawn_child_agent(
        self,
        agent_cls: Type["PromptOrchestratorAgent"],
        param: Any,
        name: Optional[str] = None,
    ) -> Tuple["PromptOrchestratorAgent", InvocationContext]:
        child_name = name or uuid.uuid4().hex[:8]
        ctx = await self._fresh_local_ctx()
        base_fixed = {"param": param}

        child = agent_cls(
            model_name=self.model_name,
            fixed_inputs=base_fixed,
            status_notifier=self.status_notifier,
            logger=self.logger,
            session_service=self.session_service,
            name=child_name,
            user_id=self.user_id,
            app_name=self.app_name,
            timeout_s=self.timeout_s,
        )
        return child, ctx

    async def invoke_one_agent(
        self,
        agent_cls: Type["PromptOrchestratorAgent"],
        param: Any,
        name: Optional[str] = None,
    ) -> Tuple[str, InvocationContext]:
        child, ctx = await self._spawn_child_agent(agent_cls, param, name=name)
        runner = child._run_async_impl(ctx)
        try:
            if inspect.isasyncgen(runner) or isinstance(runner, cabc.AsyncIterator):
                async for _ in runner:
                    pass
            else:
                await runner
            return child.name, ctx
        except Exception as e:
            tb_full = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            tail_lines = tb_full.strip().splitlines()[-12:]
            tb_tail = "\n".join(tail_lines)
            try:
                param_preview = json.dumps(param, ensure_ascii=False, default=str)
            except Exception:
                param_preview = str(param)
            if len(param_preview) > 800:
                param_preview = param_preview[:800] + "…"

            try:
                ctx.session.state.setdefault("errors", {})
                ctx.session.state["errors"][child.name] = {
                    "agent": child.__class__.__name__,
                    "message": str(e),
                    "type": f"{type(e).__module__}.{type(e).__name__}",
                    "traceback": tb_full,
                    "param_preview": param_preview,
                }
            except Exception:
                pass

            self.logger("CHILD_EXCEPTION", f"name: {child.name}, agent: {child.__class__.__name__}, type: {type(e).__module__}.{type(e).__name__}, message: {str(e)}, traceback_tail: {tb_tail}")

            raise RuntimeError(
                f"{child.__class__.__name__} '{child.name}' failed: {type(e).__name__}: {e}\n"
                f"Traceback (tail):\n{tb_tail}"
            ) from e

    async def invoke_many_agent(
        self,
        agent_cls: Type["PromptOrchestratorAgent"],
        params: Sequence[Any],
        concurrency: Optional[int] = None,
    ) -> Tuple[Dict[str, InvocationContext], List[Dict[str, Any]]]:
        if concurrency is None:
            concurrency = self.concurrency
        sem = asyncio.Semaphore(concurrency) if concurrency and concurrency > 0 else None

        failures: List[Dict[str, Any]] = []

        async def run_one(idx: int, p: Any) -> Optional[Tuple[str, InvocationContext]]:
            name = f"{self.name}.child.{idx:06d}"
            try:
                payload = {"params": p, "idx": idx}
                if sem:
                    async with sem:
                        return await self.invoke_one_agent(agent_cls, payload, name=name)
                return await self.invoke_one_agent(agent_cls, payload, name=name)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                self.logger("CHILD_ERROR", f"name: {name}, idx: {idx}, error: {err}")
                failures.append({"name": name, "idx": idx, "error": err, "param": p})
                return None

        results = await asyncio.gather(*[run_one(i, p) for i, p in enumerate(params)], return_exceptions=False)
        merged: Dict[str, InvocationContext] = {}
        for r in results:
            if r and isinstance(r, tuple):
                name, child_ctx = r
                merged[name] = child_ctx
        return merged, failures

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        raise NotImplementedError("Subclass must implement the pipeline")
