export default function RulesPage() {
  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Define what to watch for and what should happen
          </p>
        </div>
        <div className="flex gap-2">
          <button className="px-3 py-1.5 text-sm rounded-md bg-accent text-black font-medium hover:opacity-90">
            + Create rule
          </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* Rule list */}
        <section className="col-span-8">
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
              ?
            </div>
            <p className="text-muted-foreground text-sm mb-4">
              No rules created yet. Rules let you define triggers, conditions,
              and actions to automate your monitoring.
            </p>
            <button className="px-4 py-2 text-sm rounded-md border border-border hover:bg-muted transition-colors">
              Browse templates
            </button>
          </div>
        </section>

        {/* Preview panel placeholder */}
        <aside className="col-span-4">
          <div className="sticky top-20 rounded-lg border border-border bg-card p-5">
            <div className="flex items-center gap-2 mb-4">
              <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Preview
              </span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Select or create a rule to see a plain-language preview of what it
              does.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
