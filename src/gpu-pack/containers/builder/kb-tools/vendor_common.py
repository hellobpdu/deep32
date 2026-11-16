"""Public knowledge-base source helper."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent
STAGING = Path("/var/lib/deep32-forge")
DEFAULT_SRC = [STAGING / "corpus/cisco"]
DEFAULT_MANIFESTS = [STAGING / "kb-work/kb-manifest.json"]
ORIGINAL_DIRS = [STAGING / "originals/cisco"]  # Runtime copies of downloaded originals
HEADING = re.compile(r"^(#{1,6}) (.*)$")
CISCO_DIR = re.compile(r"cisco(-md(-v\d+)?)?")      # cisco-md-v2 ... cisco-md-v7, or a corpus package's cisco/
KB_DIRS = CISCO_DIR


class CorpusError(ValueError):
    """The source dirs are not a valid KB input (discover() exclude list, doc_platforms())."""


@dataclass
class Doc:
    key: str
    path: Path
    sha256: str
    lines: list[str]                                   # lines[i] is line i + 1
    in_fence: list[bool] = field(default_factory=list)  # True for fence markers and the lines between them
    headings: list[tuple[int, int, str]] = field(default_factory=list)   # (line, level, title), outside fences
    fences: list[tuple[int, int]] = field(default_factory=list)          # (opening, closing) marker lines

    def heading_before(self, line: int) -> tuple[int, int, str] | None:
        """The last heading at or before `line` (binary search over self.headings)."""
        lo, hi = 0, len(self.headings)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.headings[mid][0] <= line:
                lo = mid + 1
            else:
                hi = mid
        return self.headings[lo - 1] if lo else None


def read_doc(key: str, path: Path) -> Doc:
    data = path.read_bytes()
    lines = data.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()                                    # trailing newline, not an extra line
    doc = Doc(key, path, hashlib.sha256(data).hexdigest(), lines)
    fence, opened = False, 0
    for i, ln in enumerate(lines, start=1):
        if ln.startswith("```"):
            fence = not fence
            if fence:
                opened = i
            else:
                doc.fences.append((opened, i))
            doc.in_fence.append(True)
            continue
        doc.in_fence.append(fence)
        if not fence:
            m = HEADING.match(ln)
            if m:
                doc.headings.append((i, len(m.group(1)), m.group(2).strip()))
    return doc


def discover(src_dirs: list[Path], exclude: Iterable[str] = ()) -> dict[str, Path]:
    """doc_key -> path; later dirs win. Missing dirs are ignored (cisco-md-v3 may not exist yet). `exclude`: doc_keys
    left out wherever they are (a book a newer edition retires); naming a key that no dir has raises CorpusError, so
    a typo cannot keep the retired book in."""
    found: dict[str, Path] = {}
    for d in src_dirs:
        if d.is_dir():
            for p in sorted(d.glob("*.txt")):
                found[p.stem] = p
    skip = set(exclude)
    unknown = sorted(skip - set(found))
    if unknown:
        raise CorpusError(f"--exclude names doc_keys that no source dir has: {', '.join(unknown)}")
    return dict(sorted((k, p) for k, p in found.items() if k not in skip))


def load_manifest(paths: list[Path]) -> dict[str, dict]:
    """doc_key -> manifest entry; later files win. Reads both layouts: fetch-manifest.json ({"cisco": {key:
    {url, platform, ...}}}) and fetch-manifest-v2.json ({"documents": [{doc_key, collection, source_url,
    platform, text_sha256, fetched_at, ...}]})."""
    out: dict[str, dict] = {}
    for p in paths:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            for key, entry in (data.get("cisco") or {}).items():
                if isinstance(entry, dict):
                    out[key] = entry
            for entry in data.get("documents") or []:
                if isinstance(entry, dict) and entry.get("collection") == "cisco" and entry.get("doc_key"):
                    out[entry["doc_key"]] = entry
    return out


def original_fetched_at(doc_key: str) -> str | None:
    """mtime (UTC) of the downloaded original, the closest thing to a fetch time the fetch manifest lacks."""
    for d in ORIGINAL_DIRS:
        for p in d.glob(f"{doc_key}.*"):
            ts = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)
            return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def source_record(doc: Doc, manifest: dict[str, dict]) -> dict:
    """One entry of the .meta.json "sources": the corpus file (path, sha256, line count) and its origin URL."""
    m = manifest.get(doc.key, {})
    rec = {"url": m.get("source_url") or m.get("url") or m.get("html_url") or f"file:{doc.path.parent.name}/{doc.path.name}",
           "fetched_at": m.get("fetched_at") or original_fetched_at(doc.key), "doc_key": doc.key,
           "path": f"{doc.path.parent.name}/{doc.path.name}", "sha256": doc.sha256, "lines": len(doc.lines)}
    if m.get("text_sha256"):
        rec["matches_manifest"] = m["text_sha256"] == doc.sha256    # the line numbers hold for that corpus text
    return rec


# --- platform and book release from the doc_key -------------------------------------------------------------
PLATFORM_RULES = [('^c9300', 'cat9300'), ('^c9400', 'cat9400'), ('^c9500', 'cat9500'), ('^c9600', 'cat9600'), ('^(c9k|cat9k|wp-cat9k|cvd-campus)', 'cat9k'), ('^(cvd-sda|sda)', 'sd-access'), ('^(sdwan|cvd-sdwan)', 'sdwan'), ('^(catc|dnac)', 'catalyst-center'), ('^(iosxe\\d+-|iosxe-datapath|iosxe-cr-)', 'iosxe-router'), ('^iosxr', 'iosxr'), ('^nxos', 'nxos'), ('^iosxe', 'iosxe'), ('^ise\\d*-', 'ise'), ('^(cisco-|wp-)', 'ios')]
PLATFORMS = {'cat9300': 'Catalyst 9300 books (c9300-*)', 'cat9400': 'Catalyst 9400 books (c9400-*)', 'cat9500': 'Catalyst 9500 books (c9500-*)', 'cat9600': 'Catalyst 9600 books (c9600-*)', 'cat9k': 'Catalyst 9000 family books and white papers (c9k-*, wp-cat9k-*, cvd-campus-*)', 'sd-access': 'SD-Access design guides (cvd-sda-*, sda-*)', 'sdwan': 'Catalyst SD-WAN: the vEdge command reference (sdwan-cr) and the IOS XE SD-WAN guides (sdwan*)', 'catalyst-center': 'Catalyst Center / DNA Center (catc*, dnac*)', 'iosxe-router': 'IOS XE configuration guides and command references written for routers (iosxe17-*, iosxe-cr-*); most of what they describe is platform independent', 'iosxr': 'IOS XR', 'nxos': 'NX-OS', 'iosxe': 'other IOS XE books (programmability, hardening, packet trace)', 'ise': "Cisco ISE books (ise31-*): the ISE application CLI; ISE TechNotes (cisco-tn-ise-*) stay 'ios'", 'ios': 'TechNotes and white papers, platform independent (cisco-*, wp-*)', 'cisco': 'fallback for a doc_key that no rule knows'}
GENERIC_PLATFORMS = ["iosxe-router", "iosxe", "ios"]    # platform-independent IOS / IOS XE guidance
MANIFEST_PLATFORM_RULES = [          # fallback for doc_keys the rules above do not know
    (r"Catalyst 9300", "cat9300"), (r"Catalyst 9500", "cat9500"), (r"Catalyst 9\d00|Catalyst 9000", "cat9k"),
    (r"SD-WAN", "sdwan"), (r"SD-Access", "sd-access"), (r"Catalyst Center|DNA Center", "catalyst-center"),
    (r"IOS XE router|Catalyst 8000|ISR 4000|ASR 1000", "iosxe-router"), (r"IOS XE", "iosxe"), (r"\bIOS\b", "ios"),
]


def platform_for(doc_key: str, manifest_platform: str | None = None) -> str:
    for pat, name in PLATFORM_RULES:
        if re.search(pat, doc_key):
            return name
    for pat, name in MANIFEST_PLATFORM_RULES:
        if manifest_platform and re.search(pat, manifest_platform):
            return name
    return "cisco"


def doc_platforms(found: dict[str, Path], manifest: dict[str, dict]) -> dict[str, str]:
    """Assign platforms to Cisco source directories; refuse all other inputs."""
    outside = [f"{p.parent.name}/{p.name}" for p in found.values() if not CISCO_DIR.fullmatch(p.parent.name)]
    if outside:
        raise CorpusError(f"{len(outside)} document(s) outside the Cisco directory allowlist: {', '.join(outside[:10])}")
    return {k: platform_for(k, manifest.get(k, {}).get("platform")) for k in found}


def doc_release(doc_key: str) -> str | None:
    """Release the book documents, from its doc_key: c9300-1718-* -> 17.18, c9k-179-* -> 17.9, c9300-26x-* -> 26.x,
    catc237-* -> 2.3.7, catc-2379-* -> 2.3.7.9. Rolling books (iosxe17-*, sdwan17-*, iosxe-cr-*) name only a train:
    None."""
    m = re.match(r"(?:catc|dnac)-?(\d{3,4})\b", doc_key)
    if m:
        return ".".join(m.group(1))
    for tok in doc_key.split("-"):
        m = re.fullmatch(r"(1[5-9]|2\d)(\d{1,2}|x)", tok)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return None


# --- release strings ----------------------------------------------------------------------------------------
XE_TRAINS = r"(?:Denali|Everest|Fuji|Gibraltar|Amsterdam|Bengaluru|Cupertino|Dublin)"
RELEASE_PATTERNS = [
    ("iosxe", re.compile(r"(?:Cisco\s+)?IO[SX]\s*[- ]?XE\s+(?:Catalyst\s+SD-WAN\s+)?(?:Release\s+)?"
                         rf"(?:{XE_TRAINS}\s+)?(?:Release\s+)?(\d{{1,2}}\.\d{{1,2}}(?:\.\d{{1,2}})?[a-zA-Z]{{0,3}}\d?)\b")),
    ("sdwan-manager", re.compile(r"Cisco\s+(?:Catalyst\s+)?(?:SD-WAN\s+(?:Manager|Control\s+Components|Controllers?|"
                                 r"Validator)|vManage|vSmart|vBond|vEdge|SD-WAN)\s+Release\s+(\d{2}\.\d{1,2}(?:\.\d{1,2})?\w{0,3})\b")),
    ("catalyst-center", re.compile(r"Cisco\s+(?:Catalyst|DNA)\s+Center\s+(?:Release\s+)?(\d\.\d(?:\.\d{1,2}){0,2})\b")),
    ("ios", re.compile(r"(?<![\w.])(?:Cisco\s+IOS\s+(?:Release\s+)?)?(1[0-5]\.\d\(\d+[a-z]?\)[A-Z]{0,4}\d{0,2}[a-z]?)(?![\w(])")),
]
TRAIN_PREFERENCE = ("iosxe", "sdwan-manager", "catalyst-center", "ios")


@dataclass(frozen=True)
class Release:
    version: str       # "17.11.1a", "3.2S", "12.2(33)SXH", "20.3.1"
    train: str         # iosxe | sdwan-manager | catalyst-center | ios
    start: int         # span of the raw text in the searched string
    end: int

    @property
    def key(self) -> tuple:
        return tuple(int(n) for n in re.findall(r"\d+", self.version)[:3])


def find_releases(text: str) -> list[Release]:
    """All release mentions in `text`, in order, non-overlapping (IOS XE patterns win over classic IOS)."""
    found: list[Release] = []
    for train, rx in RELEASE_PATTERNS:
        for m in rx.finditer(text):
            if not any(m.start() < r.end and r.start < m.end() for r in found):
                found.append(Release(m.group(1), train, m.start(), m.end()))
    return sorted(found, key=lambda r: r.start)


def preferred_release(rels: list[Release]) -> Release | None:
    """The IOS XE release when there is one (earliest), else the earliest release of the next train."""
    for train in TRAIN_PREFERENCE:
        cands = [r for r in rels if r.train == train]
        if cands:
            return min(cands, key=lambda r: r.key)
    return None
