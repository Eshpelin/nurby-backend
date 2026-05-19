// Pure summary chip used in both the modal preview and the
// per-rule sidebar. Lifted verbatim from frontend/src/app/rules/page.tsx.

export function SummaryCard({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`bg-blue-500/10 border border-blue-500/20 rounded-lg text-sm text-zinc-200 flex gap-3 items-start ${className || "p-4"}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="text-blue-400 flex-shrink-0 mt-0.5">
        <path d="M9 18h6" /><path d="M10 22h4" /><path d="M12 2a7 7 0 0 0-4 12.7c.6.4 1 .8 1 1.3v2h6v-2c0-.5.4-.9 1-1.3A7 7 0 0 0 12 2z" />
      </svg>
      <span>{text}</span>
    </div>
  );
}
