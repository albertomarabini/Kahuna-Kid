import io, re, os, json, time, uuid
import zipfile
from datetime import datetime
import argparse
import asyncio
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
import inspect

from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.adk.agents.invocation_context import InvocationContext

from classes.mem_utils import leak_guard_genai as lg
lg.install()

from classes.models.models import BoundComponentDefinition_w_Helper, ComponentMethod, FileDefinition
from classes.pipeline.logging_functions_factory import LoggingFunctionsFactory

from classes.infrastructure.PromptOrchestratorAgent import PromptOrchestratorAgent
from classes.infrastructure.PromptOrchestratorSidekick import PromptOrchestratorSidekick
from classes.bk_agents.step_1 import Step_1
from classes.bk_agents.step_2 import Step_2

APP_NAME = "adk-prod-pipeline"

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# Single shared InvocationContext for the whole pipeline
SHARED_CTX: Optional[InvocationContext] = None
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

def _on_job_internal_status(code: int, max_code: int, message: str):
    pct = 0 if max_code == 0 else int(100 * min(code, max_code) / max_code)
    print(f"[STATUS] {pct}% {message}")

@dataclass
class StepResult:
    name: str
    success: bool
    attempts: int
    error: Optional[str] = None
    duration_s: float = 0.0

@dataclass
class PipelineSummary:
    steps: List[StepResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"steps": [asdict(s) for s in self.steps]}

    def print_table(self):
        str_report = "\n"
        str_report += "+-------------------------------+----------+----------+---------------------------+\n"
        str_report += "| Step                          | Success  | Attempts | Duration                  |\n"
        str_report += "+-------------------------------+----------+----------+---------------------------+\n"
        for s in self.steps:
            ok = "yes" if s.success else "no"
            dur = f"{s.duration_s:.2f}s"
            str_report += f"| {s.name:<29} | {ok:<8} | {s.attempts:^8} | {dur:<25} |\n"
        str_report += "+-------------------------------+----------+----------+---------------------------+\n"
        str_report += "\n"
        return str_report

pipeline = None


def build_and_save_final_zip(state: Dict[str, Any], logger) -> str:
    sk = PromptOrchestratorSidekick()
    file_definitions = sk.load_pv1_model_list(FileDefinition,state.get("file_definitions", ""))
    components_definitions = sk.load_pv1_model_list(
        BoundComponentDefinition_w_Helper, state.get("components_definitions", "")
    )
    components_API = sk.load_pv1_model_list(ComponentMethod, state.get("components_API", ""))
    static_report = state.get("static_report", "")
    main_components=[c for c in components_definitions if c.helper_of.strip().lower() == "none"]

    readme = (
        "\n\n# Architectural Plan:\n" + sk.serialize_pydantic_objects_to_table(main_components, ["requirement_IDs", "bindings", "technological_leverage", "lifecycle_management", "interfaces", "helper_of"]) +
        "\n\n### Component Methods definitions:\n" + sk.serialize_pydantic_objects_to_table(components_API, ["original_IDs", "method_ID"]) +
        "\n\n# File Definitions:\n" + sk.serialize_pydantic_objects_to_table(file_definitions, ["content", "dependencies", "language_structural_elements", "file_ID"]) +
        "\n\n# Static Report:\n" + static_report +
        "\n\n# Execution Report:\n" + pipeline.print_table()
    )

    # Path where the final zip will be saved
    output_zip_path = f"./results/result_files_{datetime.now().strftime('%d-%m-%y-%H_%M_%S')}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        for definition in file_definitions:
            zipf.writestr(str(definition.file_name).lstrip("/\\").replace("\\", "/"), sk.clean_triple_backticks(definition.content).encode("utf-8"))
        zipf.writestr("README.md", readme.encode("utf-8"))
    buf.seek(0)
    data = buf.getvalue()

    os.makedirs(os.path.dirname(output_zip_path), exist_ok=True)
    with open(output_zip_path, "wb") as f:
        f.write(data)
    return data


