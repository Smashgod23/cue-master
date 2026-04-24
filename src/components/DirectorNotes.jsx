import { useState } from "react";
import { directorNotes as dummyNotes, scriptLines as dummyLines, characters as dummyChars } from "../data/dummyScript";

const severityStyles = {
  note: {
    bg: "bg-parchment-warm",
    border: "border-gold/30",
    badge: "bg-gold/15 text-gold-deep",
    icon: "📝",
  },
  suggestion: {
    bg: "bg-parchment-warm",
    border: "border-warmgray-light",
    badge: "bg-ink/8 text-ink-muted",
    icon: "💡",
  },
  important: {
    bg: "bg-crimson/4",
    border: "border-crimson/20",
    badge: "bg-crimson/10 text-crimson",
    icon: "🎭",
  },
};

const typeLabels = {
  pacing: "Pacing",
  inflection: "Inflection",
  emotion: "Emotion",
  blocking: "Blocking",
};

/**
 * Props:
 *   allLines        – full script line array for line-reference previews
 *   charMap         – character display map { "OBERON": { name, color }, … }
 *   liveNotes       – director note objects from the WebSocket (live feed)
 *   savedNotes      – notes the user has marked Useful (persist across tabs)
 *   discardedKeys   – Set of triage keys the user has dismissed
 *   triageKeyOf     – fn(note) → stable content fingerprint used for both
 *                     the discard set and save dedup; survives reconnects
 *   onSaveNote      – called with a note when the user marks it Useful
 *   onDiscardNote   – called with the full note when the user dismisses it
 *   onEndSession    – called when the user clicks End Session
 *   isUploaded      – true when a real script is loaded (suppresses dummy fallback)
 */
