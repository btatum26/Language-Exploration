"""Raw and derived transcript models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Token(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str
    text: str
    index: int = Field(ge=0)


class TokenMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_token_ids: tuple[str, ...]
    alignment_token_ids: tuple[str, ...]
    relationship: Literal["one-to-one", "one-to-many", "many-to-one", "unlinked"]


class NormalizedTranscript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: str
    raw_text: str
    normalized_text: str
    raw_sha256: str
    normalized_sha256: str
    normalizer_name: str
    normalizer_version: str
    source_tokens: tuple[Token, ...] = ()
    alignment_tokens: tuple[Token, ...] = ()
    token_mappings: tuple[TokenMapping, ...] = ()
    warnings: tuple[str, ...] = ()
