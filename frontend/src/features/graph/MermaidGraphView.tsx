import type { MouseEvent, PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

interface MermaidGraphViewProps {
  chart: string;
  className: string;
  errorClassName: string;
  renderKey: string | number;
  onNodeClick?: (nodeId: string) => void;
  themeVariables?: Record<string, string>;
}

const DEFAULT_THEME_VARIABLES = {
  fontFamily: "Inter, ui-sans-serif, system-ui",
  lineColor: "#98a2b3",
  textColor: "#1d2433",
};
const EMPTY_THEME_VARIABLES: Record<string, string> = {};
const MIN_SCALE = 0.35;
const MAX_SCALE = 3;
const ZOOM_STEP = 1.18;

export function MermaidGraphView({
  chart,
  className,
  errorClassName,
  onNodeClick,
  renderKey,
  themeVariables = EMPTY_THEME_VARIABLES,
}: MermaidGraphViewProps) {
  const id = useId().replace(/:/g, "");
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const dragStateRef = useRef({
    isDragging: false,
    hasMoved: false,
    pointerId: 0,
    targetNodeId: null as string | null,
    startX: 0,
    startY: 0,
    originX: 0,
    originY: 0,
  });
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [viewport, setViewport] = useState({ scale: 1, x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [isCtrlPressed, setIsCtrlPressed] = useState(false);
  const nodeIds = useMemo(() => extractNodeIds(chart), [chart]);
  const themeVariablesKey = JSON.stringify(themeVariables);
  const mergedThemeVariables = useMemo(
    () => ({
      ...DEFAULT_THEME_VARIABLES,
      ...themeVariables,
    }),
    [themeVariablesKey],
  );

  useEffect(() => {
    if (!chart) {
      setSvg("");
      setError("");
      return;
    }

    let canceled = false;
    import("mermaid")
      .then((module) => {
        const mermaid = module.default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: "base",
          themeVariables: mergedThemeVariables,
        });
        return mermaid.render(`mermaid-graph-${id}-${renderKey}`, chart);
      })
      .then((result) => {
        if (!canceled) {
          setSvg(result.svg);
          setError("");
          setViewport({ scale: 1, x: 0, y: 0 });
        }
      })
      .catch((currentError: unknown) => {
        if (!canceled) {
          setSvg("");
          setError(currentError instanceof Error ? currentError.message : "流程图渲染失败");
        }
      });

    return () => {
      canceled = true;
    };
  }, [chart, id, renderKey, mergedThemeVariables]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Control") {
        setIsCtrlPressed(true);
      }
    }

    function handleKeyUp(event: KeyboardEvent) {
      if (event.key === "Control") {
        setIsCtrlPressed(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, []);

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || !svg) {
      return;
    }
    const currentSurface = surface;

    function handleNativeWheel(event: WheelEvent) {
      event.preventDefault();
      event.stopPropagation();
      const zoomFactor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      const rect = currentSurface.getBoundingClientRect();
      const pointerX = event.clientX - rect.left;
      const pointerY = event.clientY - rect.top;
      setViewport((current) => {
        const nextScale = clamp(current.scale * zoomFactor, MIN_SCALE, MAX_SCALE);
        const realFactor = nextScale / current.scale;
        return {
          scale: nextScale,
          x: pointerX - (pointerX - current.x) * realFactor,
          y: pointerY - (pointerY - current.y) * realFactor,
        };
      });
    }

    currentSurface.addEventListener("wheel", handleNativeWheel, { passive: false, capture: true });
    return () => {
      currentSurface.removeEventListener("wheel", handleNativeWheel, { capture: true });
    };
  }, [svg]);

  useEffect(() => {
    function handleWindowPointerMove(event: PointerEvent) {
      const dragState = dragStateRef.current;
      if (!dragState.isDragging || dragState.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      moveViewportForPointer(event.clientX, event.clientY);
    }

    function handleWindowPointerEnd(event: PointerEvent) {
      const dragState = dragStateRef.current;
      if (!dragState.isDragging || dragState.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      finishPointerInteraction(event.clientX, event.clientY, event.ctrlKey);
    }

    window.addEventListener("pointermove", handleWindowPointerMove, { passive: false });
    window.addEventListener("pointerup", handleWindowPointerEnd, { passive: false });
    window.addEventListener("pointercancel", handleWindowPointerEnd, { passive: false });
    return () => {
      window.removeEventListener("pointermove", handleWindowPointerMove);
      window.removeEventListener("pointerup", handleWindowPointerEnd);
      window.removeEventListener("pointercancel", handleWindowPointerEnd);
    };
  }, [onNodeClick, nodeIds]);

  function absorbWheel(event: React.WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }

    dragStateRef.current = {
      isDragging: true,
      hasMoved: false,
      pointerId: event.pointerId,
      targetNodeId: findClickedNodeId(event.target, nodeIds),
      startX: event.clientX,
      startY: event.clientY,
      originX: viewport.x,
      originY: viewport.y,
    };
    event.preventDefault();
    event.stopPropagation();
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Some Linux browser/SVG combinations fail pointer capture. Window listeners still handle dragging.
    }
    setIsDragging(true);
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const dragState = dragStateRef.current;
    if (!dragState.isDragging || dragState.pointerId !== event.pointerId) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    moveViewportForPointer(event.clientX, event.clientY);
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const dragState = dragStateRef.current;
    if (dragState.pointerId === event.pointerId) {
      event.preventDefault();
      event.stopPropagation();
      finishPointerInteraction(event.clientX, event.clientY, event.ctrlKey);
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may not have been established.
      }
    }
  }

  function handleDoubleClick(event: MouseEvent<HTMLDivElement>) {
    if (findClickedNodeId(event.target, nodeIds)) {
      return;
    }
    setViewport({ scale: 1, x: 0, y: 0 });
  }

  function zoomAt(clientX: number, clientY: number, factor: number) {
    const surface = surfaceRef.current;
    if (!surface) {
      return;
    }

    const rect = surface.getBoundingClientRect();
    const pointerX = clientX - rect.left;
    const pointerY = clientY - rect.top;
    setViewport((current) => {
      const nextScale = clamp(current.scale * factor, MIN_SCALE, MAX_SCALE);
      const realFactor = nextScale / current.scale;
      return {
        scale: nextScale,
        x: pointerX - (pointerX - current.x) * realFactor,
        y: pointerY - (pointerY - current.y) * realFactor,
      };
    });
  }

  function moveViewportForPointer(clientX: number, clientY: number) {
    const dragState = dragStateRef.current;
    const dx = clientX - dragState.startX;
    const dy = clientY - dragState.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      dragState.hasMoved = true;
    }
    setViewport((current) => ({
      ...current,
      x: dragState.originX + dx,
      y: dragState.originY + dy,
    }));
  }

  function finishPointerInteraction(clientX: number, clientY: number, ctrlKey: boolean) {
    const dragState = dragStateRef.current;
    if (!dragState.isDragging) {
      return;
    }
    if (!dragState.hasMoved) {
      if (dragState.targetNodeId && onNodeClick) {
        onNodeClick(dragState.targetNodeId);
      } else {
        zoomAt(clientX, clientY, ctrlKey ? 1 / ZOOM_STEP : ZOOM_STEP);
      }
    }
    dragState.isDragging = false;
    setIsDragging(false);
  }

  return (
    <div className="mermaid-viewer">
      <div className="mermaid-zoom-indicator">{Math.round(viewport.scale * 100)}%</div>
      {error ? <pre className={errorClassName}>{error}</pre> : null}
      {svg ? (
        <div
          className={`mermaid-pan-surface${isDragging ? " is-dragging" : ""}${isCtrlPressed ? " is-ctrl-pressed" : ""}`}
          onDoubleClick={handleDoubleClick}
          onPointerCancel={handlePointerUp}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onWheel={absorbWheel}
          ref={surfaceRef}
        >
          <div
            className={className}
            dangerouslySetInnerHTML={{ __html: svg }}
            ref={graphRef}
            style={{
              transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function findClickedNodeId(target: EventTarget | null, nodeIds: string[]) {
  if (!(target instanceof Element)) {
    return null;
  }
  const nodeElement = target.closest("g.node");
  if (!nodeElement) {
    return null;
  }
  const rawId = nodeElement.id || "";
  const normalizedRawId = normalizeMermaidNodeText(rawId);
  const titleText = normalizeMermaidNodeText(nodeElement.querySelector("title")?.textContent ?? "");
  const labelText = normalizeMermaidNodeText(nodeElement.textContent ?? "");
  const candidates = [titleText, normalizedRawId, labelText].filter(Boolean);
  for (const candidate of candidates) {
    const exactMatch = nodeIds.find((nodeId) => candidate === nodeId);
    if (exactMatch) {
      return exactMatch;
    }
  }
  for (const candidate of candidates) {
    const containsMatch = nodeIds.find((nodeId) => candidate.includes(nodeId));
    if (containsMatch) {
      return containsMatch;
    }
  }
  return null;
}

function normalizeMermaidNodeText(value: string) {
  return value
    .replace(/^flowchart-/, "")
    .replace(/-\d+$/, "")
    .replace(/<[^>]+>/g, "")
    .trim();
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function extractNodeIds(chart: string) {
  const nodeIds = new Set<string>();
  for (const line of chart.split(/\r?\n/)) {
    const classMatch = line.match(/^class\s+([A-Za-z0-9_:-]+)/);
    if (classMatch?.[1]) {
      nodeIds.add(classMatch[1]);
    }
    const edgeMatches = line.matchAll(/(?:^|\s|-->|---|-.->)([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[|\(|\{|-->|---|-.->|$)/g);
    for (const match of edgeMatches) {
      if (match[1] && !["flowchart", "graph", "subgraph", "classDef", "class"].includes(match[1])) {
        nodeIds.add(match[1]);
      }
    }
  }
  return [...nodeIds].sort((left, right) => right.length - left.length);
}
