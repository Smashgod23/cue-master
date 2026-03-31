# Cue Master

An offline AI theatrical rehearsal assistant built by Pratham Aithal, a high school student at Rock Hill High School in Frisco, TX (PISD).

GitHub: https://github.com/Smashgod23/cue-master
Contact: theprathamaithal@gmail.com

---

## What This Is

Cue Master is a web app that lets you rehearse a script with an AI scene partner and director, entirely on your own machine. You upload a script (PDF, photo, or text file), tell it which character you're playing, and it listens through your microphone as you deliver your lines. It reads scene partner lines aloud via text-to-speech, evaluates your delivery, and gives you director feedback in real time.

Nothing goes to the cloud. The speech recognition, language model, text-to-speech, and research pipeline all run locally on Apple Silicon. I built it because I wanted a tool that actually simulates the experience of rehearsing with a scene partner, not just a flashcard app for memorizing lines.

---

## Background and Motivation

I've done theatrical performance work and found that the hardest part of preparation isn't memorizing lines - it's building the instinct to respond in the moment to another actor. Reading lines off a page alone doesn't build that. I wanted something that would read my scene partner's lines back to me and evaluate whether my delivery matched the character I was trying to play.

Existing tools are either line-memorization apps (they quiz you but don't respond) or require a cloud subscription and send your audio to a third party. I wanted something that ran fully local so I could use it on a script that hasn't been published yet without worrying about what happens to the data.

---

## Architecture

### Frontend

Built in React 19 with Vite 8, Tailwind CSS v4, and React Router v7. Four pages:

- **Home** - mode selection (Learning vs Performance) and onboarding
- **Upload** - drag-and-drop script upload, posts to `/api/upload`
- **Setup** - character setup form, posts to `/api/research` to kick off the RAG pipeline
- **Rehearsal Room** - live rehearsal UI with WebSocket connection to the backend

The rehearsal UI has three main parts: a scrollable script view that highlights the active line, a control panel with the live status indicator and session controls, and a director notes modal with filterable feedback.

Vite proxies `/api` and `/ws` to the backend on port 8003.

### Backend (`scripts/serve_backend.py`, port 8003)

FastAPI server that handles script uploads, RAG indexing, and the real-time rehearsal WebSocket.

The script parser extracts character dialogue from whatever format you upload:
- PDFs: pdfplumber for text-based PDFs, EasyOCR for scanned pages with bad embedded text layers
- Images: EasyOCR with MPS GPU acceleration on Apple Silicon
- Plain text: direct parsing

The parser uses a Counter-based frequency filter to detect character names (anything that appears 2+ times in ALL CAPS followed by a colon or period). A second-pass discovery step catches one-off characters that only appear once. A pre-processing step inserts newlines before known character names that appear mid-paragraph, which fixes the problem where pdfplumber collapses multi-column layouts into a single long line. Preamble content (cast lists, copyright notices, synopsis text) before the first line of actual dialogue is silently dropped.

The WebSocket endpoint manages the full rehearsal loop: voice activity detection, Whisper transcription, fuzzy and semantic line matching, director evaluation, and TTS playback for scene partner lines.

### Director Model Server (`scripts/serve_director.py`, port 8001)

A fine-tuned Phi-3-mini model, trained via QLoRA with mlx-lm on Apple Silicon, that plays the role of a director giving notes on a performance. Training data was generated from Project Gutenberg plays using a self-instruct approach rather than templated examples, which produces more natural feedback patterns.

### AI Pipeline (all local)

| Component | Tool |
|---|---|
| Speech recognition | faster-whisper |
| Voice activity detection | silero-vad |
| Audio analysis | librosa (WPM, volume) |
| Text-to-speech | piper-tts (ONNX) |
| Line matching | rapidfuzz + sentence-transformers |
| RAG | ChromaDB + sentence-transformers |
| Research | DuckDuckGo search (no API key) |
| OCR | EasyOCR with MPS acceleration |

---

## Obstacles and How I Solved Them

**Script parser picking up too many false positives**

The first version of character detection matched anything in ALL CAPS at the start of a line. This caused problems immediately - words like SETTING, LIGHTS, CURTAIN, and copyright headers like DRAMATIC PUBLISHING were all being flagged as characters. I added a blocklist of common false positives and switched to a Counter-based frequency filter: a name only becomes a recognized character if it appears at least twice as a character cue. This eliminated almost all the noise. I also added a word count cap (names longer than 3 words are rejected) and a structural keyword guard to catch things like "ALL RIGHTS RESERVED" that slipped through.

**One-off characters getting dropped**

The frequency filter fixed false positives but broke detection of minor characters like FIRST BEARER or SECOND BEARER who only appear once. I needed them detected so the mid-paragraph splitter could find their lines. I added a second-pass discovery loop that looks for ALL-CAPS patterns mid-paragraph (after sentence-ending punctuation) using structural validation only, with no frequency requirement. I also had to add a suffix guard to prevent this from creating duplicate entries - for example, "SOUTH" was being discovered as a standalone character because it's the last word of "MR. SOUTH", which was already in the list.

**pdfplumber collapsing multi-column layout into blobs**

Many theatrical scripts are typeset so the character name sits in a left column and dialogue in a right column. pdfplumber flattens this into a single line, producing output like "That's a good firm stretcher, I hope? FIRST BEARER. Never broke yet. SECOND BEARER. Holds up to three hundred pounds." - one long string with multiple characters' lines concatenated. The line-by-line parser had no way to split this correctly. I added a pre-processing step that, once the character list is known, uses a regex to find character names preceded by sentence-ending punctuation and inserts a newline before them. This runs before the main parse loop and fixes the multi-character blob problem.

**pytesseract giving garbage output on scanned PDFs**

The Whodunit PDF I was testing with had a bad embedded OCR text layer from the original scan. pdfplumber reads that layer directly, so even though the image quality was fine, I was getting output like "Ncvcr brokc yet." instead of "Never broke yet." and "Wc hcfcby challenge you" instead of "We hereby challenge you." I needed to detect when the embedded text layer was corrupted and re-OCR the page from the rendered image.

I added a `_text_quality_ok()` function that checks the vowel-less word ratio (words with no vowels at all suggest corruption) and the density of backslash artifacts. If a page fails this check, the backend renders it to a PIL image and runs EasyOCR on it instead. EasyOCR with MPS correctly reads the scanned text that the embedded layer had mangled.

**PaddleOCR hanging forever on Apple Silicon**

Before landing on EasyOCR, I tried PaddleOCR PP-OCRv5 because benchmarks suggested it was more accurate on English documents. It loaded fine and downloaded its model weights, but then hung indefinitely the first time it tried to run inference. PaddlePaddle tries to JIT-compile custom C++ kernels on first use, and on Apple Silicon without proper Metal support it tries to compile indefinitely and never returns. I let it run for over an hour before killing it.

The kill didn't fully work - the process went into uninterruptible sleep (kernel wait state, visible as "UE" in `ps`) and SIGKILL had no effect. It held port 8000 for the rest of the session, which meant every subsequent backend instance had to use a different port. I abandoned PaddleOCR entirely, moved to EasyOCR (which uses PyTorch and supports MPS without any JIT step), and updated the Vite proxy to point at port 8003.

**TTS timing without knowing when audio ends**

The original TTS timing was a sleep-based estimate: wait `word_count * 0.4` seconds for the audio to finish playing. This broke badly for short lines (too long a wait) and long lines (not enough wait time, cutting them off early). I replaced it with an asyncio.Event that the client signals when audio playback actually ends. The frontend sends `{"event": "audio_done"}` when the audio element fires its `ended` event, and the backend waits on that event with a fallback timeout of `max(5.0, word_count * 0.6)` seconds.

**Director notes bleeding between sessions**

Notes accumulated in React state across WebSocket reconnections. If you ended a rehearsal and started a new one in the same browser tab, all the notes from the previous session were still visible in the modal. The fix was calling `setNotes([])` in the WebSocket `open` handler so the notes list clears every time a new connection is established.

**UX edge cases found during audit**

A full UX audit of the site caught several more issues. The script parser was emitting cast lists and production notes as stage directions because they appeared before the first dialogue line - I added a `first_dialogue_seen` flag so nothing gets emitted until actual dialogue starts. The WebSocket connection guard used `readyState <= OPEN` which allowed re-connection attempts while a socket was still in the CONNECTING state (readyState 0) - changed to strict equality. DirectorNotes had an animation delay bug where it multiplied `note.id` (a Unix timestamp in milliseconds) by 60ms, resulting in a stagger delay measured in years - fixed to use the array index. The Restart Scene button had no onClick wired up. The speed slider in the control panel had no onChange handler and did nothing, so I removed it rather than leave deceptive UI. Navigating directly to `/rehearse` without going through upload showed dummy demo data silently instead of redirecting - added a `useEffect` redirect to `/upload` if no parsed script exists in sessionStorage.

---

## Next Steps

- **Step 4 (RAG)**: The `/api/research` endpoint is a placeholder that returns immediately. It needs to run DuckDuckGo searches on the play and character, then index the results into ChromaDB so the director has dramaturgical context.
- **Step 5 (Audio pipeline)**: The full audio WebSocket loop - faster-whisper transcription, silero-vad for detecting when the actor stops speaking, and librosa analysis for WPM and volume metrics.
- **Step 6 (Director + TTS)**: Wire the director model and piper TTS so scene partner lines are read aloud and the director gives spoken feedback.
- **Performance mode**: Currently the script is always visible regardless of mode. Performance mode should hide the script so you rely on memory.
- **Launch script**: Starting the app requires three separate terminals. A single shell script or process manager would make this much easier.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite 8, Tailwind CSS v4, React Router v7 |
| Backend API | Python FastAPI, Uvicorn |
| Script parsing | pdfplumber, EasyOCR (MPS) |
| NLP / line matching | rapidfuzz, sentence-transformers (all-MiniLM-L6-v2) |
| Director model | Phi-3-mini fine-tuned via QLoRA (mlx-lm) |
| Speech-to-text | faster-whisper |
| Voice activity detection | silero-vad |
| Text-to-speech | piper-tts |
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
  serve_backend.py         - main backend API + WebSocket server (port 8003)

src/
  components/
    RehearsalRoom.jsx      - main rehearsal layout shell
    ScriptView.jsx         - scrollable script with active line tracking
    ControlPanel.jsx       - session controls and status indicator
    DirectorNotes.jsx      - filterable director notes modal
    LiveIndicator.jsx      - animated orb (idle/listening/analyzing/speaking)
    ModeToggle.jsx         - Learning / Performance toggle
  hooks/
    useRehearsalSocket.js  - WebSocket lifecycle, mic capture, audio playback
  pages/
    Home.jsx               - mode selection and onboarding
    Upload.jsx             - drag-and-drop script upload
    Setup.jsx              - character setup form
  data/
    dummyScript.js         - demo script data (dev only)
  index.css                - design system tokens (colors, fonts, animations)

data/
  director_training.jsonl  - training dataset (self-instruct from Gutenberg)
  gutenberg_cache/         - cached play texts

models/
  director_adapter/        - LoRA adapter weights
  director_merged/         - merged model for inference

tools/
  piper/                   - TTS binary
  voices/                  - ONNX voice model files
```

---

## Running Locally

### Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- Node.js 18+
- Homebrew

### Install

```bash
# Install Python deps, system packages, piper TTS, and voice model
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

# Terminal 2 - Backend API server
source .venv/bin/activate && python scripts/serve_backend.py --port 8003

# Terminal 3 - Frontend dev server
npm run dev
```

Open http://localhost:5173 in your browser (Vite will pick the next available port if 5173 is taken).

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
