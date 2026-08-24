# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

from synthelion.ccr_store import get_instance as _ccr_instance

MAX_ITEMS_DEFAULT = 15
MAX_KEYS_MARKDOWN = 6
MAX_ROWS_MARKDOWN = 50


class JsonCrusher:
    """Compresses JSON arrays to CSV, markdown table or BM25 row-drop.

    Ported from C# CavemanJsonCrusher. Three strategies:
    - MarkdownTable (≤6 keys, ≤50 rows): lossless, most compact
    - Csv (any shape): lossless schema+rows
    - LossyRowDrop (BM25 scored, with CCR hash): lossy, keeps top-k rows
    """

    def __init__(self, max_items: int = MAX_ITEMS_DEFAULT) -> None:
        self._max_items = max_items

    def crush(self, json_text: str, query: str | None = None, max_items: int | None = None) -> dict:
        """Return a dict with keys: compressed, strategy, was_crushed, ccr_hash, original_rows, kept_rows.

        `max_items` overrides `self._max_items` for this call only (adaptive-scaling
        callers can tighten the row cap for very large inputs without mutating shared
        instance state) — `None` falls back to the constructor default.
        """
        result_base = {
            "compressed": json_text,
            "strategy": "None",
            "was_crushed": False,
            "ccr_hash": None,
            "original_rows": 0,
            "kept_rows": 0,
        }
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            return result_base

        if isinstance(parsed, dict):
            return self._crush_object(parsed, json_text)

        arr = parsed
        if not isinstance(arr, list) or not arr:
            return result_base

        result_base["original_rows"] = len(arr)
        result_base["kept_rows"] = len(arr)

        rows = [r for r in arr if isinstance(r, dict)]
        if not rows:
            return result_base

        keys = list(dict.fromkeys(k for r in rows for k in r))

        # Try tool/function-schema → Python-signature compression first: this shape
        # (OpenAI/Anthropic tool-definition JSON — long free-text descriptions, a nested
        # "parameters"/"input_schema" object) compresses poorly as Markdown/CSV — those
        # either fail the 0.85 size check or produce an unreadable table — but every
        # agent turn re-sends its whole tool list, so it's worth a dedicated strategy.
        if _looks_like_tool_schema(rows):
            sig = _to_tool_signatures(rows)
            if len(sig) < len(json_text) * 0.85:
                return {
                    "compressed": sig,
                    "strategy": "ToolSignature",
                    "was_crushed": True,
                    "ccr_hash": None,
                    "original_rows": len(arr),
                    "kept_rows": len(rows),
                }

        # Try markdown table (lossless, small arrays)
        if len(keys) <= MAX_KEYS_MARKDOWN and len(rows) <= MAX_ROWS_MARKDOWN:
            md = _to_markdown(rows, keys)
            if len(md) < len(json_text) * 0.85:
                return {
                    "compressed": md,
                    "strategy": "MarkdownTable",
                    "was_crushed": True,
                    "ccr_hash": None,
                    "original_rows": len(arr),
                    "kept_rows": len(rows),
                }

        # Try CSV (lossless)
        csv = _to_csv(rows, keys)
        if len(csv) < len(json_text) * 0.85:
            return {
                "compressed": csv,
                "strategy": "Csv",
                "was_crushed": True,
                "ccr_hash": None,
                "original_rows": len(arr),
                "kept_rows": len(rows),
            }

        # Lossy BM25 row-drop if too many rows
        effective_max_items = max_items if max_items is not None else self._max_items
        if len(rows) > effective_max_items:
            kept, dropped = _bm25_select(rows, query or "", effective_max_items)
            dropped_text = json.dumps(dropped, ensure_ascii=False)
            ccr_hash = hashlib.sha256(dropped_text.encode()).hexdigest()[:12]
            _ccr_instance().store(ccr_hash, dropped_text)
            compressed = json.dumps(kept, ensure_ascii=False, indent=None)
            compressed += f"\n<!-- CCR:{ccr_hash} {len(dropped)} rows dropped -->"
            return {
                "compressed": compressed,
                "strategy": "LossyRowDrop",
                "was_crushed": True,
                "ccr_hash": ccr_hash,
                "original_rows": len(arr),
                "kept_rows": len(kept),
            }

        return result_base

    def _crush_object(self, obj: dict, json_text: str) -> dict:
        """Handles a single JSON *object* (not an array of rows) — today the only
        strategy is chain-depth collapsing: a nested chain of single-key objects
        (`{"a": {"b": {"c": "x"}}}`) is flattened to a dot-path line (`a.b.c: x`),
        the same shape a real JSON-Schema-style config often takes but with none of
        the JSON-Schema semantics `_looks_like_schema_object` guards against."""
        result_base = {
            "compressed": json_text,
            "strategy": "None",
            "was_crushed": False,
            # Why nothing was crushed. A schema object is metadata that reads
            # much like prose, so passing it on for prose compression is
            # reasonable; a data object that merely failed to shrink is not —
            # its values are the payload. The router needs to tell them apart.
            "decline_reason": None,
            "ccr_hash": None,
            "original_rows": 0,
            "kept_rows": 0,
        }
        if not obj:
            result_base["decline_reason"] = "empty"
            return result_base
        if _looks_like_schema_object(obj):
            result_base["decline_reason"] = "schema"
            return result_base

        flat = _collapse_chains(obj)
        rendered = "\n".join(f"{k}: {v}" for k, v in flat.items())
        if rendered and len(rendered) < len(json_text) * 0.85:
            return {
                "compressed": rendered,
                "strategy": "ChainCollapse",
                "was_crushed": True,
                "ccr_hash": None,
                "original_rows": 1,
                "kept_rows": 1,
            }
        result_base["decline_reason"] = "no-gain"
        return result_base


