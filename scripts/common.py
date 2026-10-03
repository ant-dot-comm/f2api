#!/usr/bin/env python3
"""
common.py — shared helpers for the F2 / F1 Academy result + standings scrapers.

This module is kept IDENTICAL in both the f2api and f1a-api repos (copy it across
when you change it). It holds only series-agnostic plumbing: HTTP fetching, PDF
text extraction, name/id normalisation, HTML-table reading, and JSON writing in
this project's house format. Series-specific parsing lives in each repo's
update_*.py and standings_*.py adapters.
"""
import json
import os
import re
import tempfile
import unicodedata
import urllib.request
from datetime import date

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
def fetch(url):
    """Raw bytes for a URL."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def fetch_text(url):
    return fetch(url).decode("utf-8", "replace")


def fetch_html(url):
    """Parsed lxml root for a URL (server-rendered HTML; no JS needed)."""
    from lxml import html
    return html.fromstring(fetch(url))


def download_pdf(url):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(fetch(url))
    return path


def pdf_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


# --------------------------------------------------------------------------- #
# names / ids
# --------------------------------------------------------------------------- #
def deaccent(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def slug(s):
    """A stable id from a name fragment: 'Câmara' -> 'camara', 'Van Amersfoort' -> 'van_amersfoort'."""
    s = deaccent(s).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def surname_of(display):
    """Surname from a display name. Handles multi-word surnames ('van Hoepen').

    'R. Câmara' -> 'Câmara', 'L. van Hoepen' -> 'van Hoepen'. If there is a leading
    'X.' initial, the surname is everything after it; otherwise the last token
    (covers 'Rafael CAMARA' -> 'CAMARA')."""
    m = re.match(r"\s*[A-Za-z]\.\s*(.+)$", display)
    if m:
        return m.group(1).strip()
    return display.split()[-1]


# --------------------------------------------------------------------------- #
# html tables
# --------------------------------------------------------------------------- #
def cell_text(el):
    return " ".join(el.text_content().split())


def table_rows(table):
    """List of row cell-text lists for a <table> body (th+td per row)."""
    rows = []
    for tr in table.xpath(".//tbody/tr"):
        rows.append([cell_text(c) for c in tr.xpath("./th|./td")])
    return rows


# --------------------------------------------------------------------------- #
# json output (house format: 4-space indent, utf-8, trailing newline)
# --------------------------------------------------------------------------- #
def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=4, ensure_ascii=False)
        f.write("\n")
    return path


def today_iso():
    return date.today().isoformat()
