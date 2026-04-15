export default function TimelinePage() {
  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Timeline</h1>
          <p className="text-sm text-muted-foreground mt-1">
            No observations yet
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 p-1 rounded-md bg-card border border-border">
            <button className="px-2.5 py-1 text-xs rounded bg-muted">
              Today
            </button>
            <button className="px-2.5 py-1 text-xs text-muted-foreground">
              7d
            </button>
            <button className="px-2.5 py-1 text-xs text-muted-foreground">
              30d
            </button>
            <button className="px-2.5 py-1 text-xs text-muted-foreground">
              Custom
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* Filter sidebar */}
        <aside className="col-span-3 space-y-5">
          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Camera
            </div>
            <p className="text-sm text-muted-foreground">
              Add cameras to filter by source
            </p>
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              People
            </div>
            <p className="text-sm text-muted-foreground">
              No people registered
            </p>
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Event type
            </div>
            <div className="space-y-1.5">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  defaultChecked
                  className="accent-green-500"
                />
                Rule fired
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  defaultChecked
                  className="accent-green-500"
                />
                Observation
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-green-500" />
                System
              </label>
            </div>
          </div>
        </aside>

        {/* Timeline feed */}
        <section className="col-span-9">
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
              ?
            </div>
            <p className="text-muted-foreground text-sm">
              Events and observations will appear here once cameras are
              connected and the perception pipeline is running.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
