#!/usr/bin/env python3
"""
Cue Master - Main Backend Server (port 8000)

Handles script upload/parsing, RAG index build, and the real-time WebSocket
rehearsal pipeline. The Director fine-tuned model runs separately on port 8001.

Endpoints:
  POST /api/upload    - Upload and parse a script file (PDF, TXT, PNG, JPG)
  POST /api/research  - Internet research + ChromaDB RAG index build
  GET  /api/health    - Health check
  WS   /ws/rehearsal  - Real-time audio pipeline (VAD → Whisper → Director → TTS)

Usage:
  python scripts/serve_backend.py [--port 8000]
"""

import argparse
import asyncio
import json
import math
import os
import re
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

app = FastAPI(title="Cue Master Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thread pool for blocking I/O (Whisper, TTS, embeddings)
_executor = ThreadPoolExecutor(max_workers=4)

# Server-side session state
_parsed_script: Optional[list] = None

# Lazily loaded heavy models
_whisper_model = None
_silero_model = None
_embedder = None
_chroma_client = None
_active_collection = None  # ChromaDB collection for the current play/character


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ScriptLine(BaseModel):
    id: int
    type: str  # "dialogue" or "stage_direction"
    character: str
    text: str


class ResearchRequest(BaseModel):
    play: str
    character: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Lazy model loaders
# ---------------------------------------------------------------------------

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print("Loading Whisper small.en …")
        _whisper_model = WhisperModel("small.en", device="cpu", compute_type="int8")
        print("Whisper loaded.")
    return _whisper_model


def _get_vad():
    global _silero_model
    if _silero_model is None:
        print("Loading Silero VAD …")
        try:
            from silero_vad import load_silero_vad
            _silero_model = load_silero_vad()
        except Exception as e:
            print(f"Silero VAD package load failed ({e}), trying torch.hub …")
            import torch
            _silero_model, _ = torch.hub.load(
                "snakers4/silero-vad", "silero_vad", force_reload=False
            )
        print("Silero VAD loaded.")
    return _silero_model


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        print("Loading sentence-transformers all-MiniLM-L6-v2 …")
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
        print("Embedder loaded.")
    return _embedder


def _get_chroma():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _chroma_client


# ---------------------------------------------------------------------------
# Script parsing helpers
# ---------------------------------------------------------------------------

# Matches a standalone character-cue line (nothing else on the line).
# Accepts both ALL CAPS (classic play format) and Title Case / mixed case
# (modern scripts, transcripts). Word-count is enforced in the loop (≤ 4 words).
CHARACTER_PATTERN = re.compile(
    r"^[ \t]*([A-Z][A-Za-z .'-]{1,30}[A-Za-z])[ \t]*[:.]?[ \t]*$",
    re.MULTILINE,
)
STAGE_DIR_PATTERN = re.compile(
    r"^\s*[\[\(].*?[\]\)]\s*$|"
    r"^\s*\(.*?\)\s*$|"
    r"^\s*Enter\s|^\s*Exit\s|^\s*Exeunt",
    re.MULTILINE | re.IGNORECASE,
)


def _extract_cast_names(full_text: str) -> set[str]:
    """
    Extract character names from a CHARACTERS / CAST section if present.
    Runs on the FULL raw text (before play-start truncation) so the cast
    list is available even though it appears before the play body.
    """
    cast_match = re.search(
        r'(?:^|\n)\s*(?:CHARACTERS|CAST(?:\s+OF\s+CHARACTERS)?)\s*[:.]*\s*\n',
        full_text, re.IGNORECASE,
    )
    if not cast_match:
        return set()

    rest = full_text[cast_match.end():]
    end_match = re.search(
        r'\n\s*(?:PLACE|TIME|SCENE|SETTING|PROPERTIES|STAGE|ACT|NOTICE|COPYRIGHT)\b',
        rest, re.IGNORECASE,
    )
    if end_match:
        rest = rest[:end_match.start()]

    names: set[str] = set()
    for line in rest.split('\n'):
        line = line.strip()
        if not line or line.startswith('.') or line.startswith('(') or line.startswith('*'):
            continue

        # Multi-character line: "A Nurse, two Stretcher Bearers and a Young Girl."
        if re.match(r'^[Aa]\s+', line) and (',' in line or ' and ' in line.lower()):
            parts = re.split(r'[,]\s*(?:and\s+)?|\s+and\s+', line, flags=re.IGNORECASE)
            for part in parts:
                part = part.strip().rstrip('.')
                part = re.sub(r'^(?:A|An|Two|Three|Four|Five)\s+', '', part, flags=re.IGNORECASE)
                m = re.match(r'^([A-Z][A-Za-z.\']+(?:\s+[A-Z][A-Za-z.\']*)*)', part)
                if m:
                    n = m.group(1).strip().rstrip('.')
                    if len(n) >= 3:
                        names.add(n.upper())
            continue

        # Standard line: leading capitalized words are the name, rest is description
        m = re.match(r'^([A-Z][A-Za-z.\']+(?:\s+[A-Z][A-Za-z.\']*)*)', line)
        if m:
            n = m.group(1).strip().rstrip('.')
            if len(n) >= 2:
                names.add(n.upper())

    return names


def _rejoin_broken_words(raw_text: str) -> str:
    """
    Rejoin words that were split across PDF lines by explicit hyphenation.
    Only handles the safe case: word fragment ending with a hyphen + newline +
    lowercase continuation (e.g. "Feather-\\nstone" -> "Featherstone").
    Implicit breaks (no hyphen) are handled later by the autocorrect fragment
    rejoiner, which has access to the spell checker for validation.
    """
    raw_text = re.sub(
        r'([a-zA-Z]{2,})-\s*\n\s*([a-z]{2,})',
        r'\1\2',
        raw_text,
    )
    return raw_text


# ---------------------------------------------------------------------------
# OCR: EasyOCR with MPS (Apple Silicon GPU) acceleration
# ---------------------------------------------------------------------------

_easyocr_reader = None


def _get_ocr_reader():
    """Lazy-load EasyOCR once and reuse. Uses Apple Silicon MPS when available."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr, torch
        use_gpu = torch.backends.mps.is_available() or torch.cuda.is_available()
        print(f"Loading EasyOCR (GPU={'MPS' if torch.backends.mps.is_available() else 'CUDA' if torch.cuda.is_available() else 'off'})…")
        _easyocr_reader = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
        print("EasyOCR ready.")
    return _easyocr_reader


def _ocr_image_to_text(image) -> str:
    """
    Run EasyOCR on a PIL Image with contrast/sharpness pre-processing and
    return properly reading-ordered text.

    Pre-processing significantly reduces errors on low-contrast scans.
    Bounding boxes are grouped into rows by vertical midpoint (dynamic tolerance
    derived from median box height) and sorted left-to-right within each row.
    """
    import numpy as np, statistics
    from PIL import ImageEnhance

    # Boost contrast and sharpness — helps a lot with old/faded scan layers
    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = ImageEnhance.Sharpness(image).enhance(1.3)

    reader = _get_ocr_reader()
    results = reader.readtext(np.array(image), detail=1, paragraph=False)

    if not results:
        return ""

    # Each result: (bbox [[x,y]×4], text, confidence)
    # Filter low-confidence detections
    filtered = [
        (bbox, text, conf) for bbox, text, conf in results
        if conf >= 0.3 and text.strip()
    ]
    if not filtered:
        return ""

    # Compute dynamic row-grouping tolerance from median box height
    heights = [max(pt[1] for pt in bbox) - min(pt[1] for pt in bbox)
               for bbox, _, _ in filtered]
    row_tol = max(6, statistics.median(heights) * 0.55)

    # Group bounding boxes into rows by vertical midpoint
    rows: list[list[tuple]] = []
    for bbox, text, _ in filtered:
        top_y = min(pt[1] for pt in bbox)
        bot_y = max(pt[1] for pt in bbox)
        mid_y = (top_y + bot_y) / 2
        left_x = min(pt[0] for pt in bbox)
        placed = False
        for row in rows:
            if abs(mid_y - row[0][0]) <= row_tol:
                row.append((mid_y, left_x, text))
                placed = True
                break
        if not placed:
            rows.append([(mid_y, left_x, text)])

    # Sort rows top-to-bottom, tokens left-to-right within each row
    rows.sort(key=lambda r: r[0][0])
    lines = []
    for row in rows:
        row.sort(key=lambda item: item[1])
        lines.append(" ".join(item[2] for item in row))

    return "\n".join(lines)


def _text_quality_ok(text: str) -> bool:
    """
    Return False when the extracted text looks like a bad OCR layer.
    Two complementary signals:
    1. Vowel-less word ratio — classic 'c' for 'e' / 'l' for 'i' substitutions.
       Normal English prose < 0.5%; bad scans routinely exceed 3%.
    2. OCR bracket corruption — backslashes (\) and [^ sequences that arise when
       the original [ ] were mis-read. These never appear in clean script text.
    """
    words = re.findall(r"[A-Za-z]{2,}", text)
    if len(words) < 15:
        return True   # not enough text to judge — trust it
    vowels = set("aeiouAEIOU")
    no_vowel = sum(1 for w in words if not any(c in vowels for c in w))
    if (no_vowel / len(words)) >= 0.030:
        return False
    # Count backslash / caret bracket artifacts per 100 words
    artifact_count = text.count("\\") + text.count("[^") + text.count("\\^")
    if (artifact_count / len(words)) * 100 >= 2.0:
        return False
    return True


def extract_text_from_pdf(file_path: str) -> str:
    """
    Extract text from a PDF per page.
    - Text-based pages: pdfplumber (instant, lossless).
    - Scanned pages (no embedded text): PaddleOCR on rendered image.
    - Pages with an embedded but corrupted OCR layer: detected via vowel-ratio
      heuristic, then re-OCR'd with PaddleOCR for better accuracy.
    """
    import pdfplumber

    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip() and _text_quality_ok(text):
                # Clean text layer — use it directly
                pages.append(text)
            else:
                # Missing or corrupted text layer — render and re-OCR
                reason = "no text" if not text.strip() else "bad OCR layer detected"
                print(f"  Page {page.page_number}: {reason}, running EasyOCR…")
                try:
                    pil_img = page.to_image(resolution=180).original
                    ocr_text = _ocr_image_to_text(pil_img)
                    if ocr_text.strip():
                        pages.append(ocr_text)
                    elif text.strip():
                        # OCR got nothing — fall back to the imperfect embedded text
                        pages.append(text)
                except Exception as e:
                    print(f"  EasyOCR failed (page {page.page_number}): {e}")
                    if text.strip():
                        pages.append(text)
    return "\n".join(pages)


def extract_text_from_image(file_path: str) -> str:
    """Extract text from an image file using EasyOCR."""
    from PIL import Image as PILImage
    img = PILImage.open(file_path)
    return _ocr_image_to_text(img)


def parse_script_text(raw_text: str) -> list[dict]:
    from collections import Counter

    # -----------------------------------------------------------------------
    # 0a. Extract cast names from CHARACTERS section (before truncation)
    # -----------------------------------------------------------------------
    full_text = raw_text  # keep reference for title detection later
    cast_names = _extract_cast_names(raw_text)

    # -----------------------------------------------------------------------
    # 0b. Locate where the actual play begins and drop everything before it.
    # -----------------------------------------------------------------------
    _play_start_re = re.compile(
        r'(?:^|\n)[ \t]*(?:SCENE\s*:|BEFORE RISE OF CURTAIN)',
        re.IGNORECASE,
    )
    _ps_match = _play_start_re.search(raw_text)
    if _ps_match:
        raw_text = raw_text[_ps_match.start():]

    # -----------------------------------------------------------------------
    # 0c. Rejoin words broken across PDF lines by hyphenation
    # -----------------------------------------------------------------------
    raw_text = _rejoin_broken_words(raw_text)

    false_positives = {
        "ACT", "SCENE", "ACT I", "ACT II", "ACT III", "ACT IV", "ACT V",
        "PROLOGUE", "EPILOGUE", "INTERMISSION",
        # Structural document keywords that appear as all-caps headers
        "NOTICE", "COPYRIGHT", "PUBLISHING", "COMPANY", "POSITIONS", "CHART",
        "PROPERTIES", "PLACE", "TIME", "CHARACTERS", "RISE", "BEFORE", "BACKING",
        "ISBN", "PRINTED", "NOTE", "CURTAIN", "STAGE", "INTERIOR", "EXTERIOR",
        "SETTING", "LIGHTS", "MUSIC", "SOUND", "END", "FADE", "CUT", "BLACKOUT",
        "PRODUCTION", "RIGHTS", "RESERVED", "ALL RIGHTS RESERVED", "CAUTION",
        "PRINTED IN USA", "DRAMATIC PUBLISHING", "CURTAIN LINE",
        # Honorific abbreviations that appear mid-text (not standalone character names)
        "MR", "MRS", "MS", "DR", "SR", "JR",
        # OCR artifacts / common words that look like character names
        "AND", "AND MRS", "AND MR", "THE", "FOR", "MB", "MR", "OF",
        "YES", "NO", "OH", "AH", "HA", "OK", "OKAY",
        # Short pronouns/words that OCR or regex can mistake for character names
        "ME", "WE", "HE", "SHE", "BE", "IF", "SO", "UP", "DO", "AM",
        "AN", "AS", "AT", "BY", "GO", "IN", "IS", "IT", "MY", "ON",
        "OR", "TO", "US", "HIS", "HER", "OUR", "BUT", "NOT", "NOR",
        "ALL", "ANY", "FEW", "OLD", "NEW", "NOW", "HOW", "WHO", "WHY",
        "MR S", "MR. S", "MRS S",
    }

    name_counter: Counter = Counter()

    for match in CHARACTER_PATTERN.finditer(raw_text):
        name = match.group(1).strip().rstrip(":.")
        if len(name) >= 2 and len(name.split()) <= 4:
            name_counter[name.upper()] += 1

    inline_pattern = re.compile(
        r"^[ \t]*([A-Z][A-Za-z .'-]{0,30}[A-Za-z])[ \t]*[:.][ \t]+\S",
        re.MULTILINE,
    )
    for match in inline_pattern.finditer(raw_text):
        name = match.group(1).strip()
        if len(name) >= 2 and len(name.split()) <= 4:
            name_counter[name.upper()] += 1

    _structural_kw = {"COPYRIGHT", "PUBLISHING", "DRAMATIC", "CURTAIN LINE",
                      "ALL RIGHTS", "STAGE POSITION", "PRINTED"}

    def _is_valid_char_name(name: str, count: int) -> bool:
        words = name.split()
        if len(words) > 3:
            return False
        if name in false_positives:
            return False
        if any(kw in name for kw in _structural_kw):
            return False
        min_count = 2 if len(words) <= 2 else 3
        return count >= min_count

    potential_characters = {
        name for name, count in name_counter.items()
        if _is_valid_char_name(name, count)
    }

    # -----------------------------------------------------------------------
    # Integrate cast list names: add them only if they appear at the start of
    # a line followed by a delimiter (character-cue format). This prevents
    # adding collective/descriptive names (e.g. "STRETCHER BEARERS") that
    # only appear in stage directions.
    # -----------------------------------------------------------------------
    for cn in cast_names:
        if cn in false_positives or any(kw in cn for kw in _structural_kw):
            continue
        if len(cn.split()) > 4:
            continue
        # Already known from frequency counting — keep it
        if cn in potential_characters:
            continue
        # Check if the name appears at start of line with a delimiter or
        # stage direction after it (character-cue format)
        _cue_re = re.compile(
            r'(?:^|\n)\s*' + re.escape(cn) + r'\s*[:.;,\[\{(\\]',
            re.IGNORECASE,
        )
        if _cue_re.search(raw_text):
            potential_characters.add(cn)

    # -----------------------------------------------------------------------
    # Accept ALL-CAPS names from inline_pattern even with count=1 when they
    # have a strong character-cue format (NAME. or NAME: followed by dialogue).
    # This catches one-off characters like FIRST BEARER, SECOND BEARER.
    # Guards: name must be ≥ 6 chars, no periods, ≤ 2 words, not in
    # false_positives, and not a fuzzy match of an existing character.
    # -----------------------------------------------------------------------
    try:
        from rapidfuzz import fuzz as _add_fuzz
        _existing_chars = list(potential_characters)
    except ImportError:
        _add_fuzz = None
        _existing_chars = []

    for name, count in name_counter.items():
        if count >= 1 and name == name.upper() and len(name.split()) <= 2:
            if name not in false_positives and not any(kw in name for kw in _structural_kw):
                if len(name) >= 6 and not re.search(r'[.]', name):
                    # Skip if this fuzzy-matches an existing character (OCR garbling)
                    if _add_fuzz and any(
                        _add_fuzz.ratio(name, ec) >= 65 for ec in _existing_chars
                    ):
                        continue
                    potential_characters.add(name)

    # Second-pass discovery: find ALL-CAPS sequences mid-paragraph that look like
    # character cues but were missed because they appear only once.
    def _is_structurally_valid(name: str) -> bool:
        words = name.split()
        if len(words) > 2:
            return False
        if name in false_positives:
            return False
        return not any(kw in name for kw in _structural_kw)

    _name_suffixes = {
        part
        for name in list(potential_characters)
        for part in name.split()
        if len(name.split()) > 1
    }

    _mid_discovery_re = re.compile(
        r'(?<=[.?!;:])\s+([A-Z][A-Z]{1,}(?:\s+[A-Z]{2,})?)\s*[.:](?=\s+[A-Z])'
    )
    for m in _mid_discovery_re.finditer(raw_text):
        cand = m.group(1).strip()
        if cand in _name_suffixes:
            continue
        # Skip if this fuzzy-matches any suffix OR any existing character.
        # Catches OCR garbling like AUCE->ALICE, SUTH->SOUTH, CRANDMA->GRANDMA.
        try:
            from rapidfuzz import fuzz as _sfuzz
            if any(_sfuzz.ratio(cand, s) >= 60 for s in _name_suffixes):
                continue
            if any(_sfuzz.ratio(cand, ec) >= 60 for ec in potential_characters):
                continue
        except ImportError:
            pass
        if _is_structurally_valid(cand):
            potential_characters.add(cand)

    # Merge OCR-garbled duplicate character names
    _honorific_re = re.compile(r'^(MR|MRS|MS|DR|SR|JR)\.\s+')
    def _safe_to_merge(cand: str, canonical: str) -> bool:
        m_cand = _honorific_re.match(cand)
        m_canon = _honorific_re.match(canonical)
        if m_cand and m_canon:
            return m_cand.group(1) == m_canon.group(1)
        return True

    try:
        from rapidfuzz import fuzz as _rfuzz
        _char_list = sorted(potential_characters, key=lambda n: -name_counter.get(n, 0))
        _ocr_remap: dict[str, str] = {}
        for i, cand in enumerate(_char_list):
            if cand in _ocr_remap:
                continue
            for canonical in _char_list[:i]:
                if canonical in _ocr_remap:
                    continue
                if _rfuzz.ratio(cand, canonical) >= 82 and _safe_to_merge(cand, canonical):
                    _ocr_remap[cand] = canonical
                    break
        for bad in _ocr_remap:
            potential_characters.discard(bad)
    except ImportError:
        pass

    # -----------------------------------------------------------------------
    # Build a sorted list of known character names for prefix matching.
    # Longest first so "MRS. SOUTH" is tried before "MRS" or "SOUTH".
    # -----------------------------------------------------------------------
    sorted_chars = sorted(potential_characters, key=len, reverse=True)

    def _match_known_character(text: str):
        """
        Check if text starts with a known character name (case-insensitive).
        Returns (matched_name, rest_of_text, extracted_stage) or (None, None, None).
        extracted_stage is a recovered stage direction string when OCR dropped
        the opening bracket but left a closing bracket immediately after the
        character name (e.g. "MALCOLM to grandma]. He's here.").
        Accepts various delimiters after the name: . : ; , _ - or OCR variants.
        Also handles an optional bracketed stage direction between name and delimiter.
        """
        text_upper = text.upper().lstrip()
        offset = len(text) - len(text.lstrip())
        for char_name in sorted_chars:
            if text_upper.startswith(char_name):
                after = text[offset + len(char_name):]
                # After the name, accept: optional whitespace, optional stage direction,
                # optional delimiter (. : ; , _ -), then whitespace or end of string.
                m = re.match(
                    r'^[ \t]*'
                    r'(?:[\[{(\\][^\]})\\]{0,80}[\]})\\][ \t]*)?'
                    r'[:.;,_\-]?[ \t]*(.*)',
                    after, re.DOTALL,
                )
                if m:
                    rest = m.group(1).strip()
                    # Guard: if there's no delimiter and no stage direction, the "rest"
                    # must start with an uppercase letter or be empty (prevents splitting
                    # on character names that appear as regular words in dialogue).
                    has_delimiter = bool(re.match(
                        r'^[ \t]*(?:[\[{(]|[:.;,_\-])',
                        after,
                    ))
                    # OCR-damaged stage direction: when the opening bracket was
                    # dropped but the closer survived within the first ~80 chars
                    # of rest with no earlier opener, extract the damaged
                    # direction as its own stage line. Catches cases like
                    # "MALCOLM to grandma]. He's here." (original was
                    # "MALCOLM [to grandma]. He's here.").
                    extracted_stage = None
                    if not has_delimiter and rest:
                        head = rest[:80]
                        orphan_close = re.search(r'[\]\)\}]', head)
                        if orphan_close and not re.search(r'[\[\(\{]', head[:orphan_close.start()]):
                            extracted_stage = rest[:orphan_close.start()].strip(" \t,.;:")
                            rest = rest[orphan_close.end():].lstrip(" \t.:,;-_")
                            has_delimiter = True
                    if not has_delimiter and rest:
                        # Reject title patterns like "ROMEO AND JULIET" where
                        # a conjunction links to another character name
                        rest_words = rest.upper().split()
                        if (rest_words and rest_words[0] in ('AND', 'OR', 'VS', 'VS.')
                                and len(rest_words) >= 2):
                            tail_name = ' '.join(rest_words[1:])
                            if any(tail_name.startswith(c) for c in sorted_chars):
                                continue  # skip this char_name, try next
                    # Rescue cues from OCR-damaged lines like
                    # "ALICE oul xvtn(loxv\\ He's been..." where the stage
                    # direction brackets were destroyed. Only apply when the
                    # name is distinctive (>= 5 chars) AND the rest contains
                    # unmistakable OCR-damage markers — bracket/backslash/caret
                    # characters that don't appear in regular prose. Plain
                    # lowercase continuations like "MALCOLM enters quietly"
                    # stay rejected so narrative prose isn't misread as a cue.
                    distinctive = (
                        len(char_name.replace(" ", "")) >= 5
                        and bool(re.search(r'[\[\]\{\}\\^]', rest[:80]))
                    )
                    if (has_delimiter or not rest or rest[0].isupper()
                            or distinctive or not after.strip()):
                        return char_name, rest, extracted_stage
        # Fuzzy fallback for OCR-garbled names (e.g. AUCE -> ALICE, CRANDMA -> GRANDMA)
        try:
            from rapidfuzz import process as rf_process
            candidate_m = re.match(
                r'^[ \t]*([A-Z][A-Za-z .\'-]{0,30}[A-Za-z])',
                text,
            )
            if candidate_m:
                candidate = candidate_m.group(1).strip().upper()
                if len(candidate.split()) <= 4:
                    # Lower threshold for ALL-CAPS candidates (strong signal that it's
                    # a character cue, not a regular word). Use 65% to catch garbled
                    # names like AUCE->ALICE (67%) while avoiding false positives.
                    is_allcaps = candidate == candidate.upper() and candidate.isalpha() or \
                                 re.fullmatch(r'[A-Z. ]+', candidate)
                    cutoff = 65 if is_allcaps else 85
                    from rapidfuzz import fuzz as _rf_fuzz
                    best = rf_process.extractOne(
                        candidate, potential_characters,
                        scorer=_rf_fuzz.ratio, score_cutoff=cutoff
                    )
                    if best:
                        matched = best[0]
                        # Reject vocative patterns: a short interjection word
                        # followed by a known character name (e.g. "O ROMEO",
                        # "OH JULIET") is dialogue, not a character cue.
                        cand_words = candidate.split()
                        _vocative_interjections = {
                            'O', 'OH', 'AH', 'DEAR', 'SWEET', 'GOOD',
                            'FAIR', 'POOR', 'MY', 'HIS', 'HER', 'A',
                        }
                        if (len(cand_words) >= 2
                                and cand_words[0] in _vocative_interjections
                                and cand_words[-1] in potential_characters):
                            return None, None, None
                        after = text[candidate_m.end():]
                        m = re.match(
                            r'^[ \t]*(?:[\[{(\\][^\]})\\]{0,80}[\]})\\][ \t]*)?'
                            r'[:.;,_\-]?[ \t]*(.*)',
                            after, re.DOTALL,
                        )
                        if m:
                            rest = m.group(1).strip()
                            tail = matched[len(candidate):].lstrip(". ").upper()
                            if tail and rest.upper().startswith(tail):
                                return None, None, None
                            return matched, rest, None
        except ImportError:
            pass
        return None, None, None

    # -----------------------------------------------------------------------
    # Pre-process: insert newlines before character names buried mid-paragraph.
    # More permissive than before: accepts , ; _ - as delimiters in addition
    # to . and :, and handles a space before the delimiter.
    # -----------------------------------------------------------------------
    if potential_characters:
        escaped = [re.escape(c) for c in sorted_chars]
        # Pattern: after sentence-ending punctuation or certain other contexts,
        # a known character name followed by any plausible delimiter
        mid_char_re = re.compile(
            r'(?<=[.?!;:\]}>)\'])\s+(' + "|".join(escaped) + r')'
            r'(?=\s*[:.;,_\-]?\s+\S)',
            re.IGNORECASE,
        )
        raw_text = mid_char_re.sub(r"\n\1", raw_text)

    # Detect repeated page-header strings (appear on 3+ pages) so we can strip them
    all_raw_lines = raw_text.split("\n")
    line_freq: Counter = Counter(
        l.strip() for l in all_raw_lines
        if l.strip() and len(l.strip()) <= 60 and not l.strip()[0].islower()
    )
    page_noise = {text for text, cnt in line_freq.items()
                  if cnt >= 4 and text.upper() not in potential_characters}

    # Also detect the play title from the cast list section (appears as page header).
    # The title typically appears on the line just before "CHARACTERS" or as the first
    # short line of the document. We look for it in cast_names' source text.
    _title_re = re.compile(
        r'(?:^|\n)\s*([A-Z][A-Za-z?!., ]{2,40}[?!.]?)\s*\n\s*(?:A\s|CHARACTERS|CAST)',
        re.IGNORECASE,
    )
    _title_match = _title_re.search(full_text)
    if _title_match:
        _play_title = _title_match.group(1).strip()
        if len(_play_title) <= 40:
            page_noise.add(_play_title)

    # Build patterns to strip page-noise from WITHIN dialogue text.
    _page_noise_inline_patterns = []
    for noise in page_noise:
        esc = re.escape(noise)
        # "Title N" pattern (e.g. "Whodunit? 13")
        _page_noise_inline_patterns.append(re.compile(
            r'\s*' + esc + r'\s+\d{1,3}\s*'
        ))
        # "N Title" pattern (e.g. "14 Whodunit?")
        _page_noise_inline_patterns.append(re.compile(
            r'\s*\d{1,3}\s+' + esc + r'\s*'
        ))
        # Bare title (e.g. "Whodunit?" on its own or at end of a line)
        _page_noise_inline_patterns.append(re.compile(
            r'\s+' + esc + r'\s*$'
        ))

    _page_num_re = re.compile(r"^\s*\d{1,3}\s*$")

    lines = all_raw_lines
    result = []
    current_character = None
    current_text_parts = []
    line_id = 1
    first_dialogue_seen = False

    _inline_stage_re = re.compile(
        r'\[.*?\]'
        r'|\(.*?\)'
        r'|\{[^}\]]{0,80}[\]}]'
        r'|\\[A-Z][^\\]{0,80}\\'
        r'|\\\^[^\\.]{0,80}\\'
        r'|\[-[^\]]{0,60}\]'
        r'|\^\^[^^]{0,60}\^'
        r'|\^[A-Z][^^]{0,60}\^',
        re.DOTALL,
    )

    def flush_dialogue():
        nonlocal line_id, current_character, current_text_parts, first_dialogue_seen
        if not (current_character and current_text_parts):
            current_text_parts = []
            return

        text = " ".join(current_text_parts).strip()
        for pat in _page_noise_inline_patterns:
            text = pat.sub(" ", text)

        # Split on inline stage directions so each becomes its own line instead
        # of being silently deleted from the surrounding dialogue.
        segments: list[tuple[str, str]] = []
        pos = 0
        for m in _inline_stage_re.finditer(text):
            if m.start() > pos:
                segments.append(("dialogue", text[pos:m.start()]))
            direction = m.group(0).strip("[](){}\\^ \t")
            if direction:
                segments.append(("stage", direction))
            pos = m.end()
        if pos < len(text):
            segments.append(("dialogue", text[pos:]))
        if not segments:
            segments.append(("dialogue", text))

        for kind, content in segments:
            content = re.sub(r"^\s*[\]})]+\s*", "", content)
            content = re.sub(r"\s*[\[{(]+\s*$", "", content)
            content = re.sub(r"\s{2,}", " ", content).strip()
            content = re.sub(r"^[.,:;]+\s*", "", content)
            if not content or len(content) < 3:
                continue
            if re.fullmatch(r'[\s\]\[)(}{.,!?;:\-]+', content):
                continue
            if kind == "stage":
                # current_character is set, so play has started — emit even
                # when this stage direction precedes the first dialogue segment
                # in the flush (e.g. "ALICE. [entering] Hello").
                result.append({
                    "id": line_id,
                    "type": "stage_direction",
                    "character": "",
                    "text": content,
                })
                line_id += 1
            else:
                result.append({
                    "id": line_id,
                    "type": "dialogue",
                    "character": current_character,
                    "text": content,
                })
                line_id += 1
                first_dialogue_seen = True
        current_text_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _page_num_re.match(stripped) or stripped in page_noise:
            continue

        # Strip "N Title" and "Title N" page headers (e.g. "14 Whodunit?", "Whodunit? 13")
        pnt_m = re.match(r'^\s*(\d{1,3})\s+(.+)$', stripped)
        if pnt_m and pnt_m.group(2).strip() in page_noise:
            continue
        for noise_text in page_noise:
            pnt_m2 = re.match(
                r'^\s*' + re.escape(noise_text) + r'\s+(\d{1,3})\s*$', stripped
            )
            if pnt_m2:
                break
        else:
            pnt_m2 = None
        if pnt_m2:
            continue

        _section_header = re.match(
            r"^(SCENE|SETTING|PLACE|TIME|ACT\s+\w+|SCENE\s+\w+)\b[:.]\s*(.*)",
            stripped, re.IGNORECASE
        )
        if _section_header:
            flush_dialogue()
            rest = _section_header.group(2).strip()
            if rest and first_dialogue_seen:
                result.append({
                    "id": line_id,
                    "type": "stage_direction",
                    "character": "",
                    "text": rest,
                })
                line_id += 1
            continue

        if STAGE_DIR_PATTERN.match(stripped):
            flush_dialogue()
            direction_text = stripped.strip("[]() \t")
            if direction_text and first_dialogue_seen:
                result.append({
                    "id": line_id,
                    "type": "stage_direction",
                    "character": "",
                    "text": direction_text,
                })
                line_id += 1
            continue

        # --- Known-character-first matching ---
        # Try to match the line against known character names (longest first).
        # This handles names with periods (MR. SOUTH), various delimiters, etc.
        matched_char, rest, extracted_stage = _match_known_character(stripped)
        if matched_char:
            flush_dialogue()
            current_character = matched_char
            if extracted_stage:
                # Emit even before first_dialogue_seen: a matched character cue
                # already signals play body, same reasoning as flush_dialogue.
                result.append({
                    "id": line_id,
                    "type": "stage_direction",
                    "character": "",
                    "text": extracted_stage,
                })
                line_id += 1
            if rest:
                current_text_parts.append(rest)
            continue

        # --- Fallback: regex-based inline matching for unknown characters ---
        char_inline = re.match(
            r"^[ \t]*([A-Z][A-Za-z .'-]{0,30}[A-Za-z])[ \t]*"
            r"(?:[\[{][^\]}{]{0,80}[\]}\)][ \t]*)?"
            r"[:.][ \t]*(.*)",
            stripped,
        )
        if char_inline:
            name = char_inline.group(1).strip()
            name_upper = name.upper()
            if len(name.split()) <= 4:
                rest = char_inline.group(2).strip()
                matched_char = None
                if name_upper in potential_characters:
                    matched_char = name_upper
                elif name in potential_characters:
                    matched_char = name
                else:
                    try:
                        from rapidfuzz import process as rf_process, fuzz as rf_fuzz
                        best = rf_process.extractOne(
                            name_upper, potential_characters,
                            scorer=rf_fuzz.ratio, score_cutoff=85
                        )
                        if best:
                            candidate = best[0]
                            tail = candidate[len(name_upper):].lstrip(". ").upper()
                            if tail and rest.upper().startswith(tail):
                                pass
                            else:
                                matched_char = candidate
                    except ImportError:
                        pass
            else:
                matched_char = None
                rest = ""
            if matched_char:
                flush_dialogue()
                current_character = matched_char
                if rest:
                    current_text_parts.append(rest)
                continue

        upper_stripped = stripped.rstrip(":.")
        standalone_char = None
        if upper_stripped in potential_characters:
            standalone_char = upper_stripped
        elif upper_stripped.upper() in potential_characters and len(upper_stripped.split()) <= 4:
            standalone_char = upper_stripped.upper()
        elif len(upper_stripped) >= 2 and upper_stripped == upper_stripped.upper():
            try:
                from rapidfuzz import process as rf_process, fuzz as rf_fuzz
                # Use fuzz.ratio (not default WRatio) so substring matches
                # like "ROMEO AND JULIET" vs "ROMEO" don't get inflated scores
                best = rf_process.extractOne(
                    upper_stripped, potential_characters,
                    scorer=rf_fuzz.ratio, score_cutoff=85
                )
                if best:
                    standalone_char = best[0]
            except ImportError:
                pass
        if standalone_char:
            flush_dialogue()
            current_character = standalone_char
            continue

        if current_character:
            current_text_parts.append(stripped)
        elif first_dialogue_seen:
            result.append({
                "id": line_id,
                "type": "stage_direction",
                "character": "",
                "text": stripped,
            })
            line_id += 1

    flush_dialogue()
    return result


# ---------------------------------------------------------------------------
# OCR autocorrect
# ---------------------------------------------------------------------------

def autocorrect_script(lines: list[dict]) -> list[dict]:
    """
    Run a conservative spell-check pass on dialogue text.

    Strategy:
    - Build a protected-word set from all character names + any word that
      appears 2+ times in the script (repeated words are almost certainly
      intentional, including unusual proper nouns like "Oberon").
    - For each remaining flagged word, accept the spell-checker's top
      correction only when edit-distance == 1 (single-character fix).
      This catches OCR transpositions ("teh"->"the") while avoiding
      aggressive rewrites of unusual but correct vocabulary.
    - All-caps tokens and tokens with digits are always skipped.
    - Words shorter than 4 characters are skipped (too likely to be fragments
      from broken PDF line wraps where correction is unreliable).
    - Corrections that produce profanity or inappropriate words are rejected.
    - Adjacent word fragments that together form a valid word are rejoined
      before spell-checking.
    - Corrections are stored in line["_corrections"] as a list of
      {original, corrected} dicts so the frontend can show diffs and
      allow per-word reverts.
    """
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return lines

    from collections import Counter as _Counter

    char_names: set[str] = set()
    word_freq: _Counter = _Counter()

    for line in lines:
        if line.get("character"):
            for tok in line["character"].split():
                char_names.add(tok.lower())
        if line.get("type") == "dialogue" and line.get("text"):
            for tok in re.findall(r"[a-zA-Z']+", line["text"]):
                word_freq[tok.lower()] += 1

    frequent_words = {w for w, c in word_freq.items() if c >= 2}
    protected = char_names | frequent_words

    spell = SpellChecker()
    spell.word_frequency.load_words(protected)

    # Words we should never "correct" a token to
    _bad_corrections = {
        "slut", "sluts", "shit", "shits", "fuck", "fucks",
        "ass", "bitch", "crap", "dick", "piss", "whore", "cock",
        "bastard", "arse", "tit", "tits", "rum", "gct", "tic", "lox",
        "hose", "zinc", "nit",
    }

    def _edit_distance(a: str, b: str) -> int:
        if abs(len(a) - len(b)) > 1:
            return 2
        if a == b:
            return 0
        if len(a) == len(b):
            diffs = sum(x != y for x, y in zip(a, b))
            return 1 if diffs == 1 else 2
        longer, shorter = (a, b) if len(a) > len(b) else (b, a)
        for i in range(len(longer)):
            if longer[:i] + longer[i+1:] == shorter:
                return 1
        return 2

    def _rejoin_fragments(parts: list[str]) -> list[str]:
        """
        Scan word tokens for adjacent fragments that are individually unknown
        but form a valid word when joined. E.g. ["trem", "bling"] -> ["trembling"].
        This handles words that were broken across PDF lines.
        """
        result = []
        i = 0
        while i < len(parts):
            # Only try joining word tokens (skip non-word separators)
            if not re.fullmatch(r"[a-zA-Z']+", parts[i]):
                result.append(parts[i])
                i += 1
                continue

            # Look ahead: skip one whitespace separator and check next word token
            if (i + 2 < len(parts)
                    and re.fullmatch(r'\s+', parts[i + 1])
                    and re.fullmatch(r"[a-zA-Z']+", parts[i + 2])):
                combined = parts[i] + parts[i + 2]
                # Join if both halves are unknown but the combination is known
                if (spell.unknown([parts[i]]) and spell.unknown([parts[i + 2]])
                        and not spell.unknown([combined])):
                    result.append(combined)
                    i += 3
                    continue

            result.append(parts[i])
            i += 1
        return result

    corrected_lines = []
    for line in lines:
        if line.get("type") != "dialogue" or not line.get("text"):
            corrected_lines.append(line)
            continue

        text = line["text"]
        corrections: list[dict] = []

        parts = re.split(r"(\b[a-zA-Z']+\b)", text)

        # Rejoin broken word fragments before spell-checking
        parts = _rejoin_fragments(parts)

        new_parts = []
        for part in parts:
            if not re.fullmatch(r"[a-zA-Z']+", part):
                new_parts.append(part)
                continue
            # Skip all-caps, very short tokens (likely fragments), protected words
            if part.isupper() or len(part) <= 3 or part.lower() in protected:
                new_parts.append(part)
                continue

            misspelled = spell.unknown([part])
            if not misspelled:
                new_parts.append(part)
                continue

            candidate = spell.correction(part)
            if candidate is None or candidate == part.lower():
                new_parts.append(part)
                continue

            if _edit_distance(part.lower(), candidate) != 1:
                new_parts.append(part)
                continue

            # Protect archaic verb forms common in plays (e.g. stumblest,
            # walketh). If removing an archaic suffix yields a known word,
            # the original is intentional, not a typo. Only -est and -eth
            # are safe; shorter suffixes like -st/-th risk protecting real
            # misspellings (e.g. "forst" -> base "for" is a word).
            _pl = part.lower()
            _is_archaic = False
            for _suffix in ('est', 'eth'):
                if _pl.endswith(_suffix) and len(_pl) > len(_suffix) + 2:
                    _base = _pl[:-len(_suffix)]
                    # Also try adding 'e' back (e.g. "stumblest" -> "stumble")
                    if not spell.unknown([_base]) or not spell.unknown([_base + 'e']):
                        _is_archaic = True
                        break
            if _is_archaic:
                new_parts.append(part)
                continue

            # Reject inappropriate corrections
            if candidate.lower() in _bad_corrections:
                new_parts.append(part)
                continue

            # Skip word fragments: if the original word is a prefix or suffix
            # of a known longer word, it's likely a fragment from a PDF line
            # break, not a misspelling. E.g. "solv" -> "solve", "fession" -> "profession"
            _part_lower = part.lower()
            # Prefix check: word + one common letter = known word?
            _known = spell.known([_part_lower + c for c in "aeiourstnlde"])
            if _known:
                new_parts.append(part)
                continue
            # Suffix check: common prefix + word = known word?
            _prefixes = ("pro", "pre", "con", "com", "dis", "mis", "un", "re",
                         "de", "ex", "in", "im", "ir", "il", "over", "under",
                         "out", "sub", "super", "inter", "trans", "non")
            if any(spell.known([pfx + _part_lower]) for pfx in _prefixes):
                new_parts.append(part)
                continue

            if part[0].isupper():
                candidate = candidate[0].upper() + candidate[1:]

            corrections.append({"original": part, "corrected": candidate})
            new_parts.append(candidate)

        if corrections:
            new_line = dict(line)
            new_line["text"] = "".join(new_parts)
            new_line["_corrections"] = corrections
            corrected_lines.append(new_line)
        else:
            corrected_lines.append(line)

    return corrected_lines


# ---------------------------------------------------------------------------
# Step 4: RAG helpers
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = 200, overlap: int = 40) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if len(chunk.strip()) > 60:
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def _scrape_url(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return paragraph text. Returns empty string on failure."""
    import requests
    from bs4 import BeautifulSoup

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CueMasterBot/1.0)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        # Keep substantial paragraphs
        paras = [p for p in paras if len(p) > 80]
        return " ".join(paras[:25])
    except Exception:
        return ""


def _build_rag_index(play: str, character: str, notes: str) -> int:
    """
    Run DuckDuckGo searches, scrape top results, chunk, embed,
    and store in ChromaDB. Returns the number of chunks indexed.
    """
    from ddgs import DDGS

    queries = [
        f"{play} {character} acting analysis",
        f"{play} historical context performance",
        f"{character} motivations objectives superobjective",
    ]
    if notes and notes.strip():
        queries.append(f"{play} {character} {notes.strip()}")

    all_chunks: list[str] = []
    all_ids: list[str] = []

    ddgs = DDGS()
    for q_idx, query in enumerate(queries):
        try:
            results = list(ddgs.text(query, max_results=3))
        except Exception as e:
            print(f"DDG search failed for '{query}': {e}")
            continue

        for r_idx, result in enumerate(results):
            url = result.get("href", "")
            if not url:
                continue
            body = _scrape_url(url)
            if not body:
                continue
            chunks = _chunk_text(body)
            for c_idx, chunk in enumerate(chunks):
                doc_id = f"q{q_idx}_r{r_idx}_c{c_idx}"
                all_chunks.append(chunk)
                all_ids.append(doc_id)

    if not all_chunks:
        return 0

    embedder = _get_embedder()
    embeddings = embedder.encode(all_chunks, show_progress_bar=False).tolist()

    collection_name = re.sub(r"[^a-z0-9_]", "_", f"{play}_{character}".lower())[:63]
    client = _get_chroma()

    # Delete stale collection if it exists, then recreate
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(collection_name)

    batch = 100
    for i in range(0, len(all_chunks), batch):
        collection.add(
            documents=all_chunks[i : i + batch],
            embeddings=embeddings[i : i + batch],
            ids=all_ids[i : i + batch],
        )

    global _active_collection
    _active_collection = collection

    return len(all_chunks)


def _query_rag(text: str, n_results: int = 3) -> list[str]:
    """Return the n most relevant context chunks for the given text."""
    if _active_collection is None:
        return []
    try:
        embedder = _get_embedder()
        embedding = embedder.encode([text], show_progress_bar=False)[0].tolist()
        results = _active_collection.query(
            query_embeddings=[embedding], n_results=min(n_results, _active_collection.count())
        )
        return results["documents"][0] if results.get("documents") else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Step 5: Audio pipeline helpers
# ---------------------------------------------------------------------------

_VAD_CHUNK = 512  # silero requires exactly 512 samples at 16 kHz
_SAMPLE_RATE = 16000


def _vad_is_speech(model, audio_float32) -> bool:
    """Return True if the chunk contains speech (uses silero VAD)."""
    import numpy as np
    import torch

    n = len(audio_float32)
    probs = []
    for i in range(0, n - _VAD_CHUNK + 1, _VAD_CHUNK):
        chunk = torch.FloatTensor(audio_float32[i : i + _VAD_CHUNK])
        try:
            prob = float(model(chunk, _SAMPLE_RATE))
        except Exception:
            # Energy fallback
            rms = float(np.sqrt(np.mean(audio_float32[i : i + _VAD_CHUNK] ** 2)))
            prob = min(1.0, rms * 20)
        probs.append(prob)

    if not probs:
        return False
    return (sum(probs) / len(probs)) > 0.5


def _transcribe(pcm_bytes: bytes) -> dict:
    """
    Transcribe raw 16 kHz mono int16 PCM.
    Returns {"text": str, "wpm": int, "volume": float}.
    Intended to run in a thread executor.
    """
    import numpy as np

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(samples) / _SAMPLE_RATE

    # RMS volume in dB
    rms = float(np.sqrt(np.mean(samples ** 2)))
    db = round(20 * math.log10(rms + 1e-9), 1)

    model = _get_whisper()
    segments, _ = model.transcribe(
        samples, language="en", beam_size=1, vad_filter=True
    )
    text = " ".join(seg.text for seg in segments).strip()

    word_count = len(text.split()) if text else 0
    wpm = int(word_count / max(duration, 0.1) * 60)

    return {"text": text, "wpm": wpm, "volume": db}


# ---------------------------------------------------------------------------
# Step 6: TTS helpers
# ---------------------------------------------------------------------------

def _raw_pcm_to_wav(pcm: bytes, sample_rate: int, channels: int, bits: int) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", data_size,
    )
    return header + pcm


