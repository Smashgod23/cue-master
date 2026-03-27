import { useNavigate } from "react-router-dom";

export default function Home() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      {/* Header */}
      <header className="px-6 py-4 border-b border-parchment-deep bg-parchment-warm/60">
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
            <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
            </svg>
          </div>
          <div>
            <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
            <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">AI Rehearsal Companion</span>
          </div>
        </div>
      </header>

      {/* Hero */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <div className="max-w-2xl w-full text-center animate-fade-in-up">
          <h2 className="font-serif text-4xl sm:text-5xl font-bold text-ink leading-tight">
            Your personal director,<br />always in the wings.
          </h2>
          <p className="font-body text-lg text-ink-soft mt-6 max-w-lg mx-auto leading-relaxed">
            Upload a script, pick your role, and rehearse with an AI that listens
            to your delivery, reads your scene partner's lines, and gives you
            real-time feedback on pacing, volume, and emotion.
          </p>

          <button
            onClick={() => navigate("/upload")}
            className="mt-10 inline-flex items-center gap-3 px-8 py-4 rounded-xl
              bg-crimson text-white font-sans font-semibold text-base
              hover:bg-crimson-muted active:scale-[0.98] transition-all duration-200
              shadow-lg shadow-crimson/25 cursor-pointer"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Start Rehearsal
          </button>
        </div>

        {/* Mode explanations */}
        <div className="max-w-3xl w-full mt-16 grid sm:grid-cols-2 gap-6">
          {/* Performance Mode */}
          <div className="bg-parchment-warm rounded-xl p-6 ring-1 ring-parchment-deep animate-fade-in-up" style={{ animationDelay: "100ms" }}>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-crimson/10 flex items-center justify-center">
                <svg className="w-5 h-5 text-crimson" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
              </div>
              <h3 className="font-serif text-lg font-bold text-ink">Performance Mode</h3>
            </div>
            <p className="font-body text-sm text-ink-soft leading-relaxed">
              Run your scene like it's opening night. The AI director watches your pacing,
              volume, and emotional delivery, then interrupts with targeted notes when
              something needs work. Your script stays hidden so you rely on memory.
            </p>
          </div>

          {/* Learning Mode */}
          <div className="bg-parchment-warm rounded-xl p-6 ring-1 ring-parchment-deep animate-fade-in-up" style={{ animationDelay: "200ms" }}>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-gold/15 flex items-center justify-center">
                <svg className="w-5 h-5 text-gold-deep" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                  <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                </svg>
              </div>
              <h3 className="font-serif text-lg font-bold text-ink">Learning Mode</h3>
            </div>
            <p className="font-body text-sm text-ink-soft leading-relaxed">
              Work through your lines with the script visible. The AI fuzzy-matches
              what you say against the expected text. Get it close enough and the scene
              advances. Miss it and you get a gentle nudge to try again.
            </p>
          </div>
        </div>

        {/* AI Director explanation */}
        <div className="max-w-3xl w-full mt-6 animate-fade-in-up" style={{ animationDelay: "300ms" }}>
          <div className="bg-parchment-warm rounded-xl p-6 ring-1 ring-parchment-deep">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-ink/8 flex items-center justify-center">
                <svg className="w-5 h-5 text-ink-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
                </svg>
              </div>
              <h3 className="font-serif text-lg font-bold text-ink">The AI Director</h3>
            </div>
            <p className="font-body text-sm text-ink-soft leading-relaxed">
              Before your rehearsal starts, the director researches your play and character
              online, building a knowledge base of historical context, acting analysis, and
              character motivations. During the scene, it listens to three things: your
              <strong className="text-ink"> pacing</strong> (words per minute),
              <strong className="text-ink"> volume</strong> (how loudly or softly you speak), and
              <strong className="text-ink"> accuracy</strong> (how closely you match the text).
              It combines that data with dramaturgical context to give you feedback
              that's specific to your scene, not generic acting advice.
            </p>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="px-6 py-4 border-t border-parchment-deep bg-parchment-warm/60">
        <p className="text-[10px] font-sans text-warmgray text-center uppercase tracking-widest">
          Cue Master — Everything runs locally on your machine. No cloud. No subscriptions.
        </p>
      </footer>
    </div>
  );
}
