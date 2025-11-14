import uuid
from pydantic.v1 import BaseModel, create_model, Field
from typing import Any, Iterable, Iterator, List, Optional, Type, TypeVar, Union

from classes.infrastructure.PromptOrchestratorSidekick import PromptOrchestratorSidekick

#! =======================================================================================
#!
#!   TYPES DEFINITION
#!
#! =======================================================================================
class Node(BaseModel):
    uid: str = Field(..., description="UID for the content")
    content: str = Field(..., description="Content")
