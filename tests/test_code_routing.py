"""Code and SQL must not be compressed as prose.

Regression tests for a routing failure that made compressed code unreadable:
indentation-based languages scored below the code-detection threshold, were
labelled PlainText with confidence 1.0, and went to the NLP compressor. That
compressor is built for prose — it strips punctuation and lemmatises — so
`price = base - (base * discount)` came back as `price base base discount`.
Every identifier survived; the arithmetic did not. Three separate language
models, asked whether the discount was applied before or after tax, all answered
that it could not be determined.
"""
from __future__ import annotations

import pytest

from synthelion.content_detector import ContentDetector
from synthelion.content_router import ContentRouter
from synthelion.models import ContentType

PY_FUNC = """def final_price(base, discount, tax):
    price = base - (base * discount)
    price = price + (price * tax)
    return round(price, 2)"""

PY_CLASS = """class ServiceSpec:
    def __init__(self, name):
        self.name = name
        self.target = None"""

PY_LOOP = """for item in items:
    if item.value > threshold:
        result.append(item)"""

JS = """function fetchUser(id) {
  return fetch('/api/users/' + id).then(r => r.json());
}"""

SQL = "SELECT id, name FROM users WHERE active = 1 ORDER BY name;"


# ── detection ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src", [PY_FUNC, PY_CLASS, PY_LOOP], ids=["func", "class", "loop"])
def test_indentation_languages_detected_as_code(src):
    """The old indicator list was brace-and-semicolon shaped, so Python scored
    at most 1 (`def `) against a threshold of 3."""
    assert ContentDetector().detect(src).type is ContentType.CODE


def test_brace_languages_still_detected():
    assert ContentDetector().detect(JS).type is ContentType.CODE


@pytest.mark.parametrize("prose", [
    "La riunione di coordinamento settimanale ha evidenziato alcune criticità.",
    "This document describes how to import the configuration and define the class of service.",
    # Prose introduces lists exactly like a code block opens one: colon, then
    # indented lines. The block signal must not fire on its own.
    "Requisiti:\n    connessione stabile\n    account amministratore",
    "Segue l'elenco delle attività:\n    revisione del codice\n    aggiornamento della documentazione",
], ids=["it", "en-keywords", "it-list", "it-list-2"])
def test_prose_is_not_mistaken_for_code(prose):
    assert ContentDetector().detect(prose).type is ContentType.PLAIN_TEXT


# ── routing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src", [PY_FUNC, PY_CLASS, JS, SQL],
                         ids=["py-func", "py-class", "js", "sql"])
def test_code_and_sql_never_reach_the_prose_compressor(src):
    assert "NlpCompression" not in ContentRouter().route(src).strategy_used


def test_arithmetic_operators_survive_routing():
    """The concrete failure: without operators a reader cannot tell whether the
    discount was subtracted or added."""
    out = ContentRouter().route(PY_FUNC).compressed
    assert "-" in out and "+" in out and "*" in out


def test_identifiers_are_not_lemmatised():
    """Language detection scored this Python as French (0.176, tied with
    Italian), and the French lemmatiser rewrote `base` to `baser`."""
    out = ContentRouter().route(PY_FUNC).compressed
    assert "baser" not in out
    assert "base" in out


def test_declined_code_compression_returns_the_original():
    """When the dedicated strategy has nothing to remove, the original is the
    correct output — 0% saved beats an unusable payload."""
    r = ContentRouter().route(PY_FUNC)
    assert r.compressed.strip() == PY_FUNC.strip()
    assert r.tokens_after == r.tokens_before


def test_prose_is_still_compressed():
    """The guard must not make the router conservative everywhere."""
    prose = ("La riunione di coordinamento settimanale ha evidenziato alcune "
             "criticità nella pianificazione delle attività previste.")
    r = ContentRouter().route(prose)
    assert r.strategy_used == "NlpCompression"
    assert r.tokens_after < r.tokens_before


# ── JSON: a declined crush must not become a prose compression ───────────────

JSON_DATA = '{"user": {"id": 4471, "full_name": "Mario Rossi", "roles": ["admin", "auditor"]}}'
JSON_CONFIG = '{"dashboard": {"host": "127.0.0.1", "port": 8787}, "proxy": {"host": "127.0.0.1", "port": 8788}}'


def test_json_keys_are_not_lemmatised():
    """`roles` came back as `role`: the prose compressor lemmatising a key."""
    out = ContentRouter().route(JSON_DATA).compressed
    assert "roles" in out


def test_json_values_keep_their_punctuation():
    """`127.0.0.1` came back as `127 0 0 1` — every key survived, so a key-level
    check saw nothing wrong, but the address was destroyed."""
    out = ContentRouter().route(JSON_CONFIG).compressed
    assert "127.0.0.1" in out


def test_schema_objects_still_fall_back_to_nlp():
    """The crusher declines on schemas by design, and that fallback is
    deliberate — only the no-gain decline is redirected."""
    import json as _json
    schema = _json.dumps({
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    })
    assert ContentRouter().route(schema).strategy_used == "NlpCompression"


def test_crushable_json_is_still_crushed():
    """The guard must not disable the crusher where it does work."""
    r = ContentRouter().route('{"a": {"b": {"c": "value"}}}')
    assert "JsonCrush" in r.strategy_used
    assert r.tokens_after < r.tokens_before
