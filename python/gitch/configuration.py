import enum
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from gitch.misc.pydantic.alias_generators import to_kebab


class Capitalization(enum.StrEnum):
    UPPER = enum.auto()
    LOWER = enum.auto()


@dataclass
class Trailer(BaseModel):
    mandatory: bool = False
    singular: bool = False
    values: set[str] = set()


@dataclass
class Summary:
    first_word_is_simple_verb: bool = False
    first_word_capitalization: Capitalization | None = None


@dataclass
class Schema(BaseModel):
    model_config = ConfigDict(
        validate_by_name=False,
        validate_by_alias=True,
        serialize_by_alias=True,
        alias_generator=to_kebab,
        extra="forbid",
    )

    first_commit_is_empty: bool = False
    starting_from: str | None = None
    summary: Summary = Summary()
    trailers: dict[str, Trailer] = dict()
