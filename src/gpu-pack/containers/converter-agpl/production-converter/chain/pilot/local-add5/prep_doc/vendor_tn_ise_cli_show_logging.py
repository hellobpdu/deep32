"""cisco-tn-ise-cli-show-logging (TechNote 222247, fetched as its live HTML article): the cCN_CmdName CLI output
blocks become <pre> with their line breaks (ISE fidelity audit D0: the ePub had flattened the four multi-line outputs
into 414-1,261-character prose lines, 0 fences, 0 cli_index rows), and the image captions become their own lines
(D1: 'then click Save.Setting Component'). See _technote.cmdname_pre and caption_block.
"""
from __future__ import annotations

from ._technote import caption_block, cmdname_pre

DOC_KEY = "cisco-tn-ise-cli-show-logging"


def fix(html: str) -> tuple[str, dict]:
    html, n_pre = cmdname_pre(html)
    html, n_cap = caption_block(html)
    return html, {"cmdname_pre": n_pre, "caption_block": n_cap}
