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

# The indicators above are brace-and-semicolon shaped, so a language that uses
# neither scores at most 1 (`def `) and falls through to PlainText — Python is
# the common case, but the gap covers every indentation-based syntax. These
# additions are chosen to be rare in prose: operators and literals rather than
# words, so a sentence about "how to import the class" cannot accumulate them.
_CODE_INDICATORS_EXTRA = (
    "elif ", "lambda ", "self.", "__init__", "print(",
    "None", "True", "False", "!=", "==", "+=", "()", "):",
    # Shell and Ruby: shebangs, block keywords, sigils. Ruby in particular has
    # neither braces nor semicolons and was invisible to the list above.
    "#!/", "fi\n", "esac", "done", "do\n", "end\n", "puts ", "$(", "${",
    "nil", "attr_", "=>", "||", "&&",
    # Declaration keywords from languages the original list predates.
    "fn ", "func ", "let ", "const ", "var ", "val ", "impl ", "struct ",
    "package ", "export ", "async ",
)

# Block shape: a colon-terminated line followed by an indented one. Prose writes
# exactly that when introducing a bulleted list, so the shape alone is not
# enough — _CODE_STMT_LINE requires the indented line to be a statement
# (assignment, call, return, or a nested conditional), which a list item is not.
_CODE_BLOCK = re.compile(r":\s*\n[ \t]{2,}\S", re.MULTILINE)
_CODE_STMT_LINE = re.compile(
    r"^[ \t]{2,}.*?(?:[\w.]+\s*=\s*\S|\w+\([^)]*\)|\breturn\b|\bif\b.*:)",
    re.MULTILINE,
)
# The block signal above is colon-shaped, which is Python's syntax. Ruby, shell
# and others indent under `def foo(x)`, `do`, `then` with no colon at all, so
# count indented statement lines directly: two or more is a body, and prose
# indents list items, not assignments and calls.
_CODE_INDENTED_STMT = re.compile(
    r"^[ \t]{2,}(?:[\w.@$]+\s*=\s*\S|[\w.@$]+\([^)]*\)|return\b|puts\b|echo\b|[\w.]+\.\w+\()",
    re.MULTILINE,
)
# Well above the detection threshold of 3: at this level the content is code
# beyond reasonable doubt, and the word-spotting heuristics should not get to
# claim it first. Chosen from the observed gap — real source files score 15-25,
# while prose that merely mentions code scores 0-3.
_STRONG_CODE_SCORE = 12

_CODE_ASSIGN = re.compile(r"^\s*[\w.]+\s*=\s*\S", re.MULTILINE)
_CODE_CALL = re.compile(r"\w+\([^)]*\)")
# The indicator lists test for *presence*, so a file of twenty-five imports
# scores the same 1 as a sentence containing the word "import". A package
# `__init__.py` is exactly that file and scored 1 against a threshold of 3,
# which sent it to the prose compressor. Counting the statements separates them:
# prose does not open three lines with an import.
_CODE_IMPORT_STMT = re.compile(r"^\s*(?:from [\w.]+ )?import [\w*(]", re.MULTILINE)


def _code_score(content: str) -> int:
    """Structural evidence that `content` is source code.

    Weighted rather than a flat count: a block of indented statements is worth
    more than a single token because prose does not produce it accidentally —
    prose indents list items, which are not assignments or calls.
    """
    score = sum(1 for ind in _CODE_INDICATORS if ind in content)
    score += sum(1 for ind in _CODE_INDICATORS_EXTRA if ind in content)
    if _CODE_BLOCK.search(content) and _CODE_STMT_LINE.search(content):
        score += 3
    elif len(_CODE_INDENTED_STMT.findall(content)) >= 2:
        score += 3
    if len(_CODE_ASSIGN.findall(content)) >= 2:
        score += 1
    if len(_CODE_CALL.findall(content)) >= 2:
        score += 1
    imports = len(_CODE_IMPORT_STMT.findall(content))
    if imports >= 3:
        score += 3
    elif imports >= 1:
        score += 1
    return score

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

        # 3b — Overwhelming code evidence.
        #
        # The checks below (log levels, HTML markers, numbered lists, SQL
        # keywords) are word-spotting heuristics, and source files contain those
        # words constantly — in strings, in comments, in docstrings. Because
        # CODE is evaluated last, an ordinary Python module was being claimed by
        # whichever of them fired first: `service.py` as SQL, `config.py` as
        # search results, this very file as HTML. Structural evidence far above
        # the threshold settles it before the weaker signals get a turn; the
        # margin is wide (threshold 3, this gate 12) so genuinely mixed content
        # still falls through to them.
        code_ev = _code_score(content)
        if code_ev >= _STRONG_CODE_SCORE:
            return ContentDetectionResult(ContentType.CODE, 0.75)

        # 4 — Log / stack trace
        lines = content.splitlines()
        log_hits = sum(1 for l in lines if _LOG_LEVEL.search(l))
        stack_hits = len(_STACK_FRAME.findall(content))
        if log_hits >= 2 or stack_hits >= 2:
            # Two occurrences of a level word is thin evidence, and source files
            # mention ERROR and WARN in strings and comments all the time — a
            # Python module scoring 23 on the code indicators was being routed
            # to the log compressor on the strength of the word "error"
            # appearing twice. Real log output also carries stack frames or a
            # much higher density of level words, so let strong code evidence
            # win where the log signal is only just over the line.
            if not (stack_hits < 2 and log_hits < 5 and _code_score(content) >= 8):
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
        if _code_score(content) >= 3:
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
