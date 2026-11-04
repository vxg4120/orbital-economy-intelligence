/** The copy for an empty pending-filings page, from the filter that page answered for (not
    the live URL: a kept page can be on screen while the next filter is still loading). */
export function emptyCopy(
  q: string,
  slug: string,
): { title: string; message: string; clearable: boolean } {
  if (q) {
    return {
      title: "No pending applications match",
      message: `Nothing matches “${q}”${slug ? ` in cohort ${slug}` : ""}. Try an applicant, a satellite name or an IBFS file number, or clear the search.`,
      clearable: true,
    };
  }
  if (slug) {
    return {
      title: "No pending applications match",
      message: `Nothing is pending from cohort ${slug}. Clear the cohort filter to see every application.`,
      clearable: false,
    };
  }
  return { title: "No pending applications", message: "The pending queue is empty.", clearable: false };
}
