"""wp-ise-c9800-gbp: source rules for this Word-export white paper (ISE fidelity audit, verified defects V1 and V7).

1. Word TOC level-3 subheads become h4: the p.pToC_Subhead3 paragraphs, and the p.pSubhead3CMT paragraphs that carry
   a Word TOC anchor (<a name="_Toc...">), through prep_classes' restructure() with that one extra class (V1: 43
   headings were served as plain lines, 'SXP Filters for N-S Enforcement', ...). A p.pSubhead3CMT without a TOC anchor
   is a body sentence ('Using CLI on C9800, the command option does not exist:', 'The conclusion is that SGACL logging
   works well on the C9800.') and stays.
2. A p.pBody or p.pCMTBody paragraph whose whole text is one CLI prompt line ('Kernow-6500(config)#cts role-based
   sgt-map <address> sgt 12', '9800-17.9.1#show flow monitor NetFlow-mon-out cache') is reclassed MsoBodyTextIndent,
   which prep_classes merges with the CLI paragraphs that follow it into one <pre> (V7: 2 CLI lines served unfenced,
   one of them just above its own output's fence).
prep_classes.py is not changed (its class table is restored after the call); run once more by the convert script, it
then finds nothing to do.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor-add2"))
import prep_classes as pc  # noqa: E402

DOC_KEY = "wp-ise-c9800-gbp"
SUBHEAD3 = re.compile(r'(?s)<p\b([^>]*?)\bclass="pSubhead3CMT"([^>]*)>(.*?)</p>')
PARA = re.compile(r'(?s)<p\b([^>]*?)\bclass="(pBody|pCMTBody)"([^>]*)>(.*?)</p>')
PROMPT_LINE = re.compile(r"[A-Za-z0-9][\w.-]*(?:\([\w-]+\))?#\S.*")
EXTRA_TAGS = {"pToC_Subhead3": "h4"}


def fix(html: str) -> tuple[str, dict]:
    counts = {"toc_subhead3_cmt": 0, "cli_paras": 0}

    def subhead(m: re.Match) -> str:
        if '<a name="_Toc' not in m.group(3):
            return m.group(0)
        counts["toc_subhead3_cmt"] += 1
        return f'<p{m.group(1)}class="pToC_Subhead3"{m.group(2)}>{m.group(3)}</p>'

    def cli(m: re.Match) -> str:
        text = re.sub(r"<[^>]+>", "", m.group(4)).replace("&nbsp;", " ").strip()
        if not PROMPT_LINE.fullmatch(text):
            return m.group(0)
        counts["cli_paras"] += 1
        return f'<p{m.group(1)}class="MsoBodyTextIndent"{m.group(3)}>{m.group(4)}</p>'

    html = SUBHEAD3.sub(subhead, html)
    html = PARA.sub(cli, html)
    saved = dict(pc.CLASS_TAGS)
    pc.CLASS_TAGS.update(EXTRA_TAGS)
    try:
        html, restructured = pc.restructure(html)
    finally:
        pc.CLASS_TAGS.clear()
        pc.CLASS_TAGS.update(saved)
    counts.update(restructured)
    return html, counts
