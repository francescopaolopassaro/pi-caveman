# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
#
# Offline tool — NOT part of the shipped package. Companion to
# devtools/build_idf_corpus.py: turns a MediaWiki XML dump (as downloaded by
# devtools/build_idf_corpus_wikipedia.ps1) into a single blank-line-separated
# plain-text file, one Wikipedia article per block, suitable as the
# --corpus-dir input for build_idf_corpus.py.
#
# Wikitext stripping here is deliberately simple/best-effort (regex-based
# template/link/markup removal, not a full MediaWiki parser) — this is a
# statistical document-frequency source, not a rendering pipeline, so
# imperfect stripping (an occasional leftover markup token counted as a
# "word") is an acceptable, minor degradation, not a correctness bug.
#
# Usage:
#   python devtools/extract_wikipedia_dump.py --dump itwiki-latest-pages-articles-multistream1.xml-p1p316052.bz2 --output ita_corpus.txt
"""Extract plain-text articles from a MediaWiki XML dump (.xml.bz2) for use as build_idf_corpus.py input."""
from __future__ import annotations

import argparse
import bz2
import re
import xml.etree.ElementTree as ET

_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_TABLE_RE = re.compile(r"\{\|.*?\|\}", re.DOTALL)
_REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_FILE_LINK_RE = re.compile(r"\[\[(File|Image|Category|Categoria|Fichier|Datei|Archivo|Ficheiro|Bestand):[^\]]*\]\]", re.IGNORECASE)
_PIPED_LINK_RE = re.compile(r"\[\[[^\]|]*\|([^\]]*)\]\]")
_PLAIN_LINK_RE = re.compile(r"\[\[([^\]]*)\]\]")
_EXTERNAL_LINK_RE = re.compile(r"\[https?://[^\s\]]+\s*([^\]]*)\]")
_BOLD_ITALIC_RE = re.compile(r"'{2,}")
_HEADING_RE = re.compile(r"={2,}\s*([^=]*?)\s*={2,}")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

_REDIRECT_RE = re.compile(r"^#REDIRECT", re.IGNORECASE)
_NS_RE = re.compile(r"\{[^}]*\}")  # strips XML namespace prefix from tag names


def _strip_wikitext(text: str) -> str:
    text = _COMMENT_RE.sub(" ", text)
    text = _REF_RE.sub(" ", text)
    # Templates can nest a couple of levels deep; a few passes of the
    # non-nested regex peels them off from the inside out.
    for _ in range(4):
        new_text = _TEMPLATE_RE.sub(" ", text)
        if new_text == text:
            break
        text = new_text
    text = _TABLE_RE.sub(" ", text)
    text = _FILE_LINK_RE.sub(" ", text)
    text = _PIPED_LINK_RE.sub(r"\1", text)
    text = _PLAIN_LINK_RE.sub(r"\1", text)
    text = _EXTERNAL_LINK_RE.sub(r"\1", text)
    text = _HEADING_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def extract(dump_path: str, output_path: str, *, min_chars: int = 200, max_articles: int | None = None) -> int:
    written = 0
    opener = bz2.open if dump_path.endswith(".bz2") else open
    with opener(dump_path, "rb") as fh, open(output_path, "w", encoding="utf-8") as out:
        context = ET.iterparse(fh, events=("end",))
        for _, elem in context:
            tag = _NS_RE.sub("", elem.tag)
            if tag != "page":
                continue
            ns_elem = elem.find("./{*}ns")
            if ns_elem is not None and ns_elem.text != "0":
                elem.clear()
                continue  # only main-namespace articles, not talk/template/category pages
            text_elem = elem.find("./{*}revision/{*}text")
            raw = text_elem.text if text_elem is not None else None
            elem.clear()
            if not raw or _REDIRECT_RE.match(raw.strip()):
                continue
            cleaned = _strip_wikitext(raw)
            if len(cleaned) < min_chars:
                continue
            out.write(cleaned)
            out.write("\n\n")
            written += 1
            if max_articles and written >= max_articles:
                break
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True, help="Path to a *-pages-articles*.xml or .xml.bz2 dump file")
    parser.add_argument("--output", required=True, help="Output plain-text file (blank-line separated articles)")
    parser.add_argument("--min-chars", type=int, default=200, help="Skip articles shorter than this after cleanup (default: 200)")
    parser.add_argument("--max-articles", type=int, default=None, help="Optional cap for a quick test run")
    args = parser.parse_args()
    n = extract(args.dump, args.output, min_chars=args.min_chars, max_articles=args.max_articles)
    print(f"Wrote {n} articles to {args.output}")


if __name__ == "__main__":
    main()
