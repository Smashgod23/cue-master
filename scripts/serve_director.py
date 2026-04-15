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

# Global model reference — loaded at startup
_model = None
_tokenizer = None
_model_source = None


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


CLEANUP_SYSTEM_PROMPT = (
    "Fix OCR letter-swap errors in a single script line. Only correct "
    "obvious misreadings where one letter was mistaken for another "
    "(c<->e, l<->I, O<->0, n<->h, t<->f, u<->n). Never add new words, "
    "never rephrase, never explain. Keep the same number of words and "
    "the same sentence structure. If you are not confident, return the "
    "line unchanged. Output ONLY the corrected line with no prefix, "
    "quotes, or commentary.\n\n"
    "Examples:\n"
    "Input: I SCC.\nOutput: I see.\n"
    "Input: Thcrc, there, lamb.\nOutput: There, there, lamb.\n"
    "Input: Hc's hcfc.\nOutput: He's here.\n"
    "Input: I ncvcf called anyone.\nOutput: I never called anyone.\n"
    "Input: Cienches his fsts.\nOutput: Clenches his fists.\n"
    "Input: Hello there.\nOutput: Hello there."
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


@app.post("/api/director/cleanup", response_model=CleanupResponse)
async def cleanup(request: CleanupRequest):
    """
    Fix OCR errors in a batch of script lines. Runs one generation per
    line (small max_tokens) so the cost scales with the number of
    actually-damaged lines the backend flags, not the whole script.
    Everything runs locally — no API calls, no cost.
    """
    from mlx_lm import generate

    cleaned: list[str] = []
    for raw_line in request.lines:
        trimmed = (raw_line or "").strip()
        if not trimmed:
            cleaned.append(raw_line)
            continue

        prompt = (
            f"<|system|>\n{CLEANUP_SYSTEM_PROMPT}<|end|>\n"
            f"<|user|>\n{trimmed}<|end|>\n"
            f"<|assistant|>\n"
        )

        raw = generate(
            _model,
            _tokenizer,
            prompt=prompt,
            max_tokens=min(200, len(trimmed.split()) * 4 + 20),
            verbose=False,
        )

        fixed = raw.strip()
        for token in ["<|end|>", "<|endoftext|>", "</s>", "<unk>"]:
            fixed = fixed.split(token)[0]
        # Take only the first line — examples in the prompt can cause the
        # model to keep generating Input/Output pairs.
        fixed = fixed.split("\n")[0].strip().strip('"').strip("'").strip()
        # Strip any "Output:" prefix the model sometimes echoes.
        if fixed.lower().startswith("output:"):
            fixed = fixed[7:].strip()

        if not fixed or fixed.startswith("{"):
            cleaned.append(raw_line)
            continue

        # Guard against hallucinations: fine-tuned director drifts toward
        # rewriting content. Enforce that every output word is either
        # present verbatim in the input or within edit distance 2 of some
        # input word (the letter-swap limit for OCR cleanup).
        import re as _re
        in_tokens_raw = _re.findall(r"[A-Za-z']+", trimmed)
        in_tokens = [t.lower() for t in in_tokens_raw]
        out_tokens = _re.findall(r"[A-Za-z']+", fixed.lower())
        if abs(len(out_tokens) - len(in_tokens)) > 1:
            cleaned.append(raw_line)
            continue
        if abs(len(fixed) - len(trimmed)) > max(6, len(trimmed) // 4):
            cleaned.append(raw_line)
            continue

        try:
            from rapidfuzz.distance import Levenshtein as _lev
        except ImportError:
            _lev = None

        def _is_damaged(tok: str) -> bool:
            if len(tok) < 3:
                return False
            # Vowel-less token of 3+ letters is almost always OCR damage
            # ("scc", "fsts", "ncvcf"). Short words like "mrs"/"dr" are
            # preserved by the model itself (it rarely rewrites them).
            if not _re.search(r'[aeiouy]', tok):
                return True
            if _re.search(r'[a-z][A-Z][a-z]', tok):
                return True
            return False

        reject = False
        if _lev is not None and in_tokens:
            in_set = set(in_tokens)
            for w in out_tokens:
                if w in in_set:
                    continue
                # Find nearest input word; allow a larger edit budget only
                # when that input word itself looks OCR-damaged. A clean
                # word like "wears" being rewritten to "wore" (distance 3)
                # should be rejected, but a garbled word like "ncvcf" being
                # rewritten to "never" (distance 3) should pass. Damage
                # detection uses the original-case token so that mixed-case
                # OCR artifacts ("cQllapses") are recognized.
                nearest_idx, nearest_dist = min(
                    enumerate(_lev.distance(w, iw) for iw in in_tokens),
                    key=lambda p: p[1],
                )
                budget = 3 if _is_damaged(in_tokens_raw[nearest_idx]) else 1
                if nearest_dist > budget:
                    reject = True
                    break

        if reject:
            cleaned.append(raw_line)
            continue
        cleaned.append(fixed)

    return CleanupResponse(cleaned=cleaned)


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
