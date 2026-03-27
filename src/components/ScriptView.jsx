import { useRef, useEffect } from "react";
import { scriptLines, characters, userCharacter, scriptMeta } from "../data/dummyScript";

export default function ScriptView({ activeLine, onLineClick }) {
  const activeRef = useRef(null);

  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeLine]);

  return (
    <div className="h-full flex flex-col">
      {/* Script header - playbill style */}
      <div className="px-8 pt-6 pb-4 border-b border-parchment-deep">
        <div className="text-center">
          <h2 className="font-serif text-2xl font-bold text-ink tracking-wide">
            {scriptMeta.title}
          </h2>
          <p className="font-serif text-sm italic text-ink-muted mt-1">
            by {scriptMeta.playwright}
          </p>
          <div className="mt-3 flex items-center justify-center gap-4">
            <span className="text-xs font-sans font-medium uppercase tracking-widest text-warmgray">
              {scriptMeta.act}
            </span>
            <span className="w-1 h-1 rounded-full bg-gold" />
            <span className="text-xs font-sans font-medium uppercase tracking-widest text-warmgray">
              {scriptMeta.scene}
            </span>
          </div>
        </div>

        {/* Character legend */}
        <div className="mt-4 flex flex-wrap justify-center gap-3">
          {Object.entries(characters).map(([key, char]) => (
            <span
              key={key}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-sans font-medium
                ${key === userCharacter
                  ? "bg-crimson/10 text-crimson ring-1 ring-crimson/30"
                  : "bg-parchment-deep/60 text-ink-muted"
                }`}
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: char.color }}
              />
              {char.name}
              {key === userCharacter && (
                <span className="text-[10px] uppercase tracking-wider opacity-70 ml-0.5">
                  (You)
                </span>
              )}
            </span>
          ))}
        </div>
      </div>

      {/* Script body */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-1">
        {scriptLines.map((line) => {
          const isActive = line.id === activeLine;
          const isUserLine = line.character === userCharacter;

          if (line.type === "stage_direction") {
            return (
              <div
                key={line.id}
                ref={isActive ? activeRef : null}
                onClick={() => onLineClick(line.id)}
                className={`py-3 px-5 cursor-pointer rounded-lg transition-all duration-300
                  ${isActive
                    ? "bg-gold/10 ring-1 ring-gold/30"
                    : "hover:bg-parchment-warm/60"
                  }`}
              >
                <p className="font-body text-base italic text-ink-muted leading-relaxed">
                  [{line.text}]
                </p>
              </div>
            );
          }

          const char = characters[line.character];

          return (
            <div
              key={line.id}
              ref={isActive ? activeRef : null}
              onClick={() => onLineClick(line.id)}
              className={`py-3 px-5 cursor-pointer rounded-lg transition-all duration-300
                ${isActive
                  ? isUserLine
                    ? "bg-crimson/8 ring-1 ring-crimson/25 animate-gentle-glow"
                    : "bg-gold/8 ring-1 ring-gold/25"
                  : "hover:bg-parchment-warm/60"
                }
                ${isUserLine ? "pl-5 border-l-3 border-crimson/40" : ""}
              `}
            >
              <span
                className="font-sans text-xs font-semibold uppercase tracking-widest mb-1.5 block"
                style={{ color: char?.color || "#6B6770" }}
              >
                {char?.name || line.character}
              </span>
              <p
                className={`font-body leading-relaxed
                  ${isUserLine ? "text-lg text-ink font-medium" : "text-base text-ink-soft"}
                `}
              >
                {line.text}
              </p>
            </div>
          );
        })}

        {/* End of scene marker */}
        <div className="text-center py-8">
          <div className="inline-flex items-center gap-3 text-warmgray">
            <span className="w-12 h-px bg-warmgray-light" />
            <span className="font-serif text-sm italic">End of excerpt</span>
            <span className="w-12 h-px bg-warmgray-light" />
          </div>
        </div>
      </div>
    </div>
  );
}
