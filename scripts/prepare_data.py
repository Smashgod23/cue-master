#!/usr/bin/env python3
"""
Cue Master — Step 1b: Training Data Pipeline (v2)

Builds high-quality director training data using three real sources:

  1. Project Gutenberg plays — real dialogue lines with character names,
     stage directions, and dramatic context parsed from full play texts.

  2. Acting/coaching content — real directorial language scraped from
     public acting resources via DuckDuckGo search.

  3. Self-instruct generation — the base Phi-3 model generates diverse,
     context-aware director feedback for each scene scenario. This uses
     the model's pretraining knowledge of theater to produce varied notes
     that go far beyond templated slot-filling.

Output: data/director_training.jsonl
Each row: {"input": "Actor said: '...' | Expected: '...' | ...", "output": '{"Action": "...", "Feedback": "..."}'}
"""

import json
import os
import random
import re
import sys
import time
import textwrap

random.seed(42)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "director_training.jsonl")
GUTENBERG_CACHE = os.path.join(DATA_DIR, "gutenberg_cache")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(GUTENBERG_CACHE, exist_ok=True)

# ── Project Gutenberg play IDs and metadata ───────────────────────────────────
# Each entry: (gutenberg_id, title, playwright)
# These are confirmed public-domain plays available as plain text.
GUTENBERG_PLAYS = [
    (1524, "Hamlet", "Shakespeare"),
    (1533, "Macbeth", "Shakespeare"),
    (1531, "The Merchant of Venice", "Shakespeare"),
    (1514, "A Midsummer Night's Dream", "Shakespeare"),
    (1513, "Romeo and Juliet", "Shakespeare"),
    (1532, "King Lear", "Shakespeare"),
    (1526, "Julius Caesar", "Shakespeare"),
    (1534, "Othello", "Shakespeare"),
    (1519, "The Tempest", "Shakespeare"),
    (1521, "Twelfth Night", "Shakespeare"),
    (2000, "The Importance of Being Earnest", "Wilde"),
    (844, "An Ideal Husband", "Wilde"),
    (885, "Lady Windermere's Fan", "Wilde"),
    (790, "A Woman of No Importance", "Wilde"),
    (4025, "A Doll's House", "Ibsen"),
    (2296, "Hedda Gabler", "Ibsen"),
    (4023, "An Enemy of the People", "Ibsen"),
    (1064, "The Cherry Orchard", "Chekhov"),
    (7986, "Three Sisters", "Chekhov"),
    (1756, "The Importance of Being Earnest", "Wilde"),  # alternate
]


def download_gutenberg_text(gid):
    """Download a Project Gutenberg text by ID, with local caching."""
    import urllib.request

    cache_path = os.path.join(GUTENBERG_CACHE, f"{gid}.txt")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    # Try multiple Gutenberg URL formats
    urls = [
        f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt",
        f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt",
        f"https://www.gutenberg.org/files/{gid}/{gid}.txt",
    ]

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CueMaster/1.0 (training data)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(text)
                return text
        except Exception:
            continue

    return None


