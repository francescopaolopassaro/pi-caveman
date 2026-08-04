# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import hashlib
import re

import sqlparse

from synthelion.ccr_store import get_instance as _ccr_instance

_VALUES_KEYWORD = re.compile(r"\bVALUES\b", re.IGNORECASE)

DEFAULT_MAX_VALUES = 8


class SqlCompressor:
    """Compresses SQL source via sqlparse whitespace normalization.

    Default path is lossless: collapses insignificant whitespace while preserving
    string-literal / quoted-identifier content and (by default) comments.
    Optional ``fold_values`` drops surplus ``VALUES (...), (...)`` tuples with a
    CCR hash, mirroring JsonCrusher's lossy row-drop.
    """

    def compress(
        self,
        sql: str,
        *,
        strip_comments: bool = False,
        fold_values: bool = False,
        max_values: int = DEFAULT_MAX_VALUES,
    ) -> tuple[str, bool]:
        """Return (compressed, was_compressed)."""
        if not sql or not sql.strip():
            return sql, False

        try:
            formatted = sqlparse.format(
                sql,
                strip_whitespace=True,
                strip_comments=strip_comments,
            )
        except Exception:
            return sql, False

        if not formatted or not formatted.strip():
            return sql, False

        result = formatted.strip()

        if fold_values:
            result = _fold_values_tuples(result, max_values)

        return result, result != sql


def _fold_values_tuples(sql: str, max_values: int) -> str:
    """Keep the first ``max_values`` row-tuples after VALUES; CCR the rest.

    Walks with string/paren awareness so commas inside literals or nested
    expressions are not mistaken for tuple separators.
    """
    if max_values < 1:
        return sql

    match = _VALUES_KEYWORD.search(sql)
    if not match:
        return sql

    start = match.end()
    # Skip whitespace after VALUES
    while start < len(sql) and sql[start].isspace():
        start += 1
    if start >= len(sql) or sql[start] != "(":
        return sql

    tuples, end = _split_value_tuples(sql, start)
    if len(tuples) <= max_values:
        return sql

    kept = tuples[:max_values]
    dropped = tuples[max_values:]
    dropped_text = ",".join(dropped)
    ccr_hash = hashlib.sha256(dropped_text.encode()).hexdigest()[:12]
    _ccr_instance().store(ccr_hash, dropped_text)

    rebuilt = sql[:start] + ",".join(kept) + sql[end:]
    rebuilt += f"\n-- CCR:{ccr_hash} {len(dropped)} value tuples dropped"
    return rebuilt


def _split_value_tuples(sql: str, start: int) -> tuple[list[str], int]:
    """Split ``(a),(b),(c)`` from ``start`` (index of first '(') into tuple strings.

    Returns (tuples, end_index) where end_index is just past the last tuple
    (before trailing semicolon / leftover SQL).
    """
    tuples: list[str] = []
    i = start
    n = len(sql)

    while i < n:
        while i < n and sql[i].isspace():
            i += 1
        if i >= n or sql[i] != "(":
            break

        close = _find_matching_paren(sql, i)
        if close < 0:
            break
        tuples.append(sql[i : close + 1])
        i = close + 1

        while i < n and sql[i].isspace():
            i += 1
        if i < n and sql[i] == ",":
            i += 1
            continue
        break

    return tuples, i


def _find_matching_paren(s: str, open_idx: int) -> int:
    """Paren-depth walk that ignores braces inside quotes / comments."""
    depth = 0
    in_single = in_double = in_backtick = False
    in_line_comment = in_block_comment = False
    i = open_idx
    n = len(s)

    while i < n:
        c = s[i]
        nxt = s[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if c == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_single:
            if c == "'" and nxt == "'":
                i += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == '"' and nxt == '"':
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if in_backtick:
            if c == "`":
                in_backtick = False
            i += 1
            continue

        if c == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "`":
            in_backtick = True
            i += 1
            continue

        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return -1
