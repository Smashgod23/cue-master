import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import ScriptView from "./ScriptView";
import ControlPanel from "./ControlPanel";
import DirectorNotes from "./DirectorNotes";
import useRehearsalSocket from "../hooks/useRehearsalSocket";

// Colour palette for dynamically assigned characters
const CHAR_PALETTE = [
  "#8B2035", "#4A7C59", "#C49A3C", "#6B6770",
  "#5B5EA6", "#7B3F00", "#00827F", "#9B4F96",
];

/**
 * Build a { "CHARACTER_KEY": { name: "Character", color: "#hex" } } map
 * from raw parsed script lines, assigning palette colours in order.
 */
function buildCharMap(lines) {
  const map = {};
  let idx = 0;
  lines.forEach((l) => {
    if (l.type === "dialogue" && l.character && !map[l.character]) {
      const name = l.character.charAt(0) + l.character.slice(1).toLowerCase();
      map[l.character] = { name, color: CHAR_PALETTE[idx % CHAR_PALETTE.length] };
      idx++;
    }
  });
  return map;
}

/**
 * Read and parse a JSON item from sessionStorage safely.
 */
function readSession(key) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Content-based fingerprint used to identify "the same note" across socket
 * reconnects. useRehearsalSocket assigns Date.now() ids which aren't stable,
 * so triage has to hash on what the director actually said instead.
 *
 * For line-less notes, two arrivals with identical type/severity/text collapse
 * into one triage entry. That is intentional: if the director repeats the
 * exact same generic feedback, the user's Useful/Discard decision should carry
 * over. If the backend ever needs to treat those as distinct, it should emit a
 * stable server-side note id we can mix in here.
 */
function triageKeyOf(note) {
  return `${note.type || ""}|${note.severity || ""}|${note.lineId ?? ""}|${note.text || ""}`;
}

/**
 * Minimal full-pane view shown when the user hides the script. Keeps the
 * rehearsal going but removes every visual distraction except what they just
 * said, rendered as large transcript text. Designed so someone can practice
 * lines from memory without peeking at the script.
 */