def parse_play_lines(text, playwright):
    """Parse a Gutenberg play text into structured dialogue lines.

    Detects character names (ALL CAPS at start of line, or followed by a period/colon)
    and extracts their dialogue. Also captures stage directions in brackets/parens.
    """
    lines = text.split("\n")
    parsed = []

    # Skip Gutenberg header/footer
    start_idx = 0
    end_idx = len(lines)
    for i, line in enumerate(lines):
        if "*** START OF" in line.upper() or "***START OF" in line.upper():
            start_idx = i + 1
        if "*** END OF" in line.upper() or "***END OF" in line.upper():
            end_idx = i
            break

    lines = lines[start_idx:end_idx]

    # Character name patterns
    # Shakespeare format: "HAMLET. To be, or not to be..." or "HAMLET:" or just "HAMLET" on its own line
    char_pattern = re.compile(r"^([A-Z][A-Z\s]{1,25}?)[\.\:\s](.+)$")
    char_solo_pattern = re.compile(r"^([A-Z][A-Z\s]{1,25}?)\s*$")
    stage_dir_pattern = re.compile(r"^\s*[\[\(](.+?)[\]\)]\s*$")

    current_char = None
    current_speech = []

    def flush_speech():
        nonlocal current_char, current_speech
        if current_char and current_speech:
            speech = " ".join(current_speech).strip()
            # Only keep speeches of reasonable length
            if 20 <= len(speech) <= 500:
                parsed.append({
                    "character": current_char.strip().title(),
                    "text": speech,
                    "type": "dialogue",
                })
        current_speech = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Stage direction
        sd_match = stage_dir_pattern.match(line)
        if sd_match:
            direction = sd_match.group(1).strip()
            if len(direction) > 15:
                parsed.append({
                    "character": None,
                    "text": direction,
                    "type": "stage_direction",
                })
            continue

        # Character name with dialogue on same line
        char_match = char_pattern.match(line)
        if char_match:
            name = char_match.group(1).strip()
            # Filter out act/scene headers, common non-character words
            skip_words = {"ACT", "SCENE", "ENTER", "EXIT", "EXEUNT", "THE", "PART", "PROLOGUE", "EPILOGUE"}
            if name not in skip_words and len(name) > 1 and not any(w in name for w in skip_words):
                flush_speech()
                current_char = name
                dialogue = char_match.group(2).strip()
                if dialogue:
                    current_speech.append(dialogue)
                continue

        # Character name alone on a line
        solo_match = char_solo_pattern.match(line)
        if solo_match:
            name = solo_match.group(1).strip()
            skip_words = {"ACT", "SCENE", "ENTER", "EXIT", "EXEUNT", "THE", "PART", "PROLOGUE", "EPILOGUE"}
            if name not in skip_words and len(name) > 1 and not any(w in name for w in skip_words):
                flush_speech()
                current_char = name
                continue

        # Continuation of current speech
        if current_char and line:
            current_speech.append(line)

    flush_speech()
    return parsed


def scrape_acting_vocabulary():
    """Scrape real directorial/coaching language from the web using DuckDuckGo."""
    coaching_phrases = []

    try:
        from duckduckgo_search import DDGS

        queries = [
            "theater director giving notes to actors examples",
            "acting coach feedback common notes rehearsal",
            "director notes for actors pacing volume emotion",
            "drama school voice projection feedback critique",
            "stage director rehearsal interruptions technique notes",
            "Stanislavski method acting objectives feedback",
            "Shakespeare performance coaching diction verse speaking",
        ]

        with DDGS() as ddgs:
            for query in queries:
                try:
                    results = ddgs.text(query, max_results=5)
                    for r in results:
                        body = r.get("body", "")
                        if body and len(body) > 30:
                            # Extract useful phrases - sentences about acting/directing
                            sentences = re.split(r'[.!?]+', body)
                            for s in sentences:
                                s = s.strip()
                                if 20 < len(s) < 300:
                                    coaching_phrases.append(s)
                    time.sleep(0.5)  # rate limit
                except Exception:
                    continue

    except ImportError:
        print("  duckduckgo-search not available, skipping web scrape.")
    except Exception as e:
        print(f"  Web scrape warning: {e}")

    return coaching_phrases


