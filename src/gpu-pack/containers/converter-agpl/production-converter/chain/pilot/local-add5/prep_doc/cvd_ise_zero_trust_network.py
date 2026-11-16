"""cvd-ise-zero-trust-network: source rules for this Word-export CVD (ISE fidelity audit, verified defects 0-3, 6).

1. The Word right-arrow glyph of menu paths, <span style="font-family:Wingdings">&agrave;</span>, becomes '>'
   (126 'à' in the served text: 'Administration à System').
2. CLI paragraphs: a p.pBody or p.pStep1CMT whose whole text is IOS command text (pasted AAA/RADIUS/cts config, the
   '# show cts pacs' prompts) is reclassed MsoBodyTextIndent, which prep_classes.py merges into one <pre> per run.
   Without it the three '#' prompt lines became H1 headings that re-parented the last 418 lines. Text with ' – ' or a
   final '.' is prose ('command - explanation' lines) and stays.
3. The page's 'Learn more' chrome (div#learnMore, class noprint) is dropped.
4. Word TOC level-3 subheads (p.pToC_Subhead3, 94 of them, with _Toc anchors) become h4, through prep_classes'
   restructure() with that one extra class (its other rules unchanged; run once more by the convert script, it then
   finds nothing to do).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor-add2"))
import prep_classes as pc  # noqa: E402

DOC_KEY = "cvd-ise-zero-trust-network"
ARROW = re.compile(r'<span style="font-family:Wingdings">&agrave;</span>')
CMD = re.compile(r"^(?:#\s*\S.*|conf t|end|exit|aaa \S.*|radius server \S+|server name \S+|address ipv4 .*|"
                 r"pac key \S+|client \S+ server-key \S+|cts \S.*|dot1x \S.*|deadtime \d+)$")
PARA = re.compile(r'(?s)<p\b([^>]*?)\bclass="(pBody|pStep1CMT)"([^>]*)>(.*?)</p>')
LEARN_MORE = re.compile(r'(?s)<div id="learnMore" class="noprint">.*?</div>')
EXTRA_TAGS = {"pToC_Subhead3": "h4"}


def fix(html: str) -> tuple[str, dict]:
    counts = {}
    html, counts["wingdings_arrow"] = ARROW.subn("&gt;", html)
    counts["cli_paras"] = 0

    def cli(m: re.Match) -> str:
        text = re.sub(r"<[^>]+>", "", m.group(4)).replace("&nbsp;", " ").strip()
        if CMD.match(text) and " – " not in text and not text.endswith("."):
            counts["cli_paras"] += 1
            return f'<p{m.group(1)}class="MsoBodyTextIndent"{m.group(3)}>{m.group(4)}</p>'
        return m.group(0)

    html = PARA.sub(cli, html)
    html, counts["learn_more"] = LEARN_MORE.subn("", html)
    saved = dict(pc.CLASS_TAGS)
    pc.CLASS_TAGS.update(EXTRA_TAGS)
    try:
        html, restructured = pc.restructure(html)
    finally:
        pc.CLASS_TAGS.clear()
        pc.CLASS_TAGS.update(saved)
    counts.update(restructured)
    return html, counts
