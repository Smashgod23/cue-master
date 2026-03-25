export default function ModeToggle({ mode, onToggle }) {
  const isPerformance = mode === "performance";

  return (
    <div className="flex flex-col items-center gap-2">
      <span className="font-sans text-[10px] uppercase tracking-[0.2em] text-warmgray font-medium">
        Rehearsal Mode
      </span>

      <button
        onClick={onToggle}
        className="relative flex items-center bg-parchment-deep rounded-full p-1 w-64 h-12 cursor-pointer
          ring-1 ring-parchment-deep hover:ring-gold/30 transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-gold/50"
        aria-label={`Switch to ${isPerformance ? "Learning" : "Performance"} mode`}
      >
        {/* Sliding indicator */}
        <span
          className={`absolute top-1 h-10 w-[calc(50%-4px)] rounded-full transition-all duration-500 ease-[cubic-bezier(0.34,1.56,0.64,1)]
            ${isPerformance
              ? "left-1 bg-crimson shadow-md shadow-crimson/20"
              : "left-[calc(50%+3px)] bg-gold-deep shadow-md shadow-gold/30"
            }`}
        />

        {/* Performance label */}
        <span
          className={`relative z-10 flex-1 flex items-center justify-center gap-1.5 text-sm font-sans font-medium transition-colors duration-300
            ${isPerformance ? "text-white" : "text-ink-muted"}`}
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41" />
          </svg>
          Performance
        </span>

        {/* Learning label */}
        <span
          className={`relative z-10 flex-1 flex items-center justify-center gap-1.5 text-sm font-sans font-medium transition-colors duration-300
            ${!isPerformance ? "text-white" : "text-ink-muted"}`}
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
          </svg>
          Learning
        </span>
      </button>

      <p className="font-body text-xs text-warmgray italic mt-0.5">
        {isPerformance
          ? "Lines hidden — speak from memory"
          : "Lines visible — learn at your pace"}
      </p>
    </div>
  );
}
