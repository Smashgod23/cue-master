import { useState } from "react";
import ModeToggle from "./ModeToggle";
import LiveIndicator from "./LiveIndicator";
import { userCharacter, characters } from "../data/dummyScript";

const liveStates = ["idle", "listening", "analyzing", "speaking"];

export default function ControlPanel({ mode, onModeToggle, liveState, connected, onConnect, onDisconnect, onOpenNotes }) {
  // Demo cycler -- used only when WebSocket isn't connected
  const [demoIndex, setDemoIndex] = useState(0);

  const cycleLiveState = () => {
    setDemoIndex((prev) => (prev + 1) % liveStates.length);
  };

  const currentState = connected ? liveState : liveStates[demoIndex];
  const userChar = characters[userCharacter];

  return (
    <div className="h-full flex flex-col bg-parchment-warm/40">
      {/* Panel header */}
      <div className="px-6 pt-6 pb-4 border-b border-parchment-deep">
        <h3 className="font-serif text-lg font-bold text-ink text-center">
          Rehearsal Booth
        </h3>
        <p className="font-body text-xs text-warmgray text-center mt-1">
          Your AI scene partner & director
        </p>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
        {/* Current role badge */}
        <div className="flex justify-center">
          <div className="inline-flex items-center gap-2 px-4 py-2.5 bg-parchment rounded-xl ring-1 ring-parchment-deep">
            <span
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: userChar.color }}
            />
            <div>
              <span className="font-sans text-[10px] uppercase tracking-widest text-warmgray block">
                Rehearsing as
              </span>
              <span className="font-serif text-base font-bold text-ink">
                {userChar.name}
              </span>
            </div>
          </div>
        </div>

        {/* Mode toggle */}
        <ModeToggle mode={mode} onToggle={onModeToggle} />

        {/* Divider */}
        <div className="flex items-center gap-3 px-4">
          <span className="flex-1 h-px bg-parchment-deep" />
          <span className="font-sans text-[10px] uppercase tracking-[0.15em] text-warmgray">
            Live Status
          </span>
          <span className="flex-1 h-px bg-parchment-deep" />
        </div>

        {/* Live indicator */}
        <LiveIndicator state={currentState} />

        {/* Demo control -- only show when not connected to WebSocket */}
        {!connected && (
          <button
            onClick={cycleLiveState}
            className="w-full py-2.5 rounded-lg bg-parchment-deep/60 text-ink-muted text-xs font-sans font-medium
              hover:bg-parchment-deep transition-colors cursor-pointer"
          >
            Cycle Status (Demo)
          </button>
        )}

        {/* Divider */}
        <div className="flex items-center gap-3 px-4">
          <span className="flex-1 h-px bg-parchment-deep" />
          <span className="font-sans text-[10px] uppercase tracking-[0.15em] text-warmgray">
            Controls
          </span>
          <span className="flex-1 h-px bg-parchment-deep" />
        </div>

        {/* Action buttons */}
        <div className="space-y-3">
          {/* Start / Stop rehearsal */}
          <button
            onClick={connected ? onDisconnect : onConnect}
            className={`w-full flex items-center justify-center gap-2 py-3.5 px-5 rounded-xl
              font-sans font-semibold text-sm
              active:scale-[0.98] transition-all duration-200
              shadow-md cursor-pointer
              ${connected
                ? "bg-ink text-white shadow-ink/20 hover:bg-ink-soft"
                : "bg-crimson text-white shadow-crimson/20 hover:bg-crimson-muted"
              }`}
          >
            {connected ? (
              <>
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="4" width="4" height="16" rx="1" />
                  <rect x="14" y="4" width="4" height="16" rx="1" />
                </svg>
                End Rehearsal
              </>
            ) : (
              <>
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Begin Rehearsal
              </>
            )}
          </button>

          {/* Director notes */}
          <button
            onClick={onOpenNotes}
            className="w-full flex items-center justify-center gap-2 py-3 px-5 rounded-xl
              bg-parchment text-ink font-sans font-medium text-sm
              ring-1 ring-parchment-deep hover:ring-gold/40 hover:bg-gold/5
              active:scale-[0.98] transition-all duration-200 cursor-pointer"
          >
            <svg className="w-4 h-4 text-gold" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
            </svg>
            Director's Notes
          </button>

          {/* Restart scene */}
          <button
            className="w-full flex items-center justify-center gap-2 py-3 px-5 rounded-xl
              bg-parchment text-ink-muted font-sans font-medium text-sm
              ring-1 ring-parchment-deep hover:ring-warmgray-light
              active:scale-[0.98] transition-all duration-200 cursor-pointer"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            Restart Scene
          </button>
        </div>

        {/* Speed control */}
        <div className="bg-parchment rounded-xl p-4 ring-1 ring-parchment-deep">
          <div className="flex items-center justify-between mb-3">
            <span className="font-sans text-xs font-medium text-ink-muted uppercase tracking-wider">
              Cue Pacing
            </span>
            <span className="font-sans text-xs font-semibold text-gold-deep">
              Natural
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="4"
            defaultValue="2"
            className="w-full accent-gold h-1.5 rounded-full appearance-none bg-parchment-deep cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-gold [&::-webkit-slider-thumb]:shadow-md
              [&::-webkit-slider-thumb]:shadow-gold/30 [&::-webkit-slider-thumb]:cursor-pointer"
          />
          <div className="flex justify-between mt-1.5">
            <span className="text-[10px] font-sans text-warmgray">Slow</span>
            <span className="text-[10px] font-sans text-warmgray">Fast</span>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-parchment-deep bg-parchment-warm/60">
        <p className="text-[10px] font-sans text-warmgray text-center uppercase tracking-widest">
          Cue Master -- AI Rehearsal Companion
        </p>
      </div>
    </div>
  );
}
