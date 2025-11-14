# StructuredOutputChain.py
from __future__ import annotations

import asyncio, re
import time
import traceback
from typing import Any, List, Optional, Tuple, Type, Union

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.output_parsers.string import StrOutputParser
from langchain_core.runnables import chain
from langchain.output_parsers.fix import OutputFixingParser
from pydantic.v1 import BaseModel, Field, ValidationError, create_model


class Logger:
    def log(self, tag: str, message: str):
        print(f"[{tag}] {message}")


logger = Logger()


def create_structured_output_chain(
    llm,
    pydantic_output: Union[Type[BaseModel], Tuple] = None,
    completion_expected_end_token: Optional[Union[str, Tuple[str, ...]]] = None,
    returns_code: bool = False,
    max_continuations: int = 4,
    logger_fn = None
):
    """Build a chain that:
      - builds a prompt (optionally with Pydantic format instructions),
      - calls `llm`,
      - if the model stalls, nudges it with "Continue..." messages a few times,
      - post-parses/fixes with OutputFixingParser (Pydantic or string).
    """

    # ---------- helpers for pydantic shape ----------
    def reduce_class(cls: Type[BaseModel], fields: List[str]) -> Type[BaseModel]:
        field_defs = {}
        for field in fields:
            if field in cls.__annotations__:
                field_defs[field] = (
                    cls.__annotations__[field],
                    Field(description=cls.__fields__[field].field_info.description),
                )
            else:
                raise ValueError(f"Field {field} not found in {cls.__name__}")
        return create_model(cls.__name__, **field_defs, __config__=cls.__config__)

    def create_new_instance(output_cls: Type[BaseModel], reduced_instance: BaseModel = None) -> BaseModel:
        if reduced_instance is None:
            return output_cls()
        data = reduced_instance.dict()
        try:
            return output_cls(**data)
        except ValidationError as e:
            raise ValueError(f"Error creating instance: {e}") from e

    # ---------- default end tokens ----------
    if logger_fn:
        logger.log=logger_fn
    if returns_code and completion_expected_end_token is None:
        completion_expected_end_token = ("```", "}")

    output_fields: Optional[List[str]] = None
    output_str = False
    if pydantic_output:
        if isinstance(pydantic_output, tuple):
            # (model, fields?, output_str?)
            i1 = pydantic_output[1] if len(pydantic_output) > 1 else None
            i2 = pydantic_output[2] if len(pydantic_output) > 2 else None
            if i1 is not None:
                output_fields = i1 if isinstance(i1, list) else None
                output_str = (
                    i2 if isinstance(i2, bool) and i1 is not None and len(i1) > 0
                    else True if i1 is not None and len(i1) == 1
                    else False
                )
            pydantic_output = pydantic_output[0]

    if pydantic_output and not output_str:
        completion_expected_end_token = ("```", "}")
        output_vessel = pydantic_output if output_fields is None else reduce_class(pydantic_output, output_fields)
        output_parser = PydanticOutputParser(pydantic_object=output_vessel)
        output_instructions = output_parser.get_format_instructions()
    else:
        output_instructions = ""

    # ---------- content utilities ----------
    def _ensure_str(x: Any) -> str:
        """Best-effort: turn model output to a plain string."""
        if x is None:
            return ""
        if isinstance(x, AIMessage):
            x = x.content
        if isinstance(x, (list, tuple)):
            # try to extract a text-like leaf
            if len(x) == 0:
                return ""
            # common LC/SDK shapes sometimes put .text/.value in first part
            part0 = x[0]
            txt = getattr(part0, "text", None)
            if isinstance(txt, str):
                return txt
            val = getattr(getattr(part0, "text", None), "value", None)
            if isinstance(val, str):
                return val
            return str(part0)
        if not isinstance(x, str):
            return str(x)
        return x

    def is_complete(text: Any) -> bool:
        s = _ensure_str(text).strip()
        if not s:
            return False
        if completion_expected_end_token is None:
            # heuristic: sentence-ish ending or closers
            return s.endswith(('.', ';', '!', '?', "`", "}", "\n", "|", "-", "*", '"', "'", ">", "]", "”", "/", "】"))
        if isinstance(completion_expected_end_token, tuple):
            return any(s.endswith(tok) for tok in completion_expected_end_token)
        return s.endswith(completion_expected_end_token)

    def create_prompt(x: dict) -> str:
        question = x.get("question") or ""
        if pydantic_output and not output_str:
            header = "You must produce valid JSON that matches the schema below. Enclose the output in a single fenced ```json code block."
        elif output_str:
            header = "You must produce only the requested string value with no surrounding commentary or markdown."
        else:
            header = ""
        prompt = f"{question}\n{output_instructions}\n{header}".strip()
        if pydantic_output and not output_str:
            prompt = f"{prompt}\nOnly output:\n```json\n"  # nudge fence start
        return prompt

    # ---------- post-processing runnable ----------
    def create_completion_parser(llm, prompt: str, parser):
        def ensure_ai_message(completion) -> AIMessage:
            # Normalize whatever came back into an AIMessage with string content
            if isinstance(completion, AIMessage):
                return AIMessage(content=_ensure_str(completion.content))

            # Some chains hand back [AIMessage]; some SDKs do custom containers
            if isinstance(completion, list) and completion:
                last = completion[-1]
                if isinstance(last, AIMessage):
                    return AIMessage(content=_ensure_str(last.content))
                return AIMessage(content=_ensure_str(last))

            # Fallback
            return AIMessage(content=_ensure_str(completion))

        def convert_message(message: Any):
            if isinstance(message, AIMessage):
                return {"role": "assistant", "content": _ensure_str(message.content)}
            if isinstance(message, HumanMessage):
                return {"role": "user", "content": _ensure_str(message.content)}
            # Already dict?
            if isinstance(message, dict):
                return {"role": (message.get("role") or "user"), "content": _ensure_str(message.get("content", ""))}
            return {"role": "assistant", "content": _ensure_str(message)}

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=llm)

        @chain
        def completion_parser(completion: AIMessage):
            # Seed history with the original prompt + model’s first reply
            first = ensure_ai_message(completion)
            history = [
                {"role": "user", "content": prompt},
                convert_message(first),
            ]

            current_completion = _ensure_str(first.content).strip()
            # Guard against empty or pathological outputs
            compact = current_completion.replace(" ", "").replace("-", "")
            while True:
                m = re.search(r'-{101}', current_completion)
                if m:
                    i = m.start()
                    end = re.compile(r'[^-]').search(current_completion, i)
                    j = end.start() if end else len(current_completion)
                    current_completion = current_completion[:i] + ('-' * 100) + current_completion[j:]
                    continue
                m = re.search(r' {101}', current_completion)
                if m:
                    i = m.start()
                    end = re.compile(r'[^ ]').search(current_completion, i)
                    j = end.start() if end else len(current_completion)
                    current_completion = current_completion[:i] + (' ' * 100) + current_completion[j:]
                    continue
                break

            if len(compact) == 0 or (len(current_completion) // max(1, len(compact))) >= 9:
                ratio = len(current_completion) // max(1, len(compact))
                raise ValueError(f"The response is empty or seems repeated. Text/Compact Ratio: {ratio}")

            turns = 0
            while current_completion.lower() != "completed." and not is_complete(current_completion):
                if turns >= max_continuations:
                    raise ValueError("The LLM seems unable to complete its response within the continuation limit")

                # ask to continue without repeating
                history.append({
                    "role": "user",
                    "content": "Continue your previous response from the last word onward, without repeating. Do not restart or summarize. If you already finished, reply only with 'completed.'",
                })

                try:
                    next_chunk = llm.invoke(history)  # sync call backed by ADKLLM bg loop
                except Exception as e:
                    # Stop trying to continue; we’ll fix what we have
                    logger.log("ERROR", f"Continuation failed: {e}\n{traceback.format_exc()}")
                    break

                nxt = ensure_ai_message(next_chunk)
                current_completion = _ensure_str(nxt.content).strip()
                if current_completion.lower() != "completed.":
                    history.append(convert_message(nxt))
                else:
                    break
                turns += 1

            # Stitch all assistant turns together
            assembled = "".join(m["content"] for m in history if m["role"] == "assistant")
            fixed = fixing_parser.parse(assembled)
            return fixed.strip() if isinstance(fixed, str) else fixed

        return completion_parser

    # ---------- the main async chain ----------
    @chain
    async def chain_fn(x):
        async def ainvoke(runnable, prompt: Union[str, List[dict]], retries=3):
            last_exception = None
            timeout_threshold = 50
            # logger.log("PROMPT", prompt)
            for attempt in range(retries):
                start_time = time.time()
                try:
                    if isinstance(prompt, str):
                        system_instruction = (
                            "You are going to be submitted a prompt somehow involved in the development or architecturing of a software system.\n"
                            "There are 2 things you should pay attention to:\n"
                            "1) Biases:\n"
                            "- Follow the explicit bias-mitigation recommendations in the prompt if present.\n"
                            "2) Output format:\n"
                            "- Follow the requested output format rigorously.\n"
                            "- Do not include any extra comments besides the required output.\n"
                            "- Always return code enclosed within triple backticks if the output is code.\n"
                            "- Never terminate a response with an alphanumeric character.\n"
                            "- If a '.' is required to terminate the output, include it.\n"
                            "- Elements that are not part of the data must not be mixed with data."
                        )
                        messages = [
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": prompt},
                        ]
                        output = await runnable.ainvoke(messages)
                    else:
                        output = await runnable.ainvoke(prompt)
                    # logger.log("RESPONSE", _ensure_str(getattr(output, "content", output)))
                    return output
                except Exception as e:
                    elapsed = time.time() - start_time
                    last_exception = e
                    msg = f"Attempt {attempt+1} failed"
                    if elapsed > timeout_threshold:
                        logger.log("ERROR", f"{msg} after {elapsed:.2f}s. {e}\n{traceback.format_exc()}")
                    else:
                        logger.log("ERROR", f"{msg}. Cause: {e}\n{traceback.format_exc()}")
            logger.log("ERROR", f"All {retries} retry attempts failed.")
            raise RuntimeError(f"All {retries} retry attempts failed.") from last_exception

        # choose parser
        if pydantic_output:
            if output_str:
                if not output_fields:
                    raise ValueError("output_str=True requires at least one output field.")
                prompt = create_prompt(x)
                completion_parser = create_completion_parser(llm, prompt, StrOutputParser())
                composed = llm | completion_parser
                out = await ainvoke(composed, prompt)
                # return a full instance with the single field set
                inst = create_new_instance(pydantic_output)
                setattr(inst, output_fields[0], out)
                return inst
            else:
                prompt = create_prompt(x)
                completion_parser = create_completion_parser(llm, prompt, output_parser)
                composed = llm | completion_parser
                out = await ainvoke(composed, prompt)
                if output_vessel == pydantic_output:
                    return out
                return create_new_instance(pydantic_output, out)
        else:
            prompt = create_prompt(x)
            completion_parser = create_completion_parser(llm, prompt, StrOutputParser())
            composed = llm | completion_parser
            return await ainvoke(composed, prompt)

    return chain_fn
