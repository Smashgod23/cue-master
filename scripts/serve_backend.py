#!/usr/bin/env python3
"""
Cue Master - Main Backend Server (port 8000)

Handles script upload/parsing, character setup, and will later host
the WebSocket rehearsal pipeline. The Director model runs separately
on port 8001 via serve_director.py.

Endpoints:
  POST /api/upload    - Upload and parse a script file (PDF, TXT, PNG, JPG)
  POST /api/research  - Placeholder for RAG index build (Step 4)
  GET  /api/health    - Health check

Usage:
  python scripts/serve_backend.py [--port 8000]
"""

import argparse
import os
import re
import tempfile

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

app = FastAPI(title="Cue Master Backend", version="1.0.0")

# Allow the Vite dev server to reach the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Server-side session state for the parsed script
_parsed_script = None


# ---- Models ----

class ScriptLine(BaseModel):
    id: int
    type: str  # "dialogue" or "stage_direction"
    character: str
    text: str


class ResearchRequest(BaseModel):
    play: str
    character: str
    notes: str = ""


# ---- Script Parsing ----

def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF using pdfplumber."""
    import pdfplumber

    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def extract_text_from_image(file_path: str) -> str:
    """Run OCR on an image file using pytesseract."""
    import pytesseract
    from PIL import Image

    img = Image.open(file_path)
    return pytesseract.image_to_string(img)


# Patterns for detecting character names in scripts
# Matches lines like "HAMLET:" or "HAMLET." or all-caps name on its own line
CHARACTER_PATTERN = re.compile(
    r"^[ \t]*([A-Z][A-Z .'-]{1,30}[A-Z])[ \t]*[:.]?[ \t]*$",
    re.MULTILINE,
)

# Stage direction markers
STAGE_DIR_PATTERN = re.compile(
    r"^\s*[\[\(].*?[\]\)]\s*$|"       # [Enter HAMLET] or (He exits)
    r"^\s*\(.*?\)\s*$|"                # (aside)
    r"^\s*Enter\s|^\s*Exit\s|^\s*Exeunt",  # Enter/Exit/Exeunt at line start
    re.MULTILINE | re.IGNORECASE,
)


def parse_script_text(raw_text: str) -> list[dict]:
    """
    Parse raw script text into structured lines.

    Heuristic approach:
    1. Scan for all-caps words followed by colons or on their own line to build
       a set of character names.
    2. Lines starting with CHARACTER_NAME: are treated as dialogue.
    3. Bracketed lines and Enter/Exit/Exeunt lines are stage directions.
    4. Continuation lines (no character prefix) are appended to the previous
       character's dialogue.
    """
    # First pass: collect character names from both standalone lines and
    # inline "NAME: dialogue" patterns
    potential_characters = set()

    # Standalone all-caps names on their own line
    for match in CHARACTER_PATTERN.finditer(raw_text):
        name = match.group(1).strip().rstrip(":.")
        if len(name) >= 2:
            potential_characters.add(name)

    # Inline pattern: "NAME: some text" or "NAME. some text"
    inline_pattern = re.compile(
        r"^[ \t]*([A-Z][A-Z .'-]{0,30}[A-Z])[ \t]*[:.][ \t]+\S",
        re.MULTILINE,
    )
    for match in inline_pattern.finditer(raw_text):
        name = match.group(1).strip()
        if len(name) >= 2:
            potential_characters.add(name)

    # Remove common false positives
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

        # Check if this line is a stage direction
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

        # Check for "CHARACTER: dialogue text" on the same line
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

        # Check if the whole line is just a character name on its own
        upper_stripped = stripped.rstrip(":.")
        if upper_stripped in potential_characters:
            flush_dialogue()
            current_character = upper_stripped
            continue

        # Continuation of current character's dialogue
        if current_character:
            current_text_parts.append(stripped)
        else:
            # No character context yet; treat as stage direction or narration
            result.append({
                "id": line_id,
                "type": "stage_direction",
                "character": "",
                "text": stripped,
            })
            line_id += 1

    flush_dialogue()

    return result


# ---- Endpoints ----

@app.get("/api/health")
async def health():
    return {"status": "ok", "parsed_lines": len(_parsed_script) if _parsed_script else 0}


@app.post("/api/upload")
async def upload_script(file: UploadFile = File(...)):
    """Accept a script file, parse it, and return structured lines."""
    global _parsed_script

    # Validate file extension
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = {"pdf", "txt", "png", "jpg", "jpeg"}

    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type (.{ext}). Accepted: {', '.join(sorted(allowed))}",
        )

    # Save to a temp file for processing
    suffix = f".{ext}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Extract raw text based on file type
        if ext == "pdf":
            raw_text = extract_text_from_pdf(tmp_path)
        elif ext in ("png", "jpg", "jpeg"):
            raw_text = extract_text_from_image(tmp_path)
        else:
            raw_text = content.decode("utf-8", errors="replace")

        if not raw_text or not raw_text.strip():
            raise HTTPException(
                status_code=422,
                detail="Could not extract any text from the uploaded file.",
            )

        # Parse the text into structured script lines
        parsed = parse_script_text(raw_text)

        if not parsed:
            raise HTTPException(
                status_code=422,
                detail="No dialogue or stage directions found. Check that your script has character names in ALL CAPS.",
            )

        # Store server-side
        _parsed_script = parsed

        return parsed

    finally:
        os.unlink(tmp_path)


@app.post("/api/research")
async def research(request: ResearchRequest):
    """
    Placeholder for Step 4: Internet research + RAG index build.
    For now, accepts the request and returns success so the frontend flow works.
    """
    return {
        "status": "ready",
        "play": request.play,
        "character": request.character,
        "message": f"Director is prepared for {request.character} in {request.play}.",
    }


def main():
    parser = argparse.ArgumentParser(description="Cue Master Backend Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    print("=" * 60)
    print("  Cue Master - Backend Server")
    print("=" * 60)
    print(f"  Upload:   http://{args.host}:{args.port}/api/upload")
    print(f"  Research: http://{args.host}:{args.port}/api/research")
    print(f"  Health:   http://{args.host}:{args.port}/api/health")
    print()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
