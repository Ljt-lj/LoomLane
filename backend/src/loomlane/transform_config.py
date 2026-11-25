from __future__ import annotations

import json
import re

from .models import TransformField


_TRANSFORM_PATH = re.compile(
    r"^[a-z][a-z0-9_]{0,63}(?:\.[a-z][a-z0-9_]{0,63}){0,7}$"
)


def is_valid_transform_path(path: str) -> bool:
    return bool(_TRANSFORM_PATH.fullmatch(path))


def transform_field_layout_errors(
    fields: list[TransformField],
) -> list[tuple[int | None, str]]:
    errors: list[tuple[int | None, str]] = []
    path_indexes: dict[str, int] = {}
    duplicate_paths: set[str] = set()
    for index, field in enumerate(fields):
        if not is_valid_transform_path(field.path):
            errors.append(
                (
                    index,
                    f"field path '{field.path}' must be dot-separated lowercase "
                    "identifiers with at most 8 segments",
                )
            )
        if field.path in path_indexes:
            if field.path not in duplicate_paths:
                errors.append(
                    (
                        index,
                        f"field path '{field.path}' is duplicated",
                    )
                )
                duplicate_paths.add(field.path)
        else:
            path_indexes[field.path] = index

    for index, field in enumerate(fields):
        parts = field.path.split(".")
        for parent_length in range(1, len(parts)):
            parent = ".".join(parts[:parent_length])
            if parent in path_indexes:
                errors.append(
                    (
                        index,
                        f"field path '{field.path}' conflicts with "
                        f"parent field '{parent}'",
                    )
                )
                break
    return errors


def build_transform_expression(fields: list[TransformField]) -> str:
    if not fields:
        raise ValueError("fields transform mode requires at least one field")

    layout_errors = transform_field_layout_errors(fields)
    if layout_errors:
        raise ValueError(layout_errors[0][1])

    tree: dict[str, str | dict[str, object]] = {}
    for field in fields:
        node = tree
        parts = field.path.split(".")
        for part in parts[:-1]:
            existing = node.get(part)
            if existing is None:
                child: dict[str, object] = {}
                node[part] = child
                node = child
                continue
            if not isinstance(existing, dict):
                raise ValueError(f"field path '{field.path}' has a parent conflict")
            node = existing
        node[parts[-1]] = field.value

    def render(node: dict[str, object]) -> str:
        entries: list[str] = []
        for key, value in node.items():
            rendered = render(value) if isinstance(value, dict) else str(value)
            entries.append(f"{json.dumps(key, ensure_ascii=False)}: {rendered}")
        return "{" + ", ".join(entries) + "}"

    return render(tree)
