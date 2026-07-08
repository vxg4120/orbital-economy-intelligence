import type { ReactNode } from "react";

export interface Column<Row> {
  key: string;
  header: string;
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
}: DataTableProps<Row>) {
  return (
    <div className="table-wrap">
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
                  onClick={sortable ? () => onSort(c.key) : undefined}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  {c.header}
                  {active ? (
                    <span className="sort-caret" aria-hidden="true">
                      {sort!.dir === "asc" ? "▲" : "▼"}
                    </span>
                  ) : null}
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
  onOffset,
}: {
  offset: number;
  limit: number;
  total: number;
  onOffset: (next: number) => void;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;
  return (
    <div className="pager">
      <span className="num">
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
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
