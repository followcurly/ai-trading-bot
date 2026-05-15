/** Map a Mermaid-rendered SVG `<g class="node">` id to a `flow-panels.json` key. */
export function resolvePanelKey(
  svgGroupId: string,
  panelKeys: ReadonlySet<string>,
): string | undefined {
  const id = svgGroupId.replace(/^flowchart-/, "");
  const parts = id.split("-").filter((p) => p && !/^\d+$/.test(p));
  for (let i = parts.length - 1; i >= 0; i--) {
    const piece = parts[i];
    if (piece && panelKeys.has(piece)) return piece;
  }
  return undefined;
}
