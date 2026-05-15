"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import {
  TransformComponent,
  TransformWrapper,
  type ReactZoomPanPinchContentRef,
} from "react-zoom-pan-pinch";
import flowPanels from "@/content/flow-panels.json";
import { resolvePanelKey } from "@/lib/resolve-panel-key";

type Panel = { title: string; body: string; module: string };

const panels = flowPanels as Record<string, Panel>;
const panelKeySet = new Set(Object.keys(panels));

const MINI_W = 144;
const MINI_H = 96;
const MINI_PAD = 6;

const CATEGORY_COLORS: Record<string, { fill: string; ring: string; label: string }> = {
  src: { fill: "bg-blue-500/15", ring: "ring-blue-500/40", label: "External feed" },
  proc: { fill: "bg-emerald-500/15", ring: "ring-emerald-500/40", label: "Pipeline stage" },
  brain: { fill: "bg-violet-500/20", ring: "ring-violet-500/50", label: "Brain / regime" },
  risk: { fill: "bg-orange-500/15", ring: "ring-orange-500/40", label: "Risk policy" },
  exec: { fill: "bg-rose-500/15", ring: "ring-rose-500/40", label: "Executor" },
  store: { fill: "bg-indigo-500/15", ring: "ring-indigo-500/40", label: "Storage" },
  sink: { fill: "bg-zinc-500/15", ring: "ring-zinc-500/40", label: "Sink" },
  sched: { fill: "bg-amber-500/15", ring: "ring-amber-500/40", label: "Schedule" },
};

const CATEGORY_FOR_KEY: Record<string, keyof typeof CATEGORY_COLORS> = {
  sched: "sched",
  reg: "brain",
  wl: "proc",
  feed: "proc",
  enr: "proc",
  snap: "proc",
  trd: "brain",
  risk: "risk",
  exe: "exec",
  journal: "proc",
  alp: "sink",
  db: "store",
  jsl: "store",
  web: "sink",
  hc: "sink",
  met: "store",
  eodMD: "store",
  crSun: "sched",
  wrpy: "brain",
  mdLatest: "store",
  blogUI: "sink",
  fbmd: "proc",
  aMD: "src",
  aTR: "src",
  aOPT: "src",
  yfin: "src",
  rss: "src",
  fred: "src",
  vix: "src",
  kal: "src",
  poly: "src",
  cnnfg: "src",
  stw: "src",
  cotF: "src",
  edgar: "src",
  fh: "src",
  mktx: "src",
  rdd: "src",
  qv: "src",
};

const toolbarBtn =
  "rounded-md border border-zinc-200 bg-white/95 px-1.5 py-0.5 text-[11px] font-semibold text-zinc-700 shadow-sm hover:bg-zinc-50 sm:px-2 sm:py-1 sm:text-xs";

