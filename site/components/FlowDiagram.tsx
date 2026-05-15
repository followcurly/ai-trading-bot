"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import flowPanels from "@/content/flow-panels.json";
import { resolvePanelKey } from "@/lib/resolve-panel-key";

type Panel = { title: string; body: string; module: string };

const panels = flowPanels as Record<string, Panel>;
const panelKeySet = new Set(Object.keys(panels));

export function FlowDiagram({ source }: { source: string }) {
  const uid = useId().replace(/\W/g, "");
  const [svg, setSvg] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const miniRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setErr(null);
      try {
        const mermaid = (await import("mermaid")).default;
        const dark =
          typeof window !== "undefined" &&
          window.matchMedia("(prefers-color-scheme: dark)").matches;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: dark ? "dark" : "default",
        });
        const { svg } = await mermaid.render(`mmd${uid}`, source);
        if (!cancelled) setSvg(svg);
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, uid]);

  useLayoutEffect(() => {
    if (!mainRef.current || !miniRef.current || !svg) return;
    const el = mainRef.current.querySelector("svg");
    if (!el) return;
    miniRef.current.innerHTML = "";
    const clone = el.cloneNode(true) as SVGSVGElement;
    clone.removeAttribute("style");
    miniRef.current.appendChild(clone);
  }, [svg]);

  const onDiagramClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    let el: Element | null = e.target as Element | null;
    while (el && el !== e.currentTarget) {
      if (el instanceof SVGGElement && el.classList.contains("node")) {
        const key = resolvePanelKey(el.id, panelKeySet);
        if (key) {
          setSelected(key);
          return;
        }
      }
      el = el.parentElement;
    }
  }, []);

  const panel = selected ? panels[selected] : null;

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-stretch">
      <div className="relative min-h-[420px] flex-1 overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950">
        <div
          className="absolute left-3 top-3 z-10 flex flex-wrap gap-2 rounded-lg border border-zinc-200 bg-white/90 px-2 py-1 text-xs text-zinc-600 shadow-sm backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/90 dark:text-zinc-300"
          role="note"
        >
          <span>Tip: click a box for a short description. Pinch or Ctrl+scroll to zoom.</span>
        </div>
        <div
          className="absolute bottom-2 right-2 z-10 h-28 w-40 overflow-hidden rounded-md border border-zinc-200 bg-white/90 shadow-sm dark:border-zinc-700 dark:bg-zinc-900/90"
          aria-hidden
        >
          <div
            ref={miniRef}
            className="flex h-full w-full items-start justify-start [&>svg]:h-auto [&>svg]:max-w-none [&>svg]:origin-top-left [&>svg]:scale-[0.14]"
          />
        </div>
        {err ? (
          <p className="p-6 text-sm text-red-600 dark:text-red-400">{err}</p>
        ) : !svg ? (
          <p className="p-6 text-sm text-zinc-500">Rendering diagram…</p>
        ) : (
          <TransformWrapper
            initialScale={0.9}
            minScale={0.25}
            maxScale={3.5}
            centerOnInit
            wheel={{ step: 0.12 }}
            doubleClick={{ disabled: true }}
          >
            <TransformComponent
              wrapperClass="!w-full !h-full min-h-[420px]"
              contentClass="flex min-h-[420px] w-full items-center justify-center p-6"
            >
              <div
                ref={mainRef}
                role="img"
                aria-label="Trading pipeline Mermaid diagram"
                className="cursor-grab active:cursor-grabbing [&_svg]:max-w-none"
                onClick={onDiagramClick}
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </TransformComponent>
          </TransformWrapper>
        )}
      </div>
      <aside
        className="w-full shrink-0 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900 lg:w-[min(100%,380px)]"
        aria-live="polite"
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Node detail
          </h2>
          {selected ? (
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
              onClick={() => setSelected(null)}
            >
              Clear
            </button>
          ) : null}
        </div>
        {panel ? (
          <div className="space-y-3">
            <h3 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">{panel.title}</h3>
            <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">{panel.body}</p>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              <span className="font-medium text-zinc-600 dark:text-zinc-300">Module:</span>{" "}
              {panel.module}
            </p>
          </div>
        ) : (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Select a node in the diagram to see curated notes. The dashed edge from{" "}
            <strong>regime</strong> to <strong>snapshot builder</strong> highlights benchmark equity
            slices (e.g. broad index ETFs) used when building cross-asset context.
          </p>
        )}
      </aside>
    </div>
  );
}
