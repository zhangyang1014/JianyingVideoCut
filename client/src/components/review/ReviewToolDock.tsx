import { useEffect, useMemo, useState, type ReactNode } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type ReviewToolId = "export" | "funds" | "highlights" | "scenario" | "speakers" | "cuts" | "script";

export type ReviewToolItem = {
  id: ReviewToolId;
  label: string;
  description?: string;
  icon: ReactNode;
  content: ReactNode;
  hidden?: boolean;
  disabled?: boolean;
  lazy?: boolean;
};

type ReviewToolDockProps = {
  tools: ReviewToolItem[];
  className?: string;
  defaultActiveTool?: ReviewToolId | null;
};

export function ReviewToolDock({
  tools,
  className,
  defaultActiveTool = null,
}: ReviewToolDockProps) {
  const [activeTool, setActiveTool] = useState<ReviewToolId | null>(defaultActiveTool);
  const [openedTools, setOpenedTools] = useState<Set<ReviewToolId>>(() => new Set(defaultActiveTool ? [defaultActiveTool] : []));

  const visibleTools = useMemo(
    () => tools.filter((tool) => !tool.hidden),
    [tools],
  );
  const activeItem = visibleTools.find((tool) => tool.id === activeTool && !tool.disabled) ?? null;
  const expanded = Boolean(activeItem);

  useEffect(() => {
    // #region agent log
    fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H1',location:'ReviewToolDock.tsx:41',message:'review tool dock initialized',data:{defaultActiveTool,tools:tools.map((tool)=>({id:tool.id,hidden:Boolean(tool.hidden),disabled:Boolean(tool.disabled)}))},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, []);

  useEffect(() => {
    const panel = activeTool ? document.getElementById(`review-tool-panel-${activeTool}`) : null;
    // #region agent log
    fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H3',location:'ReviewToolDock.tsx:48',message:'review tool active state committed',data:{activeTool,activeItemId:activeItem?.id??null,expanded,panelExists:Boolean(panel),panelHidden:panel?.hidden??null,ariaHidden:panel?.closest('section')?.getAttribute('aria-hidden')??null},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, [activeTool]);

  useEffect(() => {
    const handleWindowError = (event: ErrorEvent) => {
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H5',location:'ReviewToolDock.tsx:56',message:'window error observed during review tool interaction',data:{errorName:event.error instanceof Error?event.error.name:null,errorMessage:event.error instanceof Error?event.error.message:event.message||null},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
    };
    const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H5',location:'ReviewToolDock.tsx:61',message:'unhandled rejection observed during review tool interaction',data:{reasonName:event.reason instanceof Error?event.reason.name:typeof event.reason,reasonMessage:event.reason instanceof Error?event.reason.message:null},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
    };
    window.addEventListener("error", handleWindowError);
    window.addEventListener("unhandledrejection", handleUnhandledRejection);
    return () => {
      window.removeEventListener("error", handleWindowError);
      window.removeEventListener("unhandledrejection", handleUnhandledRejection);
    };
  }, []);

  useEffect(() => {
    if (!activeTool) return;
    const nextActiveItem = visibleTools.find((tool) => tool.id === activeTool);
    if (!nextActiveItem || nextActiveItem.disabled) {
      setActiveTool(null);
    }
  }, [activeTool, visibleTools]);

  const toggleTool = (tool: ReviewToolItem) => {
    if (tool.disabled) return;
    setOpenedTools(current => current.has(tool.id) ? current : new Set(current).add(tool.id));
    if (tool.id === "highlights") {
      const diagnostics = document
        .getElementById("review-tool-panel-highlights")
        ?.querySelector<HTMLElement>("[data-highlight-clips-panel='true']")
        ?.dataset;
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H4',location:'ReviewToolDock.tsx:69',message:'highlight panel data snapshot before toggle',data:{panelDiagnostics:diagnostics?{qualityStage:diagnostics.qualityStage,activeReels:Number(diagnostics.activeReels),localReels:Number(diagnostics.localReels),clips:Number(diagnostics.clips),occurrenceGroups:Number(diagnostics.occurrenceGroups),legacyClips:Number(diagnostics.legacyClips),displayItems:Number(diagnostics.displayItems),auditSegments:Number(diagnostics.auditSegments),qualityReport:diagnostics.qualityReport}:null},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
    }
    setActiveTool((current) => {
      const next = current === tool.id ? null : tool.id;
      // #region agent log
      fetch('http://127.0.0.1:7296/ingest/805af596-423f-410e-860c-f2f1305f2af9',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'9f99b9'},body:JSON.stringify({sessionId:'9f99b9',runId:'pre-fix',hypothesisId:'H2',location:'ReviewToolDock.tsx:69',message:'review tool toggle requested',data:{toolId:tool.id,current,next,hidden:Boolean(tool.hidden),disabled:Boolean(tool.disabled)},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      return next;
    });
  };

  return (
    <aside
      className={cn(
        "h-full min-h-0 shrink-0 overflow-hidden border-l border-border bg-background/95 transition-[width] duration-200 ease-out",
        expanded ? "w-[26rem] xl:w-[30rem]" : "w-12",
        className,
      )}
      aria-label="审阅工具坞"
      data-expanded={expanded}
    >
      <div className="flex h-full min-h-0">
        <nav
          className="flex w-12 shrink-0 flex-col items-center gap-1 border-r border-border/70 bg-card/40 px-1.5 py-2"
          aria-label="审阅工具入口"
        >
          <div className="mb-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground/60">
            工具
          </div>
          {visibleTools.map((tool) => {
            const isActive = activeTool === tool.id && !tool.disabled;
            const panelId = `review-tool-panel-${tool.id}`;
            return (
              <Tooltip key={tool.id}>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className={cn(
                      "h-8 w-8 rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground",
                      isActive && "bg-accent text-primary ring-1 ring-primary/25",
                    )}
                    aria-label={isActive ? `收起${tool.label}` : `打开${tool.label}`}
                    aria-expanded={isActive}
                    aria-controls={panelId}
                    disabled={tool.disabled}
                    onClick={() => toggleTool(tool)}
                  >
                    {tool.icon}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={8}>
                  {tool.disabled ? `${tool.label}不可用` : tool.label}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </nav>

        <section
          className={cn(
            "flex min-w-0 flex-1 flex-col transition-opacity duration-150",
            expanded ? "opacity-100" : "pointer-events-none opacity-0",
          )}
          aria-hidden={!expanded}
        >
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-foreground">
                {activeItem?.label ?? "工具"}
              </p>
              {activeItem?.description && (
                <p className="truncate text-[10px] text-muted-foreground">
                  {activeItem.description}
                </p>
              )}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="h-7 w-7 text-muted-foreground hover:text-foreground"
              aria-label="收起工具面板"
              onClick={() => setActiveTool(null)}
              disabled={!activeItem}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">
            {visibleTools.map((tool) => (
              <div
                key={tool.id}
                id={`review-tool-panel-${tool.id}`}
                role="region"
                aria-label={tool.label}
                hidden={activeTool !== tool.id}
                className="h-full min-h-0 overflow-y-auto"
              >
                {(!tool.lazy || openedTools.has(tool.id) || activeTool === tool.id) && tool.content}
              </div>
            ))}
          </div>
        </section>
      </div>
    </aside>
  );
}
