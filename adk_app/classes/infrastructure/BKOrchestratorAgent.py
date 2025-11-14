import re, os
from sys import _getframe
from pydantic.v1 import Field, BaseModel
from classes.infrastructure.PromptOrchestratorAgent import PromptOrchestratorAgent
from typing import List, Optional, Callable, Dict, Any, Sequence

class BKOrchestratorAgent(PromptOrchestratorAgent):
    upper_horizontal_threshold:int = 30
    max_potential_file_length:int = 250 # 200 code lines + 50 lines of comments

    def parse_static_report_sections(self, text: str) -> List:
        SECTION_TITLE_MAP = {
        }
        pattern = r"### ::(\w+)\n(.*?)(?=\n### ::|\Z)"  # greedy till next section or end
        matches = re.findall(pattern, text, re.DOTALL)
        StaticReportSection = self.generate_pydantic_model([
            ("id", str, Field("", description="Specification ID")),
            ("title", str, Field("", description="Section Title")),
            ("content", str, Field("", description="Content")),
        ])
        sections = []
        for section_id, content in matches:
            title = SECTION_TITLE_MAP.get(section_id, section_id)
            sections.append(StaticReportSection(
                id=section_id,
                title=title,
                content=content.strip()
            ))

        return sections

    def print_static_report_sections(self, sections: List) -> str:
        output = []
        for section in sections:
            output.append(f"##### **{section.title}\n{section.content}\n")
        return "\n\n".join(output)

    def extract_minitoken(self, input_string: str, target_key: str, is_paragraph: bool = False) -> str:
        """
        Extracts <value> from a string like:
        - **[<key>]**:<value>
        - If is_paragraph=False, it ends at \n or <br>
        - If is_paragraph=True, it captures until the next `- **[` or `### ::` or end of string
        """
        if is_paragraph:
            # match until next "- **[", or "### ::", or end of string
            pattern = rf'-\s+\*\*\[{re.escape(target_key)}\]\*\*:?\s*(.*?)(?=-\s+\*\*\[|### ::|$)'
        else:
            pattern = rf'-\s+\*\*\[{re.escape(target_key)}\]\*\*:(.*?)(?:\n|<br>)'

        match = re.search(pattern, input_string, flags=re.DOTALL)
        return match.group(1).strip() if match else ""

    def _batch(self, items: Sequence[Any], size: int) -> List[Sequence[Any]]:
        if size <= 0:
            return [list(items)]
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _should_continue(self, items, prev_len):
        if not items:
            return False
        curr = len(items)
        if curr < prev_len:
            return True

        caller = _getframe(1)  # caller frame
        file = caller.f_code.co_filename
        line = caller.f_lineno

        self.color_print(
            f"Cycle Break at {os.path.basename(file)}:{line} records={prev_len}",
            color="magenta"
        )
        return False