async def _run_agent_step(
    agent_cls: Type[PromptOrchestratorAgent],
    step_name: str,
    model_name: str,
    session_service: InMemorySessionService,
    run_id: str,
    fixed_inputs: Dict[str, Any],
    logger: Any,
    status_fn: Any,
    max_retries: int,
) -> StepResult:
    global SHARED_CTX
    global pipeline

    attempt = 0
    start_t = time.time()
    attempt_errors: List[Dict[str, Any]] = []

    while attempt < max_retries:
        attempt += 1
        try:
            agent = agent_cls(
                model_name=model_name,
                fixed_inputs=fixed_inputs,
                status_notifier=status_fn,
                logger=logger,
                session_service=session_service,
                user_id=run_id,
                app_name=APP_NAME,
                name=f"{step_name}.{attempt:02d}.{uuid.uuid4().hex[:6]}",
            )

            if SHARED_CTX is None:
                session = await session_service.get_session(app_name=APP_NAME, user_id=run_id, session_id=run_id)
                SHARED_CTX = InvocationContext(
                    session_service=session_service,
                    session=session,
                    agent=agent,
                    invocation_id=uuid.uuid4().hex,
                )
            else:
                SHARED_CTX.agent = agent
                SHARED_CTX.invocation_id = uuid.uuid4().hex

            final_text = None
            gen = agent._run_async_impl(SHARED_CTX)
            if inspect.isasyncgen(gen):
                async for event in gen:
                    is_final = False
                    if hasattr(event, "is_final_response") and callable(getattr(event, "is_final_response")):
                        try:
                            is_final = event.is_final_response()
                        except Exception:
                            is_final = False
                    if not is_final:
                        is_final = bool(getattr(event, "turn_complete", False)) and not bool(getattr(event, "partial", False))
                    if is_final and getattr(event, "content", None) and getattr(event.content, "parts", None):
                        part = event.content.parts[0]
                        if getattr(part, "text", None):
                            final_text = part.text
            else:
                await gen

            logger("STEP_FINAL_TEXT", {"step": step_name, "attempt": attempt, "text": final_text or ""})
            logger("SESSION_KEYS", {"step": step_name, "keys": list(SHARED_CTX.session.state.keys())})

            return StepResult(name=step_name, success=True, attempts=attempt, duration_s=time.time() - start_t)

        except Exception as e:
            tb = traceback.format_exc()
            err_payload: Dict[str, Any] = {
                "step": step_name,
                "attempt": attempt,
                "type": type(e).__name__,
                "message": str(e),
                "traceback": tb,
            }
            # snapshot any child/agent errors the step may have written to state
            try:
                if SHARED_CTX and SHARED_CTX.session and isinstance(SHARED_CTX.session.state, dict):
                    sess_errs = SHARED_CTX.session.state.get("errors")
                    if sess_errs:
                        err_payload["session_errors_snapshot"] = sess_errs
            except Exception:
                pass

            attempt_errors.append(err_payload)
            logger("STEP_ERROR_DETAIL", err_payload)
            status_fn(0, 0, f"{step_name} {'Failed.' if attempt >= max_retries else f'Attempting Failover {attempt + 1}'}")
            await asyncio.sleep(0.25)

    # persist full error history for this step in session state
    try:
        if SHARED_CTX and SHARED_CTX.session and isinstance(SHARED_CTX.session.state, dict):
            SHARED_CTX.session.state.setdefault("errors", {})
            SHARED_CTX.session.state["errors"].setdefault("steps", {})
            SHARED_CTX.session.state["errors"]["steps"][step_name] = attempt_errors
    except Exception:
        # best-effort; don't mask original failure
        pass

    # concise error for the table, with pointer to the full tracebacks
    last = attempt_errors[-1] if attempt_errors else {}
    last_msg = f"{last.get('type','Error')}: {last.get('message','')}"
    summary = (
        f"Failed after {attempt} attempt(s). Last error: {last_msg}. "
        f"See session.state['errors']['steps']['{step_name}'] for full tracebacks."
    )
    return StepResult(name=step_name, success=False, attempts=attempt, error=summary, duration_s=time.time() - start_t)

async def main_async(
    problem_statement: str,
    model: str,
    run_id: str,
    status_event_sink: Callable[[int, int, str], Any],
    max_retries: int = 3,
) -> Dict[str, Any]:
    global SHARED_CTX
    global pipeline

    svc = InMemorySessionService()
    try:
        await svc.create_session(app_name=APP_NAME, user_id=run_id, session_id=run_id)
    except Exception:
        pass

    lf = LoggingFunctionsFactory()
    on_status = lf.make_status_fn(status_event_sink)
    logger = lf.make_logger_fn("./logs", run_id)

    logger("START", f"Starting job:{run_id} model{model}")

    pipeline = PipelineSummary()
    fixed_inputs_common = {"problem_statement": problem_statement}

    _common = dict(
        model_name=model,
        session_service=svc,
        run_id = run_id,
        fixed_inputs=fixed_inputs_common,
        logger=logger,
        status_fn=on_status,
        max_retries=max_retries,
    )

    _steps = [
        (Step_1, "Step 1"),
        (Step_2, "Step 2"),
    ]

    for agent_cls, step_name in _steps:
        step = await _run_agent_step(agent_cls=agent_cls, step_name=step_name, **_common)
        pipeline.steps.append(step)
        if not step.success:
            SHARED_CTX = None
            return {"status":"failure"}


    state = dict(SHARED_CTX.session.state) if SHARED_CTX else {}
    # file_definitions = state.get("file_definitions")
    zip_bytes = build_and_save_final_zip(state , logger)

    # Cleanup shared ctx for next run
    SHARED_CTX = None

    return {
        "status":"success",
        "run_id": run_id,
        "zip_bytes": zip_bytes,
    }

def main(
    problem_statement: str,
    model: str,
    run_id: str,
    status_event_sink: Callable[[int, int, str], Any],
    max_retries: int = 3,
) -> Dict[str, Any]:
    return asyncio.run(
        main_async(
            problem_statement=problem_statement,
            model=model,
            run_id = run_id,
            status_event_sink = status_event_sink,
            max_retries=max_retries,
        )
    )
