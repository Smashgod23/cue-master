# Cue Master

A local AI-powered theatrical rehearsal assistant. Upload a script, pick your character, and rehearse with a real-time AI director that listens to your delivery and gives you notes on pacing, volume, accuracy, and emotion.

Everything runs on your Mac. No cloud APIs, no subscriptions, no data leaving your machine.

## How It Works

1. **Upload a script** - PDF, image, or plain text. The app parses character names and dialogue automatically.
2. **Pick your character** - Enter the play name, your character, and any personal notes for the director.
3. **Rehearse** - The app listens through your microphone, transcribes your lines in real time, and compares them against the script.
4. **Get director feedback** - The AI director evaluates your pacing, volume, and accuracy, then either lets you continue or interrupts with a note.

### Two modes

- **Learning Mode** - Your lines are visible on screen. The app checks if you got the words right (fuzzy matching) and reads scene partner lines aloud so you can practice back-and-forth.
- **Performance Mode** - Lines are hidden. The AI director uses a fine-tuned language model plus dramaturgical research about your specific play and character to give contextual feedback.

## Tech Stack

**Frontend:** React 19, Vite 8, Tailwind CSS v4, React Router

**Backend:** Python FastAPI, Uvicorn, WebSockets

**AI Pipeline (all local):**
- Fine-tuned Phi-3-mini via QLoRA (mlx-lm on Apple Silicon)
- faster-whisper for speech-to-text
- silero-vad for voice activity detection
- librosa for audio analysis (WPM, volume)
- piper-tts for text-to-speech (scene partner and director voice)
- ChromaDB + sentence-transformers for RAG (dramaturgical research)
- DuckDuckGo search for internet research (no API key needed)

**Script parsing:** pdfplumber (PDFs), pytesseract (images), plain text

## Setup

### Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- Node.js 18+
- Homebrew

### Install dependencies

```bash
# Install all Python deps, tesseract, piper TTS, and voice model
bash scripts/install_dependencies.sh

# Install frontend deps
npm install
```

### Train the director model (first time only)

```bash
# Activate the Python environment
source .venv/bin/activate

# Download plays from Project Gutenberg and generate training data
python scripts/prepare_data.py

# Fine-tune the model (~25 min on Apple Silicon)
python scripts/train_director.py
```

This produces a fine-tuned Phi-3 model at `models/director_merged/`.

### Run the app

```bash
# Terminal 1 - Start the model server (port 8001)
source .venv/bin/activate
python scripts/serve_director.py

# Terminal 2 - Start the backend API server (port 8000)
source .venv/bin/activate
python scripts/serve_backend.py

# Terminal 3 - Start the frontend dev server (port 5173)
npm run dev
```

Open http://localhost:5173 in your browser.

## Project Structure

```
scripts/
  install_dependencies.sh  - one-shot environment setup
  prepare_data.py          - downloads plays, generates training data
  train_director.py        - QLoRA fine-tuning pipeline
  serve_director.py        - model inference server (port 8001)
  serve_backend.py         - main backend API server (port 8000)

src/
  components/              - React UI components
  hooks/                   - custom React hooks (WebSocket, etc.)
  pages/                   - route pages (Home, Upload, Setup)
  data/                    - dummy script data (dev/demo)
  index.css                - design system tokens

data/
  director_training.jsonl  - training dataset
  gutenberg_cache/         - cached play texts

models/
  director_adapter/        - LoRA adapter weights
  director_merged/         - merged model for inference

tools/
  piper/                   - TTS binary
  voices/                  - ONNX voice model
```

## Current Status

- Step 1 complete: training pipeline, fine-tuned model, model serving endpoint
- Step 2 complete: React Router multi-page app with 4 pages, selectable mode cards, WebSocket wiring
- Step 3 complete: backend API server with script upload/parsing (PDF, TXT, image OCR)
- Steps 4-6 next: internet research + RAG, real-time audio pipeline, director logic + TTS

## Commands

```bash
npm run dev       # Start frontend dev server
npm run build     # Production build
npm run lint      # Run ESLint
```