export default function DirectorNotes({
  isOpen,
  onClose,
  activeLineId,
  liveNotes,
  savedNotes = [],
  discardedKeys,
  triageKeyOf,
  onSaveNote,
  onDiscardNote,
  onEndSession,
  sessionViewToken = 0,
  allLines,
  charMap,
  isUploaded,
}) {
  const [filter, setFilter] = useState("all");

  // When RehearsalRoom bumps the token (End Session clicked), snap to the
  // Saved tab so the user lands on the summary they're there to review.
  // Updating state during render (with a guard) is the React-recommended
  // pattern for syncing to a prop change without cascading an effect.
  const [prevSessionToken, setPrevSessionToken] = useState(sessionViewToken);
  if (prevSessionToken !== sessionViewToken) {
    setPrevSessionToken(sessionViewToken);
    if (sessionViewToken > 0) setFilter("session");
  }

  const scriptLines = allLines && allLines.length > 0 ? allLines : dummyLines;
  const characters = charMap || dummyChars;
  // Fall back to a note's own id when the parent doesn't pass a triage fn so
  // the component still works in isolation (tests, the dummy-note demo).
  const keyOf = triageKeyOf || ((n) => n.id);
  const discardSet = discardedKeys || new Set();
  const savedKeys = new Set(savedNotes.map(keyOf));

  // When a real script is loaded, don't fall back to dummy notes
  const liveSource =
    liveNotes && liveNotes.length > 0
      ? liveNotes
      : isUploaded
        ? []
        : dummyNotes;

  // Hide notes the user has already discarded (but keep saved ones visible in the live feed too)
  const visibleLive = liveSource.filter((n) => !discardSet.has(keyOf(n)));

  const isSessionTab = filter === "session";

  const filteredNotes = isSessionTab
    ? savedNotes
    : filter === "all"
      ? visibleLive
      : filter === "current"
        ? visibleLive.filter((n) => n.lineId === activeLineId)
        : visibleLive.filter((n) => n.type === filter);

  if (!isOpen) return null;

  const renderNote = (note, idx, { readOnly }) => {
    const style = severityStyles[note.severity] || severityStyles.note;
    const referencedLine = scriptLines.find((l) => l.id === note.lineId);
    const charInfo = referencedLine?.character
      ? characters[referencedLine.character]
      : null;
    const alreadySaved = savedKeys.has(keyOf(note));

    return (
      <div
        key={note.id}
        className={`p-5 rounded-xl border ${style.border} ${style.bg} animate-fade-in-up`}
        style={{ animationDelay: `${idx * 60}ms` }}
      >
        <div className="flex items-center gap-2 mb-2.5">
          <span className="text-base">{style.icon}</span>
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-sans font-semibold uppercase tracking-wider ${style.badge}`}
          >
            {typeLabels[note.type] || note.type}
          </span>
          {charInfo && (
            <span className="text-xs font-sans text-warmgray">
              {charInfo.name}, line {note.lineId}
            </span>
          )}
          {alreadySaved && !readOnly && (
            <span className="ml-auto text-[10px] font-sans font-semibold uppercase tracking-wider text-gold-deep">
              Saved
            </span>
          )}
        </div>

        <p className="font-body text-sm text-ink-soft leading-relaxed">
          {note.text}
        </p>

        {referencedLine && (
          <div className="mt-3 pt-3 border-t border-parchment-deep/50">
            <p className="font-body text-xs text-warmgray italic leading-relaxed line-clamp-2">
              "{referencedLine.text}"
            </p>
          </div>
        )}

        {!readOnly && (
          <div className="mt-4 flex items-center gap-2">
            <button
              onClick={() => onDiscardNote && onDiscardNote(note)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-sans font-medium
                bg-parchment text-ink-muted ring-1 ring-parchment-deep
                hover:text-crimson hover:ring-crimson/30 transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              Discard
            </button>
            <button
              onClick={() => !alreadySaved && onSaveNote && onSaveNote(note)}
              disabled={alreadySaved}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-sans font-medium transition-colors
                ${alreadySaved
                  ? "bg-gold/20 text-gold-deep cursor-default"
                  : "bg-parchment text-ink-muted ring-1 ring-parchment-deep hover:text-gold-deep hover:ring-gold/40 cursor-pointer"
                }`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              {alreadySaved ? "Saved" : "Useful"}
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-ink/40 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-2xl max-h-[80vh] bg-parchment rounded-2xl shadow-2xl border border-parchment-deep animate-fade-in-up overflow-hidden flex flex-col">
        <div className="px-8 py-6 border-b border-parchment-deep">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="font-serif text-xl font-bold text-ink flex items-center gap-2">
                <svg className="w-5 h-5 text-gold" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
                </svg>
                {isSessionTab ? "Session Notes" : "Director's Notes"}
              </h3>
              <p className="font-body text-sm text-warmgray mt-1">
                {isSessionTab
                  ? "Everything you've saved this session"
                  : "Feedback on your performance and delivery"}
              </p>
            </div>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-parchment-deep transition-colors text-ink-muted hover:text-ink cursor-pointer"
              aria-label="Close director notes"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <div className="flex flex-wrap gap-2 mt-4">
            {[
              { key: "all", label: "All Notes" },
              { key: "current", label: "Current Line" },
              { key: "pacing", label: "Pacing" },
              { key: "emotion", label: "Emotion" },
              { key: "inflection", label: "Inflection" },
              { key: "blocking", label: "Blocking" },
              { key: "session", label: `Saved (${savedNotes.length})` },
            ].map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`px-3 py-1.5 rounded-full text-xs font-sans font-medium transition-all duration-200 cursor-pointer
                  ${filter === f.key
                    ? "bg-crimson text-white shadow-sm"
                    : "bg-parchment-deep text-ink-muted hover:bg-parchment-deep/80 hover:text-ink-soft"
                  }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-8 py-5 space-y-4">
          {filteredNotes.length === 0 ? (
            <div className="text-center py-12">
              <p className="font-serif text-lg italic text-warmgray">
                {isSessionTab
                  ? "No notes saved yet"
                  : "No notes for this selection"}
              </p>
              <p className="font-body text-sm text-warmgray-light mt-1">
                {isSessionTab
                  ? "Mark a note as Useful to keep it here"
                  : "Keep rehearsing - feedback will appear as you perform"}
              </p>
            </div>
          ) : (
            filteredNotes.map((note, idx) =>
              renderNote(note, idx, { readOnly: isSessionTab })
            )
          )}
        </div>

        <div className="px-8 py-4 border-t border-parchment-deep bg-parchment-warm/50 flex items-center justify-between gap-3">
          <p className="text-xs font-sans text-warmgray">
            {filteredNotes.length} note{filteredNotes.length !== 1 ? "s" : ""}
            {isSessionTab ? " saved" : ""}
          </p>
          {onEndSession && (
            <button
              onClick={onEndSession}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-sans font-semibold
                bg-ink text-white hover:bg-ink-soft active:scale-[0.98] transition-all cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              End Session
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
