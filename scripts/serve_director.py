#!/usr/bin/env python3
"""
Cue Master — Step 1d: Director Model Server

Loads the fine-tuned Director model and serves it via a local REST endpoint.
Accepts actor performance data and returns Continue/Interrupt decisions with feedback.

Endpoint:
  POST /api/director
  Body: { "input": "Actor said: '...' | Expected: '...' | Pacing: XX WPM | Volume: XX dB" }
  Response: { "Action": "Continue|Interrupt", "Feedback": "..." }

  GET /api/director/health
  Response: { "status": "ok", "model": "..." }

Usage:
  python scripts/serve_director.py [--port 8001]
"""

import argparse
import json
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MERGED_DIR = os.path.join(PROJECT_ROOT, "models", "director_merged")
ADAPTER_DIR = os.path.join(PROJECT_ROOT, "models", "director_adapter")
BASE_MODEL = "mlx-community/Phi-3-mini-4k-instruct-4bit"

# System prompt matching training format
SYSTEM_PROMPT = (
    "You are an expert theatrical director evaluating an actor's live performance. "
    "Given the actor's delivery compared to the expected script line, along with pacing "
    "and volume metrics, decide whether to Continue (the delivery was acceptable) or "
    "Interrupt (provide corrective feedback). Respond ONLY with valid JSON: "
    '{"Action": "Continue|Interrupt", "Feedback": "your director note"}'
)

@asynccontextmanager
async def lifespan(app):
    load_model()
    yield


app = FastAPI(title="Cue Master Director API", version="1.0.0", lifespan=lifespan)

# Fine-tuned director model (loaded at startup, used for Action/Feedback in Performance mode)
_model = None
_tokenizer = None
_model_source = None

# Base instruction-tuned Phi-3 (lazy-loaded on first cleanup/classify call, used
# for OCR cleanup and line-type classification during script upload). The
# fine-tuned director has drifted so far toward the Action/Feedback JSON
# format that it cannot follow OCR-cleanup or classification instructions;
# the un-fine-tuned base model performs these tasks reliably.
_cleanup_model = None
_cleanup_tokenizer = None


class DirectorRequest(BaseModel):
    input: str
    context: str = ""  # optional RAG context


class DirectorResponse(BaseModel):
    Action: str
    Feedback: str


class CleanupRequest(BaseModel):
    lines: list[str]


class CleanupResponse(BaseModel):
    cleaned: list[str]


class ClassifyRequest(BaseModel):
    lines: list[str]


class ClassifyResponse(BaseModel):
    # Each item is one of "dialogue", "stage_direction", or "mixed".
    # When "mixed", the caller can send the line back to /cleanup with a split
    # prompt; for now we just tell the caller what the line looks like.
    labels: list[str]


CLEANUP_SYSTEM_PROMPT = (
    "You are an OCR correction assistant for theatrical scripts. Fix obvious "
    "OCR letter-swap errors only (c<->e, l<->I, n<->h, t<->f, u<->n, O<->0, "
    "etc.). Do not add words, do not remove words, do not rephrase, and do "
    "not explain. If a line is already clean, return it verbatim. Output "
    "ONLY the corrected line, nothing else."
)


CLASSIFY_SYSTEM_PROMPT = (
    "You classify a single line of a theatrical script. Reply with exactly "
    "one word: DIALOGUE if it is something a character says out loud, "
    "STAGE_DIRECTION if it is an action, setting, or instruction that is "
    "never spoken, or MIXED if the line contains both a stage direction and "
    "spoken dialogue. No other output."
)


def load_model():
    """Load the fine-tuned model (merged or base+adapter)."""
    global _model, _tokenizer, _model_source
    from mlx_lm import load

    # Prefer merged model, fall back to base + adapter
    if os.path.exists(os.path.join(MERGED_DIR, "config.json")):
        print(f"Loading merged model from {MERGED_DIR}...")
        _model, _tokenizer = load(MERGED_DIR)
        _model_source = MERGED_DIR
    elif os.path.exists(os.path.join(ADAPTER_DIR, "adapters.safetensors")):
        print(f"Loading base model + adapter...")
        _model, _tokenizer = load(BASE_MODEL, adapter_path=ADAPTER_DIR)
        _model_source = f"{BASE_MODEL} + adapter"
    else:
        print(f"No fine-tuned model found. Loading base model only...")
        _model, _tokenizer = load(BASE_MODEL)
        _model_source = BASE_MODEL

    print(f"Model loaded: {_model_source}")


