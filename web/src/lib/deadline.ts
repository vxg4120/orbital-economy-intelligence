/* Deadline arithmetic for every "N days left" widget.

   A countdown that clamps at zero keeps reading "0d left" forever once the date passes, and a
   projection "to the deadline" keeps targeting a date already gone. The Kuiper milestone panel
   did both for six weeks. Every deadline widget resolves its state here so the passed case is an
   explicit branch the component must render, never a clamp. Dates are YYYY-MM-DD, read in UTC. */

const DAY_MS = 86_400_000;

export interface DeadlineStatus {
  /** Whole days until the deadline date: 0 on the day itself, negative once it has passed. */
  daysLeft: number;
  /** True from the day after the deadline onward. */
  passed: boolean;
  /** Whole days since the deadline date; 0 until it has passed. */
  daysSince: number;
}

export function deadlineStatus(deadline: string, now: Date = new Date()): DeadlineStatus {
  const start = Date.parse(deadline + "T00:00:00Z");
  const elapsed = now.getTime() - start;
  const passed = elapsed >= DAY_MS;
  return {
    daysLeft: Math.ceil(-elapsed / DAY_MS),
    passed,
    daysSince: passed ? Math.floor(elapsed / DAY_MS) : 0,
  };
}

/** Days until `target` is reached from `current` at a trailing-30-day rate: 0 when already
    there, null when the rate is zero (no linear projection exists). */
export function daysToReach(current: number, target: number, ratePer30d: number): number | null {
  if (current >= target) return 0;
  if (ratePer30d <= 0) return null;
  return Math.ceil(((target - current) / ratePer30d) * 30);
}

/** `days` after `from`, as a YYYY-MM-DD UTC date. */
export function plusDays(from: Date, days: number): string {
  return new Date(from.getTime() + days * DAY_MS).toISOString().slice(0, 10);
}

/** "Jul 2026" for a YYYY-MM-DD date, for panel captions. */
export function monthYear(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}
