from typing import AsyncGenerator
from typing_extensions import override
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types

from classes.infrastructure.PromptOrchestratorAgent import PromptOrchestratorAgent
from classes.test_agents.Agent import Agent


class ParentAgent(PromptOrchestratorAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        child_name, child_ctx, child_store = await self.invoke_one_agent(
            Agent,
            {"purpose": "demo"},
            name=f"{self.name}.child"
        )

        ctx.session.state["spawned_child_name"] = child_name
        ctx.session.state["spawned_child_store"] = child_store

        msg = f"Spawned child agent '{child_name}'. Stored its output in session state."
        content = types.Content(role="model", parts=[types.Part(text=msg)])

        yield Event(
            author=self.name,
            content=content,
            partial=False,
            turn_complete=True
        )
