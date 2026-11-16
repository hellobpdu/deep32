"""ise31-admin-guest: re-level the chapter's DITA topicheads.

The live chapter page holds two topicheads, <h3 class="topictitle1">Sponsor Portal Application Settings</h3> and an
empty one; fetch_add.chapter_html() maps topictitleN to hN, so the stored source has them as <h1> and the 10
chapter-level sections that follow (End-User Portals ... HTML Support for a Portal Language File, 28.5% of the text)
were served under 'Sponsor Portal Application Settings' (ISE fidelity audit, verified defect 0). The chapter's own
minitoc (the related-links ulchildlink list right after the chapter h1) lists its direct child topics. Rules:
1. a topictitle heading without the 'title' class after the chapter h1 is a topichead: it becomes h2, or is dropped
   when empty;
2. the topics after a topichead, and their section titles, move one level down (+1) until a topic whose enclosing
   article is a direct child in the minitoc.
On the stored source this changes 17 heading tags and drops the empty topichead; converted with converter v2 it gives
the audit's patched text (md5 549321aa70b3a87d59bad7e014ef09ae: 1 H1, 18 H2, no level skips).
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter

DOC_KEY = "ise31-admin-guest"
CHAPTER_H1 = re.compile(r'<h1 class="title topictitle1"[^>]*>')
MINITOC = re.compile(r'(?s)<nav role="navigation" class="related-links">(.*?)</nav>')
CHILD = re.compile(r'<li class="link ulchildlink"><a href="#([^"]+)"')
ARTICLE = re.compile(r'<article\b[^>]*\bid="([^"]+)"')
HEADING = re.compile(r"(?s)<h([1-6])(\b[^>]*)>(.*?)</h\1>")
CLASS = re.compile(r'class="([^"]*)"')


def fix(html: str) -> tuple[str, dict]:
    stats = Counter(topicheads=0, dropped_empty=0, shifted=0)
    h1 = CHAPTER_H1.search(html)
    nav = MINITOC.search(html, h1.end()) if h1 else None
    children = set(CHILD.findall(nav.group(1))) if nav else set()
    if not children:
        return html, dict(stats)                          # no minitoc: nothing tells the levels apart
    arts = [(m.start(), m.group(1)) for m in ARTICLE.finditer(html)]
    starts = [p for p, _ in arts]
    shift = 0

    def enclosing_article(pos: int) -> str:
        k = bisect_right(starts, pos) - 1
        return arts[k][1] if k >= 0 else ""

    def relevel(m: re.Match) -> str:
        nonlocal shift
        n, attrs, inner = int(m.group(1)), m.group(2), m.group(3)
        c = CLASS.search(attrs)
        cls = c.group(1).split() if c else []
        topic = any(x.startswith("topictitle") for x in cls)
        after_title = m.start() > h1.start()
        if topic and "title" not in cls and after_title:
            stats["topicheads"] += 1
            if not re.sub(r"<[^>]+>|\s", "", inner):
                stats["dropped_empty"] += 1
                return ""
            shift = 1
            return f"<h2{attrs}>{inner}</h2>"
        if topic and enclosing_article(m.start()) in children:
            shift = 0
        if shift and after_title:
            stats["shifted"] += 1
            n = min(6, n + shift)
        return f"<h{n}{attrs}>{inner}</h{n}>"

    return HEADING.sub(relevel, html), dict(stats)
