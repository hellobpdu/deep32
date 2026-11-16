"""Doc-scoped source fixes, applied before conversion (after prep_source.py, before prep_classes.py) to the documents
whose source has a defect no general rule should touch. One module per doc_key (dashes become underscores); each
defines DOC_KEY and fix(html) -> (html, counts). The modules come from the ISE fidelity audit's validated prototypes
(local-add5/ise-fix/ise-audit-results.json):
- ise31_admin_guest: DITA topichead levels (scratch guest-verify/prep_topichead.py)
- cvd_ise_zero_trust_network: Word-export CVD rules (scratch cvd-admin/prep_cvd_test.py)
The ISE fix pass (ise-fix/ISE-FIX.md) adds three TechNote modules, built on the shared rules in _technote.py:
- vendor_tn_ise_sxp_catalyst: one-cell sptable code boxes -> <pre> (HTML article)
- vendor_tn_ise_cli_show_logging: cCN_CmdName CLI output -> <pre>, image captions on their own line (HTML article)
- vendor_tn_ise_repository: ISE-prompt sptable boxes -> <pre>, the empty spoiler box dropped (ePub)
and one Word-export module, built on prep_classes.restructure:
- wp_ise_c9800_gbp: Word TOC level-3 subheads -> h4, whole-paragraph CLI prompt lines merged into their <pre> (HTML)

usage: python3 -B prep_doc <doc_key> <in.html|in.epub> <out>    (or prep(doc_key, src, dst) from Python)
Like prep_source.py, it never edits its input: it writes <out> only when a rule changed something, and prints what
changed ('prep_doc: n/a' for a doc_key without a module, 'prep_doc: clean' when nothing changed). An ePub source is
fixed member by member (its XHTML/HTML members); the other members are copied byte for byte, with 'mimetype' first
and stored uncompressed, as prep_source.py writes ePubs.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

MARKUP = (".html", ".htm", ".xhtml")
DOCUMENT_MODULE_ALIASES = {'cisco_tn_ise_cli_show_logging': 'vendor_tn_ise_cli_show_logging', 'cisco_tn_ise_repository': 'vendor_tn_ise_repository', 'cisco_tn_ise_sxp_catalyst': 'vendor_tn_ise_sxp_catalyst'}


def modules() -> dict:
    """doc_key -> module, for every fix module in this package."""
    out = {}
    for info in pkgutil.iter_modules([str(Path(__file__).resolve().parent)]):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        key = getattr(mod, "DOC_KEY", None)
        if key and DOCUMENT_MODULE_ALIASES.get(key.replace("-", "_"), key.replace("-", "_")) == info.name and callable(getattr(mod, "fix", None)):
            out[key] = mod
    return out


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:                            # an ePub prep_source.py has not re-encoded yet
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor-add"))
        from prep_source import to_utf8
        return to_utf8(data)[0]


def prep_epub(mod, src: Path, dst: Path) -> str:
    changed: dict[str, bytes] = {}
    counts: Counter = Counter()
    with zipfile.ZipFile(src) as zin:
        for i in zin.infolist():
            if i.filename.lower().endswith(MARKUP):
                text = _text(zin.read(i))
                new, c = mod.fix(text)
                if new != text:
                    changed[i.filename] = new.encode("utf-8")
                    counts.update({k: v for k, v in c.items() if isinstance(v, int)})
        if not changed:
            return "prep_doc: clean"
        with zipfile.ZipFile(dst, "w") as zout:
            for i in sorted(zin.infolist(), key=lambda i: i.filename != "mimetype"):
                method = zipfile.ZIP_STORED if i.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zipfile.ZipInfo(i.filename, i.date_time), changed.get(i.filename, zin.read(i)),
                              compress_type=method)
    return ("prep_doc: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v)
            + f" ({len(changed)} member{'s' if len(changed) > 1 else ''})")


def prep(doc_key: str, src: Path, dst: Path) -> str:
    mod = modules().get(doc_key)
    if mod is None:
        return "prep_doc: n/a"
    if src.suffix.lower() == ".epub":
        return prep_epub(mod, src, dst)
    if src.suffix.lower() not in MARKUP:
        raise ValueError(f"{doc_key}: prep_doc fixes HTML and ePub sources, not {src.name}")
    html = src.read_text(encoding="utf-8")
    new, counts = mod.fix(html)
    if new == html:
        return "prep_doc: clean"
    dst.write_text(new, encoding="utf-8")
    return "prep_doc: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v)


def main(argv: list[str]) -> int:
    key, src, dst = argv[0], Path(argv[1]), Path(argv[2])
    print(prep(key, src, dst))
    return 0
