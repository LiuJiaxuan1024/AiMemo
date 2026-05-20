import { useEffect, useId, useState } from "react";

interface MermaidGraphViewProps {
  chart: string;
  className: string;
  errorClassName: string;
  renderKey: string | number;
  themeVariables?: Record<string, string>;
}

const DEFAULT_THEME_VARIABLES = {
  fontFamily: "Inter, ui-sans-serif, system-ui",
  lineColor: "#98a2b3",
  textColor: "#1d2433",
};

export function MermaidGraphView({
  chart,
  className,
  errorClassName,
  renderKey,
  themeVariables = {},
}: MermaidGraphViewProps) {
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

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
          themeVariables: {
            ...DEFAULT_THEME_VARIABLES,
            ...themeVariables,
          },
        });
        return mermaid.render(`mermaid-graph-${id}-${renderKey}`, chart);
      })
      .then((result) => {
        if (!canceled) {
          setSvg(result.svg);
          setError("");
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
  }, [chart, id, renderKey, themeVariables]);

  return (
    <>
      {error ? <pre className={errorClassName}>{error}</pre> : null}
      {svg ? <div className={className} dangerouslySetInnerHTML={{ __html: svg }} /> : null}
    </>
  );
}
