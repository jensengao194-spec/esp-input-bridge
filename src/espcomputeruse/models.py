from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MoveTo(StrictModel):
    type: Literal["move_to"]
    x: int = Field(ge=0, le=32767)
    y: int = Field(ge=0, le=32767)


class Move(StrictModel):
    type: Literal["move"]
    dx: int = Field(ge=-127, le=127)
    dy: int = Field(ge=-127, le=127)


class Click(StrictModel):
    type: Literal["click"]
    button: Literal["left", "right", "middle"] = "left"
    count: int = Field(default=1, ge=1, le=2)


class Scroll(StrictModel):
    type: Literal["scroll"]
    steps: int = Field(ge=-20, le=20)


class Key(StrictModel):
    type: Literal["key"]
    keys: list[str] = Field(min_length=1, max_length=6)


class TypeText(StrictModel):
    type: Literal["type_text"]
    text: str = Field(min_length=1, max_length=128)


class Wait(StrictModel):
    type: Literal["wait"]
    ms: int = Field(ge=0, le=5000)


Action = Annotated[MoveTo | Move | Click | Scroll | Key | TypeText | Wait, Field(discriminator="type")]


class Plan(StrictModel):
    observation_id: str
    status: Literal["act", "done", "blocked"]
    reason: str = Field(min_length=1, max_length=1000)
    actions: list[Action] = Field(max_length=8)

    @model_validator(mode="after")
    def coherent(self):
        if (self.status == "act") != bool(self.actions):
            raise ValueError("act requires actions; done/blocked require an empty actions array")
        return self
