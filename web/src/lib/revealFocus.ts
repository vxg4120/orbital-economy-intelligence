/** Reveal a control the keyboard focuses inside a horizontal scroller (a tab strip, the phone
    nav rail): the browser does not scroll a partly visible element into view on sequential
    focus, so the ring could land under the strip's edge fade. scroll-margin on the controls
    (theme.css) keeps them clear of the fade. */
export function revealFocus(): () => void {
  const onFocus = (e: FocusEvent) => {
    const el = e.target;
    if (!(el instanceof HTMLElement) || !el.closest(".tabs, .rail")) return;
    el.scrollIntoView({ inline: "nearest", block: "nearest" });
  };
  document.addEventListener("focusin", onFocus);
  return () => document.removeEventListener("focusin", onFocus);
}
