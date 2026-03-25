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