def _get_cleanup_model():
    """
    Lazy-load the un-fine-tuned base Phi-3 model for OCR cleanup and line
    classification. The fine-tuned director has drifted so far toward the
    JSON Action/Feedback format that it cannot reliably follow simple
    instructions; the base instruction-tuned model handles them correctly.
    """
    global _cleanup_model, _cleanup_tokenizer
    if _cleanup_model is None:
        from mlx_lm import load
        print(f"Loading base cleanup model {BASE_MODEL}...")
        _cleanup_model, _cleanup_tokenizer = load(BASE_MODEL)
        print("Base cleanup model loaded.")
    return _cleanup_model, _cleanup_tokenizer


def build_prompt(user_input: str, context: str = "") -> str:
    """Build the chat prompt in Phi-3 format."""
    system = SYSTEM_PROMPT
    if context:
        system += f"\n\nDramaturgical Context:\n{context}"

    return (
        f"<|system|>\n{system}<|end|>\n"
        f"<|user|>\n{user_input}<|end|>\n"
        f"<|assistant|>\n"
    )


def parse_director_response(raw: str) -> dict:
    """Extract valid JSON from model output, handling potential extra text."""
    raw = raw.strip()

    # Remove end tokens
    for token in ["<|end|>", "<|endoftext|>", "</s>", "<unk>"]:
        raw = raw.split(token)[0]

    raw = raw.strip()

    # Try direct JSON parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass

    # Fallback: return a Continue with the raw text as feedback
    return {"Action": "Continue", "Feedback": raw[:200] if raw else "No feedback generated."}


@app.get("/api/director/health")
async def health():
    return {
        "status": "ok" if _model is not None else "loading",
        "model": _model_source or "not loaded",
    }


def _strip_generation(raw: str) -> str:
    """Trim end-tokens, quotes, and common prefixes from a generation."""
    out = raw.strip()
    for token in ("<|end|>", "<|endoftext|>", "</s>", "<unk>", "<|user|>", "<|assistant|>"):
        out = out.split(token)[0]
    # Only take the first line — the few-shot prompt can make the model
    # keep generating additional Input/Output pairs.
    out = out.split("\n")[0].strip()
    # Strip common leading markers.
    for prefix in ("Output:", "Corrected:", "Answer:"):
        if out.lower().startswith(prefix.lower()):
            out = out[len(prefix):].strip()
    # Trim surrounding quotes.
    out = out.strip().strip('"').strip("'").strip()
    return out


