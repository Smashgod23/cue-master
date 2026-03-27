import { useState, useEffect, useRef, useCallback } from "react";

/**
 * Connects to the rehearsal WebSocket and dispatches incoming events
 * to state that RehearsalRoom can consume.
 *
 * Returns: { status, notes, activeLine, connect, disconnect, sendAudio }
 *   - status: "idle" | "listening" | "analyzing" | "speaking"
 *   - notes: array of director note objects received so far
 *   - activeLine: current script line id from advance_line events
 *   - connect(): open the WebSocket
 *   - disconnect(): close it
 *   - sendAudio(blob): send binary audio frame
 */
export default function useRehearsalSocket() {
  const wsRef = useRef(null);
  const [status, setStatus] = useState("idle");
  const [notes, setNotes] = useState([]);
  const [activeLine, setActiveLine] = useState(1);
  const [connected, setConnected] = useState(false);

  const handleMessage = useCallback((event) => {
    // Binary messages are audio playback data -- handled separately
    if (event.data instanceof Blob) {
      playAudioBlob(event.data, setStatus);
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
          // Could expose this if needed; for now the backend drives state
          break;

        default:
          break;
      }
    } catch {
      // Non-JSON text frame -- ignore
    }
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= 1) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//localhost:8000/ws/rehearsal`);

    ws.binaryType = "blob";

    ws.addEventListener("open", () => {
      setConnected(true);
      setStatus("listening");
    });

    ws.addEventListener("message", handleMessage);

    ws.addEventListener("close", () => {
      setConnected(false);
      setStatus("idle");
    });

    ws.addEventListener("error", () => {
      setConnected(false);
      setStatus("idle");
    });

    wsRef.current = ws;
  }, [handleMessage]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
    setStatus("idle");
  }, []);

  const sendAudio = useCallback((blob) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(blob);
    }
  }, []);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  return { status, notes, activeLine, connected, connect, disconnect, sendAudio };
}

// Play a .wav blob through the Web Audio API and set status to "speaking" while it plays
function playAudioBlob(blob, setStatus) {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);

  setStatus("speaking");

  audio.addEventListener("ended", () => {
    setStatus("listening");
    URL.revokeObjectURL(url);
  });

  audio.addEventListener("error", () => {
    setStatus("listening");
    URL.revokeObjectURL(url);
  });

  audio.play().catch(() => {
    setStatus("listening");
    URL.revokeObjectURL(url);
  });
}
