import { useEffect, useState } from "react";

import { SPINNER_VERBS } from "./spinnerVerbs";

const ROTATION_MS = 2000;

interface VerbRotatorProps {
  fallback?: string;
}

function randomVerb(prev?: string): string {
  if (SPINNER_VERBS.length === 0) {
    return "思考中";
  }
  let next = SPINNER_VERBS[Math.floor(Math.random() * SPINNER_VERBS.length)] ?? "思考中";
  if (SPINNER_VERBS.length > 1 && next === prev) {
    next = SPINNER_VERBS[(SPINNER_VERBS.indexOf(next) + 1) % SPINNER_VERBS.length] ?? next;
  }
  return next;
}

export function VerbRotator({ fallback = "思考中" }: VerbRotatorProps) {
  const [verb, setVerb] = useState(() => randomVerb());
  const [fadeKey, setFadeKey] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => {
      setVerb((prev) => randomVerb(prev));
      setFadeKey((value) => value + 1);
    }, ROTATION_MS);
    return () => window.clearInterval(id);
  }, []);

  return (
    <span className="chat-verb-rotator" key={fadeKey}>
      {verb || fallback}
    </span>
  );
}
