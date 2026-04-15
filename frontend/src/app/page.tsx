export default function CamerasPage() {
  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cameras</h1>
          <p className="text-sm text-muted-foreground mt-1">
            No cameras configured yet
          </p>
        </div>
        <div className="flex gap-2">
          <button className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">
            Grid view
          </button>
          <button className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90">
            + Add camera
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {/* Empty state / add camera tile */}
        <div className="rounded-lg border border-dashed border-border bg-transparent hover:border-accent transition-colors cursor-pointer flex items-center justify-center aspect-video">
          <div className="text-center">
            <div className="w-10 h-10 rounded-full border border-border flex items-center justify-center mx-auto mb-2 text-muted-foreground">
              +
            </div>
            <div className="text-sm text-muted-foreground">Add camera</div>
            <div className="font-mono text-[11px] text-muted-foreground mt-1">
              ONVIF discover or RTSP url
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