def _tts(text: str) -> bytes:
    """
    Synthesize text using macOS built-in TTS (say + afconvert) and return WAV bytes.
    Intended to run in a thread executor.
    """
    aiff_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        subprocess.run(
            ["say", "-v", "Alex", text, "-o", aiff_path],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", aiff_path, wav_path],
            check=True, capture_output=True, timeout=10,
        )
        with open(wav_path, "rb") as f:
            return f.read()
    finally:
        for p in [aiff_path, wav_path]:
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Step 6: Dummy script fallback (mirrors dummyScript.js IDs for the demo)
# ---------------------------------------------------------------------------

_DUMMY_DIALOGUE = [
    {"id": 2,  "type": "dialogue", "character": "PUCK",    "text": "How now, spirit! Whither wander you?"},
    {"id": 3,  "type": "dialogue", "character": "FAIRY",   "text": "Over hill, over dale, thorough bush, thorough brier, over park, over pale, thorough flood, thorough fire - I do wander everywhere, swifter than the moon's sphere; and I serve the Fairy Queen, to dew her orbs upon the green."},
    {"id": 4,  "type": "dialogue", "character": "FAIRY",   "text": "The cowslips tall her pensioners be; in their gold coats spots you see. Those be rubies, fairy favours, in those freckles live their savours. I must go seek some dewdrops here, and hang a pearl in every cowslip's ear."},
    {"id": 6,  "type": "dialogue", "character": "PUCK",    "text": "The King doth keep his revels here tonight. Take heed the Queen come not within his sight, for Oberon is passing fell and wrath, because that she, as her attendant, hath a lovely boy stolen from an Indian king."},
    {"id": 7,  "type": "dialogue", "character": "PUCK",    "text": "She never had so sweet a changeling. And jealous Oberon would have the child knight of his train, to trace the forests wild. But she perforce withholds the loved boy, crowns him with flowers, and makes him all her joy."},
    {"id": 9,  "type": "dialogue", "character": "OBERON",  "text": "Ill met by moonlight, proud Titania."},
    {"id": 10, "type": "dialogue", "character": "TITANIA", "text": "What, jealous Oberon? Fairies, skip hence. I have forsworn his bed and company."},
    {"id": 11, "type": "dialogue", "character": "OBERON",  "text": "Tarry, rash wanton! Am not I thy lord?"},
    {"id": 12, "type": "dialogue", "character": "TITANIA", "text": "Then I must be thy lady; but I know when thou hast stolen away from Fairyland, and in the shape of Corin sat all day, playing on pipes of corn, and versing love to amorous Phillida."},
    {"id": 14, "type": "dialogue", "character": "OBERON",  "text": "How canst thou thus, for shame, Titania, glance at my credit with Hippolyta, knowing I know thy love to Theseus?"},
    {"id": 15, "type": "dialogue", "character": "TITANIA", "text": "These are the forgeries of jealousy; and never, since the middle summer's spring, met we on hill, in dale, forest, or mead, by paved fountain or by rushy brook, or in the beached margent of the sea, to dance our ringlets to the whistling wind, but with thy brawls thou hast disturbed our sport."},
    {"id": 17, "type": "dialogue", "character": "OBERON",  "text": "Do you amend it, then; it lies in you. Why should Titania cross her Oberon? I do but beg a little changeling boy to be my henchman."},
    {"id": 18, "type": "dialogue", "character": "TITANIA", "text": "Set your heart at rest. The Fairyland buys not the child of me. His mother was a votaress of my order, and in the spiced Indian air by night full often hath she gossiped by my side."},
    {"id": 20, "type": "dialogue", "character": "OBERON",  "text": "Well, go thy way. Thou shalt not from this grove till I torment thee for this injury. My gentle Puck, come hither."},
]