def _looks_like_schema_object(obj: dict) -> bool:
    """True if *obj* (at any level visited) looks like a real JSON-Schema object —
    `type`/`enum`/`minimum`/`maximum`/`pattern` alongside `properties` — rather than
    an ordinary data object that merely has a key named the same. Deliberately
    conservative: a false positive here just means a real schema stays uncollapsed
    (safe), a false negative would mangle schema semantics into meaningless
    dot-paths (unsafe), so only clearly schema-shaped objects are excluded."""
    schema_markers = ("enum", "minimum", "maximum", "pattern")
    if "properties" in obj and (obj.get("type") == "object" or any(m in obj for m in schema_markers)):
        return True
    return any(
        isinstance(v, dict) and _looks_like_schema_object(v) for v in obj.values()
    )


def _collapse_chains(obj: dict, prefix: str = "") -> dict:
    """Flattens chains of single-key nested objects into dot-path keys. A dict with
    more than one key, a list, or a scalar all terminate the chain at that point —
    only unambiguous single-child nesting (pure structural boilerplate, no
    information lost by flattening) gets collapsed."""
    flat: dict = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and len(value) == 1:
            flat.update(_collapse_chains(value, path))
        elif isinstance(value, dict):
            flat[path] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            flat[path] = value
    return flat


def _looks_like_tool_schema(rows: list[dict]) -> bool:
    """True if every row looks like an OpenAI/Anthropic tool-definition object:
    a `name`, and a `parameters`/`input_schema` object with `type: "object"` and a
    `properties` dict. Deliberately strict (every row must match, not just most) —
    a false positive would mangle an ordinary data array into a nonsense signature."""
    if not rows:
        return False
    for r in rows:
        if not isinstance(r.get("name"), str) or not r["name"]:
            return False
        schema = r.get("parameters") or r.get("input_schema")
        if not isinstance(schema, dict):
            return False
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            return False
    return True


def _to_tool_signatures(rows: list[dict]) -> str:
    """Renders tool-definition rows as one Python-style signature line each:
    `tool_name(required:type, optional?:type) — description`, dropping the
    JSON-schema boilerplate every agent turn otherwise re-sends verbatim."""
    lines = []
    for r in rows:
        schema = r.get("parameters") or r.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        params = []
        for pname, pschema in props.items():
            ptype = pschema.get("type", "any") if isinstance(pschema, dict) else "any"
            marker = "" if pname in required else "?"
            params.append(f"{pname}{marker}:{ptype}")
        signature = f"{r['name']}({', '.join(params)})"
        description = r.get("description")
        lines.append(f"{signature} — {description}" if description else signature)
    return "\n".join(lines)


def _to_markdown(rows: list[dict], keys: list[str]) -> str:
    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    lines = [header, sep]
    for row in rows:
        cells = [str(row.get(k, "")).replace("|", "\\|") for k in keys]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _to_csv(rows: list[dict], keys: list[str]) -> str:
    lines = [",".join(f'"{k}"' for k in keys)]
    for row in rows:
        cells = ['"' + str(row.get(k, "")).replace('"', '""') + '"' for k in keys]
        lines.append(",".join(cells))
    return "\n".join(lines)


def _bm25_select(
    rows: list[dict], query: str, top_k: int, delta: float = 1.0
) -> tuple[list[dict], list[dict]]:
    """Select top_k rows by BM25+ relevance to query. Falls back to first top_k if no query.

    `delta` (ported from Caveman C# 1.4.1's CavemanJsonCrusher.Bm25Delta, default 1.0):
    BM25+'s lower-bound term, added to every non-zero term match. Plain BM25 can score a
    long row that mentions the query term only once close to zero — the term-frequency
    component shrinks as document length grows, so a genuinely relevant long row can lose
    to a short row that barely mentions the term. Set to 0 to recover plain BM25.
    """
    if not query.strip():
        return rows[:top_k], rows[top_k:]

    query_terms = query.lower().split()
    # Build corpus for IDF
    corpus = [" ".join(str(v) for v in r.values()).lower() for r in rows]
    n = len(corpus)
    df: Counter[str] = Counter()
    for doc in corpus:
        for term in set(doc.split()):
            df[term] += 1

    k1, b = 1.5, 0.75
    avg_dl = sum(len(d.split()) for d in corpus) / max(n, 1)

    scores = []
    for doc in corpus:
        words = doc.split()
        dl = len(words)
        wc: Counter[str] = Counter(words)
        score = 0.0
        for term in query_terms:
            if term not in df:
                continue
            idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1)
            tf = wc[term]
            if tf == 0:
                continue
            score += idf * (delta + (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl)))
        scores.append(score)

    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
    kept_idx = set(ranked[:top_k])
    kept = [rows[i] for i in ranked[:top_k]]
    dropped = [rows[i] for i in range(n) if i not in kept_idx]
    return kept, dropped
