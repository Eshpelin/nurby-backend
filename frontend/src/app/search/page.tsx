export default function SearchPage() {
  return (
    <div className="px-6 py-10 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight mb-2">
          Search your footage
        </h1>
        <p className="text-sm text-muted-foreground">
          Ask anything about what has happened across your cameras
        </p>
      </div>

      <div className="relative mb-4">
        <input
          type="text"
          placeholder="when did the fedex driver come this week"
          className="w-full bg-card border border-border focus:border-accent rounded-lg pl-12 pr-28 py-4 text-base focus:outline-none transition-colors"
        />
        <svg
          className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.35-4.35" />
        </svg>
        <span className="absolute right-4 top-1/2 -translate-y-1/2 font-mono text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground">
          enter to search
        </span>
      </div>

      <div className="flex flex-wrap gap-2 mb-8">
        <button className="px-3 py-1 text-xs rounded-full border border-border text-muted-foreground hover:bg-muted transition-colors">
          + camera
        </button>
        <button className="px-3 py-1 text-xs rounded-full border border-border text-muted-foreground hover:bg-muted transition-colors">
          + person
        </button>
        <button className="px-3 py-1 text-xs rounded-full border border-border text-muted-foreground hover:bg-muted transition-colors">
          + object
        </button>
        <button className="px-3 py-1 text-xs rounded-full border border-border text-muted-foreground hover:bg-muted transition-colors">
          + time range
        </button>
      </div>

      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-muted-foreground text-sm">
          Search requires a configured VLM provider and observation history.
          Connect cameras and configure a provider to get started.
        </p>
      </div>
    </div>
  );
}