def generate_delivery_variant(text, accuracy="accurate"):
    """Generate a variant of the expected line simulating actor delivery.

    accuracy: "accurate" (good delivery), "close" (minor mistakes), "wrong" (significant errors)
    """
    if accuracy == "accurate":
        # Return mostly the same, with very minor natural speech variations
        if random.random() < 0.6:
            return text
        # Drop some punctuation (people don't speak with visible commas)
        return text.replace(",", "").replace(";", "").replace(":", "")

    elif accuracy == "close":
        words = text.split()
        if len(words) < 3:
            return text
        mutation = random.choice(["synonym", "drop_word", "filler", "contract"])
        if mutation == "drop_word" and len(words) > 4:
            idx = random.randint(1, len(words) - 2)
            words.pop(idx)
        elif mutation == "filler":
            idx = random.randint(1, len(words) - 1)
            words.insert(idx, random.choice(["um", "uh", "like"]))
        elif mutation == "contract":
            text = text.replace("it is", "it's").replace("do not", "don't").replace("I am", "I'm")
            return text
        return " ".join(words)

    else:  # wrong
        words = text.split()
        if len(words) < 4:
            return "I... I don't remember the line"
        # Significant deviation
        mutation = random.choice(["truncate", "scramble", "substitute_many", "wrong_line"])
        if mutation == "truncate":
            cut = max(2, len(words) // 3)
            return " ".join(words[:cut]) + "... um..."
        elif mutation == "scramble":
            segment = words[:]
            random.shuffle(segment)
            return " ".join(segment[:len(words)])
        elif mutation == "substitute_many":
            for _ in range(min(3, len(words) // 2)):
                idx = random.randint(0, len(words) - 1)
                words[idx] = random.choice(["something", "the", "uh", "that", "what", "so"])
            return " ".join(words)
        else:
            return "Wait, what's my line again?"


def self_instruct_generate_feedback(model, tokenizer, scenarios):
    """Use the base model to generate diverse director feedback for each scenario.

    This is the core improvement: instead of templated feedback, we ask the model
    to produce contextual, varied director notes using its pretraining knowledge.
    """
    from mlx_lm import generate

    results = []
    total = len(scenarios)

    for i, scenario in enumerate(scenarios):
        if (i + 1) % 20 == 0:
            print(f"  Self-instruct generation: {i + 1}/{total}...", flush=True)

        character = scenario.get("character", "the actor")
        expected_line = scenario["expected"]
        actual_line = scenario["actual"]
        wpm = scenario["wpm"]
        vol = scenario["vol"]
        play = scenario.get("play", "the play")
        scene_context = scenario.get("context", "")
        desired_action = scenario["action"]

        # Build a rich prompt that leverages the model's theater knowledge
        if desired_action == "Continue":
            instruction = (
                f"You are an experienced theater director in rehearsal for {play}. "
                f"The actor playing {character} just delivered this line:\n"
                f'  "{actual_line}"\n'
                f"The expected line was:\n"
                f'  "{expected_line}"\n'
                f"Their pacing was {wpm} WPM and volume was {vol} dB (both acceptable range).\n"
            )
            if scene_context:
                instruction += f"Scene context: {scene_context}\n"
            instruction += (
                "The delivery was good. Write a brief, specific director note (1-2 sentences) "
                "acknowledging what worked. Be concrete about what was effective "
                "(rhythm, emphasis, emotional tone, breath control, etc). "
                "Do not be generic. Respond with ONLY the feedback text, nothing else."
            )
        else:  # Interrupt
            issue = scenario.get("issue", "general")
            instruction = (
                f"You are an experienced theater director in rehearsal for {play}. "
                f"The actor playing {character} just delivered this line:\n"
                f'  "{actual_line}"\n'
                f"The expected line was:\n"
                f'  "{expected_line}"\n'
                f"Their pacing was {wpm} WPM and volume was {vol} dB.\n"
            )
            if scene_context:
                instruction += f"Scene context: {scene_context}\n"

            issue_prompts = {
                "pacing": f"The pacing at {wpm} WPM is {'too fast — they are rushing' if wpm > 160 else 'too slow — they are dragging'}. ",
                "volume": f"The volume at {vol} dB is {'too quiet — they cannot be heard' if vol < -25 else 'too loud — they are shouting'}. ",
                "accuracy": f"They got the words wrong. The actual delivery deviates from the script. ",
                "emotion": "The emotional delivery doesn't match what the scene demands. ",
                "technique": "There's a technical issue with their delivery (diction, breath, inflection). ",
                "general": "Something about the delivery needs correction. ",
            }
            instruction += issue_prompts.get(issue, issue_prompts["general"])
            instruction += (
                "Write a brief, specific director note (1-3 sentences) giving corrective feedback. "
                "Be direct but constructive. Reference the specific text and what needs to change. "
                "Respond with ONLY the feedback text, nothing else."
            )

        prompt = f"<|system|>\nYou are a helpful assistant.<|end|>\n<|user|>\n{instruction}<|end|>\n<|assistant|>\n"

        try:
            response = generate(
                model, tokenizer,
                prompt=prompt,
                max_tokens=120,
                verbose=False,
            )
            # Clean up response
            feedback = response.strip()
            for token in ["<|end|>", "<|endoftext|>", "</s>"]:
                feedback = feedback.split(token)[0]
            feedback = feedback.strip().strip('"').strip()

            # Validate it's usable
            if len(feedback) > 15 and len(feedback) < 500:
                input_text = (
                    f"Actor said: '{actual_line}' | "
                    f"Expected: '{expected_line}' | "
                    f"Pacing: {wpm} WPM | "
                    f"Volume: {vol} dB"
                )
                output_text = json.dumps({
                    "Action": desired_action,
                    "Feedback": feedback
                })
                results.append({"input": input_text, "output": output_text})
        except Exception as e:
            # Skip failed generations silently
            continue

    return results


def build_scenarios_from_plays(play_lines, play_metadata):
    """Build training scenarios from parsed play data.

    For each dialogue line, creates multiple scenarios varying accuracy,
    pacing, volume, and issue type. Includes scene context from nearby
    stage directions.
    """
    scenarios = []

    for play_meta, lines in zip(play_metadata, play_lines):
        title = play_meta[1]
        playwright = play_meta[2]

        # Build a map of nearby stage directions for context
        for i, line in enumerate(lines):
            if line["type"] != "dialogue":
                continue

            character = line["character"]
            text = line["text"]

            # Grab nearby stage direction for context
            context = ""
            for j in range(max(0, i - 3), i):
                if lines[j]["type"] == "stage_direction":
                    context = lines[j]["text"]
                    break

            # Good delivery — Continue
            wpm = random.randint(120, 155)
            vol = round(random.uniform(-20, -12), 1)
            scenarios.append({
                "character": character,
                "expected": text,
                "actual": generate_delivery_variant(text, "accurate"),
                "wpm": wpm, "vol": vol,
                "play": f"{title} by {playwright}",
                "context": context,
                "action": "Continue",
            })

            # Pacing issue — too fast
            if random.random() < 0.5:
                wpm_bad = random.randint(175, 220)
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "accurate"),
                    "wpm": wpm_bad, "vol": round(random.uniform(-20, -12), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "pacing",
                })

            # Pacing issue — too slow
            if random.random() < 0.5:
                wpm_bad = random.randint(75, 105)
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "accurate"),
                    "wpm": wpm_bad, "vol": round(random.uniform(-20, -12), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "pacing",
                })

            # Volume issue
            if random.random() < 0.4:
                if random.random() < 0.5:
                    vol_bad = round(random.uniform(-35, -26), 1)
                else:
                    vol_bad = round(random.uniform(-7, -3), 1)
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "accurate"),
                    "wpm": random.randint(120, 155),
                    "vol": vol_bad,
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "volume",
                })

            # Accuracy issue — close
            if random.random() < 0.5:
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "close"),
                    "wpm": random.randint(120, 155),
                    "vol": round(random.uniform(-20, -12), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "accuracy",
                })

            # Accuracy issue — very wrong
            if random.random() < 0.3:
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "wrong"),
                    "wpm": random.randint(100, 170),
                    "vol": round(random.uniform(-25, -10), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "accuracy",
                })

            # Emotion issue
            if random.random() < 0.35:
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "accurate"),
                    "wpm": random.randint(120, 155),
                    "vol": round(random.uniform(-20, -12), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "emotion",
                })

            # Technique issue
            if random.random() < 0.3:
                scenarios.append({
                    "character": character,
                    "expected": text,
                    "actual": generate_delivery_variant(text, "accurate"),
                    "wpm": random.randint(120, 155),
                    "vol": round(random.uniform(-20, -12), 1),
                    "play": f"{title} by {playwright}",
                    "context": context,
                    "action": "Interrupt", "issue": "technique",
                })

    return scenarios


