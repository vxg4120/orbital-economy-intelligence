/* =============================================================================
   First-visit guided tour (driver.js — MIT-licensed, ~5kb). A short, jargon-light
   walkthrough of the Overview page: it auto-runs once (gated by a localStorage
   flag) and can be replayed anytime from the "?" button in the top bar.
   The popover is repainted for the terminal palette in theme.css (.oei-tour).
   ============================================================================= */
import { driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";

const TOUR_FLAG = "oei_tour_done";

const STEPS: DriveStep[] = [
  {
    popover: {
      title: "Welcome to the Orbital Terminal",
      description:
        "An identity graph over ~70,000 satellites, reconciled from four conflicting public catalogs into one resolved record per real spacecraft. Here's a 20-second tour.",
    },
  },
  {
    element: '[data-tour="stats"]',
    popover: {
      title: "The graph at a glance",
      description:
        "How big the reconciled picture is — tracked objects, operators, and element sets (each object's latest orbit). One row per physical satellite.",
      side: "bottom",
      align: "start",
    },
  },
  {
    element: '[data-tour="nav"]',
    popover: {
      title: "Five sections",
      description:
        "Overview (you're here) · Resolver looks up one object · Conflicts shows where catalogs disagree · Operators maps who owns what · Review is the human-checked cases.",
      side: "right",
      align: "start",
    },
  },
  {
    element: '[data-tour="conflicts"]',
    popover: {
      title: "The catalogs disagree",
      description:
        "Four public sources rarely tell the same story. This surfaces the disagreements — status, decay, stale ownership — and every number traces back to which source said what.",
      side: "top",
      align: "start",
    },
  },
  {
    element: '[data-tour="resolver"]',
    popover: {
      title: "Start with the Resolver",
      description:
        "Look up any object to see who-says-what across the catalogs, plus its full ownership history over time.",
      side: "right",
      align: "center",
    },
  },
  {
    popover: {
      title: "That's the tour",
      description:
        "Everything here is read-only and sourced — no edits, no guesses. Built solo. Press the “?” in the top bar to replay this anytime.",
    },
  },
];

/** Build and run the tour. Safe to call repeatedly (each call is a fresh instance). */
export function startTour(): void {
  const d = driver({
    showProgress: true,
    animate: true,
    overlayColor: "#05070a",
    overlayOpacity: 0.72,
    stagePadding: 6,
    stageRadius: 4,
    popoverClass: "oei-tour",
    progressText: "{{current}} / {{total}}",
    nextBtnText: "Next →",
    prevBtnText: "← Back",
    doneBtnText: "Done",
  });
  d.setSteps(STEPS);
  d.drive();
}

/** Poll briefly for an anchor to mount (Overview data is async), then run once. */
function whenReady(selector: string, run: () => void, tries = 20): void {
  if (document.querySelector(selector) || tries <= 0) {
    run();
    return;
  }
  window.setTimeout(() => whenReady(selector, run, tries - 1), 150);
}

/** Auto-start on the first visit only, and only on the Overview landing page. */
export function maybeAutoStartTour(): void {
  try {
    if (localStorage.getItem(TOUR_FLAG)) return;
  } catch {
    return; // storage blocked → don't nag, don't crash
  }
  if (window.location.pathname !== "/") return;
  whenReady('[data-tour="stats"]', () => {
    try {
      localStorage.setItem(TOUR_FLAG, "1");
    } catch {
      /* ignore */
    }
    startTour();
  });
}
