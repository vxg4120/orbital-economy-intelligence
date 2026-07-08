import { sourceClass } from "../lib/status";

/** Provenance source (satcat / gcat / ucs / resolve / …) as a small mono badge
    with a low-chroma identity tick on the left edge. */
export function SourceBadge({ source }: { source: string }) {
  return (
    <span className={`badge badge--src ${sourceClass(source)}`} title={`Source: ${source}`}>
      {source}
    </span>
  );
}
