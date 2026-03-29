import { useState, useMemo } from "react";
import { useNavigate, Link } from "react-router-dom";

// Derive the unique character names from the parsed script stored by Upload
function getScriptCharacters() {
  try {
    const raw = sessionStorage.getItem("parsedScript");
    if (!raw) return [];
    const lines = JSON.parse(raw);
    const seen = new Set();
    lines.forEach((l) => {
      if (l.type === "dialogue" && l.character) seen.add(l.character);
    });
    // Title-case for display (e.g. "OBERON" -> "Oberon")
    return [...seen].map((c) => c.charAt(0) + c.slice(1).toLowerCase());
  } catch {
    return [];
  }
}

export default function Setup() {
  const navigate = useNavigate();
  const detectedChars = useMemo(() => getScriptCharacters(), []);
  const [playName, setPlayName] = useState("");
  const [characterName, setCharacterName] = useState("");
  const [notes, setNotes] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState(null);

  const canSubmit = playName.trim() && characterName.trim() && !preparing;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;

    setPreparing(true);
    setError(null);

    try {
      const res = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          play: playName.trim(),
          character: characterName.trim(),
          notes: notes.trim(),
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Research failed (${res.status})`);
      }

      // Store setup info for the rehearsal page
      sessionStorage.setItem("rehearsalSetup", JSON.stringify({
        play: playName.trim(),
        character: characterName.trim(),
        notes: notes.trim(),
      }));

      navigate("/rehearse");
    } catch (err) {
      setError(err.message);
      setPreparing(false);
    }
  };

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      {/* Header */}
      <header className="px-6 py-4 border-b border-parchment-deep bg-parchment-warm/60">
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
              <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
              </svg>
            </div>
            <div>
              <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
              <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">Character Setup</span>
            </div>
          </Link>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        {preparing ? (
          /* Loading state */
          <div className="text-center animate-fade-in-up">
            <div className="w-16 h-16 mx-auto rounded-full bg-crimson/10 flex items-center justify-center mb-6">
              <div className="w-10 h-10 rounded-full border-3 border-parchment-deep border-t-crimson animate-spin" />
            </div>
            <h2 className="font-serif text-2xl font-bold text-ink">
              Your Director is studying the play...
            </h2>
            <p className="font-body text-sm text-ink-soft mt-3 max-w-md mx-auto">
              Searching for historical context, character analysis, and performance
              notes for {characterName} in <em>{playName}</em>. This may take a moment.
            </p>
          </div>
        ) : (
          /* Form */
          <form onSubmit={handleSubmit} className="max-w-lg w-full animate-fade-in-up">
            <h2 className="font-serif text-2xl sm:text-3xl font-bold text-ink text-center">
              Tell us about your role
            </h2>
            <p className="font-body text-sm text-ink-soft text-center mt-3">
              The director will research your play and character before rehearsal begins.
            </p>

            <div className="mt-8 space-y-5">
              {/* Play Name */}
              <div>
                <label htmlFor="play-name" className="block font-sans text-xs font-medium text-ink-muted uppercase tracking-wider mb-2">
                  Play Title
                </label>
                <input
                  id="play-name"
                  type="text"
                  value={playName}
                  onChange={(e) => setPlayName(e.target.value)}
                  placeholder="A Midsummer Night's Dream"
                  className="w-full px-4 py-3 rounded-xl bg-parchment-warm border border-parchment-deep
                    font-body text-sm text-ink placeholder:text-warmgray-light
                    focus:outline-none focus:border-gold focus:ring-1 focus:ring-gold/30
                    transition-all duration-200"
                />
              </div>

              {/* Character Name */}
              <div>
                <label htmlFor="character-name" className="block font-sans text-xs font-medium text-ink-muted uppercase tracking-wider mb-2">
                  Your Character
                </label>
                <input
                  id="character-name"
                  type="text"
                  list="character-suggestions"
                  value={characterName}
                  onChange={(e) => setCharacterName(e.target.value)}
                  placeholder="Oberon"
                  className="w-full px-4 py-3 rounded-xl bg-parchment-warm border border-parchment-deep
                    font-body text-sm text-ink placeholder:text-warmgray-light
                    focus:outline-none focus:border-gold focus:ring-1 focus:ring-gold/30
                    transition-all duration-200"
                />
                {detectedChars.length > 0 && (
                  <>
                    <datalist id="character-suggestions">
                      {detectedChars.map((c) => <option key={c} value={c} />)}
                    </datalist>
                    <p className="mt-1.5 font-sans text-[11px] text-warmgray">
                      Detected in your script: {detectedChars.join(", ")}
                    </p>
                  </>
                )}
              </div>

              {/* Notes */}
              <div>
                <label htmlFor="notes" className="block font-sans text-xs font-medium text-ink-muted uppercase tracking-wider mb-2">
                  Personal Notes <span className="text-warmgray-light">(optional)</span>
                </label>
                <textarea
                  id="notes"
                  rows={4}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Any context for the director: your interpretation, areas you want feedback on, scenes you're struggling with..."
                  className="w-full px-4 py-3 rounded-xl bg-parchment-warm border border-parchment-deep
                    font-body text-sm text-ink placeholder:text-warmgray-light resize-none
                    focus:outline-none focus:border-gold focus:ring-1 focus:ring-gold/30
                    transition-all duration-200"
                />
              </div>
            </div>

            {/* Error */}
            {error && (
              <div className="mt-4 p-4 rounded-xl bg-crimson/5 border border-crimson/20 text-center animate-fade-in-up">
                <p className="font-body text-sm text-crimson">{error}</p>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={!canSubmit}
              className={`mt-8 w-full flex items-center justify-center gap-2 py-4 px-6 rounded-xl
                font-sans font-semibold text-base transition-all duration-200
                ${canSubmit
                  ? "bg-crimson text-white hover:bg-crimson-muted active:scale-[0.98] shadow-lg shadow-crimson/25 cursor-pointer"
                  : "bg-parchment-deep text-warmgray cursor-not-allowed"
                }`}
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
              </svg>
              Prepare My Director
            </button>

            {/* Back */}
            <div className="mt-6 text-center">
              <Link
                to="/upload"
                className="font-sans text-xs text-warmgray hover:text-ink-soft transition-colors no-underline"
              >
                &larr; Back to upload
              </Link>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}
