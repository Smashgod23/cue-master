# Cue Master

An offline AI theatrical rehearsal assistant built by Pratham Aithal, a high school student at Rock Hill High School in Frisco, TX (PISD).

GitHub: https://github.com/Smashgod23/cue-master
Contact: theprathamaithal@gmail.com

---

## What This Is

Cue Master is a web app that lets you rehearse a script with an AI scene partner and director, entirely on your own machine. You upload a script (PDF, photo, or text file), tell it which character you're playing, and it listens through your microphone as you deliver your lines. It reads scene partner lines aloud via text-to-speech, evaluates your delivery, and gives you real-time director feedback.

Nothing goes to the cloud. Speech recognition, the language model, TTS, and the research pipeline all run locally on Apple Silicon. I built it because I wanted a tool that actually simulates rehearsing with a scene partner - not just a flashcard app for memorizing lines.

---

## Background and Motivation

I've done theatrical performance work and found that the hardest part of preparation isn't memorizing lines - it's building the instinct to respond in the moment to another actor. Reading lines off a page alone doesn't build that reflex. I wanted something that would read my scene partner's lines back to me, listen as I said mine, and evaluate whether my delivery matched the character I was trying to play.

Existing tools are either line-memorization apps (they quiz you but don't respond) or cloud-based products that send your audio to a third party. I wanted something that ran fully local so I could use it on an unpublished script without worrying about data.

---

## Architecture

### Frontend

Built in React 19 with Vite 8, Tailwind CSS v4, and React Router v7. Four pages:

- **Home** - mode selection (Learning vs Performance) and onboarding
- **Upload** - drag-and-drop script upload, posts to `/api/upload`
- **Setup** - character setup form, posts to `/api/research` to kick off the RAG pipeline; has a skip path if research fails
- **Rehearsal Room** - live rehearsal UI with WebSocket connection to the backend; shows a demo scene if no script is loaded

The rehearsal UI has three main parts: a scrollable script view that highlights the active line, a control panel with the live status indicator and session controls, and a director notes modal with filterable feedback.

Vite proxies `/api` and `/ws` to the backend on port 8003.

### Backend (`scripts/serve_backend.py`, port 8003)

FastAPI server that handles script uploads, RAG indexing, and the real-time rehearsal WebSocket.

The script parser extracts character dialogue from whatever format you upload:
- PDFs: pdfplumber for text-based PDFs, EasyOCR for scanned pages with bad embedded text layers (detected via vowel-less word ratio)
- Images: EasyOCR with MPS GPU acceleration on Apple Silicon
- Plain text: direct parsing

The parser uses a Counter-based frequency filter to detect character names (anything that appears 2+ times in ALL CAPS as a character cue). A second-pass discovery step catches one-off characters. A pre-processing step inserts newlines before known character names that appear mid-paragraph. Fuzzy matching via rapidfuzz resolves OCR-garbled names (ANNOUNCHR -> ANNOUNCER, CRANDMA -> GRANDMA). Preamble content before the first line of dialogue is silently dropped.

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

---

## Obstacles and How I Solved Them

**Script parser picking up too many false positives**

The first version of character detection matched anything in ALL CAPS at the start of a line. This caused problems immediately - words like SETTING, LIGHTS, CURTAIN, and copyright headers like DRAMATIC PUBLISHING were all being flagged as characters. I added a blocklist of common false positives and switched to a Counter-based frequency filter: a name only becomes a recognized character if it appears at least twice as a character cue. This eliminated almost all the noise. I also added a word count cap (names longer than 3 words are rejected) and a structural keyword guard to catch things like "ALL RIGHTS RESERVED" that slipped through.

**One-off characters getting dropped**

The frequency filter fixed false positives but broke detection of minor characters like FIRST BEARER or SECOND BEARER who only appear once. I needed them detected so the mid-paragraph splitter could find their lines. I added a second-pass discovery loop that looks for ALL-CAPS patterns mid-paragraph (after sentence-ending punctuation) using structural validation only, no frequency requirement. I also had to add a suffix guard to prevent duplicate entries - "SOUTH" was being discovered as a standalone character because it's the last word of "MR. SOUTH", which was already in the list.

**pdfplumber collapsing multi-column layout into blobs**

Many theatrical scripts typeset the character name in a left column and dialogue in a right column. pdfplumber flattens this into a single line, producing output like "That's a good firm stretcher, I hope? FIRST BEARER. Never broke yet. SECOND BEARER. Holds up to three hundred pounds." - one long string with multiple characters' lines concatenated. I added a pre-processing step that, once the character list is known, uses a regex to find character names preceded by sentence-ending punctuation and inserts a newline before them. This runs before the main parse loop and fixes the multi-character blob problem.

**EasyOCR producing bracket artifacts that broke character detection**

The Whodunit PDF I was testing with had a 1940s-era scan where bracket characters were commonly garbled. The embedded text layer had things like `ANNOUNCER \^pleadhjg\` (backslash artifacts), `ANNOUNCER {seriously]` (mixed curly/square bracket), and entirely wrong character names like ANNOUNCHR and CRANDMA. The `char_inline` regex only matched `[standard]` brackets, so `ANNOUNCER {seriously]. Tonight...` never recognized ANNOUNCER as a character.

I extended the regex to handle OCR bracket variants: `{dir]`, `{dir}`, `\dir\`, and `\^dir\`. I also added a rapidfuzz fuzzy matching fallback: when a parsed name doesn't appear in the known character set, the code checks if it's within 85% similarity of a known name. ANNOUNCHR -> ANNOUNCER and CRANDMA -> GRANDMA both resolve correctly this way. Running this dedup pass after both character discovery passes (not just the first) was important - the garbled names often only appear once and only get added via the second-pass discovery.

**Fuzzy matching creating false positives via prefix backtracking**

Once I had fuzzy matching, a new problem appeared: the line `MRS. SOUTH wears frivolous clothes and a ridiculous hat.]` was matching as `[MRS. SOUTH]: SOUTH wears frivolous clothes...` because the `char_inline` regex backtracked and parsed `MRS` as the character name, then `MRS` fuzzily matched `MRS. SOUTH` at 90%. The rest of the line (`SOUTH wears...`) then became the dialogue text, polluting the scene partner's first speech.

I added a prefix guard: after a fuzzy match succeeds, the code checks whether the unmatched name is a prefix of the matched canonical name and whether the rest of the line starts with the missing tail. If both are true, it's a backtracking artifact and the match is rejected. `MRS` is a prefix of `MRS. SOUTH`, and `SOUTH wears...` starts with `SOUTH`, so the match is correctly discarded.

**PaddleOCR hanging forever on Apple Silicon**

Before landing on EasyOCR, I tried PaddleOCR because benchmarks suggested it was more accurate on English documents. It loaded fine and downloaded its model weights, but then hung indefinitely the first time it tried to run inference. PaddlePaddle tries to JIT-compile custom C++ kernels on first use, and on Apple Silicon without proper Metal support it tries to compile indefinitely and never returns. I let it run for over an hour before killing it.

The kill didn't fully work - the process went into uninterruptible sleep (kernel wait state, visible as "UE" in `ps`) and SIGKILL had no effect. It held port 8000 for the rest of the session. I abandoned PaddleOCR entirely, moved to EasyOCR (which uses PyTorch and supports MPS without any JIT step), and updated the Vite proxy to point at port 8003.

**piper TTS hanging indefinitely on Apple Silicon**

After getting the audio pipeline wired up, every scene partner line caused a 30-second hang followed by a timeout. The TTS was completely blocking rehearsal. I checked the binary: `file tools/piper/piper` returned `Mach-O 64-bit executable x86_64`. It runs via Rosetta 2 translation, and under Rosetta the audio output path hangs forever waiting for an audio device that never responds correctly.

I replaced the entire piper call with macOS built-in `say -v Alex` piped through `afconvert -f WAVE -d LEI16` to produce standard 22050Hz mono 16-bit WAV. This produces audio in about 1-2 seconds, returns valid WAV bytes, and never hangs. The WebSocket test confirmed: 900KB WAV for a 20-second speech, valid headers, plays correctly in the browser.

**TTS timing without knowing when audio ends**

The original TTS timing was a sleep-based estimate: wait `word_count * 0.4` seconds for the audio to finish playing. This broke for short lines (too long a wait) and long lines (not enough wait, cutting them off early). I replaced it with an asyncio.Event that the client signals when audio playback actually ends. The frontend sends `{"event": "audio_done"}` when the audio element fires its `ended` event, and the backend waits on that event with a fallback timeout.

**CORS blocking API calls from the dev server**

The backend only allowed `localhost:5173` in its CORS origins. Vite was running on port 5175 because 5173 and 5174 were already in use. Every API call was being rejected with a CORS error. I changed the CORS config to use `allow_origin_regex` matching any `localhost` or `127.0.0.1` port, so port changes don't break it.

**Rehearsal room hard-redirecting away from itself**

The original `RehearsalRoom.jsx` had a `useEffect` that checked for `parsedScript` in sessionStorage and immediately called `navigate("/upload")` if it wasn't there. This made it impossible to visit `/rehearse` directly or refresh the page mid-session. I replaced the redirect with a demo mode banner that shows a built-in scene from A Midsummer Night's Dream, letting the route work without any uploaded script.

**Setup page blocking rehearsal entry on research failure**

The Setup page called `/api/research` and only navigated to `/rehearse` if the call succeeded. If the research API was slow or the backend wasn't running, the user was stuck. I added a `saveAndGo()` helper that writes the setup data to sessionStorage and navigates directly, bypassing research. There's now a "Skip research" link below the main submit button, plus a fallback button that appears in the error state if research fails.

---

## Next Steps

- **Step 4 (RAG)**: The `/api/research` endpoint is a placeholder. It needs to run DuckDuckGo searches on the play and character, then index the results into ChromaDB so the director has dramaturgical context.
- **Performance mode**: Currently the script is always visible regardless of mode. Performance mode should hide it so you rely on memory.
- **Better OCR for degraded scans**: For heavily degraded 1940s-era PDFs, EasyOCR still produces noise like AUCE, SUTH, and garbled dialogue. A post-processing spell-check pass using a theatrical vocabulary would help.
- **Launch script**: Starting the app requires three separate terminals (director server, backend, frontend). A single shell script or process manager (like overmind or foreman) would reduce friction.
- **MR./MRS. character handling**: When a script has both MR. SOUTH and MRS. SOUTH, EasyOCR sometimes produces fragments like "AND MRS" or "SOUTH" as standalone character names from stage direction text. Better prefix filtering would clean this up.

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
  serve_backend.py         - main backend: script parser, TTS, WebSocket (port 8003)

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
    Upload.jsx             - drag-and-drop script upload
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
source .venv/bin/activate && python scripts/serve_backend.py --port 8003

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
