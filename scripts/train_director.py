#!/usr/bin/env python3
"""
Cue Master — Step 1c: Director Model Fine-Tuning

QLoRA fine-tunes a small language model on theatrical director feedback data
using mlx-lm on Apple Silicon.

Steps:
  1. Converts data/director_training.jsonl → data/mlx_train/{train,valid}.jsonl
     in mlx-lm's expected "prompt"/"completion" format
  2. Runs QLoRA fine-tuning → saves adapter to models/director_adapter/
  3. Merges adapter with base → saves to models/director_merged/
"""

import json
import os
import random
import sys
import time

random.seed(42)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MLX_DATA_DIR = os.path.join(DATA_DIR, "mlx_train")
RAW_DATA = os.path.join(DATA_DIR, "director_training.jsonl")
ADAPTER_DIR = os.path.join(PROJECT_ROOT, "models", "director_adapter")
MERGED_DIR = os.path.join(PROJECT_ROOT, "models", "director_merged")

# Base model — small enough to fit in Mac RAM (even 8GB)
BASE_MODEL = "mlx-community/Phi-3-mini-4k-instruct-4bit"

# Training hyperparameters
NUM_ITERS = 200
BATCH_SIZE = 2
LEARNING_RATE = 1e-5
NUM_LORA_LAYERS = 8
STEPS_PER_REPORT = 10
STEPS_PER_EVAL = 50
SAVE_EVERY = 50
MAX_SEQ_LENGTH = 512
GRAD_ACCUMULATION = 2


def convert_data():
    """Convert our training data to mlx-lm's prompt/completion format and split train/valid."""
    os.makedirs(MLX_DATA_DIR, exist_ok=True)

    print("Converting training data to mlx-lm format...")

    with open(RAW_DATA, "r") as f:
        rows = [json.loads(line) for line in f]

    # Build prompt/completion pairs using a system prompt
    system_prompt = (
        "You are an expert theatrical director evaluating an actor's live performance. "
        "Given the actor's delivery compared to the expected script line, along with pacing "
        "and volume metrics, decide whether to Continue (the delivery was acceptable) or "
        "Interrupt (provide corrective feedback). Respond ONLY with valid JSON: "
        '{\"Action\": \"Continue|Interrupt\", \"Feedback\": \"your director note\"}'
    )

    converted = []
    for row in rows:
        prompt = f"<|system|>\n{system_prompt}<|end|>\n<|user|>\n{row['input']}<|end|>\n<|assistant|>\n"
        completion = row["output"] + "<|end|>"
        converted.append({"prompt": prompt, "completion": completion})

    # Shuffle and split 90/10
    random.shuffle(converted)
    split_idx = int(len(converted) * 0.9)
    train_data = converted[:split_idx]
    valid_data = converted[split_idx:]

    train_path = os.path.join(MLX_DATA_DIR, "train.jsonl")
    valid_path = os.path.join(MLX_DATA_DIR, "valid.jsonl")

    for path, data in [(train_path, train_data), (valid_path, valid_data)]:
        with open(path, "w") as f:
            for item in data:
                f.write(json.dumps(item) + "\n")

    print(f"  Train: {len(train_data)} examples → {train_path}")
    print(f"  Valid: {len(valid_data)} examples → {valid_path}")

    return train_path, valid_path


def run_training():
    """Run QLoRA fine-tuning using mlx-lm."""
    from mlx_lm.lora import run, TrainingArgs
    import types

    os.makedirs(ADAPTER_DIR, exist_ok=True)

    print(f"\nStarting QLoRA fine-tuning...")
    print(f"  Base model: {BASE_MODEL}")
    print(f"  Iterations: {NUM_ITERS}")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Learning rate: {LEARNING_RATE}")
    print(f"  LoRA layers: {NUM_LORA_LAYERS}")
    print(f"  Max seq length: {MAX_SEQ_LENGTH}")
    print()

    # Build args namespace matching what mlx-lm expects
    args = types.SimpleNamespace(
        model=BASE_MODEL,
        train=True,
        test=False,
        data=MLX_DATA_DIR,
        fine_tune_type="lora",
        optimizer="adam",
        mask_prompt=True,
        num_layers=NUM_LORA_LAYERS,
        batch_size=BATCH_SIZE,
        iters=NUM_ITERS,
        val_batches=10,
        learning_rate=LEARNING_RATE,
        steps_per_report=STEPS_PER_REPORT,
        steps_per_eval=STEPS_PER_EVAL,
        grad_accumulation_steps=GRAD_ACCUMULATION,
        resume_adapter_file=None,
        adapter_path=ADAPTER_DIR,
        save_every=SAVE_EVERY,
        max_seq_length=MAX_SEQ_LENGTH,
        config=None,
        grad_checkpoint=False,
        report_to=None,
        project_name=None,
        seed=42,
        hf_dataset=False,
        lora_parameters={"rank": 8, "dropout": 0.0, "scale": 20.0},
        lr_schedule=None,
        optimizer_config={"adam": {}},
    )

    start_time = time.time()
    run(args)
    elapsed = time.time() - start_time

    print(f"\n  Training completed in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Adapter saved to: {ADAPTER_DIR}")

    return elapsed


