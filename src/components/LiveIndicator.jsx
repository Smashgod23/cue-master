const stateConfig = {
  idle: {
    label: "Ready",
    color: "bg-warmgray",
    ringColor: "bg-warmgray/30",
    textColor: "text-warmgray",
    description: "Awaiting your cue",
  },
  listening: {
    label: "Listening",
    color: "bg-gold",
    ringColor: "bg-gold/30",
    textColor: "text-gold-deep",
    description: "Hearing your performance",
  },
  analyzing: {
    label: "Analyzing",
    color: "bg-crimson-soft",
    ringColor: "bg-crimson-soft/30",
    textColor: "text-crimson",
    description: "Reviewing your delivery",
  },
  speaking: {
    label: "Speaking",
    color: "bg-success",
    ringColor: "bg-success/30",
    textColor: "text-success",
    description: "Reading the other part",
  },
};

export default function LiveIndicator({ state = "idle" }) {
  const config = stateConfig[state] || stateConfig.idle;
  const isActive = state !== "idle";

  return (
    <div className="flex flex-col items-center gap-3 py-4">
      {/* Pulsing orb */}
      <div className="relative flex items-center justify-center w-20 h-20">
        {/* Outer breathing ring */}
        {isActive && (
          <span
            className={`absolute inset-0 rounded-full ${config.ringColor} animate-breathe-ring`}
          />
        )}

        {/* Middle ring */}
        {isActive && (
          <span
            className={`absolute inset-2 rounded-full ${config.ringColor} animate-breathe-ring`}
            style={{ animationDelay: "0.5s" }}
          />
        )}

        {/* Core orb */}
        <span
          className={`relative w-10 h-10 rounded-full ${config.color} transition-colors duration-700
            ${isActive ? "animate-breathe" : "opacity-40"}`}
        />
      </div>

      {/* State label */}
      <div className="text-center">
        <span
          className={`font-sans text-sm font-semibold uppercase tracking-widest ${config.textColor} transition-colors duration-500`}
        >
          {config.label}
        </span>
        <p className="font-body text-xs text-warmgray mt-0.5 italic">
          {config.description}
        </p>
      </div>
    </div>
  );
}