def _get_dialogue_lines() -> list[dict]:
    """Return dialogue-only lines from the uploaded script, or the dummy fallback."""
    if _parsed_script:
        return [l for l in _parsed_script if l["type"] == "dialogue"]
    return _DUMMY_DIALOGUE


# ---------------------------------------------------------------------------
# WebSocket session state
# ---------------------------------------------------------------------------

@dataclass
class RehearsalSession:
    mode: str = "learning"
    character: str = "OBERON"
    dialogue_lines: list = field(default_factory=list)
    current_idx: int = 0          # index into dialogue_lines
    speech_buffer: bytearray = field(default_factory=bytearray)
    silence_chunks: int = 0
    is_speech_active: bool = False
    # Set by client when audio playback finishes so _cue_next doesn't guess timing
    audio_done: asyncio.Event = field(default_factory=asyncio.Event)

    def current_line(self) -> Optional[dict]:
        if 0 <= self.current_idx < len(self.dialogue_lines):
            return self.dialogue_lines[self.current_idx]
        return None

    def advance(self):
        self.current_idx += 1


# How many consecutive 300 ms chunks of silence end a speech segment (~600 ms)
_SILENCE_THRESHOLD = 2
# Minimum speech buffer length before we bother transcribing (300 ms worth of bytes)
_MIN_SPEECH_BYTES = _SAMPLE_RATE * 2 * 0.3  # 9600 bytes