def _cleanup_one_line(line: str) -> str:
    """
    Run a single OCR cleanup generation on one line. Returns the cleaned
    text, or the original line when the model's output fails sanity
    checks (extra words, hallucinated rephrasing, empty output, etc.).
    """
    from mlx_lm import generate
    import re as _re

    trimmed = (line or "").strip()
    if not trimmed:
        return line

    model, tokenizer = _get_cleanup_model()

    # Few-shot prompt in the user turn so the assistant doesn't get trapped
    # completing an "Output:" marker from the prior turn (which caused the
    # old fine-tuned endpoint to often return blank strings).
    user_prompt = (
        "Fix the OCR errors in this line. Return ONLY the corrected line, "
        "with no prefix, quotes, or commentary.\n\n"
        "Examples:\n"
        "  Hc's hcfc. -> He's here.\n"
        "  I SCC you. -> I see you.\n"
        "  I ncvcf callcd. -> I never called.\n"
        "  Don't worry, dear. -> Don't worry, dear.\n\n"
        f"Line: {trimmed}\nCorrected:"
    )
    prompt = (
        f"<|system|>\n{CLEANUP_SYSTEM_PROMPT}<|end|>\n"
        f"<|user|>\n{user_prompt}<|end|>\n"
        f"<|assistant|>\n"
    )

    raw = generate(
        model, tokenizer,
        prompt=prompt,
        max_tokens=min(220, len(trimmed.split()) * 4 + 20),
        verbose=False,
    )
    fixed = _strip_generation(raw)

    if not fixed or fixed.startswith("{"):
        return line

    # Hallucination guards. Word count tolerance of ±1 (was ±1 before, keep
    # same — but the backend-side caller tolerates it differently now).
    in_tokens_raw = _re.findall(r"[A-Za-z']+", trimmed)
    out_tokens = _re.findall(r"[A-Za-z']+", fixed)
    if abs(len(out_tokens) - len(in_tokens_raw)) > 1:
        return line
    if abs(len(fixed) - len(trimmed)) > max(8, len(trimmed) // 3):
        return line

    try:
        from rapidfuzz.distance import Levenshtein as _lev
    except ImportError:
        _lev = None

    def _is_damaged(tok: str) -> bool:
        if len(tok) < 3:
            return False
        if not _re.search(r'[aeiouy]', tok):
            return True
        if _re.search(r'[a-z][A-Z][a-z]', tok):
            return True
        return False

    if _lev is not None and in_tokens_raw:
        in_tokens = [t.lower() for t in in_tokens_raw]
        in_set = set(in_tokens)
        for w in (t.lower() for t in out_tokens):
            if w in in_set:
                continue
            nearest_idx, nearest_dist = min(
                enumerate(_lev.distance(w, iw) for iw in in_tokens),
                key=lambda p: p[1],
            )
            budget = 3 if _is_damaged(in_tokens_raw[nearest_idx]) else 1
            if nearest_dist > budget:
                return line
    return fixed


@app.post("/api/director/cleanup", response_model=CleanupResponse)
async def cleanup(request: CleanupRequest):
    """
    Fix OCR errors in a batch of script lines using the base Phi-3 model.
    Runs one generation per line; cost scales with the flagged-line count,
    not the whole script. Everything runs locally — no API, no cost.
    """
    cleaned = [_cleanup_one_line(ln) for ln in request.lines]
    return CleanupResponse(cleaned=cleaned)


@app.post("/api/director/classify", response_model=ClassifyResponse)
async def classify(request: ClassifyRequest):
    """
    Decide whether each line is dialogue, a stage direction, or a mixed
    line containing both. Used by the backend to rescue lines where OCR
    damaged the brackets so badly that the regex classifier misread them
    (e.g. a line that LOOKS like dialogue but is really "[GRANDMA enters
    slowly.]" with a mangled closing bracket).

    Runs on the base Phi-3 model — fine-tuned director is too specialised
    for JSON Action/Feedback to follow simple classification instructions.
    """
    from mlx_lm import generate

    model, tokenizer = _get_cleanup_model()
    labels: list[str] = []

    for raw_line in request.lines:
        trimmed = (raw_line or "").strip()
        if not trimmed:
            labels.append("dialogue")
            continue

        user_prompt = (
            "Classify this script line. Reply with exactly one of: "
            "DIALOGUE, STAGE_DIRECTION, or MIXED.\n\n"
            "DIALOGUE = something a character speaks out loud.\n"
            "STAGE_DIRECTION = an action, setting, or instruction that is "
            "never spoken (stage movement, entrance/exit, description of "
            "scenery, costume, or sound cues).\n"
            "MIXED = one line that contains BOTH a stage direction and "
            "spoken dialogue (e.g. \"[smiling] Don't worry, dear.\").\n\n"
            "Examples:\n"
            "  Don't worry, dear. -> DIALOGUE\n"
            "  [ALICE enters from stage left.] -> STAGE_DIRECTION\n"
            "  Grandma sits in the easy chair. -> STAGE_DIRECTION\n"
            "  [smiling] Don't worry, dear. -> MIXED\n"
            "  Picks up the cane and studies it thoughtfully. -> STAGE_DIRECTION\n\n"
            f"Line: {trimmed}\nLabel:"
        )
        prompt = (
            f"<|system|>\n{CLASSIFY_SYSTEM_PROMPT}<|end|>\n"
            f"<|user|>\n{user_prompt}<|end|>\n"
            f"<|assistant|>\n"
        )

        raw = generate(
            model, tokenizer,
            prompt=prompt,
            max_tokens=10,
            verbose=False,
        )
        out = _strip_generation(raw).upper()
        # Map the token the model emits to one of the three canonical labels.
        if out.startswith("MIXED"):
            labels.append("mixed")
        elif out.startswith("STAGE"):
            labels.append("stage_direction")
        elif out.startswith("DIALOG"):
            labels.append("dialogue")
        else:
            # Unparseable — default to keeping whatever the caller had.
            labels.append("dialogue")

    return ClassifyResponse(labels=labels)


@app.post("/api/director", response_model=DirectorResponse)
async def evaluate(request: DirectorRequest):
    """Evaluate an actor's line delivery and return director feedback."""
    from mlx_lm import generate

    prompt = build_prompt(request.input, request.context)

    start = time.time()
    raw_response = generate(
        _model,
        _tokenizer,
        prompt=prompt,
        max_tokens=150,
        verbose=False,
    )
    elapsed = time.time() - start

    result = parse_director_response(raw_response)

    # Validate Action field
    if result.get("Action") not in ("Continue", "Interrupt"):
        result["Action"] = "Continue"

    print(f"  [{elapsed:.2f}s] {result['Action']}: {result['Feedback'][:80]}...")

    return DirectorResponse(**result)


def main():
    parser = argparse.ArgumentParser(description="Cue Master Director Model Server")
    parser.add_argument("--port", type=int, default=8001, help="Port to serve on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    print("=" * 60)
    print("  Cue Master — Director Model Server")
    print("=" * 60)
    print(f"  Endpoint: http://{args.host}:{args.port}/api/director")
    print(f"  Health:   http://{args.host}:{args.port}/api/director/health")
    print()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
