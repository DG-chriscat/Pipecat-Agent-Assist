"""Use case packs (PRD IN-4): the intake questions for one kind of call, as YAML.

A builder changes what the bot asks by editing `packs/<id>.yaml`; no code changes.
The intake flow's steps are fixed (IN-3): greeting, identity, reason, the pack's
questions, read back. The pack supplies the wording for each step and the list of
use case questions; each question's `key` becomes a field in `calls.intake` (IN-5).

    id: claims_status
    name: Claim status
    identity:
      prompt: Ask for the caller's full name, date of birth, and member ID.
    reason:
      prompt: Ask what they are calling about today.
    questions:
      - key: claim_number
        ask: Do you have the claim number handy?
        description: Claim number as said by the caller, or "unknown".
    readback:
      prompt: Read back a one or two sentence summary and ask if it is right.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# bot/agent_assist_bot/packs.py -> repo root / packs
PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
DEFAULT_PACK = "claims_status"

# Intake fields the flow itself owns. Pack questions may not reuse these keys.
RESERVED_KEYS = frozenset({"full_name", "dob", "member_id", "reason", "summary"})
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class PackError(ValueError):
    """A pack is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StepPrompt(_Strict):
    prompt: str = Field(min_length=1)


class Question(_Strict):
    key: str
    ask: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = False

    @field_validator("key")
    @classmethod
    def _key_shape(cls, v: str) -> str:
        if not _KEY.match(v):
            raise ValueError(f"question key {v!r} must be snake_case")
        if v in RESERVED_KEYS:
            raise ValueError(f"question key {v!r} is reserved for the core intake")
        return v


class Pack(_Strict):
    id: str
    name: str
    identity: StepPrompt
    reason: StepPrompt
    questions: list[Question] = Field(default_factory=list)
    readback: StepPrompt

    @field_validator("questions")
    @classmethod
    def _unique_keys(cls, v: list[Question]) -> list[Question]:
        seen: set[str] = set()
        for q in v:
            if q.key in seen:
                raise ValueError(f"duplicate question key {q.key!r}")
            seen.add(q.key)
        return v


def load_pack(name_or_path: str | Path, packs_dir: Path = PACKS_DIR) -> Pack:
    """Load a pack by id (`packs/<id>.yaml`) or by file path."""
    path = Path(name_or_path)
    if path.suffix not in (".yaml", ".yml"):
        path = packs_dir / f"{name_or_path}.yaml"
    if not path.is_file():
        raise PackError(f"pack not found: {name_or_path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PackError(f"pack {path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"pack {path.name} must be a mapping at the top level")
    try:
        return Pack.model_validate(data)
    except ValidationError as e:
        raise PackError(f"pack {path.name} is invalid: {e}") from e
