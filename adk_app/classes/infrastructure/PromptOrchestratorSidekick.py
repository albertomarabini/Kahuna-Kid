import json
import re
from typing import Any, Iterable, List, Optional, Sequence, Type, Union, TypeVar
import uuid
from pydantic.v1 import BaseModel, create_model, Field
from pydantic.v1.json import pydantic_encoder
import commentjson
from itertools import zip_longest
from classes.infrastructure.StructuredOutputChain import create_structured_output_chain

T = TypeVar("T", bound=BaseModel)

import asyncio, threading

_bg_loop = None
_bg_thread = None

def _ensure_bg_loop():
    global _bg_loop, _bg_thread
    if _bg_loop is None:
        _bg_loop = asyncio.new_event_loop()
        _bg_thread = threading.Thread(target=_bg_loop.run_forever, daemon=True)
        _bg_thread.start()
    return _bg_loop

def run_coro_sync(coroutine):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    else:
        bg = _ensure_bg_loop()
        return asyncio.run_coroutine_threadsafe(coroutine, bg).result()


class PromptOrchestratorSidekick:
    def obnoxious_text_to_pydantic_list(self, data:str ,  model: Type[BaseModel], column_id= 0, output_format = None, backup_LLM = None):
        main_prompt = """
Convert the following markdown table into a JSON object that conforms to the provided schema.
Each row represents one item. The columns in each row should be **mapped to the object fields in the schema** based on **their left-to-right order of appearance**.

If there are **fewer columns than fields in the schema**, fill in the fields in order and use empty strings (`""`) for any remaining fields.
If there are **more columns than fields**, ignore the extra columns.

Example:
Given a schema with 3 fields: `field_1`, `field_2`, and `field_3`, and a row like this:

```
| value1 | value2 |
```

The resulting object should be:

```json
{ "field_1": "value1", "field_2": "value2", "field_3": "" }
```

Always include any  fields required by the schema, even if they are not present in the data (use empty strings).

⚠️ Pay attention to elements in the data that might be just part of the formatting and not the data content (eg: often queries might include some tags like "End of report." to indicate the end of the report, but these are not part of the data and should not be included in the JSON output).
⚠️ Do not alter or summarize every other element in the table that appears as data table content. Output only the resulting JSON object.

**The entire data fits within this single query and output without breaking the response.**
    ⚠️ Treat this prompt as a SINGLE SHOT FULL STRUCTURE: **Do not drop any content from the table return **ALL** The records you were given.**

# ACTUAL DATA TO BE CONVERTED
---
{data}
---

Again **UNDER NO CIRCUMSTANCES** teh following rules might be violated:
⚠️ The data content should be reproduced verbatim.
⚠️ **Any Data from the original table should NOT be dropped: return **ALL** The records you were given.**
⚠️ Elements in the data that are just part of the formmatting must be excluded from the output.

{output_format}
"""
        def parse_report_sections(text: str, model: Type[BaseModel]) -> List[BaseModel]:
            """
            Parses sections from text using '### ::Label' headers and returns a list of instances.

            Args:
                text: The input text to parse.
                model: A Pydantic model class with exactly two fields (e.g., component and content).

            Returns:
                A list of model instances with extracted values.
            """
            pattern = r"### ::\s*(.+?)\s*\n(.*?)(?=\n### ::|\Z)"
            matches = re.findall(pattern, text, re.DOTALL)

            # Dynamically grab the field names (we assume there's exactly 2)
            field_names = list(model.__annotations__.keys())
            if len(field_names) < 2:
                raise ValueError("Model must have more than 1 field")
            return [
                model(**{
                    field_names[0]: label.strip(),
                    field_names[1]: content.strip()
                })
                for label, content in matches
            ]

        def create_fallback_recordset(data, row_count):
            def extract_tables(md_text):
                tables = []
                current_table = []
                table_found = False
                pending_header = None
                last_was_separator = False
                md_text = md_text.replace("||","‖").replace("\\|", "¦")
                md_text = md_text[md_text.find('|'):md_text.rfind('|') + 1]
                lines = md_text.split("\n")

                for i, line in enumerate(lines):
                    stripped_line = line.strip()
                    if stripped_line.startswith("|") and stripped_line.endswith("|"):
                        if len(stripped_line.replace("|", "").replace("-", "").strip()) == 0:
                            table_found = True
                            if pending_header:
                                current_table = [pending_header, stripped_line]
                                pending_header = None
                                last_was_separator = True
                            else:
                                last_was_separator = True
                            continue

                        if last_was_separator:
                            current_table.append(stripped_line)
                            last_was_separator = False
                        elif current_table:
                            current_table.append(stripped_line)
                        else:
                            pending_header = stripped_line
                    elif len(stripped_line) == 0:
                        continue
                    else:
                        if current_table:
                            tables.append(current_table)
                            current_table = []
                        elif pending_header and table_found:
                            tables.append([pending_header])
                        pending_header = None
                        last_was_separator = False

                # Handle any leftovers
                if current_table:
                    tables.append(current_table)
                elif pending_header:
                    tables.append([pending_header])

                if not tables:
                    return [] if table_found else None

                return tables

            def merge_tables(tables):
                if not tables:
                    return []

                merged_lines = []
                first_table = True

                for table in tables:
                    if first_table:
                        merged_lines.extend(table)
                        first_table = False
                        continue

                    i = 0
                    header_check = True

                    while i < len(table):
                        line = table[i].strip()
                        is_separator = len(line.replace("|", "").replace("-", "").strip()) == 0

                        if header_check:
                            if is_separator:
                                # Line 0 is just a separator (invalid?), skip it
                                i += 1
                                header_check = False
                            elif i + 1 < len(table):
                                next_line = table[i + 1].strip()
                                is_next_separator = len(next_line.replace("|", "").replace("-", "").strip()) == 0

                                if is_next_separator:
                                    # This is a header + separator pair → discard both
                                    i += 2
                                else:
                                    # Not a header pair, treat both as rows
                                    merged_lines.append(line)
                                    merged_lines.append(next_line)
                                    i += 2
                                header_check = False
                            else:
                                # Only one line left, just keep it
                                merged_lines.append(line)
                                i += 1
                                header_check = False
                        else:
                            if not is_separator:
                                merged_lines.append(line)
                            i += 1

                return merged_lines


            def split_into_recordsets(merged_lines, row_count):
                if not merged_lines or row_count < 1:
                    return []

                header = []
                data_start = 0

                # Try to detect a header
                if len(merged_lines) >= 2:
                    maybe_separator = merged_lines[1]
                    is_separator = len(maybe_separator.replace("|", "").replace("-", "").strip()) == 0
                    if is_separator:
                        header = merged_lines[:2]
                        data_start = 2

                data_lines = merged_lines[data_start:]
                chunks = [data_lines[i:i + row_count] for i in range(0, len(data_lines), row_count)]

                recordsets = []
                for chunk in chunks:
                    recordsets.append(header + chunk)

                return recordsets

            return split_into_recordsets(merge_tables(extract_tables(data)), row_count)

        def extract_defective_lines_header_rows(md_text):
            pending_header = None
            md_text = md_text[md_text.find('|'):md_text.rfind('|') + 1]
            lines = md_text.split("\n")
            for line in lines:
                stripped_line = line.strip()
                if stripped_line.startswith("|") and stripped_line.endswith("|"):
                    if len(stripped_line.replace("|", "").replace("-", "").strip()) == 0:
                        if pending_header:
                            return [pending_header, stripped_line]
                    else:
                        pending_header = stripped_line
                elif len(stripped_line) == 0:
                    continue
                else:
                    pending_header = None
            return []

        def execute_emergency_fallback_call(model, output_format, backup_LLM, main_prompt, recordsets):
            async def _async_impl():
                list_model = create_model(f"{model.__name__}List_{uuid.uuid4().hex}", items=(list[model], ...))
                backup_chain = create_structured_output_chain(backup_LLM, list_model)
                instances = []
                self.color_print(f"executing emergency fallback call", "yellow")
                try:
                    for rs in recordsets:
                        prompt = self.unsafe_string_format(
                            main_prompt,
                            data=rs,
                            output_format = output_format if output_format else ""
                        )
                        instances += getattr(await backup_chain.ainvoke({"question": prompt, "instance": None}), "items")
                except Exception as e:
                    self.color_print(f"execute_emergency_fallback_call: Error in create_structured_output_chain: {e}", "red")
                return instances
            return run_coro_sync(_async_impl())

        if backup_LLM is None:
            backup_LLM = self.llm
        instances = None
        isDataInTableFormat= False
        defective_lines = None
        try:
            table_lines = [line for line in data.splitlines() if line.strip().startswith("|") and line.strip().endswith("|")]
            backtick_lines = [i for i, line in enumerate(data.splitlines()) if line.strip().startswith("```")]
            report_lines = [i for i, line in enumerate(data.splitlines()) if line.strip().startswith("### ::")]
            if len(table_lines) == 0 and len(report_lines) == 0  and len(backtick_lines) == 2:
                instances = self.json_to_pydantic_list(data, model)
            elif len(report_lines) > 0:
                instances = parse_report_sections(data, model)
            else:
                isDataInTableFormat = True
                instances, defective_lines = self.md_table_to_pydantic_list(data, model, column_id)
        except Exception as e:
            self.color_print(f"Error in obnoxious_text_to_pydantic_list: {e}", "red")
            instances = None

        if instances is None:
            instances, recordsets = [], [data] if not isDataInTableFormat else create_fallback_recordset(data, 20)
            if not recordsets:
                recordsets = [data]
            instances = execute_emergency_fallback_call(model, output_format, backup_LLM, main_prompt, recordsets)
        if defective_lines:
            defective_lines = extract_defective_lines_header_rows(data) + defective_lines
            defective_instances = execute_emergency_fallback_call(model, output_format, backup_LLM, main_prompt, ["\n".join(defective_lines)])
            if defective_instances:
                instances += defective_instances
        # Attempted ex-post cleanup
        for instance in instances:
            for field_name in instance.__annotations__:
                value = getattr(instance, field_name)
                if isinstance(value, str):
                    setattr(instance, field_name, value.replace("||", "or").replace("|", "or").replace("\n", "<br>"))
        return instances

    def unsafe_string_format(self, dest_string, print_unused_keys_report=True, **kwargs):
        """
        Formats a destination string by replacing placeholders with corresponding values from kwargs.
        If any placeholder key in the string is not found in kwargs, it raises a ValueError.

        it works differently from the standard "format" method as instead of looking for all the potential keys, looks only for the keys as passed in kwargs
        """
        # List to track keys that were not found
        missing_keys = []
        # Regex pattern to match placeholders like {key}
        def replacer(match):
            key = match.group(1)
            if key in kwargs:
                return str(kwargs[key])
            else:
                missing_keys.append(key)
                return match.group(0)  # Leave the placeholder unchanged

        pattern = re.compile(r'\{(\w+)\}')
        result = pattern.sub(replacer, dest_string)
        if missing_keys and print_unused_keys_report:
            print(f"\033[93m\033[3mMissing keys within string-to-format in unsafe_string_format: {', '.join(missing_keys)}\033[0m", flush=True)
            # print(f"\033[93m\033[3mOriginal string: {dest_string}\033[0m", flush=True)
        return result

    def demote_headers(self, markdown, starting_level=3):
        """
        Demotes all markdown headers in a document by a certain number of levels.
        The first header will be demoted to at least the starting_level, while preserving the internal hierarchy.
        """
        lines = markdown.split('\n')

        for i, line in enumerate(lines):
            # Check if the line is a header
            header_match = re.match(r'^(#+)\s', line)

            if header_match:
                header_level = len(header_match.group(1))
                demoted_level = header_level + (starting_level - 1)
                lines[i] = re.sub(r'^#+', '#' * demoted_level, line)

        return '\n'.join(lines)

    def normalize_headers(self, markdown):
        """
        Normalizes markdown headers by adjusting the levels such that the first header is demoted to level 1 (`#`).
        Subsequent headers are adjusted relative to the first header to maintain the hierarchy.
        """
        lines = markdown.split('\n')
        current_level = None
        level_adjustment = None

        for i, line in enumerate(lines):
            header_match = re.match(r'^(#+)\s', line)

            if header_match:
                header_level = len(header_match.group(1))
                if current_level is None:
                    current_level = header_level
                    level_adjustment = current_level - 1  # This ensures the first header is demoted to level 1

                # Calculate the new level for the header, adjusting by the starting level
                new_level = header_level - level_adjustment
                if new_level < 1:
                    new_level = 1  # Ensure no header goes below level 1

                lines[i] = re.sub(r'^#+', '#' * new_level, line)

        return '\n'.join(lines)

    def normalize_demote_headers(self, markdown, starting_level=3):
        """
        Normalizes and then demotes all markdown headers in a document by a certain number of levels.
        The first header will be demoted to at least the starting_level, while preserving the internal hierarchy.
        """
        normalized = self.normalize_headers(markdown)
        demoted = self.demote_headers(normalized, starting_level)
        return demoted

    def create_tag(self, tag_name, content):
        return f"<{tag_name}>\n{content}\n</{tag_name}>"

    def remove_tag(self, tag_name, text, with_removed=False):
        start_tag = f"<{tag_name}>"
        end_tag = f"</{tag_name}>"
        removed_text = ""

        start_index = text.find(start_tag)
        end_index = text.find(end_tag)

        if start_index != -1 and end_index != -1:
            content_start_index = start_index + len(start_tag)
            content_end_index = end_index
            removed_text = text[content_start_index:content_end_index]
            text = text[:start_index] + text[end_index + len(end_tag):]

        if with_removed:
            return text, removed_text
        return text

    def replace_tag(self, tag_name, text, new_content):
        start_tag = f"<{tag_name}>"
        end_tag = f"</{tag_name}>"

        start_index = text.find(start_tag)
        end_index = text.find(end_tag)

        if start_index != -1 and end_index != -1:
            # Calculate where the content starts and ends
            content_start_index = start_index + len(start_tag)
            content_end_index = end_index

            # Replace the content between the tags
            text = text[:content_start_index] + new_content + text[content_end_index:]
        else:
            # If the tag is not found, append the new tag at the bottom of the text
            text += f"\n{start_tag}{new_content}{end_tag}"

        return text

    def retrieve_tag(self, tag_name, text):
        start_tag = f"<{tag_name}>"
        end_tag = f"</{tag_name}>"

        start_index = text.find(start_tag)
        if start_index != -1:
            end_index = text.find(end_tag, start_index)
            if end_index != -1:
                content_start_index = start_index + len(start_tag)
                content_end_index = end_index
                return text[content_start_index:content_end_index]
        # If the tag is not found, return ""
        return ""

    def clean_triple_backticks(self, code) -> str:
        pattern = r'```[a-zA-Z]*\n?|```\n?'
        return re.sub(pattern, '', code)

    def color_print(self, text, color=None, end_value=None):
        COLOR_CODES = {
            'black': '30', 'red': '31', 'green': '32', 'yellow': '33', 'blue': '34', 'magenta': '35',
            'cyan': '36', 'white': '37', 'bright_black': '90', 'bright_red': '91', 'bright_green': '92',
            'bright_yellow': '93', 'bright_blue': '94', 'bright_magenta': '95', 'bright_cyan': '96', 'bright_white': '97'
        }
        if color and color.lower() in COLOR_CODES:
            color_code = COLOR_CODES[color.lower()]
            start = f"\033[{color_code}m"
            end = "\033[0m"
            text = f"{start}{text}{end}"
        if end_value == None:
            print(text, flush=True)
        else:
            print(text, end=end_value, flush=True)
        return False

    def reprotect_brackets(self, inpt:str):
        return inpt.replace("{", "{{").replace("}", "}}")

    def replace_md_newlines(self, text: str) -> str:
        return text.replace("\n", "<br>")

    def deep_lstrip(self, text: str, chars: str ="") -> str:
        """Remove specified characters or non-alphanumerics from the start."""
        pattern = f'^[{re.escape(chars)}]+' if chars else r'^[^a-zA-Z0-9]+'
        return re.sub(pattern, '', text)

    def deep_rstrip(self, text: str = '', chars: str ="") -> str:
        """Remove specified characters or non-alphanumerics from the end."""
        pattern = f'[{re.escape(chars)}]+$' if chars else r'[^a-zA-Z0-9]+$'
        return re.sub(pattern, '', text)

    def deep_strip(self, text: str, chars: str ="") -> str:
        """Remove specified characters or non-alphanumerics from both ends."""
        return self.deep_rstrip(self.deep_lstrip(text, chars), chars)

    def flatten_key_value_pairs(self, input_data):
        """
        Flattens a list, single item of key-value pairs, or nested structure into a string.
        Each pair is formatted as "key=value", with dictionaries and lists handled recursively.
        Results are separated by new lines. Single key-value pairs do not use {}.
        """
        if isinstance(input_data, dict):
            if len(input_data) == 1:
                # Single key-value pair, no braces
                key, value = next(iter(input_data.items()))
                return f"'{key}':'{value}'"
            else:
                # Multiple key-value pairs, use braces
                return f"{{{', '.join(f"'{key}':'{value}'" for key, value in input_data.items())}}}"
        elif isinstance(input_data, list):
            # If a list, handle dictionaries and other nested lists recursively
            flattened_items = []
            for item in input_data:
                if isinstance(item, dict):
                    flattened_items.append(self.flatten_key_value_pairs(item))
                elif isinstance(item, list):
                    flattened_items.append(self.flatten_key_value_pairs(item))
            return "\n".join(flattened_items)
        else:
            # If input is not a dictionary or list, return it as-is
            return str(input_data)

    def safe_to_int(self, value, default_value = 0):
        try:
            return int(round(float(value)))
        except Exception:
            pass
        try:
            numeric_part = re.search(r'-?\d+(\.\d+)?', value)
            if numeric_part:
                return int(round(float(numeric_part.group())))
        except Exception:
            pass
        return default_value

    def sum_int_tuples(self, t1, t2):
        """
        Sums two tuples of integers element-wise.
        If they have different lengths, the missing values are treated as 0.
        """
        return tuple(a + b for a, b in zip_longest(t1, t2, fillvalue=0))

    def custom_phrasal_string_list_join(self, string_list, sep: str, last_sep: str = None,
                        singl_prefix: str = "", singl_suffix: str = "",
                        plrl_prefix: str = "", plrl_suffix: str = "") -> str:
        if len(string_list) == 0:
            return ""
        if last_sep is not None:
            if len(string_list) > 1:
                rs = sep.join(string_list[:-1]) + last_sep + string_list[-1]
            else:
                rs = "".join(string_list)
        else:
            rs = sep.join(string_list)
        if len(string_list) > 1:
            rs = f"{plrl_prefix}{rs}{plrl_suffix}"
        else:
            rs = f"{singl_prefix}{rs}{singl_suffix}"
        return rs

    def md_table_to_pydantic_list(self, md_text: str, model: Type[BaseModel], column_id:int = 0):
        """
        Converts a markdown table to a list of Pydantic model instances.

        - Detects and trims text before the first '|' and after the last '|'.
        - Uses column positions to map values (does NOT rely on headers).
        - Converts data to match the Pydantic model fields.
        - It doesn't care about headers so make sure the table matches the fields.
        - Handles multiple tables by detecting blocks of table rows.
        - Handle some edge cases (dividers between rows, tables without headers, results itemized in multiple cells, empty lines in between rows)

        Args:
            md_text (str): The Markdown text containing the table.
            model (Type[T]): A Pydantic model defining the expected structure.
            column_id (int, optional): The column index to use as the unique identifier. Defaults to 0.
                                        Is used to merge rows with an empty id (itemized) to a common parent

        Returns:
            List[T]: A list of Pydantic model instances.

        Example:
        class ExampleRow(BaseModel):
            column_1: str
            column_2: int
            column_3: float
            column_4: bool

        md_text = ```
            Some intro text...

            | Column A | Column B | Column C |
            |----------|----------|----------|
            | Apple    | 42       | True     |
            | Orange   | 100      | False    |

            Some non-table text...

            | Column A | Column B | Column C |
            |----------|----------|----------|
            | Banana   | 7        | Yes      |
            | Grapes   | 55       | No       |

            More irrelevant text...

            | DP-2           | Lewt me itemize this:           |
            |                | - `components`: your components |
            |                | - `connections`: your friens.   |
        ```
        """

        def remove_markdown(text):
            md_chars = ["***", "**", " _", "_ "]

            for char in md_chars:
                text = text.replace(char, " ")

            return text.strip()
        instances = []
        tables = []
        current_table = []
        table_found = False

        def sanitize_table(text):
            KEYWORD_ROWS = {
                "end of report.",
                "end of report"
            }
            def smart_pipe_replace(line):
                chars = list(line)
                for i in range(2, len(chars) - 2):
                    if chars[i] != '|':
                        continue

                    left = chars[i - 2:i]
                    right = chars[i + 1:i + 3]

                    # Count spaces: max 1 space on each side allowed
                    if (chars[i-2] != " " or chars[i-1] != " ") and (chars[i+2] != " " or chars[i+1] != " "):
                        chars[i] = '¦'

                return ''.join(chars)

            def _is_keyword_row(line: str) -> bool:
                s = line.strip()
                if not (s.startswith('|') and s.endswith('|')):
                    return False
                if re.fullmatch(r'\|[\s:\-|]+\|', s):
                    return False
                inner = s[1:-1]  # keep raw inner content
                lowered = inner.lower()
                for kw in KEYWORD_ROWS:
                    if kw in lowered:
                        inner = re.sub(re.escape(kw), "", inner, flags=re.IGNORECASE)
                return inner.replace(" ", "").replace("-", "").replace(":", "").replace("|","").strip() == ""

            # Step 1: Trim everything before first and after last '|'
            text = text.replace("||","‖").replace("\\|", "¦")
            first_pipe = text.find('|')
            last_pipe = text.rfind('|')
            if first_pipe == -1 or last_pipe == -1 or first_pipe >= last_pipe:
                raise ValueError("Table boundaries invalid: could not find full table between '|' characters.")
            trimmed = text[first_pipe:last_pipe + 1]

            # Step 2: Get header info
            lines, clean_lines, num_cols, defective_lines = trimmed.strip().split('\n'), [], 0, []
            for line in lines:
                if line.strip():
                    clean_lines.append(line)
            for line in clean_lines:
                if re.fullmatch(r'\|[\s:\-|]+\|', line.strip()):
                    num_cols = line.count('|') - 1
                    break
            if num_cols == 0:
                for line in clean_lines:
                    if line.strip().startswith('|') and line.strip().endswith('|'):
                        num_cols = line.count('|') - 1
                        break
            if num_cols == 0:
                raise ValueError("Unable to determine column count from any valid row")
            trimmed = "\n".join(clean_lines)

            # Step 3: Replace all \n with <br>
            br_text = trimmed.replace('\n', '<br>')

            # Step 4: Replace |<br>| patterns with real newlines, tolerant to space
            br_text = re.sub(r'\|\s*<br>\s*\|', '|\n|', br_text)

            # Step 5: Validate row by row
            fixed_text = []
            for i, line in enumerate(br_text.strip().split('\n')):
                if _is_keyword_row(line):
                    continue
                if line.startswith('|') and line.endswith('|'):
                    cells = line.split('|')[1:-1]
                    if len(cells) != num_cols:
                        fixed_line = smart_pipe_replace(line)
                        cells = fixed_line.split('|')[1:-1]
                        if len(cells) != num_cols:
                            defective_lines.append(line)
                        else:
                            fixed_text.append(fixed_line.strip())
                    else:
                        fixed_text.append(line.strip())
                else:
                    fixed_text.append(line.strip())
            return "\n".join(fixed_text), defective_lines

        md_text, defective_lines = sanitize_table(md_text)
        # Process each line and identify table blocks
        for line in md_text.split("\n"):
            stripped_line = line.strip()

            if stripped_line.startswith("|") and stripped_line.endswith("|"):
                if len(stripped_line.replace("|", "").replace("-", "").replace(":", "").strip()) == 0:
                    table_found = True
                    if len(current_table) == 1:
                        current_table = []
                    continue
                current_table.append(stripped_line)  # Table row
            elif len(stripped_line) == 0: #empty line
                continue
            elif current_table:  # If we hit a non-table row, save the current table
                tables.append(current_table)
                current_table = []

        # Add the last table if any
        if current_table:
            tables.append(current_table)

        if not tables:
            if table_found:
                return []
            return None
        # Process each table separately
        for table in tables:
            # Remove headers (First two rows: headers & dividers)
            table_rows = [row.strip("|").strip() for row in table]

            # Get model's field order
            model_fields = list(model.__annotations__.keys())

            if column_id is not None:
                while True:
                    i_h = 0
                    for i, row in enumerate(table_rows):
                        cols = [str(col).rstrip() for col in row.split("|")]
                        i_h = i
                        if len(cols) < column_id + 1 or i == 0:
                            continue
                        if len(cols[column_id]) == 0:
                            parent_row = table_rows[i - 1]
                            parent_cols = [str(col).rstrip() for col in parent_row.split("|")]
                            for j, parent_col in enumerate(parent_cols):
                                parent_cols[j] = parent_col + " <br> " + cols[j] if j != column_id else parent_col
                            table_rows[i - 1] = "|".join(parent_cols)
                            table_rows.pop(i)
                            break
                    if i_h == len(table_rows) - 1:
                        break

            for row in table_rows:
                cols = [remove_markdown(col).strip() for col in row.split("|")]

                # Convert values to match the model
                data_dict = {}
                for i, field in enumerate(model_fields):
                    if len(cols) == i:
                        break
                    field_type = model.__annotations__[field]  # Get expected type

                    # Convert types dynamically
                    if field_type == int:
                        data_dict[field] = int(cols[i]) if cols[i].isdigit() else None
                    elif field_type == float:
                        data_dict[field] = float(cols[i]) if re.match(r"^\d+(\.\d+)?$", cols[i]) else None
                    elif field_type == bool:
                        data_dict[field] = cols[i].lower() in ["true", "yes", "1"]
                    else:
                        data_dict[field] = cols[i]  # Default to str

                # Create a Pydantic instance
                instance = model(**data_dict)
                instances.append(instance)

        return instances, defective_lines

    def json_to_pydantic_list(self, text: str, model: Type[BaseModel]) -> List[BaseModel]:
        """
        Converts JSON data embedded in a text response into a list of Pydantic model instances.

        - Extracts JSON from text (handles block & inline formats).
        - Matches field names dynamically with the Pydantic model.
        - Converts data types when necessary (string -> int, float, bool).
        - Flattens lists into single strings where needed.

        Args:
            text (str): The raw response text containing JSON.
            model (Type[BaseModel]): The Pydantic model defining the expected structure.

        Returns:
            List[BaseModel]: A list of instances of the provided Pydantic model.
        """
        def extract_json_from_text(text: str) -> dict:
            json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if not json_match:
                json_match = re.search(r"({[\s\S]*})", text)
            if not json_match:
                return None
            json_str = json_match.group(1).strip()
            try:
                return commentjson.loads(json_str)
            except Exception as e:
                return None

        def reduce_json_complex_value(value, indent=0):
            indent_str = "    " * indent
            if isinstance(value, list):
                if all(isinstance(v, str) for v in value):
                    return indent_str + ", ".join(v.strip() for v in value)
                else:
                    formatted_list = [f"{indent_str}- {reduce_json_complex_value(v, indent + 1)}" for v in value]
                    return "<br>".join(formatted_list)

            elif isinstance(value, dict):
                formatted_dict = [
                    f"{indent_str}- **{k}**: {reduce_json_complex_value(v, indent + 1)}"
                    for k, v in value.items()
                ]
                return "<br>".join(formatted_dict)

            return indent_str + str(value).strip()  # Default case (if it's a simple value)


        json_data = extract_json_from_text(text)
        if json_data is None:
            return None
        if not json_data:
            return []
        instances = []
        items = None
        if isinstance(json_data, dict):
            for key, value in json_data.items():
                if isinstance(value, list):
                    items = value
                    break
                if isinstance(value, dict):
                    items = [value]
                    break
        elif isinstance(json_data, list):
            items = json_data
        else:
            return []

        for item in items:
            item_data = {}

            for field, field_type in model.__annotations__.items():
                normalized_keys = {k.lower(): k for k in item.keys()}
                matching_key = normalized_keys.get(field.lower(), None)
                if matching_key is None:
                    continue
                value = item.get(matching_key, "")

                value = item.get(field, "")

                # Convert types dynamically based on the model
                if field_type == str and not isinstance(value, str):
                    value = reduce_json_complex_value(value)
                elif field_type == str:
                    value = str(value)
                elif field_type == int:
                    value = int(value) if str(value).isdigit() else None
                elif field_type == float:
                    try:
                        value = float(value)
                    except ValueError:
                        value = None
                elif field_type == bool:
                    value = str(value).lower() in ["true", "yes", "1"]
                item_data[field] = value  # Default to str

            instances.append(model(**item_data))

        return instances

    def _config_with_enum_values(self, base_cfg: Type[Any] = None) -> Type[Any]:
        """
        Build a Config class that ensures use_enum_values=True while preserving
        any existing settings (like json_encoders) from base_cfg if provided.
        """
        base_cfg = base_cfg or type("ConfigBase", (), {})
        # Create a new subclass so we don't mutate the original
        return type(
            "Config",
            (base_cfg,),
            {
                "use_enum_values": True,
                # keep any existing json_encoders from base_cfg if present
                "json_encoders": getattr(base_cfg, "json_encoders", {}),
            },
        )

    def generate_pydantic_model(self, fields: List[tuple[str, Type[Any], Field]]) -> Type[BaseModel]:
        """
        Generates a unique Pydantic model dynamically using a list of fields.

        :param fields: List of tuples (field_name, field_type, Field(...))
        :return: A dynamically created Pydantic model type with a unique name
        """
        unique_name = f"Model_{uuid.uuid4().hex}"  # Generate a unique name
        field_definitions = {fname: (ftype, fdefault) for fname, ftype, fdefault in fields}
        Config = self._config_with_enum_values()
        return create_model(unique_name, __config__=Config, **field_definitions)

    def extend_pydantic_model(
        self,
        base_model: Type[BaseModel],
        new_fields: List[tuple[str, Type[Any], Any]]
    ) -> Type[BaseModel]:
        """
        Creates a new Pydantic model extending an existing one with additional fields.

        :param base_model: The base Pydantic model to extend.
        :param new_fields: List of tuples (field_name, field_type, default_or_Field).
        :return: A new Pydantic model class with extended fields.
        """
        unique_name = f"{base_model.__name__}_Extended_{uuid.uuid4().hex}"

        # Extract all fields from the base model
        combined_fields = {}

        for name, field in base_model.__fields__.items():
            # Reconstruct a fresh Field() using the metadata inside field.field_info
            default = field.default if field.default is not None else ...
            field_info = field.field_info
            new_default = Field(
                default,
                title=field_info.title,
                description=field_info.description,
                alias=field.alias,
                const=field_info.const,
                gt=field_info.gt,
                ge=field_info.ge,
                lt=field_info.lt,
                le=field_info.le,
                multiple_of=field_info.multiple_of,
                max_length=field_info.max_length,
                min_length=field_info.min_length,
                regex=field_info.regex,
                example=field_info.extra.get("example") if field_info.extra else None,
            )
            combined_fields[name] = (field.outer_type_, new_default)

        # Merge new fields
        for name, typ, default in new_fields:
            combined_fields[name] = (typ, default)
        Config = self._config_with_enum_values()
        return create_model(unique_name, __config__=Config, **combined_fields)

    def zip_pydantic_lists(
        self,
        list1: List[BaseModel],
        list2: List[BaseModel],
        zipped_class: Optional[Type[BaseModel]] = None
    ) -> List[BaseModel]:
        """
        Zips two lists of Pydantic models into a new merged Pydantic class based on a detected common field.
        If a zipped_class is provided, only its fields are retained.

        Args:
            list1 (List[BaseModel]): First list of Pydantic objects.
            list2 (List[BaseModel]): Second list of Pydantic objects.
            zipped_class (Optional[Type[BaseModel]]): A predefined Pydantic class that dictates the merged structure.

        Returns:
            List[BaseModel]: A list of objects of the newly created merged Pydantic class.

        Raises:
            ValueError: If no shared field exists between the models.
        """

        # Validate input
        if not list1 or not list2:
            raise ValueError("Both lists must be non-empty.")

        # Extract field names from both models
        model1_fields = list1[0].__fields__.keys()
        model2_fields = list2[0].__fields__.keys()

        # Find the first shared field to use as PK
        common_fields = [field for field in model1_fields if field in model2_fields]

        if not common_fields:
            raise ValueError("No common field found between the two models.")

        common_field = common_fields[0]  # First shared field becomes PK

        # If a zipped class is provided, use only its fields
        if zipped_class:
            merged_fields = zipped_class.__fields__  # Keep only fields from the zipped class
        else:
            # Default behavior: Merge all fields from both lists (List 1 takes precedence)
            merged_fields = {
                **list1[0].__fields__,  # List 1 fields take precedence
                **{k: v for k, v in list2[0].__fields__.items() if k not in common_fields}  # Avoid duplicate PK
            }
            # Create a new merged Pydantic model dynamically
            unique_model_name = f"MergedModel_{uuid.uuid4().hex[:8]}"
            zipped_class = create_model(unique_model_name, **merged_fields)

        # Build merged objects
        merged_objects = []
        lookup_dict = {getattr(obj, common_field): obj for obj in list2}

        for obj1 in list1:
            common_value = getattr(obj1, common_field)
            obj2 = lookup_dict.get(common_value)

            if obj2:
                merged_data = {}  # Start fresh with only allowed fields
                for field in merged_fields.keys():
                    if field in obj1.__fields__ and getattr(obj1, field):
                        merged_data[field] = getattr(obj1, field)
                    elif obj2 and field in obj2.__fields__:
                        merged_data[field] = getattr(obj2, field)

                merged_objects.append(zipped_class(**merged_data))

        return merged_objects

    def serialize_pydantic_objects_to_table(self, pydantic_objects: List[BaseModel], excluded_fields: Union[str, List[str]] = None) -> str:
        if not pydantic_objects:
            return "No data available."
        if not isinstance(pydantic_objects, list):
            pydantic_objects = [pydantic_objects]
        if isinstance(excluded_fields, str):
            excluded_fields = {excluded_fields}
        elif isinstance(excluded_fields, list):
            excluded_fields = set(excluded_fields)
        else:
            excluded_fields = set()

        # Get the Pydantic model class from the first object
        model_class = type(pydantic_objects[0])

        # Extract field names and descriptions
        model_class.__fields__ = model_class.__fields__
        headers = [
            model_class.__fields__[field].field_info.description if model_class.__fields__[field].field_info.description else field
            for field in model_class.__fields__
            if field not in excluded_fields
        ]
        separator = "| " + " | ".join(["-" * len(header) for header in headers]) + " |"

        # Format table header
        header_row = "| " + " | ".join(headers) + " |"

        # Format table rows
        rows = []

        for obj in pydantic_objects:
            row = "| " + " | ".join(str(getattr(obj, field, '')).replace("\n","<br>") for field in model_class.__fields__ if field not in excluded_fields) + " |"
            rows.append(row)

        # Compile table
        table = "\n".join([header_row, separator] + rows)

        return table

    def dump_pv1_model_list(self, models: Iterable[BaseModel]) -> str:
        """
        Serialize a list (or any iterable) of Pydantic models to a single JSON string.
        Safe for storage: UTF-8, no NaN/Infinity, compact, handles datetime/UUID/Decimal, etc.
        """
        return json.dumps(
            list(models),
            default=pydantic_encoder,   # lets Pydantic encode its own types
            ensure_ascii=False,         # keep Unicode
            allow_nan=False,            # strict JSON
            separators=(",", ":")       # compact but still JSON
        )

    def load_pv1_model_list(self, cls: Type[T], s: str) -> List[T]:
        """
        Deserialize a JSON string produced by dump_models back into a list of models of type `cls`.
        """
        data = json.loads(s)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array.")
        return [cls.parse_obj(item) for item in data]





