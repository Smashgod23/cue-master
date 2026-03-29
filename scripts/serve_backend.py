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
PIPER_BIN = os.path.join(PROJECT_ROOT, "tools", "piper", "piper")
PIPER_VOICE = os.path.join(PROJECT_ROOT, "tools", "voices", "en_US-lessac-high.onnx")
CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

app = FastAPI(title="Cue Master Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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

CHARACTER_PATTERN = re.compile(
    r"^[ \t]*([A-Z][A-Z .'-]{1,30}[A-Z])[ \t]*[:.]?[ \t]*$",
    re.MULTILINE,
)
STAGE_DIR_PATTERN = re.compile(
    r"^\s*[\[\(].*?[\]\)]\s*$|"
    r"^\s*\(.*?\)\s*$|"
    r"^\s*Enter\s|^\s*Exit\s|^\s*Exeunt",
    re.MULTILINE | re.IGNORECASE,
)


def extract_text_from_pdf(file_path: str) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def extract_text_from_image(file_path: str) -> str:
    import pytesseract
    from PIL import Image
    img = Image.open(file_path)
    return pytesseract.image_to_string(img)


def parse_script_text(raw_text: str) -> list[dict]:
    potential_characters = set()

    for match in CHARACTER_PATTERN.finditer(raw_text):
        name = match.group(1).strip().rstrip(":.")
        if len(name) >= 2:
            potential_characters.add(name)

    inline_pattern = re.compile(
        r"^[ \t]*([A-Z][A-Z .'-]{0,30}[A-Z])[ \t]*[:.][ \t]+\S",
        re.MULTILINE,
    )
    for match in inline_pattern.finditer(raw_text):
        name = match.group(1).strip()
        if len(name) >= 2:
            potential_characters.add(name)

    false_positives = {
        "ACT", "SCENE", "ACT I", "ACT II", "ACT III", "ACT IV", "ACT V",
        "PROLOGUE", "EPILOGUE", "INTERMISSION",
    }
    potential_characters -= false_positives

    lines = raw_text.split("\n")
    result = []
    current_character = None
    current_text_parts = []
    line_id = 1

    def flush_dialogue():
        nonlocal line_id, current_character, current_text_parts
        if current_character and current_text_parts:
            text = " ".join(current_text_parts).strip()
            if text:
                result.append({
                    "id": line_id,
                    "type": "dialogue",
                    "character": current_character,
                    "text": text,
                })
                line_id += 1
        current_text_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if STAGE_DIR_PATTERN.match(stripped):
            flush_dialogue()
            direction_text = stripped.strip("[]() \t")
            if direction_text:
                result.append({
                    "id": line_id,
                    "type": "stage_direction",
                    "character": "",
                    "text": direction_text,
                })
                line_id += 1
            continue

        char_inline = re.match(
            r"^[ \t]*([A-Z][A-Z .'-]{0,30}[A-Z])[ \t]*[:.][ \t]*(.*)",
            stripped,
        )
        if char_inline:
            name = char_inline.group(1).strip()
            rest = char_inline.group(2).strip()
            if name in potential_characters:
                flush_dialogue()
                current_character = name
                if rest:
                    current_text_parts.append(rest)
                continue

        upper_stripped = stripped.rstrip(":.")
        if upper_stripped in potential_characters:
            flush_dialogue()
            current_character = upper_stripped
            continue

        if current_character:
            current_text_parts.append(stripped)
        else:
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
    from duckduckgo_search import DDGS

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
    Synthesize text with piper and return WAV bytes.
    Intended to run in a thread executor.
    """
    proc = subprocess.run(
        [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Piper failed: {proc.stderr.decode()[:200]}")
    # lessac-high outputs 22050 Hz mono 16-bit PCM
    return _raw_pcm_to_wav(proc.stdout, sample_rate=22050, channels=1, bits=16)


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
            await ws.send_bytes(wav_bytes)
            # Give the client time to start playing before we advance further
            await asyncio.sleep(max(0.5, len(line["text"].split()) * 0.4))
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
        from rapidfuzz import fuzz
        score = fuzz.partial_ratio(spoken.lower(), expected.lower())

        if score >= 80:
            session.advance()
            await _cue_next(ws, session, loop)
        else:
            await ws.send_text(json.dumps({
                "event": "director_note",
                "note": (
                    f'Not quite. The line is: "{expected[:120]}" — '
                    f"try again with more confidence."
                ),
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

@app.websocket("/ws/rehearsal")
async def ws_rehearsal(websocket: WebSocket):
    await websocket.accept()
    session: Optional[RehearsalSession] = None
    loop = asyncio.get_running_loop()
    vad_model = None

    try:
        while True:
            message = await websocket.receive()

            # ---- Text frame: JSON control messages ----
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if data.get("event") == "init":
                    mode = data.get("mode", "learning")
                    character = data.get("character", "OBERON")
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

    try:
        if ext == "pdf":
            raw_text = extract_text_from_pdf(tmp_path)
        elif ext in ("png", "jpg", "jpeg"):
            raw_text = extract_text_from_image(tmp_path)
        else:
            raw_text = content.decode("utf-8", errors="replace")

        if not raw_text or not raw_text.strip():
            raise HTTPException(status_code=422, detail="Could not extract any text from the uploaded file.")

        parsed = parse_script_text(raw_text)
        if not parsed:
            raise HTTPException(
                status_code=422,
                detail="No dialogue or stage directions found. Check that your script has character names in ALL CAPS.",
            )

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

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
