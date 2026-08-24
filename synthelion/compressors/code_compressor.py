# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import re

import regex

_LINE_COMMENT = re.compile(r"(//|#)[^\n]*")
# Python has no `//` comment: that is floor division, and it occurs inside URL
# string literals. Only `#` opens a comment here.
_PY_LINE_COMMENT = re.compile(r"#[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_HASH_BANG = re.compile(r"^#!")

# Matches a plausible function/method signature ending in an opening brace, capturing
# everything up to and including that brace so the body (found by brace-depth counting
# below, not regex — nesting can go arbitrarily deep) can be replaced with a placeholder.
# Intentionally conservative: control-flow blocks (if/for/while/switch/try) are excluded,
# since collapsing "if (x) { ... }" would remove branching logic, not implementation
# detail — this only targets declarations. Ported from Caveman C# 1.4.1.
_CSTYLE_SIGNATURE = regex.compile(
    r"^([ \t]*(?:public|private|protected|internal|static|async|virtual|override|abstract|"
    r"sealed|final|export|default|fn|func|pub)?[\w<>\[\],.?\s]*?\b"
    r"(?!if|for|while|switch|catch|using|lock|foreach)(\w+)\s*\(([^;{}]*)\)\s*"
    r"(?:where[^{]*)?\{)",
    regex.MULTILINE,
)

# "class" is deliberately excluded: it's a container, not implementation to hide (the
# C-style regex has the same property for free, since a class declaration has no
# parentheses to match) — only leaf "def" bodies get collapsed, so a class with several
# methods keeps every method signature instead of vanishing into a single "...".
_PYTHON_DEF_LINE = re.compile(r"^([ \t]*)(?:async\s+)?def\s+\w", re.MULTILINE)
_PYTHON_INDENT = re.compile(r"^([ \t]*)")


class CodeCompressor:
    """Strips comments and blank lines from source code.

    Ported from C# CavemanCodeCompressor. Detects language by syntax heuristics.
    Preserves shebangs. Does NOT strip string literals (safety).
    """

    def compress(self, code: str, skeletonize: bool = False) -> tuple[str, str, bool, int]:
        """Return (compressed, detected_language, was_compressed, functions_skeletonized).

        `skeletonize` (ported from Caveman C# 1.4.1, default off): an additional pass
        that replaces function/method bodies with a placeholder, keeping only
        signatures. Unlike the default comment-stripping pass (always a valid subset
        of the input), skeletonization is lossy by design and off by default — opt in
        when you want structure/signatures but not implementations.
        """
        if not code or not code.strip():
            return code, "", False, 0

        lang = _detect_lang(code)
        lines = code.splitlines()
        out: list[str] = []
        comments_removed = 0
        blanks_removed = 0

        if lang in ("python",):
            # Python: remove # comments (but not shebangs), remove blank lines.
            # `_LINE_COMMENT` also treats `//` as a comment opener, which Python
            # does not — it is floor division, and it appears inside string
            # literals as part of URLs. Stripping from `//` truncated
            # `"redis://localhost:6379"` to `"redis:` and left the file
            # syntactically broken, so use the tokenizer, which knows where
            # strings begin and end. It only works on a complete, parseable
            # module; partial snippets fall back to the line-based pass below.
            tokenized = _strip_python_comments(code)
            if tokenized is not None:
                out = tokenized.splitlines()
                comments_removed += 1
            else:
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped:
                        blanks_removed += 1
                        continue
                    if i == 0 and _HASH_BANG.match(line):
                        out.append(line)
                        continue
                    clean = _PY_LINE_COMMENT.sub("", line).rstrip()
                    if not clean.strip():
                        comments_removed += 1
                        continue
                    out.append(clean)
        else:
            # C-family, JS, TS, Java, etc.: remove // and /* */ comments
            # First pass: strip block comments
            code_no_blocks = _BLOCK_COMMENT.sub("", code)
            comments_removed += code.count("/*")
            for line in code_no_blocks.splitlines():
                stripped = line.strip()
                if not stripped:
                    blanks_removed += 1
                    continue
                clean = _LINE_COMMENT.sub("", line).rstrip()
                if not clean.strip():
                    comments_removed += 1
                    continue
                out.append(clean)

        result = "\n".join(out)
        functions_skeletonized = 0

        if skeletonize:
            if lang == "python":
                result, functions_skeletonized = _skeletonize_python(result)
            else:
                result, functions_skeletonized = _skeletonize_cstyle(result)

        return result, lang, result != code, functions_skeletonized

    @staticmethod
    def detect_language(code: str) -> str:
        return _detect_lang(code)


# ------------------------------------------------------------------
# Skeletonization (ported from Caveman C# 1.4.1)
# ------------------------------------------------------------------

def _find_matching_brace(s: str, open_idx: int) -> int:
    """Real brace-depth counting (not regex) — nesting can go arbitrarily deep, and a
    regex can't balance that. String/char literals are tracked so a brace inside a
    string (e.g. "{") is never mistaken for real code structure."""
    depth = 0
    in_string = in_char = False
    i = open_idx
    n = len(s)
    while i < n:
        c = s[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
        elif in_char:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_char = False
        elif c == '"':
            in_string = True
        elif c == "'":
            in_char = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _skeletonize_cstyle(code: str) -> tuple[str, int]:
    parts: list[str] = []
    pos = 0
    count = 0

    for m in _CSTYLE_SIGNATURE.finditer(code):
        if m.start() < pos:
            continue  # inside a body already collapsed above — skip
        open_idx = m.end() - 1  # the signature match ends in '{'
        close_idx = _find_matching_brace(code, open_idx)
        if close_idx < 0:
            continue  # unbalanced (or a false-positive match) — leave as-is
        if close_idx - open_idx - 1 < 40:
            continue  # trivial/near-empty body — nothing meaningful to collapse

        parts.append(code[pos:open_idx + 1])  # up to and including the opening '{'
        parts.append(" /* ... */ ")
        parts.append("}")
        pos = close_idx + 1
        count += 1

    parts.append(code[pos:])
    return "".join(parts), count


def _skeletonize_python(code: str) -> tuple[str, int]:
    lines = code.split("\n")
    result: list[str] = []
    count = 0
    i = 0
    n = len(lines)

    while i < n:
        if not _PYTHON_DEF_LINE.match(lines[i]):
            result.append(lines[i])
            i += 1
            continue

        def_indent = _PYTHON_INDENT.match(lines[i]).group(1)
        result.append(lines[i])
        j = i + 1
        body: list[str] = []
        while j < n:
            if not lines[j].strip():
                body.append(lines[j])
                j += 1
                continue
            line_indent = _PYTHON_INDENT.match(lines[j]).group(1)
            if len(line_indent) <= len(def_indent):
                break
            body.append(lines[j])
            j += 1

        # Only collapse a real multi-statement body — a one-liner isn't worth it.
        if sum(1 for line in body if line.strip()) >= 2:
            result.append(def_indent + "    ...")
            count += 1
        else:
            result.extend(body)
        i = j

    return "\n".join(result), count


def _detect_lang(code: str) -> str:
    """Best-guess language, by weighing evidence rather than first match.

    The previous rule excluded Python as soon as the word "function" appeared
    anywhere in the window — which a Python file does the moment a docstring
    says "this function returns…". It was then claimed by the JavaScript branch
    and stripped with C-family rules. Counting signals per language and taking
    the strongest avoids a single incidental word overriding everything else.
    """
    # Scan well past any module docstring or licence header: a 500-char window
    # returned "unknown" on ordinary source files that open with documentation.
    sample = code[:4000]
    lower = sample.lower()

    scores = {
        "python": (
            2 * len(re.findall(r"^\s*def \w+\(.*\):", sample, re.M))
            + 2 * len(re.findall(r"^\s*(?:from [\w.]+ )?import \w", sample, re.M))
            + 2 * len(re.findall(r"^\s*class \w+.*:", sample, re.M))
            + sample.count("self.")
            + sample.count("__init__")
            + sample.count("elif ")
            + sample.count("None")
        ),
        "csharp": (
            3 * lower.count("public class")
            + 3 * lower.count("private void")
            + 3 * lower.count("namespace ")
            + lower.count("public ")
        ),
        "javascript": (
            2 * len(re.findall(r"\bfunction\s+\w+\s*\(", sample))
            + 2 * sample.count("=>")
            + 2 * len(re.findall(r"\b(?:const|let|var)\s+\w+\s*=", sample))
            + sample.count("===")
        ),
        "cpp": 3 * lower.count("#include") + lower.count("std::"),
        "go": 2 * len(re.findall(r"\bfunc\s+\w*\s*\(", sample))
              + 2 * lower.count("package ")
              + sample.count(":="),
    }
    best = max(scores, key=scores.get)
    # A single incidental match is not evidence; require a small margin.
    return best if scores[best] >= 2 else "unknown"


def compress_python_ast(code: str, drop_docstrings: bool = True) -> tuple[str, bool]:
    """Rebuild Python source from its syntax tree. Returns (output, changed).

    The line-based pass strips comments, which leaves documented code barely
    smaller than it started and untouched code unchanged. Round-tripping through
    `ast` goes further while keeping the result valid *by construction* — it is
    generated from the parsed tree, so it cannot be malformed the way a textual
    transform can. Measured on documented Python: ~64% fewer tokens on a
    commented function with a docstring, ~40% on a class, 0% on code that was
    already minimal.

    What is lost: comments, docstrings (when `drop_docstrings`), original
    spacing, and redundant parentheses — `base - (base * discount)` comes back
    as `base - base * discount`, which is the same expression under Python's
    precedence rules. What is preserved: every identifier, every operator, and
    the semantics.

    That loss is real where the code is meant to be read back and edited by a
    person, so this is a separate entry point rather than part of the default
    pass. Returns the input unchanged if it does not parse — partial snippets,
    other languages, and Python 2 all reach here in practice.
    """
    import ast

    if not code or not code.strip():
        return code, False
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code, False

    if drop_docstrings:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                # A function whose body was only a docstring still needs one
                # statement to remain syntactically valid.
                node.body = body[1:] or [ast.Pass()]

    try:
        out = ast.unparse(ast.fix_missing_locations(tree))
    except Exception:
        return code, False

    # Never hand back something longer than what came in.
    if len(out) >= len(code):
        return code, False
    return out, True



def _strip_python_comments(code: str) -> str | None:
    """Remove comments and blank lines from a complete Python module.

    Uses `tokenize`, which tracks string boundaries, so a `#` or `//` inside a
    literal is left alone — the regex pass cannot make that distinction and
    truncated URLs in strings. Returns None when the source does not tokenize
    (partial snippets, other languages, Python 2), leaving the caller to fall
    back to the line-based pass.
    """
    import io
    import tokenize

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None

    # Lines that fall inside a multi-line string literal. Dropping a blank line
    # there rewrites the string's contents — `cli.py` embeds a JavaScript
    # template whose blank lines are part of the emitted file — so those lines
    # are kept verbatim even when they look empty.
    in_string: set[int] = set()
    for t in tokens:
        if t.type == tokenize.STRING and t.end[0] > t.start[0]:
            in_string.update(range(t.start[0], t.end[0] + 1))

    kept: list[str] = []
    for line_no, line in enumerate(code.splitlines(), start=1):
        if line_no in in_string:
            kept.append(line)
            continue
        comments = [t for t in tokens
                    if t.type == tokenize.COMMENT and t.start[0] == line_no]
        if comments:
            # A comment token's start column is where the code on that line
            # ends; slicing there keeps any code preceding an inline comment.
            line = line[:comments[0].start[1]].rstrip()
        if line.strip():
            kept.append(line)
    return "\n".join(kept)