# ---------------------------------------------------------------------------
# WebSocket: cue helpers
# ---------------------------------------------------------------------------

async def _cue_next(ws: WebSocket, session: RehearsalSession, loop):
    """
    TTS all scene-partner dialogue lines until we reach the user's next line,
    then highlight it and set status to 'listening'.
    """
    while True:
        line = session.current_line()
        if line is None:
            await ws.send_text(json.dumps({"event": "status", "state": "idle"}))
            return

        if line["character"] == session.character:
            await ws.send_text(json.dumps({"event": "advance_line", "lineId": line["id"]}))
            await ws.send_text(json.dumps({"event": "status", "state": "listening"}))
            return

        # Scene partner's line — TTS it
        await ws.send_text(json.dumps({"event": "advance_line", "lineId": line["id"]}))
        await ws.send_text(json.dumps({"event": "status", "state": "speaking"}))
        try:
            wav_bytes = await loop.run_in_executor(_executor, _tts, line["text"])
            session.audio_done.clear()
            await ws.send_bytes(wav_bytes)
            # Wait for client to signal playback complete; fall back after generous timeout
            word_count = len(line["text"].split())
            timeout = max(5.0, word_count * 0.6)
            try:
                await asyncio.wait_for(session.audio_done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            print(f"TTS error line {line['id']}: {e}")
        session.advance()


# ---------------------------------------------------------------------------
# WebSocket: director logic after a transcription
# ---------------------------------------------------------------------------

async def _apply_director(ws: WebSocket, session: RehearsalSession, result: dict, loop):
    """
    Apply Learning or Performance mode logic after a successful transcription.
    Advances the script and/or sends director notes.
    """
    line = session.current_line()
    if line is None or line["character"] != session.character:
        return

    spoken = result["text"]
    expected = line["text"]
    wpm = result["wpm"]
    volume = result["volume"]

    if session.mode == "learning":
        import numpy as np
        from rapidfuzz import fuzz

        fuzzy_score = fuzz.partial_ratio(spoken.lower(), expected.lower())

        # Semantic similarity using the already-loaded embedder (reuse RAG model)
        semantic_score = 0.0
        if spoken.strip() and expected.strip():
            try:
                embedder = _get_embedder()
                embs = await loop.run_in_executor(
                    _executor,
                    lambda: embedder.encode([spoken, expected], show_progress_bar=False),
                )
                a, b = embs[0], embs[1]
                denom = (np.linalg.norm(a) * np.linalg.norm(b))
                if denom > 0:
                    semantic_score = float(np.dot(a, b) / denom)
            except Exception as e:
                print(f"Semantic scoring failed: {e}")

        # Accept if the actor got it right character-by-character OR said the same thing
        accepted = fuzzy_score >= 78 or semantic_score >= 0.82

        if accepted:
            session.advance()
            await _cue_next(ws, session, loop)
        else:
            # Give a more specific hint based on which dimension failed
            if fuzzy_score >= 55:
                hint = f'Close — the exact wording is: "{expected[:120]}"'
            else:
                hint = f'Not quite. The line is: "{expected[:120]}"'
            await ws.send_text(json.dumps({
                "event": "director_note",
                "note": hint,
                "severity": "note",
                "type": "accuracy",
                "lineId": line["id"],
            }))
            await ws.send_text(json.dumps({"event": "status", "state": "listening"}))

    else:  # performance mode
        context_chunks = _query_rag(spoken)
        context_str = "\n".join(context_chunks)

        prompt_input = (
            f"Actor said: '{spoken}' | Expected: '{expected[:100]}' | "
            f"Pacing: {wpm} WPM | Volume: {volume} dB"
        )

        action = "Continue"
        feedback = ""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://127.0.0.1:8001/api/director",
                    json={"input": prompt_input, "context": context_str},
                    timeout=30.0,
                )
                dr = resp.json()
                action = dr.get("Action", "Continue")
                feedback = dr.get("Feedback", "")
        except Exception as e:
            print(f"Director model unreachable: {e}")

        if action == "Interrupt" and feedback:
            await ws.send_text(json.dumps({
                "event": "director_note",
                "note": feedback,
                "severity": "note",
                "type": "general",
                "lineId": line["id"],
            }))

        session.advance()
        await _cue_next(ws, session, loop)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

