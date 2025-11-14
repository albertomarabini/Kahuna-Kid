import asyncio
from typing import AsyncGenerator, List
from typing_extensions import override
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from pydantic import Field

from classes.infrastructure.BKOrchestratorAgent import BKOrchestratorAgent
from classes.models.models import TopologyReportItem, SliceModel

from classes.prompts.GLOBAL_PROMPTS import GLOBAL_PROMPTS


class Step1Agent(BKOrchestratorAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        self.set_status(0, 2, "Step 1 🗿 please wait.")
        problem_statement = self.fixed_inputs.get("problem_statement")
        if problem_statement is None:
            ctx.session.state.setdefault("errors", {}).setdefault("missing_inputs", {})[self.name] = ["problem_statement"]
            raise ValueError("Missing required input")

        final_text = "Step 1 Completed"
        content = types.Content(role="model", parts=[types.Part(text=final_text)])
        yield Event(author=self.name, content=content, partial=False, turn_complete=True)
