#!/usr/bin/env python3
from __future__ import annotations

import a52_diag94_core_scoped_base as _impl


def _function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    """Find a function definition matching anchor, skipping prototypes."""
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"{label}: function definition anchor not found")
        brace = text.find("{", start)
        semicolon = text.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: function closing brace not found")


def _line_span_in_function(
    text: str, function_anchor: str, statement: str, label: str
) -> tuple[int, int]:
    """Find one statement line by normalized text, ignoring indentation."""
    start, end = _function_bounds(text, function_anchor, label)
    wanted = statement.strip()
    matches: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() == wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: expected one normalized statement in function, found {len(matches)}"
        )
    return matches[0]


def _insert_after_in_function(
    text: str, function_anchor: str, statement: str, insertion: str, label: str
) -> str:
    _, end = _line_span_in_function(text, function_anchor, statement, label)
    return text[:end] + insertion + text[end:]


def _insert_around_in_function(
    text: str,
    function_anchor: str,
    statement: str,
    before: str,
    after: str,
    label: str,
) -> str:
    start, end = _line_span_in_function(text, function_anchor, statement, label)
    actual = text[start:end]
    return text[:start] + before + actual + after + text[end:]


def _insert_before_last_return_in_function(
    text: str, function_anchor: str, statement: str, insertion: str, label: str
) -> str:
    start, end = _function_bounds(text, function_anchor, label)
    wanted = statement.strip()
    matches: list[int] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        if line.strip() == wanted:
            matches.append(cursor)
        cursor += len(line)
    if not matches:
        raise SystemExit(f"{label}: normalized return statement not found in function")
    pos = matches[-1]
    return text[:pos] + insertion + text[pos:]


# The preserved implementation resolves these globals at call time.
_impl._function_bounds = _function_bounds
_impl._insert_after_in_function = _insert_after_in_function
_impl._insert_around_in_function = _insert_around_in_function
_impl._insert_before_last_return_in_function = _insert_before_last_return_in_function


def instrument_ufshcd(core: str) -> str:
    return _impl.instrument_ufshcd(core)