async def _ping_loop(ws: WebSocket, interval: int = 30):
    """Send a ping every `interval` seconds to keep the connection alive."""
    try:
        while True:
            await asyncio.sleep(interval)
            await ws.send_text(json.dumps({"event": "ping"}))
    except Exception:
        pass


@app.websocket("/ws/rehearsal")
async def ws_rehearsal(websocket: WebSocket):
    await websocket.accept()
    session: Optional[RehearsalSession] = None
    loop = asyncio.get_running_loop()
    vad_model = None
    ping_task = asyncio.create_task(_ping_loop(websocket))

    try:
        while True:
            message = await websocket.receive()

            # ---- Text frame: JSON control messages ----
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                # Client signals that TTS audio finished playing
                if data.get("event") == "audio_done":
                    if session:
                        session.audio_done.set()
                    continue

                if data.get("event") == "init":
                    mode = data.get("mode", "learning")
                    # Normalise to ALL CAPS to match parsed script character names
                    character = data.get("character", "OBERON").upper().strip()
                    session = RehearsalSession(
                        mode=mode,
                        character=character,
                        dialogue_lines=_get_dialogue_lines(),
                    )
                    # Pre-load VAD so it's ready before audio arrives
                    vad_model = await loop.run_in_executor(_executor, _get_vad)
                    # Cue through any leading scene-partner lines
                    await _cue_next(ws=websocket, session=session, loop=loop)

            # ---- Binary frame: raw 16 kHz mono int16 PCM ----
            elif "bytes" in message:
                if session is None or vad_model is None:
                    continue

                chunk_bytes = message["bytes"]
                import numpy as np
                samples = (
                    np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )

                is_speech = await loop.run_in_executor(
                    _executor, _vad_is_speech, vad_model, samples
                )

                if is_speech:
                    session.is_speech_active = True
                    session.silence_chunks = 0
                    session.speech_buffer.extend(chunk_bytes)
                elif session.is_speech_active:
                    session.silence_chunks += 1
                    session.speech_buffer.extend(chunk_bytes)  # include trailing silence

                    if session.silence_chunks >= _SILENCE_THRESHOLD:
                        # Speech segment ended — extract and transcribe
                        session.is_speech_active = False
                        speech = bytes(session.speech_buffer)
                        session.speech_buffer = bytearray()
                        session.silence_chunks = 0

                        if len(speech) >= _MIN_SPEECH_BYTES:
                            await websocket.send_text(
                                json.dumps({"event": "status", "state": "analyzing"})
                            )
                            result = await loop.run_in_executor(
                                _executor, _transcribe, speech
                            )
                            await websocket.send_text(json.dumps({
                                "event": "transcription",
                                "text": result["text"],
                                "wpm": result["wpm"],
                                "volume": result["volume"],
                            }))
                            # Apply director logic (advances script, sends notes)
                            await _apply_director(websocket, session, result, loop)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        ping_task.cancel()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "parsed_lines": len(_parsed_script) if _parsed_script else 0,
        "rag_ready": _active_collection is not None,
    }


