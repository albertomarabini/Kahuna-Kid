from typing import AsyncGenerator
from typing_extensions import override
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from pydantic.v1 import BaseModel

from classes.infrastructure.PromptOrchestratorAgent import PromptOrchestratorAgent

class Agent(PromptOrchestratorAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # Minimal format suffix (includes the visual example)
        FORMAT_SUFFIX = (
            " Return only a Markdown table with one column 'Answer' and a single row "
            "containing the complete answer. Example:\n\n"
            "| Answer |\n|---------|\n| <the answer> |"
        )

        prompts = [
            "Give three bullet points on why structured output helps integration." + FORMAT_SUFFIX,
            "Summarize the difference between an agent and a tool in two sentences." + FORMAT_SUFFIX,
            "List five short test prompts for validating an LLM wrapper." + FORMAT_SUFFIX,
            "Describe a minimal retry policy for LLM calls in one paragraph." + FORMAT_SUFFIX,
        ]

        class AnswerTable(BaseModel):
            answer: str

        # Rephrased prompts with the format instructions appended
        single_result = await self.invoke(
            "Give three bullet points on why peace is good. (3 words each)." + FORMAT_SUFFIX, AnswerTable
        )

        results = await self.invoke_many(prompts, AnswerTable)

        ctx.session.state["single_result"] = single_result
        ctx.session.state["results"] = results

        final_text = "Done. Stored single_result and results in session state."
        content = types.Content(role="model", parts=[types.Part(text=final_text)])

        yield Event(
            author=self.name,
            content=content,
            partial=False,
            turn_complete=True
        )
