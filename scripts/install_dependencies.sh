#!/usr/bin/env bash
set -euo pipefail

# Cue Master — Stage 2 Dependency Installer
# Installs all Python packages, system tools, and TTS assets needed for the local AI pipeline.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/.venv"
PIPER_DIR="$PROJECT_ROOT/tools/piper"
VOICE_DIR="$PROJECT_ROOT/tools/voices"

echo "════════════════════════════════════════"
echo "  Cue Master — Dependency Installer"
echo "════════════════════════════════════════"

# ── 1. Python virtual environment ──────────────────────────────────────────────
echo ""
echo "[1/5] Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
else
    echo "  Venv already exists at $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel -q
echo "  Python: $(python3 --version)"
echo "  pip: $(pip --version | awk '{print $2}')"

# ── 2. Python packages ────────────────────────────────────────────────────────
echo ""
echo "[2/5] Installing Python packages..."

pip install -q \
    mlx-lm \
    datasets \
    chromadb \
    sentence-transformers \
    duckduckgo-search \
    faster-whisper \
    piper-tts \
    silero-vad \
    pdfplumber \
    pytesseract \
    librosa \
    fastapi \
    uvicorn[standard] \
    pydantic \
    rapidfuzz \
    beautifulsoup4 \
    requests \
    python-multipart \
    websockets \
    aiofiles \
    numpy

echo "  All Python packages installed."

# ── 3. Tesseract OCR ──────────────────────────────────────────────────────────
echo ""
echo "[3/5] Checking for Tesseract OCR..."
if command -v tesseract &>/dev/null; then
    echo "  Tesseract found: $(tesseract --version 2>&1 | head -1)"
else
    echo "  Tesseract not found. Installing via Homebrew..."
    if command -v brew &>/dev/null; then
        brew install tesseract
        echo "  Tesseract installed: $(tesseract --version 2>&1 | head -1)"
    else
        echo "  WARNING: Homebrew not found. Install tesseract manually:"
        echo "    brew install tesseract"
    fi
fi

# ── 4. Piper TTS binary ───────────────────────────────────────────────────────
echo ""
echo "[4/5] Checking for Piper TTS binary..."
mkdir -p "$PIPER_DIR" "$VOICE_DIR"

ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    PIPER_PLATFORM="macos_aarch64"
else
    PIPER_PLATFORM="macos_x64"
fi

PIPER_RELEASE="2023.11.14-2"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_RELEASE}/piper_${PIPER_PLATFORM}.tar.gz"

if [ -f "$PIPER_DIR/piper" ]; then
    echo "  Piper binary already exists at $PIPER_DIR/piper"
else
    echo "  Downloading Piper TTS ($PIPER_PLATFORM)..."
    curl -L "$PIPER_URL" -o /tmp/piper.tar.gz
    tar -xzf /tmp/piper.tar.gz -C "$PIPER_DIR" --strip-components=1
    rm /tmp/piper.tar.gz
    chmod +x "$PIPER_DIR/piper"
    echo "  Piper binary installed at $PIPER_DIR/piper"
fi

# ── 5. Piper voice model ──────────────────────────────────────────────────────
echo ""
echo "[5/5] Checking for Piper voice model (en_US-lessac-high)..."
VOICE_NAME="en_US-lessac-high"
VOICE_ONNX="$VOICE_DIR/${VOICE_NAME}.onnx"
VOICE_JSON="$VOICE_DIR/${VOICE_NAME}.onnx.json"
VOICE_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/high"

if [ -f "$VOICE_ONNX" ] && [ -f "$VOICE_JSON" ]; then
    echo "  Voice model already exists."
else
    echo "  Downloading voice model..."
    curl -L "${VOICE_BASE_URL}/${VOICE_NAME}.onnx" -o "$VOICE_ONNX"
    curl -L "${VOICE_BASE_URL}/${VOICE_NAME}.onnx.json" -o "$VOICE_JSON"
    echo "  Voice model downloaded to $VOICE_DIR/"
fi

# ── 6. Create project directories ─────────────────────────────────────────────
echo ""
echo "Creating project directories..."
mkdir -p "$PROJECT_ROOT/data"
mkdir -p "$PROJECT_ROOT/models/director_adapter"
mkdir -p "$PROJECT_ROOT/models/director_merged"
mkdir -p "$PROJECT_ROOT/backend"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  Installation complete!"
echo "════════════════════════════════════════"
echo ""
echo "To activate the environment in future sessions:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "Next steps:"
echo "  1. python scripts/prepare_data.py    — Download & format training data"
echo "  2. python scripts/train_director.py  — Fine-tune the Director model"
echo "  3. python scripts/serve_director.py  — Start the model server"
echo ""
