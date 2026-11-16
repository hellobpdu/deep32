"""cisco-tn-ise-repository (TechNote 215348, ePub): the two one-cell sptable boxes that hold ISE CLI transcripts
('ise-pri/admin#show repository Test ...', 'ise-pri/admin#delete disk:/test.txt ...') become <pre> (ISE fidelity
audit D0: served as one ' | '/'; '-joined line each), and the empty community spoiler box that left a bare 'Spoiler'
last line is dropped (D2). See _technote.sptable_pre (with need=ISE prompt) and drop_empty_spoilers.
"""
from __future__ import annotations

from ._technote import ISE_PROMPT, drop_empty_spoilers, sptable_pre

DOC_KEY = "cisco-tn-ise-repository"


def fix(html: str) -> tuple[str, dict]:
    html, n_pre = sptable_pre(html, need=ISE_PROMPT)
    html, n_sp = drop_empty_spoilers(html)
    return html, {"sptable_pre": n_pre, "empty_spoilers": n_sp}
