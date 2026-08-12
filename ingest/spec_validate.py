"""Independent confirmation that a cited page really carries the value cited to it.

The worst failure available to the extraction layer is not a missing altitude. It is a plausible
citation attached to the wrong fact: a reader clicks through, finds the page does not support the
number, and every other cited figure on the platform becomes suspect at once. A coverage gap costs
one value; a bad citation costs the premise.

So extraction and validation are deliberately separate passes with separate code. This module never
imports the parser and knows nothing about Schedule S layout. It answers one question -- are this
value's tokens physically present on the page it claims -- and a row stays unpublishable until the
answer is yes. That independence is the point: a bug shared between parser and checker would let
both agree on something false, which is exactly what a single combined pass invites.

Matching is token-boundary anchored on purpose. A naive substring test validates 25 against "525.0"
and 30 against "1030.0", and that coincidence is precisely how a wrong citation would survive
review looking correct.
"""

from __future__ import annotations

import re


def _number_forms(value: float) -> list[str]:
    """Renderings the report might use for the same number.

    525 and 525.0 are the same fact, and rejecting one would fail valid rows over a formatting
    difference. A validator that produces false alarms gets ignored, which is its own failure mode.
    """
    forms = {f"{value}"}
    if float(value).is_integer():
        forms.add(str(int(value)))
        forms.add(f"{int(value)}.0")
    return sorted(forms)


def page_supports(page_text: str, value) -> bool:
    """True when `value` appears on `page_text` as a whole token.

    None is never supported. An absent field has not been confirmed, it has been skipped, and
    treating those alike would let abstentions through wearing a citation.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        candidates = _number_forms(float(value))
    else:
        candidates = [str(value)]
    for form in candidates:
        # Reject a match that is part of a longer number: 25 inside 525.0, 30 inside 1030.0.
        if re.search(rf"(?<![\d.]){re.escape(form)}(?![\d.])", page_text):
            return True
    return False


def validate_rows(pages: list[str], rows: list[dict], fields: tuple[str, ...]) -> list[bool]:
    """One verdict per row: True only when every non-null field is supported by its cited page.

    A row is rejected when its citation points off the end of the document, when it has no non-null
    field among `fields` (nothing was actually checked), or when any single field fails. One good
    field does not carry a bad one -- the row is the unit that gets published, so the row is the
    unit that has to hold up.
    """
    verdicts: list[bool] = []
    for row in rows:
        page_no = row.get("source_page")
        if not page_no or page_no > len(pages):
            verdicts.append(False)
            continue
        text = pages[page_no - 1]
        present = [row.get(f) for f in fields if row.get(f) is not None]
        verdicts.append(bool(present) and all(page_supports(text, v) for v in present))
    return verdicts


def validate_fieldwise(pages: list[str], rows: list[dict], field_pages: dict[str, str]) -> list[bool]:
    """Row verdicts where each field is checked against ITS OWN cited page.

    Needed wherever a row's values can straddle a page break, which for orbital planes is the
    common case rather than the exception: on SATAMD2017030100030, 17 of 74 planes carry their
    inclination on one page and their apogee and perigee on the next. Validating those against a
    single row-level page fails them all, and "fixing" that by widening the search to nearby pages
    would defeat the point, since a citation a reader cannot land on is the defect being guarded
    against.

    `field_pages` maps each value column to the column holding its page number.
    """
    verdicts: list[bool] = []
    for row in rows:
        checked = 0
        ok = True
        for field, page_key in field_pages.items():
            value = row.get(field)
            if value is None:
                continue
            page_no = row.get(page_key)
            if not page_no or page_no > len(pages):
                ok = False
                break
            checked += 1
            if not page_supports(pages[page_no - 1], value):
                ok = False
                break
        verdicts.append(ok and checked > 0)
    return verdicts