def merge_adapter():
    """Merge LoRA adapter with base model to create a standalone model."""
    print("\nMerging adapter with base model...")
    os.makedirs(MERGED_DIR, exist_ok=True)

    try:
        from pathlib import Path
        from mlx_lm import load as mlx_load
        from mlx_lm.fuse import save, dequantize_model
        from mlx.utils import tree_unflatten

        model, tokenizer, config = mlx_load(
            BASE_MODEL, adapter_path=ADAPTER_DIR, return_config=True
        )

        # Fuse LoRA layers into the base weights
        fused_linears = [
            (n, m.fuse(dequantize=False))
            for n, m in model.named_modules()
            if hasattr(m, "fuse")
        ]

        if fused_linears:
            model.update_modules(tree_unflatten(fused_linears))
            print(f"  Fused {len(fused_linears)} LoRA layers into base model.")

        save(
            Path(MERGED_DIR),
            BASE_MODEL,
            model,
            tokenizer,
            config,
            donate_model=False,
        )
        print(f"  Merged model saved to: {MERGED_DIR}")
        return True
    except Exception as e:
        print(f"  Warning: Fuse failed ({e}). The adapter can still be used separately.")
        print("  The adapter at models/director_adapter/ is fully functional for inference.")
        return False


def verify_model():
    """Quick sanity check: load the model and run a test inference."""
    print("\nVerifying model with test inference...")

    from mlx_lm import load, generate

    # Try merged model first, fall back to base + adapter
    try:
        if os.path.exists(os.path.join(MERGED_DIR, "config.json")):
            print(f"  Loading merged model from {MERGED_DIR}...")
            model, tokenizer = load(MERGED_DIR)
        else:
            print(f"  Loading base model + adapter...")
            model, tokenizer = load(BASE_MODEL, adapter_path=ADAPTER_DIR)
    except Exception as e:
        print(f"  Loading base model + adapter (fallback)...")
        model, tokenizer = load(BASE_MODEL, adapter_path=ADAPTER_DIR)

    test_prompt = (
        "<|system|>\nYou are an expert theatrical director evaluating an actor's live performance. "
        "Given the actor's delivery compared to the expected script line, along with pacing "
        "and volume metrics, decide whether to Continue or Interrupt. Respond ONLY with valid JSON: "
        '{\"Action\": \"Continue|Interrupt\", \"Feedback\": \"your director note\"}<|end|>\n'
        "<|user|>\nActor said: 'To be or not to be that is the question' | "
        "Expected: 'To be, or not to be, that is the question' | "
        "Pacing: 185 WPM | Volume: -18.5 dB<|end|>\n<|assistant|>\n"
    )

    print("  Running test inference...")
    response = generate(
        model,
        tokenizer,
        prompt=test_prompt,
        max_tokens=150,
        verbose=False,
    )

    print(f"\n  Test prompt: 'To be or not to be...' at 185 WPM")
    print(f"  Model response: {response}")

    # Try to parse as JSON
    try:
        # Extract JSON from response
        response_clean = response.strip()
        if response_clean.startswith("{"):
            end = response_clean.find("}") + 1
            parsed = json.loads(response_clean[:end])
            print(f"  Parsed JSON: {json.dumps(parsed, indent=2)}")
            print("  Model produces valid JSON output!")
        else:
            print("  Note: Response is not pure JSON, but model is responding.")
    except json.JSONDecodeError:
        print("  Note: Response is not valid JSON yet. Fine-tuning may need more iterations.")

    return response


def main():
    print("=" * 60)
    print("  Cue Master — Director Model Training")
    print("=" * 60)
    print()

    # Step 1: Convert data
    convert_data()

    # Step 2: Fine-tune
    elapsed = run_training()

    # Step 3: Merge
    merge_adapter()

    # Step 4: Verify
    verify_model()

    print()
    print("=" * 60)
    print("  Training Pipeline Complete!")
    print("=" * 60)
    print(f"  Training time: {elapsed:.1f}s")
    print(f"  Adapter:  {ADAPTER_DIR}")
    print(f"  Merged:   {MERGED_DIR}")
    print(f"  Data:     {MLX_DATA_DIR}")
    print()


if __name__ == "__main__":
    main()