export function FlowDiagram({ source }: { source: string }) {
  const uid = useId().replace(/\W/g, "");
  const [svg, setSvg] = useState<string>("");
  const [miniSvg, setMiniSvg] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showMinimap, setShowMinimap] = useState(true);
  const [fitScale, setFitScale] = useState<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const miniMountRef = useRef<HTMLDivElement>(null);
  const tfRef = useRef<ReactZoomPanPinchContentRef | null>(null);

  const fitToView = useCallback(() => {
    const wrapper = wrapperRef.current;
    const svgEl = mainRef.current?.querySelector("svg") as SVGSVGElement | null;
    if (!wrapper || !svgEl || !tfRef.current) return;
    const wrapperW = wrapper.clientWidth;
    const wrapperH = wrapper.clientHeight;
    const bbox = svgEl.getBBox();
    if (bbox.width <= 0 || bbox.height <= 0 || wrapperW <= 0 || wrapperH <= 0) return;
    const PAD = 32;
    const sx = (wrapperW - PAD * 2) / bbox.width;
    const sy = (wrapperH - PAD * 2) / bbox.height;
    const scale = Math.min(sx, sy, 1);
    const x = (wrapperW - bbox.width * scale) / 2 - bbox.x * scale;
    const y = (wrapperH - bbox.height * scale) / 2 - bbox.y * scale;
    tfRef.current.setTransform(x, y, scale, 0);
    setFitScale(scale);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setErr(null);
      setMiniSvg("");
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: "default",
          flowchart: { useMaxWidth: false },
        });
        const mainId = `mmdMain${uid}`;
        const miniId = `mmdMini${uid}`;
        const [mainOut, miniOut] = await Promise.all([
          mermaid.render(mainId, source),
          mermaid.render(miniId, source),
        ]);
        if (!cancelled) {
          setSvg(mainOut.svg);
          setMiniSvg(miniOut.svg);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, uid]);

  useLayoutEffect(() => {
    if (!showMinimap || !miniSvg || !miniMountRef.current) return;
    const svg = miniMountRef.current.querySelector("svg") as SVGSVGElement | null;
    if (!svg) return;
    const bbox = svg.getBBox();
    if (bbox.width <= 0 || bbox.height <= 0) return;
    const iw = MINI_W - MINI_PAD * 2;
    const ih = MINI_H - MINI_PAD * 2;
    const s = Math.min(iw / bbox.width, ih / bbox.height, 1);
    svg.style.transformOrigin = "0 0";
    svg.style.transform = `scale(${s})`;
  }, [miniSvg, showMinimap]);

  useEffect(() => {
    if (!svg) return;
    const raf1 = requestAnimationFrame(() => {
      requestAnimationFrame(() => fitToView());
    });
    return () => cancelAnimationFrame(raf1);
  }, [svg, fitToView]);

  useEffect(() => {
    if (!svg) return;
    const onResize = () => fitToView();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [svg, fitToView]);

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
  const cat = selected ? CATEGORY_FOR_KEY[selected] : undefined;
  const catTone = cat ? CATEGORY_COLORS[cat] : null;

  return (
    <div className="flex flex-col gap-4">
      <div
        ref={wrapperRef}
        className="relative h-[420px] overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-inner sm:h-[520px] lg:h-[640px] dark:border-zinc-800 dark:bg-zinc-100"
      >
        <div
          className="pointer-events-none absolute left-3 top-3 z-30 max-w-[55%] rounded-lg border border-zinc-200 bg-white/95 px-2 py-1 text-[11px] font-medium text-zinc-700 shadow-sm backdrop-blur sm:max-w-none sm:px-2.5 sm:text-xs"
          role="note"
        >
          <span className="sm:hidden">Tap a box · pinch to zoom</span>
          <span className="hidden sm:inline">
            Tip: click a box for a description · pinch / Ctrl+scroll to zoom · drag to pan ·
            double-click to reset
          </span>
        </div>
        <div className="absolute right-3 top-3 z-30 flex items-center gap-1 sm:gap-1.5">
          <button
            type="button"
            onClick={() => tfRef.current?.zoomOut()}
            className={toolbarBtn}
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            onClick={() => fitToView()}
            className={toolbarBtn}
            aria-label="Fit to view"
          >
            Fit
          </button>
          <button
            type="button"
            onClick={() => tfRef.current?.zoomIn()}
            className={toolbarBtn}
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setShowMinimap((v) => !v)}
            className={`${toolbarBtn} hidden sm:inline-flex`}
            aria-pressed={showMinimap}
          >
            {showMinimap ? "Hide map" : "Map"}
          </button>
        </div>
        {err ? (
          <p className="p-6 text-sm text-red-600">{err}</p>
        ) : !svg ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-zinc-500">Rendering diagram…</p>
          </div>
        ) : (
          <TransformWrapper
            initialScale={fitScale ?? 1}
            minScale={fitScale ? Math.max(fitScale * 0.6, 0.1) : 0.1}
            maxScale={4}
            limitToBounds={false}
            centerZoomedOut={false}
            wheel={{ step: 0.15 }}
            doubleClick={{ mode: "reset" }}
            ref={(ref) => {
              tfRef.current = ref;
            }}
          >
            <TransformComponent wrapperClass="!w-full !h-full" contentClass="!w-auto !h-auto">
              <div
                ref={mainRef}
                role="img"
                aria-label="Trading pipeline Mermaid diagram"
                className="cursor-grab select-none active:cursor-grabbing [&_svg]:block [&_svg]:max-w-none"
                onClick={onDiagramClick}
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </TransformComponent>
          </TransformWrapper>
        )}
        {showMinimap && miniSvg ? (
          <div
            className="pointer-events-none absolute bottom-3 right-3 z-20 hidden h-24 w-36 overflow-hidden rounded-md border border-zinc-200 bg-white/95 shadow-sm md:block"
            aria-hidden
          >
            <div
              ref={miniMountRef}
              className="flex h-full w-full items-start justify-start overflow-hidden p-1.5 [&_svg]:block [&_svg]:max-w-none"
              dangerouslySetInnerHTML={{ __html: miniSvg }}
            />
          </div>
        ) : null}
      </div>

      <aside
        className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5 dark:border-zinc-800 dark:bg-zinc-900"
        aria-live="polite"
      >
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
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
          <div className="grid gap-4 md:grid-cols-[1fr_280px]">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                  {panel.title}
                </h3>
                {catTone ? (
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-800 ring-1 ${catTone.fill} ${catTone.ring} dark:text-zinc-100`}
                  >
                    {catTone.label}
                  </span>
                ) : null}
              </div>
              <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                {panel.body}
              </p>
            </div>
            <div className="self-start rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-300">
              <div className="mb-1 font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Module
              </div>
              <code className="font-mono text-[12.5px] text-zinc-800 dark:text-zinc-100">
                {panel.module}
              </code>
            </div>
          </div>
        ) : (
          <div className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
            <p>
              Click a box in the diagram to see curated notes. The dashed edge from{" "}
              <strong className="text-zinc-800 dark:text-zinc-100">regime</strong> to{" "}
              <strong className="text-zinc-800 dark:text-zinc-100">snapshot builder</strong>{" "}
              highlights benchmark equity slices (e.g. broad index ETFs) used when assembling
              cross-asset context.
            </p>
            <div className="flex flex-wrap gap-2 pt-2">
              {Object.entries(CATEGORY_COLORS).map(([key, c]) => (
                <span
                  key={key}
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-800 ring-1 ${c.fill} ${c.ring} dark:text-zinc-100`}
                >
                  {c.label}
                </span>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
