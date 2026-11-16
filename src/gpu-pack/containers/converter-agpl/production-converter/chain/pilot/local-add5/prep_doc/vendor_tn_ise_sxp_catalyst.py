"""cisco-tn-ise-sxp-catalyst (TechNote 222201, fetched as its HTML article): Cisco's one-cell sptable code boxes
become <pre> (ISE fidelity audit, verified defect V0: 8 boxes, 67 logical lines of config, show output and logs were
served as run-on lines, 0 fences). See _technote.sptable_pre. The article keeps the two Verify step headings the PDF
lost (V1) and the source tokens the PDF route damaged ('[ip=', ':256 - MasterDbListener', V2/V3).
"""
from __future__ import annotations

from ._technote import sptable_pre

DOC_KEY = "cisco-tn-ise-sxp-catalyst"


def fix(html: str) -> tuple[str, dict]:
    html, n = sptable_pre(html)
    return html, {"sptable_pre": n}
