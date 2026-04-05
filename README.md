# Cue Master

An offline AI theatrical rehearsal assistant built by Pratham Aithal, a high school student at Rock Hill High School in Frisco, TX (PISD).

GitHub: https://github.com/Smashgod23/cue-master
Contact: theprathamaithal@gmail.com

---

## What This Is

Cue Master is a web app that lets you rehearse a script with an AI scene partner and director, entirely on your own machine. You upload a script (PDF, photo, or text file), review and correct the parsed lines, tell it which character you're playing, and it listens through your microphone as you deliver your lines. It reads scene partner lines aloud via text-to-speech, evaluates your delivery, and gives you real-time director feedback.

Nothing goes to the cloud. Speech recognition, the language model, TTS, and the research pipeline all run locally on Apple Silicon. I built it because I wanted a tool that actually simulates rehearsing with a scene partner - not just a flashcard app for memorizing lines.

---

## Background and Motivation

I've done theatrical performance work and found that the hardest part of preparation isn't memorizing lines - it's building the instinct to respond in the moment to another actor. Reading lines off a page alone doesn't build that reflex. I wanted something that would read my scene partner's lines back to me, listen as I said mine, and evaluate whether my delivery matched the character I was trying to play.

Existing tools are either line-memorization apps (they quiz you but don't respond) or cloud-based products that send your audio to a third party. I wanted something that ran fully local so I could use it on an unpublished script without worrying about data.

---

## Architecture

### Frontend

Built in React 19 with Vite 8, Tailwind CSS v4, and React Router v7. Five pages:

- **Home** - mode selection (Learning vs Performance) and onboarding
- **Upload** - drag-and-drop script upload, posts to `/api/upload`
- **Script Review** - lets you verify and fix the parsed script before rehearsal: edit any line, reassign it to a different character, split merged lines at the cursor, delete junk, and insert missing lines. Auto-corrected typos are highlighted in gold with a click-to-undo option.
- **Setup** - character setup form, posts to `/api/research` to kick off the RAG pipeline; has a skip path if research fails
- **Rehearsal Room** - live rehearsal UI with WebSocket connection to the backend; shows a demo scene if no script is loaded

The rehearsal UI has three main parts: a scrollable script view that highlights the active line, a control panel with the live status indicator and session controls, and a director notes modal with filterable feedback.

Vite proxies `/api` and `/ws` to the backend on port 8004.

### Backend (`scripts/serve_backend.py`, port 8004)

FastAPI server that handles script uploads, RAG indexing, and the real-time rehearsal WebSocket.

The script parser extracts character dialogue from whatever format you upload:
- PDFs: pdfplumber for text-based PDFs, EasyOCR for scanned pages with bad embedded text layers (detected via vowel-less word ratio)
- Images: EasyOCR with MPS GPU acceleration on Apple Silicon
- Plain text: direct parsing

The parser uses a Counter-based frequency filter to detect character names (anything that appears 2+ times in ALL CAPS as a character cue). A second-pass discovery step catches one-off characters. A pre-processing step inserts newlines before known character names that appear mid-paragraph, but only when real dialogue text follows - this prevents vocative addresses like "Are you ready, NURSE." from being split into a false NURSE cue. Fuzzy matching via rapidfuzz resolves OCR-garbled names. Preamble content before the first line of dialogue is silently dropped.

After parsing, an autocorrect pass runs pyspellchecker on the dialogue text. It builds a protected-word set from all character names and any word appearing 2+ times (so proper nouns and repeated vocabulary are never altered), then accepts only single-edit-distance corrections. The frontend receives `_corrections` metadata per line so users can see and undo specific changes.

All blocking work (OCR, parse, autocorrect) runs in a thread pool executor so the FastAPI event loop stays responsive during long uploads.

The WebSocket endpoint manages the full rehearsal loop: voice activity detection via silero-vad, Whisper transcription, fuzzy and semantic line matching, director evaluation, and TTS playback for scene partner lines.

### TTS Pipeline

Scene partner lines are read aloud using macOS built-in `say -v Alex` piped through `afconvert` to produce 22050Hz mono 16-bit WAV. This replaced piper-tts, which is compiled for x86_64 and hangs indefinitely on Apple Silicon under Rosetta. The macOS pipeline produces audio in about 1-2 seconds per line with no hanging.

### Director Model Server (`scripts/serve_director.py`, port 8001)

A fine-tuned Phi-3-mini model, trained via QLoRA with mlx-lm on Apple Silicon, that plays the role of a director giving notes on a performance. Training data was generated from Project Gutenberg plays using a self-instruct approach rather than templated examples, which produces more natural feedback patterns.

### AI Pipeline (all local)

| Component | Tool |
|---|---|
| Speech recognition | faster-whisper |
| Voice activity detection | silero-vad |
| Audio analysis | librosa (WPM, volume) |
| Text-to-speech | macOS say + afconvert (WAV output) |
| Line matching | rapidfuzz + sentence-transformers |
| RAG | ChromaDB + sentence-transformers |
| Research | DuckDuckGo search (no API key) |
| OCR | EasyOCR with MPS acceleration |
| Spell check | pyspellchecker |

---

## Obstacles and How I Solved Them

**Script parser picking up too many false positives**

The first version of character detection matched anything in ALL CAPS at the start of a line. This caused problems immediately - words like SETTING, LIGHTS, CURTAIN, and copyright headers like DRAMATIC PUBLISHING were all being flagged as characters. I added a blocklist of common false positives and switched to a Counter-based frequency filter: a name only becomes a recognized character if it appears at least twice as a character cue. This eliminated almost all the noise. I also added a word count cap (names longer than 3 words are rejected) and a structural keyword guard to catch things like "ALL RIGHTS RESERVED" that slipped through.

**One-off characters getting dropped**

The frequency filter fixed false positives but broke detection of minor characters like FIRST BEARER or SECOND BEARER who only appear once. I needed them detected so the mid-paragraph splitter could find their lines. I added a second-pass discovery loop that looks for ALL-CAPS patterns mid-paragraph (after sentence-ending punctuation) using structural validation only, no frequency requirement. I also had to add a suffix guard to prevent duplicate entries - "SOUTH" was being discovered as a standalone character because it's the last word of "MR. SOUTH", which was already in the list.

**pdfplumber collapsing multi-column layout into blobs**

Many theatrical scripts typeset the character name in a left column and dialogue in a right column. pdfplumber flattens this into a single line, producing output like "That's a good firm stretcher, I hope? FIRST BEARER. Never broke yet. SECOND BEARER. Holds up to three hundred pounds." - one long string with multiple characters' lines concatenated. I added a pre-processing step that, once the character list is known, uses a regex to find character names preceded by sentence-ending punctuation and inserts a newline before them. This runs before the main parse loop and fixes the multi-character blob problem.

**Mid-paragraph split treating vocatives as character cues**

After adding the mid-paragraph splitter, a new class of false positives appeared: lines like "Are you ready, NURSE?" were getting split as if NURSE was about to speak, because the regex fired on any character name preceded by sentence-ending punctuation. In a script with a NURSE character, every time someone said "Are you ready, NURSE." the parser created an empty NURSE line and attributed the following text to her.

The fix was tightening the lookahead in `mid_char_re` from `(?=\s*[.:[\(])` to `(?=\s*[:.][ \t]+\S)` - the split only fires if there's actual dialogue text after the delimiter. A period followed by nothing (end of sentence, vocative) no longer triggers a split. This is a generic fix - it doesn't depend on any script-specific knowledge.

**EasyOCR producing bracket artifacts that broke character detection**

The Whodunit PDF I was testing with had a 1940s-era scan where bracket characters were commonly garbled. The embedded text layer had things like `ANNOUNCER \^pleadhjg\` (backslash artifacts), `ANNOUNCER {seriously]` (mixed curly/square bracket), and entirely wrong character names like ANNOUNCHR and CRANDMA. I extended the regex to handle OCR bracket variants and added rapidfuzz fuzzy matching so ANNOUNCHR -> ANNOUNCER and CRANDMA -> GRANDMA both resolve correctly.

**Fuzzy matching creating false positives via prefix backtracking**

Once I had fuzzy matching, the line `MRS. SOUTH wears frivolous clothes...` was matching as `[MRS. SOUTH]: SOUTH wears frivolous clothes...` because the `char_inline` regex backtracked and parsed `MRS` as the character name, then `MRS` fuzzily matched `MRS. SOUTH` at 90%. The rest of the line became polluted dialogue. I added a prefix guard: after a fuzzy match, the code checks whether the unmatched name is a prefix of the canonical name and whether the rest starts with the missing tail. If both are true, it's rejected as a backtracking artifact.

**Upload blocking the entire event loop**

After the script review feature was working, I uploaded a large scanned PDF and the whole backend froze - not just the upload, but health checks, everything. The `/api/upload` handler was calling `extract_text_from_pdf` (which runs EasyOCR) synchronously inside an `async def`. EasyOCR on Apple Silicon can take several minutes on a multi-page scan, and because it was blocking the asyncio event loop, nothing else could run.

The fix was wrapping every blocking call - `extract_text_from_pdf`, `extract_text_from_image`, `parse_script_text`, and `autocorrect_script` - in `await loop.run_in_executor(_executor, ...)` so they run in a thread pool. The frontend also got an `AbortController` with a 5-minute timeout so it shows a clear error message instead of hanging forever.

**EasyOCR process going into uninterruptible sleep, holding the port**

The first time this happened, I killed the backend process with SIGKILL to free port 8003 for a restart. The kill had no effect - the process went into `UNE` (uninterruptible sleep) during a Metal/MPS GPU operation and couldn't be killed without a full reboot. I moved the backend to port 8004. Port 8000 had the same problem from an earlier PaddleOCR test. Both ports are now unusable until a machine reboot. The pattern is that any Python process that enters a Metal GPU wait on Apple Silicon can become unkillable.

**PaddleOCR hanging forever on Apple Silicon**

Before landing on EasyOCR, I tried PaddleOCR because benchmarks suggested it was more accurate on English documents. It loaded fine and downloaded its model weights, but then hung indefinitely the first time it tried to run inference. PaddlePaddle tries to JIT-compile custom C++ kernels on first use, and on Apple Silicon without proper Metal support it tries to compile indefinitely and never returns. After waiting over an hour I abandoned it and moved to EasyOCR.

**piper TTS hanging indefinitely on Apple Silicon**

After getting the audio pipeline wired up, every scene partner line caused a 30-second hang followed by a timeout. The TTS binary was compiled for x86_64 and runs via Rosetta 2, where the audio output path hangs forever waiting for an audio device. I replaced it with macOS built-in `say -v Alex` piped through `afconvert -f WAVE -d LEI16`. This produces audio in about 1-2 seconds, returns valid WAV bytes, and never hangs.

**TTS timing without knowing when audio ends**

The original TTS timing was a sleep-based estimate: wait `word_count * 0.4` seconds. This broke for short lines (too long a wait) and long lines (cut off early). I replaced it with an asyncio.Event that the client signals when audio playback actually ends. The frontend sends `{"event": "audio_done"}` when the audio element fires its `ended` event, and the backend waits on that event with a fallback timeout.

**CORS blocking API calls from the dev server**

The backend only allowed `localhost:5173` in its CORS origins. Vite was running on a different port because 5173 was already in use. I changed CORS to use `allow_origin_regex` matching any `localhost` or `127.0.0.1` port, so port changes don't break it.

---

## Next Steps

- **Step 4 (RAG)**: The `/api/research` endpoint is a placeholder. It needs to run DuckDuckGo searches on the play and character, then index the results into ChromaDB so the director has dramaturgical context.
- **Performance mode**: Currently the script is always visible. Performance mode should hide it so you rely on memory.
- **Launch script**: Starting the app requires three separate terminals. A single shell script or process manager (overmind, foreman) would reduce friction.
- **Machine reboot**: Ports 8000 and 8003 are held by unkillable EasyOCR processes. After reboot, consolidate back to a single stable port.
- **MR./MRS. character handling**: When a script has both MR. SOUTH and MRS. SOUTH, OCR sometimes produces fragments like "AND MRS" as standalone character names from stage direction text. Better prefix filtering would clean this up.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite 8, Tailwind CSS v4, React Router v7 |
| Backend API | Python FastAPI, Uvicorn |
| Script parsing | pdfplumber, EasyOCR (MPS) |
| Spell check | pyspellchecker |
| NLP / line matching | rapidfuzz, sentence-transformers (all-MiniLM-L6-v2) |
| Director model | Phi-3-mini fine-tuned via QLoRA (mlx-lm) |
| Speech-to-text | faster-whisper |
| Voice activity detection | silero-vad |
| Text-to-speech | macOS say + afconvert (WAV, 22050Hz mono) |
| Audio analysis | librosa |
| RAG | ChromaDB, sentence-transformers |
| Research | DuckDuckGo search (ddgs) |
| Version control | GitHub |

---

## Project Structure

```
scripts/
  install_dependencies.sh  - one-shot environment setup
  prepare_data.py          - downloads Gutenberg plays, generates training data
  train_director.py        - QLoRA fine-tuning pipeline
  serve_director.py        - director model inference server (port 8001)
  serve_backend.py         - main backend: script parser, autocorrect, TTS, WebSocket (port 8004)

src/
  components/
    RehearsalRoom.jsx      - main rehearsal layout shell; demo mode if no script loaded
    ScriptView.jsx         - scrollable script with active line tracking
    ControlPanel.jsx       - session controls and live status indicator
    DirectorNotes.jsx      - filterable director notes modal
    LiveIndicator.jsx      - animated orb (idle/listening/analyzing/speaking)
    ModeToggle.jsx         - Learning / Performance toggle
  hooks/
    useRehearsalSocket.js  - WebSocket lifecycle, mic capture, audio playback
  pages/
    Home.jsx               - mode selection and onboarding
    Upload.jsx             - drag-and-drop script upload with 5-min timeout
    ScriptReview.jsx       - post-upload review: edit lines, split merged cues, undo autocorrect
    Setup.jsx              - character setup form with research skip fallback
  data/
    dummyScript.js         - demo script data for no-upload mode
  index.css                - design system tokens (colors, fonts, animations)

samples/
  midsummer_act2_scene1.txt - sample script for testing the parser

data/
  director_training.jsonl  - training dataset (self-instruct from Gutenberg)

models/
  director_adapter/        - LoRA adapter weights
  director_merged/         - merged model for inference

public/
  audio/
    pcm-processor.js       - AudioWorklet: mic capture, 16kHz downsampling, PCM output
```

---

## Running Locally

### Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- Node.js 18+

### Install

```bash
# Install Python deps
bash scripts/install_dependencies.sh

# Install frontend deps
npm install
```

### Train the director model (first time only)

```bash
source .venv/bin/activate

# Download plays from Project Gutenberg and generate training data
python scripts/prepare_data.py

# Fine-tune Phi-3-mini (~25 min on Apple Silicon)
python scripts/train_director.py
```

This produces a merged model at `models/director_merged/`.

### Run

```bash
# Terminal 1 - Director model server
source .venv/bin/activate && python scripts/serve_director.py

# Terminal 2 - Backend API + WebSocket server
source .venv/bin/activate && python scripts/serve_backend.py --port 8004

# Terminal 3 - Frontend dev server
npm run dev
```

Open http://localhost:5173 (Vite will pick the next available port if 5173 is taken).

### Other commands

```bash
npm run build     # Production build to dist/
npm run preview   # Preview the production build
npm run lint      # Run ESLint
```

---

Built by Pratham Aithal
Rock Hill High School, Frisco, TX (PISD)
theprathamaithal@gmail.com
https://github.com/Smashgod23/cue-master
