import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getReviewCases, getReviewNext, getReviewStats } from "../api/client";
import type { ReviewOnly, ReviewStats, ReviewStratum } from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtInt, fmtPct } from "../lib/format";
import { orderStrata, stratumAccuracy, stratumLabel } from "../lib/reviewStrata";
import { Panel } from "../components/Panel";
import { Pager } from "../components/DataTable";
import { Async } from "../components/States";
import { VerdictBadge } from "../components/VerdictBadge";

const LIMIT = 50;
const ONLY_TABS: { key: ReviewOnly; label: string }[] = [
  { key: "unlabeled", label: "Unlabeled" },
  { key: "labeled", label: "Labeled" },
  { key: "all", label: "All" },
];

export function Review() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const type = params.get("type") ?? undefined;
  const only = (params.get("only") as ReviewOnly) ?? "unlabeled";
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [type, only]);

  const stats = useApi(() => getReviewStats(), []);
  const page = useApi(() => getReviewCases(type, only, LIMIT, offset), [type, only, offset]);

  const setFilter = (next: { type?: string; only?: ReviewOnly }) => {
    const p: Record<string, string> = {};
    const t = "type" in next ? next.type : type;
    const o = "only" in next ? next.only : only;
    if (t) p.type = t;
    if (o && o !== "unlabeled") p.only = o;
    setParams(p);
  };

  const startReviewing = async () => {
    const { next_case_id } = await getReviewNext(type);
    if (next_case_id !== null) {
      navigate(`/review/${next_case_id}${type ? `?type=${type}` : ""}`);
    }
  };

  const openCase = (caseId: number) => {
    const qs = new URLSearchParams();
    if (type) qs.set("type", type);
    if (only !== "unlabeled") qs.set("only", only);
    const s = qs.toString();
    navigate(`/review/${caseId}${s ? `?${s}` : ""}`);
  };

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Review</h1>
          <p className="vhead__desc">
            The arbitration workbench for the {stats.data ? fmtInt(stats.data.overall.total) : "gold"}
            {" "}hard cases. Each is a resolved identity decision the graph made under conflicting
            sources; you record the verdict that turns coverage into an honest, per-failure-mode
            accuracy. Keyboard-first — open a case and press c/i/p/u.
          </p>
        </div>
        <button className="btn btn--go" onClick={startReviewing}>
          Start reviewing →
        </button>
      </header>

      <Async state={stats} loadingLabel="Loading progress">
        {(s) => <ProgressPanel stats={s} activeType={type} onPick={(t) => setFilter({ type: t })} />}
      </Async>

      <Panel
        title="Cases"
        meta={
          <>
            {page.data ? `${fmtInt(page.data.total)} in filter` : ""}
            {stats.data && stats.data.overall.dossiers_ready > 0
              ? `${page.data ? " · " : ""}${fmtInt(stats.data.overall.dossiers_ready)} researched`
              : ""}
          </>
        }
        flush
      >
        <div className="review-filters">
          <div className="tabs tabs--sub">
            {ONLY_TABS.map((t) => (
              <button
                key={t.key}
                className={`tab${only === t.key ? " is-active" : ""}`}
                onClick={() => setFilter({ only: t.key })}
              >
                {t.label}
              </button>
            ))}
          </div>
          {type ? (
            <button className="chip chip--clear" onClick={() => setFilter({ type: undefined })}>
              {stratumLabel(type)} ✕
            </button>
          ) : (
            <span className="hint">All strata</span>
          )}
        </div>

        <Async state={page} loadingLabel="Loading cases">
          {(data) =>
            data.rows.length === 0 ? (
              <div className="state">
                <span className="state__title">Nothing here</span>
                <span className="state__msg">
                  No {only} cases{type ? ` in ${stratumLabel(type)}` : ""}.
                </span>
              </div>
            ) : (
              <>
                <ul className="case-list">
                  {data.rows.map((r) => (
                    <li key={r.case_id}>
                      <button className="case-row" onClick={() => openCase(r.case_id)}>
                        <span className="case-row__type" data-stratum={r.case_type}>
                          {stratumLabel(r.case_type)}
                        </span>
                        <span className="case-row__body">
                          <span className="case-row__subject num">
                            {r.subject_ref}
                            {r.has_dossier ? (
                              <span
                                className="case-row__dot"
                                title="AI research ready"
                                aria-hidden="true"
                              />
                            ) : null}
                          </span>
                          <span className="case-row__q">{r.question}</span>
                        </span>
                        {r.verdict ? (
                          <VerdictBadge verdict={r.verdict} />
                        ) : (
                          <span className="case-row__open" aria-hidden="true">
                            →
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
                <Pager offset={offset} limit={LIMIT} total={data.total} onOffset={setOffset} />
              </>
            )
          }
        </Async>
      </Panel>
    </div>
  );
}

function ProgressPanel({
  stats,
  activeType,
  onPick,
}: {
  stats: ReviewStats;
  activeType: string | undefined;
  onPick: (type: string | undefined) => void;
}) {
  const strata = orderStrata(stats.strata);
  return (
    <Panel
      title="Progress by stratum"
      meta={
        stats.accuracy_so_far !== null ? (
          <span className="acc-chip">
            accuracy so far <span className="num">{fmtPct(stats.accuracy_so_far * 100)}</span>
          </span>
        ) : (
          <span className="hint">0 labeled</span>
        )
      }
    >
      <div className="strata-grid">
        <StratumMeter
          label="All strata"
          labeled={stats.overall.labeled}
          total={stats.overall.total}
          accuracy={stats.accuracy_so_far}
          active={activeType === undefined}
          onClick={() => onPick(undefined)}
        />
        {strata.map((s) => (
          <StratumMeter
            key={s.case_type}
            label={stratumLabel(s.case_type)}
            labeled={s.labeled}
            total={s.total}
            accuracy={accuracyFor(s)}
            active={activeType === s.case_type}
            onClick={() => onPick(s.case_type)}
          />
        ))}
      </div>
    </Panel>
  );
}

function accuracyFor(s: ReviewStratum): number | null {
  return stratumAccuracy(s);
}

function StratumMeter({
  label,
  labeled,
  total,
  accuracy,
  active,
  onClick,
}: {
  label: string;
  labeled: number;
  total: number;
  accuracy: number | null;
  active: boolean;
  onClick: () => void;
}) {
  const pct = total > 0 ? (labeled / total) * 100 : 0;
  const done = total > 0 && labeled >= total;
  return (
    <button className={`stratum-meter${active ? " is-active" : ""}`} onClick={onClick}>
      <div className="stratum-meter__head">
        <span className="stratum-meter__label">{label}</span>
        {accuracy !== null ? (
          <span className="acc-chip acc-chip--sm">{fmtPct(accuracy * 100, 0)}</span>
        ) : null}
      </div>
      <div className="stratum-meter__track">
        <div
          className={`stratum-meter__fill${done ? " is-done" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="stratum-meter__foot num">
        {fmtInt(labeled)} / {fmtInt(total)}
      </span>
    </button>
  );
}
