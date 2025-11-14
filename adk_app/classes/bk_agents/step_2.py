from typing import AsyncGenerator, List, Dict, Any, Sequence
from typing_extensions import override
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from pydantic import Field

from classes.infrastructure.BKOrchestratorAgent import BKOrchestratorAgent
from classes.prompts.GLOBAL_PROMPTS import GLOBAL_PROMPTS
from classes.models.models import topologies_interaction_phase_questions


class Step2ParallelSubAgent(BKOrchestratorAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        fixed_inputs = self.fixed_inputs['param']
        problem_statement = fixed_inputs.get('params').get("problem_statement")


class Step2Orchestrator(BKOrchestratorAgent):
    def extract_data(self, non_functional_breakdown, no_custom_implementation_breakdown):
        data = ""
        return data

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:

        params = [{"problem_statement": self.fixed_inputs.get("problem_statement")} for fs in ffs]
        children, child_errors = await self.invoke_many_agent(Step2ParallelSubAgent, params, concurrency=self.concurrency)

        if child_errors:
            # persist full errors
            ctx.session.state.setdefault("errors", {})
            ctx.session.state["errors"]["step_2"] = child_errors
            ctx.session.state["errors"]["step_2_count"] = len(child_errors)
            self.logger("SLICE_ERRORS", {"count": len(child_errors)})

            # surface a readable final message (optional)
            lines = "\n".join(
                [f"- {e.get('name','<unknown>')}: {e.get('error','<no message>')}" for e in child_errors]
            )
            msg = (
                f"Aborting  step. Failed children: {len(child_errors)}\n{lines}\n"
                "See ctx.session.state['errors']['step_2'] for details."
            )
            content = types.Content(role="model", parts=[types.Part(text=msg)])
            # send a final event so you still see a nice message in logs/UI
            yield Event(author=self.name, content=content, partial=False, turn_complete=True)

            # CRITICAL: fail the step so the governor reports failure and stops the pipeline
            raise RuntimeError(
                f"{self.__class__.__name__}: {len(child_errors)} child agent(s) failed; aborting."
            )


        for _, child_ctx in children.items():
            pass


        content = types.Content(role="model", parts=[types.Part(text="Aggregated child slice outputs and saved final state.")])
        yield Event(author=self.name, content=content, partial=False, turn_complete=True)
