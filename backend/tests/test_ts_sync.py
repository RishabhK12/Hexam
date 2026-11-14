"""Guard against drift between Pydantic models and frontend TypeScript types.

For every exported Pydantic model: a TS interface of the same name exists with
exactly the same field names, and a field is ``| null`` in TS iff it accepts
``None`` in Python. Every StrEnum maps to a TS string-literal union.
"""

import re
import types
import typing
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel

import hexam.schemas as schemas

TS_FILE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "hexam.ts"
SOURCE = TS_FILE.read_text(encoding="utf-8")

INTERFACES = {
    m.group(1): dict(re.findall(r"^\s{2}(\w+): (.+);$", m.group(2), re.M))
    for m in re.finditer(r"^export interface (\w+) \{\n(.*?)^\}", SOURCE, re.M | re.S)
}
UNIONS = {
    m.group(1): set(re.findall(r'"([^"]+)"', m.group(2)))
    for m in re.finditer(r"^export type (\w+) =((?:\s*\|?\s*\"[^\"]+\")+);", SOURCE, re.M)
}

MODELS = {
    name: obj
    for name, obj in vars(schemas).items()
    if isinstance(obj, type) and issubclass(obj, BaseModel)
    and obj.__module__.startswith("hexam.schemas")
    and name not in {"HexamModel", "ProvenancedModel"}
}
ENUMS = {
    name: obj
    for name, obj in vars(schemas).items()
    if isinstance(obj, type) and issubclass(obj, StrEnum) and obj.__module__.startswith("hexam.schemas")
}


def accepts_none(annotation) -> bool:
    if annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(accepts_none(a) for a in typing.get_args(annotation))
    if origin is typing.Annotated:
        return accepts_none(typing.get_args(annotation)[0])
    return False


@pytest.mark.parametrize("name", sorted(MODELS))
def test_interface_matches_model(name):
    model = MODELS[name]
    assert name in INTERFACES, f"missing `export interface {name}` in {TS_FILE.name}"
    ts_fields = INTERFACES[name]
    py_fields = {**model.model_fields, **model.model_computed_fields}
    assert set(ts_fields) == set(py_fields)
    for fname, info in model.model_fields.items():
        ts_nullable = "| null" in ts_fields[fname]
        assert ts_nullable == accepts_none(info.annotation), f"{name}.{fname} nullability differs"


@pytest.mark.parametrize("name", sorted(ENUMS))
def test_union_matches_enum(name):
    assert name in UNIONS, f"missing `export type {name}` union in {TS_FILE.name}"
    assert UNIONS[name] == {e.value for e in ENUMS[name]}


def test_parser_found_everything():
    assert len(INTERFACES) >= len(MODELS) and len(MODELS) > 25
