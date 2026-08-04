# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
#
# Offline tool — NOT part of the shipped package (not referenced by any
# runtime import, not listed in pyproject.toml's package-data).
#
# Builds {iso3}.idf.br: a precomputed global document-frequency table
# consumed by synthelion/global_idf_provider.py to improve TF-IDF scoring
# (see synthelion/nlp/summarizer.py's _tfidf_scores) beyond the current
# document's own local frequencies.
#
# ---------------------------------------------------------------------------
# NOTE — reference corpus is an open question, deliberately not decided here.
#
# This script does not ship, embed, or assume any particular reference
# corpus. Point it at a directory of plain-text documents for a given
# language (one file per document, or one file with blank-line-separated
# documents) and it will compute real document frequencies from whatever
# text you provide. No output file is generated/shipped by this repo until a
# real corpus (size, license, per-language coverage) is chosen — see the
# 1.2.5 plan's open questions.
# ---------------------------------------------------------------------------
#
# Usage:
#   python devtools/build_idf_corpus.py --iso3 eng --corpus-dir /path/to/eng_docs
#   (repeat per language; writes synthelion/worddata/{iso3}.idf.br)
"""Generate {iso3}.idf.br global document-frequency tables from a reference corpus."""
from __future__ import annotations

import argparse
import pathlib
import re
from collections import Counter

import brotli

OUTPUT_WD = pathlib.Path(__file__).parent.parent / "synthelion" / "worddata"

# Same tokenization as synthelion/nlp/summarizer.py's _tokenize, minus the
# function-word filter (document frequency is computed over all content
# tokens; function words end up with a very high df naturally, which is the
# correct signal for them to contribute little to IDF).
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [w for w in _TOKEN_RE.findall(text.lower()) if len(w) > 1]


def _iter_documents(corpus_dir: pathlib.Path) -> list[str]:
    """Each file is one document; a file containing blank-line-separated
    blocks is treated as multiple documents (so a single large corpus file
    works too, not just one-file-per-document layouts)."""
    docs: list[str] = []
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        docs.extend(blocks or [text])
    return docs


def build_idf_table(iso3: str, corpus_dir: pathlib.Path, *, min_df: int = 2) -> None:
    documents = _iter_documents(corpus_dir)
    if not documents:
        raise SystemExit(f"No documents found under {corpus_dir}")

    doc_freq: Counter[str] = Counter()
    for doc in documents:
        for token in set(_tokenize(doc)):
            doc_freq[token] += 1

    lines = [str(len(documents))]
    for token, df in sorted(doc_freq.items()):
        if df < min_df:
            continue  # drop hapax-per-corpus noise; keeps the table smaller
        lines.append(f"{token}\t{df}")

    OUTPUT_WD.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_WD / f"{iso3}.idf.br"
    payload = "\n".join(lines).encode("utf-8")
    out_path.write_bytes(brotli.compress(payload, quality=11))
    print(f"Wrote {out_path} ({len(documents)} documents, {len(lines) - 1} terms)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso3", required=True, help="ISO 639-3 language code, e.g. eng")
    parser.add_argument("--corpus-dir", required=True, type=pathlib.Path, help="Directory of reference-corpus text files")
    parser.add_argument("--min-df", type=int, default=2, help="Drop terms appearing in fewer than this many documents (default: 2)")
    args = parser.parse_args()
    build_idf_table(args.iso3, args.corpus_dir, min_df=args.min_df)


if __name__ == "__main__":
    main()
