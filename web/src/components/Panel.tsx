import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}

/** A ruled panel: uppercase micro title, optional right-aligned meta, hairline
    body. No shadows, no rounded cards — the instrument look. */
export function Panel({ title, meta, children, flush, className }: PanelProps) {
  return (
    <section className={`panel${className ? " " + className : ""}`}>
      <header className="panel__head">
        <span className="panel__title">{title}</span>
        {meta !== undefined ? <span className="panel__meta">{meta}</span> : null}
      </header>
      <div className={`panel__body${flush ? " panel__body--flush" : ""}`}>{children}</div>
    </section>
  );
}
