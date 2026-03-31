import { useState, useEffect, useRef, useCallback } from "react";

/**
 * Connects to the rehearsal WebSocket, captures microphone audio via
 * AudioWorklet, and streams 16 kHz PCM frames to the backend.
 *
 * Returns: { status, notes, activeLine, connected, connect, disconnect }
 *   - status:     "idle" | "listening" | "analyzing" | "speaking"
 *   - notes:      director note objects received so far
 *   - activeLine: current script line id from advance_line events
 *   - connect(mode, character): open the WebSocket and start mic capture
 *   - disconnect(): stop everything and close the connection
 */
export default function useRehearsalSocket() {
  const wsRef = useRef(null);
  const audioCtxRef = useRef(null);
  const workletRef = useRef(null);
  const streamRef = useRef(null);

  const [status, setStatus] = useState("idle");
  const [notes, setNotes] = useState([]);
  const [activeLine, setActiveLine] = useState(1);
  const [connected, setConnected] = useState(false);
  const [micError, setMicError] = useState(null);

  // ------------------------------------------------------------------
  // Incoming message handler
  // ------------------------------------------------------------------
  const handleMessage = useCallback((event) => {
    if (event.data instanceof Blob || event.data instanceof ArrayBuffer) {
      const blob = event.data instanceof Blob ? event.data : new Blob([event.data]);
      playAudioBlob(blob, setStatus, wsRef.current);
      return;
    }

    try {
      const msg = JSON.parse(event.data);
      switch (msg.event) {
        case "status":
          setStatus(msg.state || "idle");
          break;
        case "director_note":
          setNotes((prev) => [
            ...prev,
            {
              id: Date.now(),
              text: msg.note,
              severity: msg.severity || "note",
              type: msg.type || "general",
              lineId: msg.lineId || null,
            },
          ]);
          break;
        case "advance_line":
          setActiveLine(msg.lineId);
          break;
        case "transcription":
          // Backend drives all state changes; nothing extra needed here
          break;
        default:
          break;
      }
    } catch {
      // Non-JSON text frame — ignore
    }
  }, []);

  // ------------------------------------------------------------------
  // Mic capture: starts the AudioWorklet pipeline and pipes PCM to ws
  // ------------------------------------------------------------------
  const startMicCapture = useCallback(async (ws) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      setMicError(null);
      streamRef.current = stream;

      const ctx = new AudioContext();
      audioCtxRef.current = ctx;

      await ctx.audioWorklet.addModule("/audio/pcm-processor.js");

      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, "pcm-processor");
      workletRef.current = worklet;

      worklet.port.onmessage = (e) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(e.data); // ArrayBuffer — sent as binary frame
        }
      };

      source.connect(worklet);
      // worklet output not connected to speakers (we don't want mic feedback)
    } catch (err) {
      const msg = err.name === "NotAllowedError"
        ? "Microphone access denied. Allow mic access in your browser and try again."
        : `Microphone unavailable: ${err.message}`;
      setMicError(msg);
      console.error("Mic capture failed:", err);
    }
  }, []);

  // ------------------------------------------------------------------
  // Stop mic capture
  // ------------------------------------------------------------------
  const stopMicCapture = useCallback(() => {
    if (workletRef.current) {
      workletRef.current.disconnect();
      workletRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  // ------------------------------------------------------------------
  // Connect
  // ------------------------------------------------------------------
  const connect = useCallback(
    (mode = "learning", character = "OBERON") => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/rehearsal`);
      ws.binaryType = "arraybuffer";

      ws.addEventListener("open", () => {
        setConnected(true);
        setStatus("listening");
        setNotes([]);         // clear notes from any previous session
        setMicError(null);
        // Tell the backend which mode and character we're using
        ws.send(JSON.stringify({ event: "init", mode, character }));
        // Start streaming microphone audio
        startMicCapture(ws);
      });

      ws.addEventListener("message", handleMessage);

      ws.addEventListener("close", () => {
        setConnected(false);
        setStatus("idle");
        stopMicCapture();
      });

      ws.addEventListener("error", () => {
        setConnected(false);
        setStatus("idle");
        stopMicCapture();
      });

      wsRef.current = ws;
    },
    [handleMessage, startMicCapture, stopMicCapture]
  );

  // ------------------------------------------------------------------
  // Disconnect
  // ------------------------------------------------------------------
  const disconnect = useCallback(() => {
    stopMicCapture();
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
    setStatus("idle");
  }, [stopMicCapture]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      stopMicCapture();
      if (wsRef.current) wsRef.current.close();
    };
  }, [stopMicCapture]);

  return { status, notes, activeLine, connected, micError, connect, disconnect };
}

// ---------------------------------------------------------------------------
// Audio playback helper
// ---------------------------------------------------------------------------
function playAudioBlob(blob, setStatus, ws) {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);

  setStatus("speaking");

  const cleanup = () => {
    setStatus("listening");
    URL.revokeObjectURL(url);
    // Tell the backend playback is done so it can advance without guessing timing
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ event: "audio_done" }));
    }
  };

  audio.addEventListener("ended", cleanup);
  audio.addEventListener("error", cleanup);
  audio.play().catch(cleanup);
}
