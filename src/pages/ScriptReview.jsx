import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";

function loadScript() {
  try {
    const raw = sessionStorage.getItem("parsedScript");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function getCharacters(lines) {
  const seen = new Set();
  lines.forEach((l) => { if (l.character) seen.add(l.character); });
  return [...seen].sort();
}

let _nextId = 1;
function freshId() { return `new-${_nextId++}`; }

function escapeRegexLiteral(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Decide what kind of line the after-cursor text should become when the
// user splits a merged line. Returns { type, character?, text }.
//
// - Bracketed content like "(He exits)" or "[Aside]" -> stage_direction
//   with brackets stripped.
// - Stage-cue verbs ("Enter X", "Exit Y", "Exeunt") -> stage_direction.
// - Text that begins with a known character name followed by a cue
//   delimiter (period, colon, space) -> dialogue under that character.
// - Generic ALL-CAPS prefix that looks like a name (e.g. "ROMEO. What ho!")
//   -> dialogue under that new character name.
// - Anything else -> dialogue continuation; caller fills in the character.
function classifySplitContent(rawText, knownCharacters) {
  const text = rawText.replace(/^\s+/, "");
  if (!text) return { type: "continuation", text: "" };

  // Bracketed stage direction. Strip outer brackets if the bracketed
  // segment is the whole string; keep any trailing text alongside it.
  const bracketStart = text.match(/^([\(\[\{])/);
  if (bracketStart) {
    const open = bracketStart[1];
    const close = open === "(" ? ")" : open === "[" ? "]" : "}";
    const closeIdx = text.indexOf(close, 1);
    if (closeIdx !== -1) {
      const inner = text.slice(1, closeIdx).trim();
      const trailing = text.slice(closeIdx + 1).trim();
      // When prose follows the bracket, surface the trailing chunk as a
      // separate follow-on so the caller can emit it as its own dialogue
      // line. Collapsing both into a single stage_direction would lose
      // spoken text inside metadata.
      if (trailing) {
        return {
          type: "stage_direction",
          text: inner,
          pureDirection: false,
          followOn: classifySplitContent(trailing, knownCharacters),
        };
      }
      return { type: "stage_direction", text: inner, pureDirection: true };
    }
    return { type: "stage_direction", text: text.slice(1).trim(), pureDirection: true };
  }

  // Common stage-cue verbs (Shakespeare and modern scripts both use these).
  if (/^(Enter|Exit|Exeunt)\b/i.test(text)) {
    return { type: "stage_direction", text: text.trim() };
  }

  // Known character cue. Try longest names first so "MR. SOUTH" wins over "MR".
  // Accept either punctuation (. , : ;) after the name, or whitespace
  // followed by an uppercase letter. The uppercase test distinguishes a
  // real cue ("JULIET But, soft!") from a prose mention
  // ("I warned JULIET not to go").
  const sortedChars = [...knownCharacters].sort((a, b) => b.length - a.length);
  for (const c of sortedChars) {
    const escaped = escapeRegexLiteral(c);
    const re = new RegExp(
      `^${escaped}(?:\\s*[.,:;]+\\s*|\\s+(?=[A-Z]))(.+)$`,
      "s",
    );
    const m = text.match(re);
    if (m && m[1].trim()) {
      return { type: "dialogue", character: c, text: m[1].trim() };
    }
  }

  // Generic ALL-CAPS character cue: "ROMEO. What ho!" or "MRS. SMITH: Hello".
  // Up to ~30 chars, allowing internal periods (titles like "MR.") and spaces.
  const capsMatch = text.match(/^([A-Z][A-Z'.\s]{1,28}?)[.:]\s+(.+)$/s);
  if (capsMatch) {
    const possibleName = capsMatch[1].replace(/\s+$/, "").trim();
    // Require at least one capital letter run of length >= 2 to avoid
    // matching single-letter prefixes like "I."
    if (/[A-Z]{2,}/.test(possibleName) && capsMatch[2].trim()) {
      return {
        type: "dialogue",
        character: possibleName,
        text: capsMatch[2].trim(),
      };
    }
  }

  return { type: "continuation", text: text.trim() };
}

export default function ScriptReview() {
  const navigate = useNavigate();
  const original = useMemo(() => loadScript(), []);
  const [lines, setLines] = useState(() => original ?? []);
  const [editingId, setEditingId] = useState(null);
  // Track cursor position in the active textarea so split-at-cursor works
  const cursorRef = useRef(0);
  const editRef = useRef(null);

  // Count total auto-corrections across all lines
  const totalCorrections = useMemo(
    () => lines.reduce((sum, l) => sum + (l._corrections?.length ?? 0), 0),
    [lines]
  );

  useEffect(() => {
    if (editingId !== null && editRef.current) {
      editRef.current.focus();
    }
  }, [editingId]);

  if (!original) {
    return (
      <div className="min-h-screen bg-parchment flex flex-col items-center justify-center gap-4 px-6">
        <p className="font-body text-ink-soft text-center">No script found. Please upload one first.</p>
        <Link to="/upload" className="font-sans text-sm text-crimson hover:underline no-underline">
          Go to Upload
        </Link>
      </div>
    );
  }

  const characters = getCharacters(lines);

  // --- mutations ---

  function updateLine(id, patch) {
    setLines((prev) => prev.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  }

  function deleteLine(id) {
    setLines((prev) => prev.filter((l) => l.id !== id));
    if (editingId === id) setEditingId(null);
  }

  function insertAfter(id, overrides = {}) {
    const newLine = {
      id: freshId(),
      type: "dialogue",
      character: characters[0] ?? "",
      text: "",
      ...overrides,
    };
    setLines((prev) => {
      const idx = prev.findIndex((l) => l.id === id);
      const next = [...prev];
      next.splice(idx + 1, 0, newLine);
      return next;
    });
    setEditingId(newLine.id);
  }

  function insertManyAfter(id, overridesList) {
    if (overridesList.length === 0) return;
    const newLines = overridesList.map((o) => ({
      id: freshId(),
      type: "dialogue",
      character: characters[0] ?? "",
      text: "",
      ...o,
    }));
    setLines((prev) => {
      const idx = prev.findIndex((l) => l.id === id);
      const next = [...prev];
      next.splice(idx + 1, 0, ...newLines);
      return next;
    });
    // Drop the user into the first new line so they can immediately tweak it.
    setEditingId(newLines[0].id);
  }

  // Split the active line at the current cursor position. The text before
  // the cursor stays put; the text after gets classified so the new line
  // (a) inherits the right type/character without the user having to fix
  // it manually and (b) merges into an adjacent stage_direction line when
  // it itself looks like a direction.
  function splitAtCursor(id) {
    const pos = cursorRef.current;
    const idx = lines.findIndex((l) => l.id === id);
    if (idx === -1) return;
    const line = lines[idx];
    const before = line.text.slice(0, pos).trimEnd();
    const after = line.text.slice(pos);
    if (!after.trim()) return;

    const detected = classifySplitContent(after, characters);
    updateLine(id, { text: before, _corrections: undefined });

    // Build the list of new lines to insert below the current line, in order.
    const newLines = [];
    let cursor = detected;
    while (cursor) {
      if (cursor.type === "stage_direction") {
        newLines.push({ type: "stage_direction", character: "", text: cursor.text });
      } else if (cursor.type === "dialogue") {
        newLines.push({
          type: "dialogue",
          character: cursor.character || line.character,
          text: cursor.text,
        });
      } else {
        newLines.push({
          type: line.type,
          character: line.character,
          text: cursor.text,
        });
      }
      cursor = cursor.followOn;
    }

    // Special case: a single pure stage_direction can merge into the next
    // existing stage_direction line, preserving line count.
    const next = lines[idx + 1];
    if (
      newLines.length === 1
      && newLines[0].type === "stage_direction"
      && detected.pureDirection
      && next && next.type === "stage_direction"
    ) {
      updateLine(next.id, {
        text: `${newLines[0].text} ${next.text}`.trim(),
        _corrections: undefined,
      });
      return;
    }

    insertManyAfter(id, newLines);
  }

  function revertCorrection(id, originalWord) {
    const line = lines.find((l) => l.id === id);
    if (!line) return;
    const correction = line._corrections?.find((c) => c.corrected === originalWord || c.corrected === originalWord[0].toLowerCase() + originalWord.slice(1));
    if (!correction) return;
    // Replace the corrected word back with the original (first occurrence only)
    const regex = new RegExp(`\\b${escapeRegex(correction.corrected)}\\b`);
    const newText = line.text.replace(regex, correction.original);
    const newCorrections = line._corrections.filter((c) => c !== correction);
    updateLine(id, { text: newText, _corrections: newCorrections.length ? newCorrections : undefined });
  }

  function handleContinue() {
    // Strip _corrections metadata before saving, re-number ids
    const clean = lines.map((l, i) => {
      const { _corrections, ...rest } = l;
      return { ...rest, id: i + 1 };
    });
    sessionStorage.setItem("parsedScript", JSON.stringify(clean));
    navigate("/setup");
  }

  const dialogueCount = lines.filter((l) => l.type === "dialogue").length;

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      {/* Header */}
      <header className="px-6 py-4 border-b border-parchment-deep bg-parchment-warm/60 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
              <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
              </svg>
            </div>
            <div>
              <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
              <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">Review Your Script</span>
            </div>
          </Link>

          <div className="flex items-center gap-3">
            <span className="font-sans text-xs text-warmgray hidden sm:block">
              {dialogueCount} lines &middot; {characters.length} characters
              {totalCorrections > 0 && (
                <span className="ml-2 text-gold-deep">&middot; {totalCorrections} typo{totalCorrections !== 1 ? "s" : ""} fixed</span>
              )}
            </span>
            <button
              onClick={handleContinue}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-crimson text-white font-sans text-sm font-semibold
                hover:bg-crimson-muted active:scale-[0.98] shadow shadow-crimson/25 transition-all cursor-pointer"
            >
              Looks good
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        </div>
      </header>

      {/* Instructions */}
      <div className="max-w-4xl mx-auto w-full px-6 pt-5 pb-1">
        <div className="flex flex-wrap gap-x-6 gap-y-1 font-sans text-xs text-ink-soft">
          <span>Click any line to edit or reassign it.</span>
          <span>Place your cursor in the text and hit <span className="bg-parchment-deep px-1.5 py-0.5 rounded text-ink-muted">Split here</span> to break a merged line in two.</span>
          <span>Hover a line for insert / delete buttons.</span>
          {totalCorrections > 0 && (
            <span className="text-gold-deep">Words highlighted in gold were auto-corrected - click to undo.</span>
          )}
        </div>
      </div>

      {/* Script lines */}
      <main className="flex-1 max-w-4xl mx-auto w-full px-6 py-4 space-y-1 pb-16">
        {lines.map((line) => (
          <LineRow
            key={line.id}
            line={line}
            characters={characters}
            isEditing={editingId === line.id}
            editRef={editingId === line.id ? editRef : null}
            cursorRef={editingId === line.id ? cursorRef : null}
            onEdit={() => setEditingId(line.id)}
            onClose={() => setEditingId(null)}
            onChange={(patch) => updateLine(line.id, patch)}
            onDelete={() => deleteLine(line.id)}
            onInsertAfter={() => insertAfter(line.id)}
            onSplit={() => splitAtCursor(line.id)}
            onRevertCorrection={(word) => revertCorrection(line.id, word)}
          />
        ))}

        {lines.length === 0 && (
          <div className="text-center py-16">
            <p className="font-body text-ink-soft">All lines deleted.</p>
            <Link to="/upload" className="mt-4 inline-block font-sans text-sm text-crimson hover:underline no-underline">
              Back to Upload
            </Link>
          </div>
        )}
      </main>

      {/* Footer */}
      <div className="sticky bottom-0 border-t border-parchment-deep bg-parchment-warm/80 backdrop-blur-sm px-6 py-3">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <Link to="/upload" className="font-sans text-xs text-warmgray hover:text-ink-soft transition-colors no-underline">
            &larr; Re-upload script
          </Link>
          <button
            onClick={handleContinue}
            className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl bg-crimson text-white font-sans text-sm font-semibold
              hover:bg-crimson-muted active:scale-[0.98] shadow-lg shadow-crimson/25 transition-all cursor-pointer"
          >
            Continue to Setup
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// LineRow
// -----------------------------------------------------------------------

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Build a set of corrected words from _corrections so we can highlight them
function correctedWordSet(corrections) {
  if (!corrections?.length) return new Set();
  return new Set(corrections.map((c) => c.corrected.toLowerCase()));
}

function LineRow({ line, characters, isEditing, editRef, cursorRef, onEdit, onClose, onChange, onDelete, onInsertAfter, onSplit, onRevertCorrection }) {
  const isDirection = line.type === "stage_direction";
  const corrected = correctedWordSet(line._corrections);

  // Track cursor position in the textarea
  const handleCursorMove = useCallback((e) => {
    if (cursorRef) cursorRef.current = e.target.selectionStart;
  }, [cursorRef]);

  // Render dialogue text with auto-corrected words highlighted in gold
  function renderTextWithHighlights(text) {
    if (!corrected.size) return text;
    // Split on word boundaries, highlight matching tokens
    const parts = text.split(/(\b[a-zA-Z']+\b)/);
    return parts.map((part, i) => {
      if (corrected.has(part.toLowerCase())) {
        return (
          <button
            key={i}
            type="button"
            title={`Auto-corrected - click to undo`}
            onClick={(e) => { e.stopPropagation(); onRevertCorrection(part); }}
            className="bg-gold/30 text-ink-muted rounded px-0.5 cursor-pointer hover:bg-crimson/20 hover:text-crimson transition-colors underline decoration-dotted"
          >
            {part}
          </button>
        );
      }
      return part;
    });
  }

  return (
    <div className={`group relative rounded-xl border transition-all duration-150
      ${isEditing
        ? "border-gold/60 bg-gold/5 shadow-sm"
        : isDirection
          ? "border-transparent bg-parchment-deep/30 hover:border-parchment-deep"
          : "border-transparent bg-parchment-warm/50 hover:border-parchment-deep"
      }`}
    >
      {isEditing ? (
        // --- Edit mode ---
        <div className="p-3 space-y-2">
          {/* Controls row */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Type toggle */}
            <button
              type="button"
              onClick={() => onChange({ type: isDirection ? "dialogue" : "stage_direction", character: isDirection ? (characters[0] ?? "") : "" })}
              className={`px-2 py-1 rounded text-[10px] font-sans font-medium uppercase tracking-wide cursor-pointer transition-colors
                ${isDirection ? "bg-parchment-deep text-ink-muted" : "bg-crimson/10 text-crimson"}`}
            >
              {isDirection ? "Stage direction" : "Dialogue"}
            </button>

            {/* Character selector */}
            {!isDirection && (
              <select
                value={line.character}
                onChange={(e) => {
                  if (e.target.value === "__new__") {
                    const name = prompt("Character name (ALL CAPS):")?.trim().toUpperCase();
                    if (name) onChange({ character: name });
                  } else {
                    onChange({ character: e.target.value });
                  }
                }}
                className="px-2 py-1 rounded-lg bg-parchment-warm border border-parchment-deep
                  font-sans text-xs text-ink focus:outline-none focus:border-gold cursor-pointer"
              >
                {characters.map((c) => <option key={c} value={c}>{c}</option>)}
                <option value="__new__">+ New character...</option>
              </select>
            )}

            {/* Split at cursor */}
            <button
              type="button"
              title="Split the line at cursor position into two separate lines"
              onClick={onSplit}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-sans font-medium uppercase tracking-wide
                bg-parchment-deep text-ink-muted hover:bg-gold/20 hover:text-ink transition-colors cursor-pointer"
            >
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />
              </svg>
              Split here
            </button>

            <div className="ml-auto">
              <button
                type="button"
                onClick={onClose}
                className="px-2.5 py-1 rounded-lg bg-gold text-ink font-sans text-xs font-medium cursor-pointer hover:bg-gold/80 transition-colors"
              >
                Done
              </button>
            </div>
          </div>

          {/* Textarea */}
          <textarea
            ref={editRef}
            value={line.text}
            rows={3}
            onChange={(e) => {
              if (cursorRef) cursorRef.current = e.target.selectionStart;
              onChange({ text: e.target.value, _corrections: undefined });
            }}
            onKeyUp={handleCursorMove}
            onMouseUp={handleCursorMove}
            onSelect={handleCursorMove}
            className="w-full px-3 py-2 rounded-lg bg-parchment-warm border border-parchment-deep
              font-body text-sm text-ink resize-none
              focus:outline-none focus:border-gold focus:ring-1 focus:ring-gold/30"
          />

          {line._corrections?.length > 0 && (
            <p className="font-sans text-[10px] text-gold-deep">
              {line._corrections.length} word{line._corrections.length !== 1 ? "s" : ""} auto-corrected.
              Editing this text will clear the corrections.
            </p>
          )}
        </div>
      ) : (
        // --- View mode ---
        <div
          className="flex items-start gap-3 px-3 py-2.5 cursor-pointer"
          onClick={onEdit}
          title="Click to edit"
        >
          {/* Character label */}
          <div className="shrink-0 w-28 pt-0.5">
            {isDirection ? (
              <span className="font-sans text-[10px] uppercase tracking-wider text-warmgray italic">Direction</span>
            ) : (
              <span className="font-sans text-xs font-semibold text-crimson uppercase tracking-wide truncate block">{line.character}</span>
            )}
          </div>

          {/* Line text with optional correction highlights */}
          <p className={`flex-1 font-body text-sm leading-relaxed min-w-0
            ${isDirection ? "text-warmgray italic" : "text-ink"}`}
          >
            {line.text
              ? renderTextWithHighlights(line.text)
              : <span className="text-warmgray-light">(empty)</span>
            }
          </p>

          {/* Row actions on hover */}
          <div className="shrink-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              title="Insert line below"
              onClick={(e) => { e.stopPropagation(); onInsertAfter(); }}
              className="w-6 h-6 flex items-center justify-center rounded bg-parchment-deep text-ink-muted hover:bg-parchment-deep/70 transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
            <button
              type="button"
              title="Delete line"
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              className="w-6 h-6 flex items-center justify-center rounded bg-parchment-deep text-ink-muted hover:bg-crimson/10 hover:text-crimson transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /><path d="M9 6V4h6v2" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
