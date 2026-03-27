import { useState } from "react";
import { Link } from "react-router-dom";
import ScriptView from "./ScriptView";
import ControlPanel from "./ControlPanel";
import DirectorNotes from "./DirectorNotes";
import useRehearsalSocket from "../hooks/useRehearsalSocket";

export default function RehearsalRoom() {
  const [mode, setMode] = useState("learning");
  const [notesOpen, setNotesOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const ws = useRehearsalSocket();

  const toggleMode = () => {
    setMode((prev) => (prev === "learning" ? "performance" : "learning"));
  };

  return (
    <div className="h-screen flex flex-col bg-parchment">
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-parchment-deep bg-parchment-warm/60">
        <Link to="/" className="flex items-center gap-3 no-underline">
          {/* Logo mark */}
          <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
            <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
            </svg>
          </div>
          <div>
            <h1 className="font-serif text-lg font-bold text-ink leading-tight">
              Cue Master
            </h1>
            <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">
              Rehearsal Studio
            </span>
          </div>
        </Link>

        <div className="flex items-center gap-3">
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

          {/* Quick notes button (always visible) */}
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

      {/* Main content -- split screen */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left pane -- Script */}
        <div className="flex-1 min-w-0 lg:flex-[3]">
          <ScriptView
            activeLine={ws.activeLine}
            onLineClick={() => {}}
          />
        </div>

        {/* Divider */}
        <div className="hidden lg:block w-px bg-parchment-deep" />

        {/* Right pane -- Control Panel (desktop: always visible, mobile: overlay) */}
        <div
          className={`
            lg:flex-[1.2] lg:min-w-[320px] lg:max-w-[400px] lg:relative lg:translate-x-0
            fixed inset-y-0 right-0 w-[85vw] max-w-[400px] z-40
            transform transition-transform duration-300 ease-out
            ${panelOpen ? "translate-x-0" : "translate-x-full lg:translate-x-0"}
            shadow-2xl lg:shadow-none
          `}
        >
          {/* Mobile backdrop */}
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
            onConnect={ws.connect}
            onDisconnect={ws.disconnect}
            onOpenNotes={() => {
              setNotesOpen(true);
              setPanelOpen(false);
            }}
          />
        </div>
      </div>

      {/* Director Notes Modal -- uses live notes from WebSocket when available, falls back to dummy data */}
      <DirectorNotes
        isOpen={notesOpen}
        onClose={() => setNotesOpen(false)}
        activeLineId={ws.activeLine}
        liveNotes={ws.notes}
      />
    </div>
  );
}