def add_coaching_language_examples(coaching_phrases, count=200):
    """Create additional training examples using scraped coaching language.

    Uses real directorial vocabulary found on the web as the feedback text,
    paired with generated input scenarios.
    """
    examples = []

    if not coaching_phrases:
        return examples

    # Famous lines to pair with the scraped feedback
    sample_lines = [
        "To be, or not to be, that is the question",
        "Now is the winter of our discontent",
        "All that glitters is not gold",
        "The lady doth protest too much, methinks",
        "If music be the food of love, play on",
        "Out, damned spot! Out, I say!",
        "Et tu, Brute?",
        "We are such stuff as dreams are made on",
        "Lord, what fools these mortals be!",
        "The course of true love never did run smooth",
        "I have always depended on the kindness of strangers",
        "Attention must be paid to such a person",
        "A lie told often enough becomes the truth",
        "Hell is other people",
        "Waiting for Godot",
    ]

    for _ in range(min(count, len(coaching_phrases))):
        phrase = random.choice(coaching_phrases)
        line = random.choice(sample_lines)

        wpm = random.randint(80, 220)
        vol = round(random.uniform(-35, -5), 1)

        # Determine if this sounds like positive or corrective feedback
        positive_words = ["good", "well", "nice", "great", "effective", "strong", "clear", "excellent"]
        is_positive = any(w in phrase.lower() for w in positive_words)

        action = "Continue" if is_positive else "Interrupt"
        actual = generate_delivery_variant(line, "accurate" if is_positive else "close")

        input_text = (
            f"Actor said: '{actual}' | "
            f"Expected: '{line}' | "
            f"Pacing: {wpm} WPM | "
            f"Volume: {vol} dB"
        )
        output_text = json.dumps({"Action": action, "Feedback": phrase})
        examples.append({"input": input_text, "output": output_text})

    return examples


