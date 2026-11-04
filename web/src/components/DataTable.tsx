import type { ReactNode } from "react";
import { fmtInt } from "../lib/format";

export interface Column<Row> {
  key: string;
  header: string;
  /** The sort button's tooltip; defaults to "Sort by <header>", so an abbreviated header
      spells itself out here. */
  title?: string;
  num?: boolean;
  sortable?: boolean;
  render: (row: Row) => ReactNode;
}

export interface SortSpec {
  key: string;
  dir: "asc" | "desc";
}

interface DataTableProps<Row> {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string | number;
  onRowClick?: (row: Row) => void;
  sort?: SortSpec;
  onSort?: (key: string) => void;
  zebra?: boolean;
  /** Pin the first column while the rest scrolls sideways (phones only, see theme.css). */
  stickyFirst?: boolean;
}

/** Dense hairline table. Numeric columns are right-aligned tabular. When
    onRowClick is set the whole row is a keyboard-reachable deep link. */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  onRowClick,
  sort,
  onSort,
  zebra,
  stickyFirst,
}: DataTableProps<Row>) {
  return (
    <div className={`table-wrap${stickyFirst ? " table-wrap--sticky-first" : ""}`}>
      <table className={`dtable${zebra ? " dtable--zebra" : ""}`}>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const sortable = c.sortable && onSort;
              return (
                <th
                  key={c.key}
                  className={`${c.num ? "is-num" : ""}${sortable ? " is-sortable" : ""}`}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  {sortable ? (
                    // a real button, so the sort is reachable from the keyboard; it inherits
                    // the header's type and fills the cell (the th drops its padding for it)
                    <button
                      type="button"
                      className="sort-btn"
                      onClick={() => onSort(c.key)}
                      title={c.title ?? `Sort by ${c.header}`}
                    >
                      {c.header}
                      {active ? (
                        <span className="sort-caret" aria-hidden="true">
                          {sort!.dir === "asc" ? "▲" : "▼"}
                        </span>
                      ) : (
                        // a dim ↕ on every other sortable column, so it is clear they sort too
                        <span className="sort-caret is-idle" aria-hidden="true">
                          ↕
                        </span>
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const clickable = Boolean(onRowClick);
            return (
              // Keep the native row semantics (role="row") so screen readers still
              // associate cells with their column headers; the row stays clickable and
              // Enter/Space-activatable via tabIndex + onKeyDown, no role override.
              <tr
                key={rowKey(row)}
                className={clickable ? "is-link" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                tabIndex={clickable ? 0 : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        // a key on a link or control inside the row (a receipt link) is that
                        // control's own activation, not the row's
                        if (e.target !== e.currentTarget) return;
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick(row);
                        }
                      }
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.num ? "is-num" : undefined}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A right-aligned pager for LIMIT/OFFSET endpoints. */
export function Pager({
  offset,
  limit,
  total,
  shown,
  onOffset,
}: {
  offset: number;
  limit: number;
  total: number;
  /** Rows on this page, when known: the range then ends where the page really ends. */
  shown?: number;
  onOffset: (next: number) => void;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + (shown ?? limit), total);
  // a page past the end (the list shrank since it was counted) has no range to print
  const empty = shown === 0 && total > 0;
  const canPrev = offset > 0;
  const canNext = offset + limit < total;
  return (
    <div className="pager">
      <span className="num">
        {empty
          ? `nothing on this page · ${fmtInt(total)} total`
          : `${fmtInt(from)}–${fmtInt(to)} of ${fmtInt(total)}`}
      </span>
      <span className="pager__spacer" />
      <button className="btn" disabled={!canPrev} onClick={() => onOffset(Math.max(0, offset - limit))}>
        ‹ Prev
      </button>
      <button className="btn" disabled={!canNext} onClick={() => onOffset(offset + limit)}>
        Next ›
      </button>
    </div>
  );
}

/** Renders a nullable numeric/text cell as em-dash when absent. */
export function Cell({ children }: { children: ReactNode }) {
  if (children === null || children === undefined || children === "") {
    return <span className="dash">—</span>;
  }
  return <>{children}</>;
}
