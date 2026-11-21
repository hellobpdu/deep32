"""Offline policy regression checks for the operator pre-run handoff.

Run directly with isolated Python; no hosts, tools, credentials or networks are inspected.
The table is deliberately machine-checkable: a missing gate or permissive failure action
must fail this suite, even if the document otherwise renders correctly.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
PRERUN = ROOT / "docs" / "PRE-RUN.md"
HARDWARE = ROOT / "docs" / "HARDWARE.md"

# Each gate must retain its evidence subjects as well as the fail-closed action.
GATES = {
    "PR-01": ("baseline", "window", "restore", "authenticated query"),
    "PR-02": ("Workstation/Forge", "engine/gateway", "forwarding", "no full-role"),
    "PR-03": ("resolved pins", "hashes", "signatures", "saved door image"),
    "PR-04": ("driver", "VRAM", "Linux engine", "isolation", "persistence"),
    "PR-05": ("per-filesystem", "peak bytes", "free bytes", "rollback"),
    "PR-06": ("headroom", "DHCP", "empty target", "overlay/restore"),
    "PR-07": ("OpenBSD", "Serve", "consent", "anonymous denial", "eleven-tool"),
    "PR-08": ("Both models", "warm", "UTC", "window", "lockout"),
    "PR-09": ("isolated", "production markers", "distinct", "launchers"),
    "PR-10": ("generation", "attestation", "dense/hybrid", "verify_quote"),
    "PR-11": ("private", "startup", "restart", "identities", "retrieval"),
    "PR-12": ("data retained", "restored", "production query", "window-close"),
}
BOUNDARIES = (
    "Missing evidence is BLOCKED",
    "A failed checkpoint stops dependent stages",
    "Never invoke WSL to inspect or repair production",
    "route unchanged",
    "production distribution must remain disabled",
    "Never prune Docker",
    "A restored process alone is insufficient",
    "Do not count an additional full Docker engine/gateway as the",
    "Do not invent pins, sign media",
    "A data-root option is not a substitute for\nthe reviewed persistence recipe",
    "do not guess a bulk reverse copy",
    "Do not enable Funnel",
    "never adjust a production clock automatically",
    "clear lock files or reset identities",
    "Never remove a marker or spoof a home",
    "do not assume\n   that `rag_status` exposes those fields",
    "it must lose access while the other enrolled agent still works",
    "real-host qualification **NOT RUN**",
)
TOOLS = {
    "rag_status", "search_rag", "search_many", "get_section", "get_outline",
    "lookup_id", "lookup_rfc", "find_command", "grep_corpus", "list_documents",
    "verify_quote",
}


def policy_errors(text):
    """Return precise public-doc policy failures without executing any document text."""
    errors = []
    rows = {}
    for line in text.splitlines():
        if not re.match(r"^\| PR-\d\d\b", line):
            continue
        cells = [value.strip() for value in line.strip().strip("|").split("|")]
        key = cells[0].split()[0]
        if key in rows:
            errors.append(f"duplicate gate {key}")
        rows[key] = cells
    if set(rows) != set(GATES):
        errors.append("missing or unknown stop gate")
    for key, terms in GATES.items():
        cells = rows.get(key, [])
        if len(cells) != 3 or cells[-1] != "STOP":
            errors.append(f"{key} does not stop on missing/failed evidence")
            continue
        for term in terms:
            if term.casefold() not in cells[1].casefold():
                errors.append(f"{key} lacks required evidence: {term}")
    for boundary in BOUNDARIES:
        if boundary not in text:
            errors.append(f"missing protected boundary: {boundary}")
    # This public evidence checklist must not grow unqualified execution recipes.
    if re.search(r"(?m)^\s*(```|~~~)", text):
        errors.append("executable/code block in evidence-only checklist")
    private_patterns = (
        r"(?i)\b[a-z][a-z0-9]*-(?:main|prod|production)\b",
        r"(?i)[A-Z]:[\\/]", r"(?i)/home/[^\s/]+", r"(?i)/mnt/user/",
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    )
    for pattern in private_patterns:
        if re.search(pattern, text):
            errors.append("private identity, address or host path in public checklist")
            break
    if not TOOLS.issubset(set(re.findall(r"`([a-z_]+)`", text))):
        errors.append("incomplete real-agent tool acceptance")
    for required in ("five minutes", "one attempt", "thirty minutes", "fifteen-minute lockout"):
        if required not in text:
            errors.append(f"missing pairing limit: {required}")
    return errors


class PreRunPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PRERUN.read_text(encoding="utf-8")

    def test_public_prerun_policy(self):
        self.assertEqual(policy_errors(self.text), [])

    def test_each_gate_is_mandatory_and_stops_on_failure(self):
        for key in GATES:
            with self.subTest(gate=key):
                line = next(line for line in self.text.splitlines() if line.startswith(f"| {key} "))
                self.assertTrue(policy_errors(self.text.replace(line + "\n", "")))
                self.assertTrue(policy_errors(self.text.replace(line, line.replace("| STOP |", "| CONTINUE |"))))

    def test_each_evidence_subject_is_required(self):
        for key, terms in GATES.items():
            line = next(line for line in self.text.splitlines() if line.startswith(f"| {key} "))
            for term in terms:
                with self.subTest(gate=key, subject=term):
                    changed = re.sub(re.escape(term), "omitted", line, flags=re.IGNORECASE)
                    self.assertTrue(policy_errors(self.text.replace(line, changed)))

    def test_protected_boundaries_cannot_be_removed(self):
        for boundary in BOUNDARIES:
            with self.subTest(boundary=boundary):
                self.assertTrue(policy_errors(self.text.replace(boundary, "omitted")))

    def test_private_values_and_commands_are_rejected(self):
        synthetic_bad_values = (
            "synthetic-main", "C:/private/test", "192.0.2.4", "user@example.invalid",
            "```sh\nrun-an-unqualified-command\n```",
        )
        for value in synthetic_bad_values:
            with self.subTest(value=value):
                self.assertTrue(policy_errors(self.text + "\n" + value))

    def test_duplicate_gate_is_rejected(self):
        line = next(line for line in self.text.splitlines() if line.startswith("| PR-01 "))
        self.assertTrue(policy_errors(self.text + "\n" + line))

    def test_hardware_links_to_current_stop_and_storage_gates(self):
        text = HARDWARE.read_text(encoding="utf-8")
        self.assertIn("PRE-RUN.md#measure-each-backing-filesystem-pr-05", text)
        self.assertIn("[pre-run stop gates](PRE-RUN.md)", text)
        self.assertIn("Windows uses Docker Desktop's Linux engine", text)
        self.assertIn("portable Windows Forge lifecycle is supplied", text)
        self.assertIn("keep integration for an existing production distribution disabled", text)

    def test_local_document_links_exist(self):
        for target in re.findall(r"\]\(([^)]+)\)", self.text):
            with self.subTest(target=target):
                self.assertNotIn("://", target)
                self.assertTrue((PRERUN.parent / target.split("#")[0]).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
