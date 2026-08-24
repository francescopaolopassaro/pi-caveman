from __future__ import annotations

import pytest

from synthelion.core import CompressionService, _tokenize
from synthelion.models import CompressionLevel

LEVELS = [
    CompressionLevel.LIGHT,
    CompressionLevel.SEMANTIC,
    CompressionLevel.SYNTACTIC,
    CompressionLevel.AGGRESSIVE,
    CompressionLevel.STATISTICAL,
]

VALUES = [
    "127.0.0.1:8080",
    "1.2.5",
    "mario.rossi@example.com",
    "https://example.com/api",
]

TEXTS = {
    "eng": "The service uses 127.0.0.1:8080, version 1.2.5, contact mario.rossi@example.com and API https://example.com/api.",
    "ita": "Il servizio usa 127.0.0.1:8080, la versione 1.2.5, il contatto mario.rossi@example.com e l'API https://example.com/api.",
    "zho": "服务使用127.0.0.1:8080，版本1.2.5，联系人mario.rossi@example.com，API地址https://example.com/api。",
}


@pytest.mark.parametrize("iso3,text", TEXTS.items())
@pytest.mark.parametrize("level", LEVELS)
def test_structured_values_survive_all_levels(iso3, text, level):
    result = CompressionService().apply_compression(text, iso3, level)
    missing = [v for v in VALUES if v not in result.compressed_text]
    assert not missing, (iso3, level, missing)


def test_cjk_attached_values_are_atomic():
    text = "版本1.2.5已经发布，地址127.0.0.1:8080，邮件mario.rossi@example.com，接口https://example.com/api。"
    tokens = _tokenize(text)
    assert [t.text for t in tokens if t.protected] == [
        "1.2.5", "127.0.0.1:8080", "mario.rossi@example.com", "https://example.com/api"
    ]


def test_protected_flag_is_set_not_just_token_boundary():
    """The end-to-end tests above pass even when the `protected` flag is never
    set, because isolating a value as one token is already enough to carry it
    through most filters. Only STATISTICAL's per-sentence selection actually
    needs the flag — so assert the flag directly, or a regression that drops it
    stays invisible until someone touches that one level.
    """
    tokens = _tokenize("host 127.0.0.1:8080 versione 1.2.5 mail a@b.com url https://x.io/p")
    assert [t.text for t in tokens if t.protected] == [
        "127.0.0.1:8080", "1.2.5", "a@b.com", "https://x.io/p"
    ]