@app.post("/api/upload")
async def upload_script(file: UploadFile = File(...)):
    """Accept a script file, parse it, and return structured lines."""
    global _parsed_script

    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = {"pdf", "txt", "png", "jpg", "jpeg"}

    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type (.{ext}). Accepted: {', '.join(sorted(allowed))}",
        )

    suffix = f".{ext}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    loop = asyncio.get_running_loop()
    try:
        if ext == "pdf":
            raw_text = await loop.run_in_executor(_executor, extract_text_from_pdf, tmp_path)
        elif ext in ("png", "jpg", "jpeg"):
            raw_text = await loop.run_in_executor(_executor, extract_text_from_image, tmp_path)
        else:
            raw_text = content.decode("utf-8", errors="replace")

        if not raw_text or not raw_text.strip():
            raise HTTPException(status_code=422, detail="Could not extract any text from the uploaded file.")

        parsed = await loop.run_in_executor(_executor, parse_script_text, raw_text)
        dialogue_count = sum(1 for l in parsed if l.get("type") == "dialogue")
        if not parsed or dialogue_count == 0:
            raise HTTPException(
                status_code=422,
                detail="No dialogue found. Make sure character names appear on their own line or are followed by a colon (e.g. 'JOHN: Hello' or 'John: Hello').",
            )

        parsed = await loop.run_in_executor(_executor, autocorrect_script, parsed)

        _parsed_script = parsed
        return parsed

    finally:
        os.unlink(tmp_path)


@app.post("/api/research")
async def research(request: ResearchRequest):
    """
    Build a ChromaDB RAG index from live internet research.
    Runs DuckDuckGo searches, scrapes top results, chunks + embeds the text.
    """
    loop = asyncio.get_running_loop()
    chunks_indexed = await loop.run_in_executor(
        _executor,
        _build_rag_index,
        request.play,
        request.character,
        request.notes,
    )
    return {
        "status": "ready",
        "play": request.play,
        "character": request.character,
        "chunks_indexed": chunks_indexed,
        "message": (
            f"Director prepared for {request.character} in {request.play} "
            f"({chunks_indexed} research passages indexed)."
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cue Master Backend Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    print("=" * 60)
    print("  Cue Master - Backend Server v2")
    print("=" * 60)
    print(f"  Upload:   http://{args.host}:{args.port}/api/upload")
    print(f"  Research: http://{args.host}:{args.port}/api/research")
    print(f"  WS:       ws://{args.host}:{args.port}/ws/rehearsal")
    print(f"  Health:   http://{args.host}:{args.port}/api/health")
    print()

    # Pre-warm PaddleOCR in a background thread so the first upload isn't slow
    import threading
    threading.Thread(target=_get_ocr_reader, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