def main():
    print("=" * 60)
    print("  Cue Master — Training Data Pipeline v2")
    print("=" * 60)
    print()

    # ── Phase 1: Download and parse plays from Project Gutenberg ──────────────
    print("[1/4] Downloading plays from Project Gutenberg...")
    all_play_lines = []
    play_metadata = []

    for gid, title, playwright in GUTENBERG_PLAYS:
        text = download_gutenberg_text(gid)
        if text:
            lines = parse_play_lines(text, playwright)
            if len(lines) > 20:
                all_play_lines.append(lines)
                play_metadata.append((gid, title, playwright))
                print(f"  {title} ({playwright}): {len(lines)} parsed lines")
            else:
                print(f"  {title}: too few lines parsed, skipping")
        else:
            print(f"  {title} (ID {gid}): download failed, skipping")

    total_lines = sum(len(l) for l in all_play_lines)
    dialogue_lines = sum(1 for lines in all_play_lines for l in lines if l["type"] == "dialogue")
    print(f"  Total: {total_lines} lines ({dialogue_lines} dialogue) from {len(all_play_lines)} plays\n")

    # ── Phase 2: Scrape acting/coaching vocabulary from the web ────────────────
    print("[2/4] Scraping acting coaching language from the web...")
    coaching_phrases = scrape_acting_vocabulary()
    print(f"  Collected {len(coaching_phrases)} coaching phrases\n")

    # ── Phase 3: Build scenarios from play data ────────────────────────────────
    print("[3/4] Building training scenarios from play data...")
    scenarios = build_scenarios_from_plays(all_play_lines, play_metadata)

    # Cap scenarios to keep generation time under ~30 min on Apple Silicon
    MAX_SCENARIOS = 800
    if len(scenarios) > MAX_SCENARIOS:
        random.shuffle(scenarios)
        scenarios = scenarios[:MAX_SCENARIOS]
    print(f"  Built {len(scenarios)} scenarios for self-instruct generation\n")

    # ── Phase 4: Self-instruct — use the base model to generate feedback ───────
    print("[4/4] Running self-instruct feedback generation with Phi-3...")
    print("  Loading base model (this may take a moment)...")

    from mlx_lm import load
    model, tokenizer = load("mlx-community/Phi-3-mini-4k-instruct-4bit")
    print("  Model loaded. Generating director feedback...\n")

    start_time = time.time()
    self_instruct_examples = self_instruct_generate_feedback(model, tokenizer, scenarios)
    gen_time = time.time() - start_time
    print(f"\n  Generated {len(self_instruct_examples)} examples in {gen_time:.0f}s")

    # Add coaching language examples
    coaching_examples = add_coaching_language_examples(coaching_phrases)
    print(f"  Added {len(coaching_examples)} coaching vocabulary examples")

    # Combine all examples
    all_examples = self_instruct_examples + coaching_examples
    random.shuffle(all_examples)

    # Write output
    with open(OUTPUT_FILE, "w") as f:
        for ex in all_examples:
            f.write(json.dumps(ex) + "\n")

    print()
    print("=" * 60)
    print("  Data Pipeline Complete")
    print("=" * 60)
    print(f"  Total training examples:  {len(all_examples)}")
    print(f"  From self-instruct:       {len(self_instruct_examples)}")
    print(f"  From coaching scrape:     {len(coaching_examples)}")
    print(f"  Source plays:             {len(all_play_lines)}")
    print(f"  Saved to:                 {OUTPUT_FILE}")
    print()

    # Print samples
    print("── Sample training examples ──")
    for i, ex in enumerate(all_examples[:5]):
        print(f"\nExample {i + 1}:")
        print(f"  INPUT:  {ex['input'][:120]}...")
        out = json.loads(ex['output'])
        print(f"  ACTION: {out['Action']}")
        print(f"  FEEDBACK: {out['Feedback'][:120]}...")

    print()
    print("Run `python scripts/train_director.py` to fine-tune on this data.")


if __name__ == "__main__":
    main()
