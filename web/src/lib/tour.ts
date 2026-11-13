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
      title: "Welcome to the Orbital Economy Terminal",
      description:
        "This is an identity graph over more than 70,000 cataloged objects, which it reconciles from SATCAT, GCAT and Space-Track decay messages into one resolved record per physical object. Here's a 20-second tour.",
    },
  },
  {
    element: '[data-tour="stats"]',
    popover: {
      title: "Recent activity at a glance",
      description:
        "What happened in orbit recently: payloads launched and reentered, the active fleet, and the new payloads still waiting for the catalogs to name their company owner. The stories below explain each number.",
      side: "bottom",
      align: "start",
    },
  },
  {
    element: '[data-tour="nav"]',
    popover: {
      title: "Eight sections",
      description:
        "Overview is where you are now. Resolver looks up one object, Conflicts shows where the catalogs disagree, Operators maps who owns what, and Review is where hard identity cases get a human verdict. Buses scores spacecraft platforms, Reachability shows what is overhead from a spot on Earth, and Filings is the FCC's queue of pending space-station applications.",
      side: "right",
      align: "start",
    },
  },
  {
    element: '[data-tour="conflicts"]',
    popover: {
      title: "Under the hood: the catalogs disagree",
      description:
        "The sources rarely tell the same story. This surfaces their disagreements about status, decay and stale ownership, and every number traces back to which source said what.",
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
  // Under 900px the rail is a horizontal strip wider than the screen (1068px of nav in
  // 390px), so a step spotlighting the whole strip points at mostly off-screen items:
  // skip it there. The Resolver step still anchors on its own (visible) nav item.
  const narrow = window.matchMedia("(max-width: 900px)").matches;
  d.setSteps(narrow ? STEPS.filter((s) => s.element !== '[data-tour="nav"]') : STEPS);
  d.drive();
}

/** Wait for the anchors to mount (Overview data is async; a cold API can take ten seconds),
    then run once. Gives up quietly when they never appear, rather than driving a tour whose
    steps point at nothing. Returns a cancel for the caller's unmount. */
function whenReady(selectors: string[], run: () => void, tries = 80): () => void {
  let timer = 0;
  const tick = (left: number) => {
    if (selectors.every((sel) => document.querySelector(sel))) {
      run();
      return;
    }
    if (left <= 0) return;
    timer = window.setTimeout(() => tick(left - 1), 150);
  };
  tick(tries);
  return () => window.clearTimeout(timer);
}

/** Auto-start on the first visit only, and only on the Overview landing page. The flag is
    written when the tour actually starts, so a visit that left before the data landed gets the
    tour next time. Returns a cancel so navigating away stops the wait. */
export function maybeAutoStartTour(): () => void {
  try {
    if (localStorage.getItem(TOUR_FLAG)) return () => {};
  } catch {
    return () => {}; // storage blocked → don't nag, don't crash
  }
  if (window.location.pathname !== "/") return () => {};
  return whenReady(['[data-tour="stats"]', '[data-tour="conflicts"]'], () => {
    try {
      localStorage.setItem(TOUR_FLAG, "1");
    } catch {
      /* ignore */
    }
    startTour();
  });
}