function HiddenScriptView({ status, transcript, userCharName }) {
  const hasText = Boolean(transcript?.text);
  const statusLabel =
    status === "speaking" ? "Scene partner speaking"
    : status === "analyzing" ? "Analyzing your delivery"
    : status === "listening" ? "Listening"
    : "Idle";

  return (
    <div className="h-full flex flex-col items-center justify-center px-8 py-12 text-center bg-parchment">
      <div className="mb-10 flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${
          status === "listening" ? "bg-gold animate-pulse"
          : status === "analyzing" ? "bg-gold"
          : status === "speaking" ? "bg-crimson"
          : "bg-warmgray-light"
        }`} />
        <span className="font-sans text-[11px] uppercase tracking-[0.25em] text-warmgray">
          {statusLabel}
        </span>
      </div>

      {userCharName && (
        <p className="font-sans text-[10px] uppercase tracking-[0.3em] text-warmgray-light mb-6">
          You are {userCharName}
        </p>
      )}

      <div className="max-w-4xl w-full">
        {hasText ? (
          <p className="font-serif text-4xl md:text-5xl leading-snug text-ink italic">
            "{transcript.text}"
          </p>
        ) : (
          <p className="font-serif text-3xl md:text-4xl text-warmgray italic leading-snug">
            Speak your line. The script is hidden so you can rehearse from memory.
          </p>
        )}
      </div>

      {transcript?.wpm != null && (
        <p className="mt-10 font-sans text-xs uppercase tracking-widest text-warmgray-light">
          {transcript.wpm} wpm
        </p>
      )}
    </div>
  );
}

export default function RehearsalRoom() {
  // --- Derive script + setup from sessionStorage ---
  const parsedScript = useMemo(() => readSession("parsedScript"), []);
  const setup = useMemo(() => readSession("rehearsalSetup"), []);

  const isUploaded = Boolean(parsedScript && parsedScript.length > 0);

  // Character the user is rehearsing as (ALL CAPS to match parsed script keys)
  const userCharKey = setup?.character
    ? setup.character.toUpperCase().trim()
    : "OBERON";

  // Build charMap from the uploaded script, or fall back to dummy inside ScriptView
  const charMap = useMemo(
    () => (isUploaded ? buildCharMap(parsedScript) : null),
    [isUploaded, parsedScript]
  );

  // Script meta from setup data
  const scriptMeta = useMemo(() => {
    if (!isUploaded) return null;
    return {
      title: setup?.play || "Uploaded Script",
      playwright: "",
      act: "",
      scene: "",
    };
  }, [isUploaded, setup]);

  // Character display info for ControlPanel
  const userCharInfo = charMap?.[userCharKey] || null;

  // --- Mode ---
  const [mode, setMode] = useState(() => {
    const saved = sessionStorage.getItem("rehearsalMode");
    return saved === "performance" ? "performance" : "learning";
  });

  const toggleMode = () => {
    setMode((prev) => prev === "learning" ? "performance" : "learning");
    // Reconciliation happens in a useEffect below, so handshake-window
    // toggles (clicking Begin then flipping mode before the socket opens)
    // are caught once wsConnected turns true.
  };

  // --- UI state ---
  const [notesOpen, setNotesOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [scriptHidden, setScriptHidden] = useState(false);

  // Director-notes triage state. These are deliberately kept here rather than
  // in the socket hook because they reflect user judgment on notes, not
  // protocol state — the socket only knows what came in.
  //
  // Triage is stored by content fingerprint (type|severity|lineId|text) rather
  // than by note.id, because useRehearsalSocket assigns a fresh Date.now() id
  // every time a director_note arrives. Without a stable key, a Restart Scene
  // or mode-switch reconnect would resurface previously-discarded notes and
  // double-save repeats of ones the user already flagged.
  const [savedNotes, setSavedNotes] = useState([]);
  const [discardedKeys, setDiscardedKeys] = useState(() => new Set());

  // --- WebSocket ---
  const ws = useRehearsalSocket();
  const { connected: wsConnected, connect: wsConnect, disconnect: wsDisconnect } = ws;

  // Track which (mode, character) the backend was last told via `init`.
  // wrappedConnect updates these refs so the reconciliation effect below
  // can detect a drift between the UI and the backend session.
  const committedModeRef = useRef(null);
  const committedCharRef = useRef(null);
  const wrappedConnect = useCallback((m, c) => {
    committedModeRef.current = m;
    committedCharRef.current = c;
    wsConnect(m, c);
  }, [wsConnect]);

  // A fresh rehearsal starts with a clean triage slate. Only called when the
  // user explicitly clicks Begin Rehearsal — mode-switch and Restart Scene
  // both reuse wrappedConnect so they preserve saved/discarded notes.
  const beginRehearsal = useCallback(() => {
    setSavedNotes([]);
    setDiscardedKeys(new Set());
    wrappedConnect(mode, userCharKey);
  }, [mode, userCharKey, wrappedConnect]);

  // Reconcile UI mode/character with backend session. Runs whenever either
  // side changes. Crucially this effect does NOT return a cleanup function:
  // if it did, React would clear the reconnect timer when wsDisconnect
  // flipped wsConnected and re-ran the effect, cancelling the reconnect.
  // The reconcileTimerRef is cleared explicitly by stopRehearsal if the
  // user ends the session during the 300ms reconnect window.
  const reconcileTimerRef = useRef(null);
  useEffect(() => {
    if (!wsConnected) return;
    if (committedModeRef.current === null) return;
    if (mode === committedModeRef.current && userCharKey === committedCharRef.current) return;
    wsDisconnect();
    if (reconcileTimerRef.current) clearTimeout(reconcileTimerRef.current);
    reconcileTimerRef.current = setTimeout(() => {
      reconcileTimerRef.current = null;
      wrappedConnect(mode, userCharKey);
    }, 300);
  }, [mode, userCharKey, wsConnected, wsDisconnect, wrappedConnect]);

  // Wraps ws.disconnect so the reconciliation reconnect can't reopen a
  // session the user just explicitly ended.
  const stopRehearsal = useCallback(() => {
    if (reconcileTimerRef.current) {
      clearTimeout(reconcileTimerRef.current);
      reconcileTimerRef.current = null;
    }
    committedModeRef.current = null;
    committedCharRef.current = null;
    wsDisconnect();
  }, [wsDisconnect]);

  // Clear any pending reconnect on unmount too.
  useEffect(() => () => {
    if (reconcileTimerRef.current) clearTimeout(reconcileTimerRef.current);
  }, []);

  const handleSaveNote = useCallback((note) => {
    const key = triageKeyOf(note);
    setSavedNotes((prev) =>
      prev.some((n) => triageKeyOf(n) === key) ? prev : [...prev, note]
    );
  }, []);

  const handleDiscardNote = useCallback((note) => {
    const key = triageKeyOf(note);
    setDiscardedKeys((prev) => {
      const next = new Set(prev);
      next.add(key);
      return next;
    });
    // Discarding also drops it from Saved if it was previously saved.
    setSavedNotes((prev) => prev.filter((n) => triageKeyOf(n) !== key));
  }, []);

  // End Session: stop the rehearsal and open the modal on the Saved tab so the
  // user can review everything they flagged before leaving the page. The
  // incrementing token tells DirectorNotes to snap its filter to "session";
  // without it, the modal would reopen on whichever tab the user visited last.
  const [sessionViewToken, setSessionViewToken] = useState(0);
  const handleEndSession = useCallback(() => {
    stopRehearsal();
    setSessionViewToken((t) => t + 1);
    setNotesOpen(true);
  }, [stopRehearsal]);

  return (
    <div className="h-screen flex flex-col bg-parchment">
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-parchment-deep bg-parchment-warm/60">
        <Link to="/" className="flex items-center gap-3 no-underline">
          <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
            <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
            </svg>
          </div>
          <div>
            <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
            <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">Rehearsal Studio</span>
          </div>
        </Link>

        <div className="flex items-center gap-3">
          {/* Hide / Show script */}
          <button
            onClick={() => setScriptHidden((v) => !v)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-sans font-medium transition-all cursor-pointer
              ${scriptHidden
                ? "bg-crimson text-white hover:bg-crimson-muted"
                : "text-ink-muted hover:bg-crimson/10 hover:text-crimson"
              }`}
            aria-pressed={scriptHidden}
            aria-label={scriptHidden ? "Show script" : "Hide script"}
          >
            {scriptHidden ? (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a19.77 19.77 0 0 1 5.06-5.94" />
                <path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a19.77 19.77 0 0 1-3.16 4.19" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            )}
            <span className="hidden sm:inline">{scriptHidden ? "Show Script" : "Hide Script"}</span>
          </button>

          {/* Mobile panel toggle */}
          <button
            onClick={() => setPanelOpen(!panelOpen)}
            className="lg:hidden p-2 rounded-lg hover:bg-parchment-deep transition-colors text-ink-muted cursor-pointer"
            aria-label="Toggle control panel"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7" />
              <rect x="14" y="3" width="7" height="7" />
              <rect x="14" y="14" width="7" height="7" />
              <rect x="3" y="14" width="7" height="7" />
            </svg>
          </button>

          {/* Notes button */}
          <button
            onClick={() => setNotesOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-sans font-medium
              text-ink-muted hover:bg-gold/10 hover:text-gold-deep transition-all cursor-pointer"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
            </svg>
            <span className="hidden sm:inline">Notes</span>
            {ws.notes.length > 0 && (
              <span className="bg-crimson text-white text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center">
                {ws.notes.length}
              </span>
            )}
          </button>
        </div>
      </header>

      {/* Demo mode banner */}
      {!isUploaded && (
        <div className="bg-gold/10 border-b border-gold/25 px-6 py-2 flex items-center justify-between">
          <p className="font-sans text-xs text-gold-deep">
            Demo mode - using built-in scene from A Midsummer Night's Dream (Oberon). Upload a script to rehearse your own lines.
          </p>
          <Link to="/upload" className="font-sans text-xs font-medium text-gold-deep underline underline-offset-2 no-underline hover:text-gold">
            Upload script
          </Link>
        </div>
      )}

      {/* Live caption bar — shows what Whisper heard you say. In Hide mode the
          big HiddenScriptView already surfaces the transcript, so the thin bar
          becomes redundant noise. */}
      {ws.connected && !scriptHidden && (
        <div className="border-b border-parchment-deep bg-ink text-parchment px-6 py-2.5 flex items-center gap-3">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
            ws.status === "listening" ? "bg-gold animate-pulse"
            : ws.status === "analyzing" ? "bg-gold"
            : ws.status === "speaking" ? "bg-crimson"
            : "bg-warmgray"
          }`} />
          <span className="font-sans text-[10px] uppercase tracking-widest text-warmgray-light flex-shrink-0">
            {ws.status === "speaking" ? "Scene partner" :
             ws.status === "analyzing" ? "Analyzing" :
             ws.status === "listening" ? "Listening" : "Idle"}
          </span>
          <span className="flex-1 font-body text-sm text-parchment italic truncate">
            {ws.transcript?.text
              ? `"${ws.transcript.text}"`
              : ws.status === "listening"
                ? "Speak your line. I'll show you what I hear."
                : ""}
          </span>
          {ws.transcript?.wpm != null && (
            <span className="font-sans text-[10px] text-warmgray-light flex-shrink-0">
              {ws.transcript.wpm} wpm
            </span>
          )}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left pane — Script OR Hide view */}
        <div className="flex-1 min-w-0 lg:flex-[3]">
          {scriptHidden ? (
            <HiddenScriptView
              status={ws.connected ? ws.status : "idle"}
              transcript={ws.connected ? ws.transcript : null}
              userCharName={userCharInfo?.name}
            />
          ) : (
            <ScriptView
              lines={isUploaded ? parsedScript : null}
              meta={scriptMeta}
              userCharKey={userCharKey}
              charMap={charMap}
              activeLine={ws.activeLine}
              onLineClick={() => {}}
            />
          )}
        </div>

        <div className="hidden lg:block w-px bg-parchment-deep" />

        {/* Right pane — Control Panel */}
        <div
          className={`
            lg:flex-[1.2] lg:min-w-[320px] lg:max-w-[400px] lg:relative lg:translate-x-0
            fixed inset-y-0 right-0 w-[85vw] max-w-[400px] z-40
            transform transition-transform duration-300 ease-out
            ${panelOpen ? "translate-x-0" : "translate-x-full lg:translate-x-0"}
            shadow-2xl lg:shadow-none
          `}
        >
          {panelOpen && (
            <div
              className="fixed inset-0 bg-ink/30 lg:hidden -z-10"
              onClick={() => setPanelOpen(false)}
            />
          )}
          <ControlPanel
            mode={mode}
            onModeToggle={toggleMode}
            liveState={ws.status}
            connected={ws.connected}
            micError={ws.micError}
            onConnect={beginRehearsal}
            onDisconnect={stopRehearsal}
            onRestart={() => { stopRehearsal(); setTimeout(() => wrappedConnect(mode, userCharKey), 300); }}
            onEndSession={handleEndSession}
            savedCount={savedNotes.length}
            userCharName={userCharInfo?.name}
            userCharColor={userCharInfo?.color}
            onOpenNotes={() => {
              setNotesOpen(true);
              setPanelOpen(false);
            }}
          />
        </div>
      </div>

      {/* Director Notes Modal */}
      <DirectorNotes
        isOpen={notesOpen}
        onClose={() => setNotesOpen(false)}
        activeLineId={ws.activeLine}
        liveNotes={ws.notes}
        savedNotes={savedNotes}
        discardedKeys={discardedKeys}
        triageKeyOf={triageKeyOf}
        onSaveNote={handleSaveNote}
        onDiscardNote={handleDiscardNote}
        onEndSession={handleEndSession}
        sessionViewToken={sessionViewToken}
        allLines={isUploaded ? parsedScript : null}
        charMap={charMap}
        isUploaded={isUploaded}
      />
    </div>
  );
}
