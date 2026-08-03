# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import json
import re

from synthelion.models import ContentDetectionResult, ContentType

_LOG_LEVEL = re.compile(r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL|TRACE)\b", re.IGNORECASE)
_STACK_FRAME = re.compile(r"^\s+at\s+\S", re.MULTILINE)
_DIFF_HEADER = re.compile(r"^(\+\+\+|---)\s", re.MULTILINE)
_SEARCH_RESULT = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
_GREP_RESULT = re.compile(r"^[A-Za-z0-9_./@\-\\][^:\n]*:\d+:\S", re.MULTILINE)

_HTML_MARKERS = ("<html", "<!doctype", "<body", "<div", "<p>")
_CODE_INDICATORS = ("{", "}", ";", "=>", "->", "def ", "function ", "class ", "import ", "#include", "public ", "private ")

# SQL: statement keyword + companion clause. Leading-statement (or multi-stmt
# scripts) avoids false positives on prose like "please select from … where …".
_SQL_STATEMENT = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|WITH|MERGE)\b",
    re.IGNORECASE,
)
_SQL_CLAUSE = re.compile(
    r"\b(FROM|INTO|SET|VALUES|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY)\b",
    re.IGNORECASE,
)
_SQL_LEADING = re.compile(
    r"^\s*(?:(?:--[^\n]*\n)|(?:/\*.*?\*/)\s*)*"
    r"(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|WITH|MERGE)\b",
    re.IGNORECASE | re.DOTALL,
)


class ContentDetector:
    """Classifies content type using purely structural/lexical heuristics.

    Ported from C# CavemanContentDetector. Stateless — instantiate once and reuse.
    """

    def detect(self, content: str) -> ContentDetectionResult:
        if not content or not content.strip():
            return ContentDetectionResult(ContentType.PLAIN_TEXT, 1.0)

        trimmed = content.strip()
        first = trimmed[0]

        # 1 — JSON Array
        if first == "[" and trimmed[-1] == "]":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return ContentDetectionResult(ContentType.JSON_ARRAY, 0.98)
            except json.JSONDecodeError:
                pass

        # 2 — JSON Object
        if first == "{" and trimmed[-1] == "}":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return ContentDetectionResult(ContentType.JSON_OBJECT, 0.95)
            except json.JSONDecodeError:
                pass

        # 3 — Git diff
        if _DIFF_HEADER.search(content):
            return ContentDetectionResult(ContentType.GIT_DIFF, 0.93)

        # 4 — Log / stack trace
        lines = content.splitlines()
        log_hits = sum(1 for l in lines if _LOG_LEVEL.search(l))
        stack_hits = len(_STACK_FRAME.findall(content))
        if log_hits >= 2 or stack_hits >= 2:
            return ContentDetectionResult(ContentType.LOG_OR_STACKTRACE, 0.88)

        # 5 — HTML
        low = content.lower()
        if any(m in low for m in _HTML_MARKERS):
            return ContentDetectionResult(ContentType.HTML, 0.90)

        # 6 — Search results (numbered list or grep output)
        if _SEARCH_RESULT.search(content) or _GREP_RESULT.search(content):
            return ContentDetectionResult(ContentType.SEARCH_RESULTS, 0.80)

        # 7 — Markdown table (| header | … |)
        if content.count("|") > 4 and any(l.startswith("|") for l in lines[:5]):
            return ContentDetectionResult(ContentType.TABULAR, 0.75)

        # 8 — SQL (before CODE: SQL's `;` would otherwise inflate code_score)
        if _looks_like_sql(content):
            return ContentDetectionResult(ContentType.SQL, 0.85)

        # 9 — Code (structural indicators)
        code_score = sum(1 for ind in _CODE_INDICATORS if ind in content)
        if code_score >= 3:
            return ContentDetectionResult(ContentType.CODE, 0.70)

        return ContentDetectionResult(ContentType.PLAIN_TEXT, 1.0)


def _looks_like_sql(content: str) -> bool:
    """Require a statement keyword + companion clause, plus a shape signal.

    Shape = leading statement keyword (after optional comments), or at least two
    statement keywords (multi-statement scripts / dumps).
    """
    stmt_hits = len(_SQL_STATEMENT.findall(content))
    clause_hits = len(_SQL_CLAUSE.findall(content))
    if stmt_hits < 1 or clause_hits < 1:
        return False
    if _SQL_LEADING.search(content):
        return True
    return stmt_hits >= 2 and clause_hits >= 2
