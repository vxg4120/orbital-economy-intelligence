import { useLayoutEffect, useRef, useState } from "react";

/** The rendered content width of an element, kept current with a ResizeObserver. Charts draw
    in real pixels from it, so their 10 to 11px labels stay legible on a phone instead of
    shrinking with a scaled viewBox. Zero until the first measurement. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(Math.floor(el.clientWidth));
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w !== undefined) setWidth(Math.floor(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}
