"""Offline alpha.2 contract checks; these checks do not authenticate a release.

Only the JSON Schema keywords used by the bundled contracts are supported. An
unknown keyword is an error, never an ignored validation rule. No schema is
downloaded, no command is executed and no input is changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

RELEASE = "v0.1.0-alpha.3"
CONCEPT_DOI = "https://doi.org/10.5281/zenodo.24272647"
CONTRACTS = Path(__file__).resolve().parent / "contracts"
MAX_JSON_BYTES = 4 * 1024 * 1024
CONTRACT_LIMITS = {"doctor-output": 256 * 1024, "selftest-questions": 1024 * 1024,
                   "selftest-reference": 64 * 1024, "capability-manifest": 8 * 1024 * 1024}


class ContractError(ValueError):
    """A bounded public contract was refused."""


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def strict_loads(data, *, max_bytes=MAX_JSON_BYTES):
    """Read exactly one bounded UTF-8 JSON value without duplicate keys."""
    require(isinstance(data, (bytes, str)), "JSON input must be bytes or text")
    require(len(data if isinstance(data, bytes) else data.encode("utf-8")) <= max_bytes,
            "JSON input exceeds limit")
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        def finite_float(text):
            value = float(text)
            require(math.isfinite(value), "non-finite JSON number")
            return value
        return json.loads(data, object_pairs_hook=_pairs, parse_float=finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ContractError("non-finite JSON number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError("invalid JSON") from exc


def strict_load(path, *, max_bytes=MAX_JSON_BYTES):
    with Path(path).open("rb") as handle:
        data = handle.read(max_bytes + 1)
    return strict_loads(data, max_bytes=max_bytes)


def load_contract(kind, path):
    """Apply the raw-byte limit before parsing the selected contract."""
    return validate_contract(kind, strict_load(path, max_bytes=CONTRACT_LIMITS.get(kind, MAX_JSON_BYTES)))


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       indent=2) + "\n").encode("ascii")


def _equal(a, b):
    # JSON numbers compare numerically, but booleans are a separate type.
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


_KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "title", "description", "$comment",
    "type", "const", "enum", "properties", "required", "additionalProperties",
    "propertyNames", "minProperties", "maxProperties", "items", "minItems",
    "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "oneOf", "anyOf", "allOf", "not", "if", "then", "else", "default", "examples",
}


def _check_schema(schema, root, depth=0):
    require(depth <= 64, "schema nesting limit")
    if isinstance(schema, bool):
        return
    require(isinstance(schema, dict), "invalid schema")
    require(not set(schema) - _KEYWORDS, "unsupported schema keyword")
    if "$ref" in schema:
        require(schema["$ref"].startswith("#/"), "external schema references refused")
        _resolve(root, schema["$ref"])
    if "pattern" in schema:
        re.compile(schema["pattern"])
    if "format" in schema:
        require(schema["format"] in {"date-time", "uri"}, "unsupported schema format")
    for key in ("properties", "$defs"):
        for child in schema.get(key, {}).values():
            _check_schema(child, root, depth + 1)
    for key in ("items", "additionalProperties", "propertyNames", "if", "then", "else", "not"):
        if key in schema:
            _check_schema(schema[key], root, depth + 1)
    for key in ("allOf", "oneOf", "anyOf"):
        for child in schema.get(key, []):
            _check_schema(child, root, depth + 1)


def _resolve(root, ref):
    target = root
    try:
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError) as exc:
        raise ContractError("unresolved schema reference") from exc
    return target


def _matches(value, spec, root, depth=0):
    require(depth <= 96, "validation nesting limit")
    if isinstance(spec, bool):
        return spec
    if "$ref" in spec and not _matches(value, _resolve(root, spec["$ref"]), root, depth + 1):
        return False
    kinds = {
        "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float) and math.isfinite(v),
    }
    types = spec.get("type", [])
    types = types if isinstance(types, list) else [types]
    if types and not any(kinds[t](value) for t in types):
        return False
    if "const" in spec and not _equal(value, spec["const"]):
        return False
    if "enum" in spec and not any(_equal(value, v) for v in spec["enum"]):
        return False
    match = lambda s: _matches(value, s, root, depth + 1)
    if "oneOf" in spec and sum(match(s) for s in spec["oneOf"]) != 1:
        return False
    if "anyOf" in spec and not any(match(s) for s in spec["anyOf"]):
        return False
    if "allOf" in spec and not all(match(s) for s in spec["allOf"]):
        return False
    if "not" in spec and match(spec["not"]):
        return False
    if "if" in spec and not match(spec.get("then" if match(spec["if"]) else "else", True)):
        return False
    if isinstance(value, dict):
        if not spec.get("minProperties", 0) <= len(value) <= spec.get("maxProperties", math.inf):
            return False
        if not all(k in value for k in spec.get("required", [])):
            return False
        props = spec.get("properties", {})
        for key, item in value.items():
            if "propertyNames" in spec and not _matches(key, spec["propertyNames"], root, depth + 1):
                return False
            if not _matches(item, props.get(key, spec.get("additionalProperties", True)), root, depth + 1):
                return False
    if isinstance(value, list):
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", math.inf):
            return False
        if spec.get("uniqueItems") and any(_equal(v, p) for i, v in enumerate(value) for p in value[:i]):
            return False
        if "items" in spec and not all(_matches(v, spec["items"], root, depth + 1) for v in value):
            return False
    if isinstance(value, str):
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", math.inf):
            return False
        if "pattern" in spec and re.search(spec["pattern"], value) is None:
            return False
        if spec.get("format") == "date-time":
            try:
                require(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", value), "invalid timestamp")
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
        if spec.get("format") == "uri":
            try:
                safe_url(value)
            except ContractError:
                return False
    if type(value) in (int, float):
        if not math.isfinite(value) or not spec.get("minimum", -math.inf) <= value <= spec.get("maximum", math.inf):
            return False
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            return False
        if "exclusiveMaximum" in spec and value >= spec["exclusiveMaximum"]:
            return False
        if "multipleOf" in spec and value % spec["multipleOf"]:
            return False
    return True


def validate_schema(value, schema):
    _check_schema(schema, schema)
    require(_matches(value, schema, schema), "contract schema mismatch")
    return value


def safe_path(value, *, basename=False):
    require(isinstance(value, str) and 0 < len(value) <= 240, "invalid path")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value), "unsafe path")
    parts = value.split("/")
    require(not basename or len(parts) == 1, "asset must be a basename")
    for part in parts:
        require(part not in {"", ".", ".."} and not part.endswith("."), "unsafe path component")
        require(".." not in part, "ambiguous path component")
        require(part.split(".")[0].casefold() not in {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
                *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}, "reserved path component")
    return value


def safe_url(value):
    require(isinstance(value, str) and 0 < len(value) <= 2048, "invalid URL")
    require(all(33 <= ord(c) < 127 for c in value) and "\\" not in value, "unsafe URL text")
    require(not re.search(r"%(?![0-9a-fA-F]{2})", value), "malformed URL escape")
    try:
        url = urlsplit(value)
        host = url.hostname
        require(url.scheme in {"http", "https"} and host and not url.username and not url.password,
                "unsafe URL authority")
        require("@" not in url.netloc and not url.query and not url.fragment and "?" not in value and "#" not in value,
                "unsafe URL suffix")
        require(url.port is None or 1 <= url.port <= 65535, "invalid URL port")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            require(re.fullmatch(r"[a-z0-9.-]+", host), "unsafe URL host")
            require("." in host and not host.endswith((".local", ".localhost", ".internal", ".lan", ".test", ".invalid", ".example")), "non-public URL host")
            require(all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")), "invalid URL host")
            require(not all(c.isdigit() or c == "." for c in host), "ambiguous numeric URL host")
        else:
            require(address.is_global, "non-public URL address")
        decoded = unquote(url.path)
        require(decoded.startswith("/") and all(33 <= ord(c) < 127 for c in decoded), "invalid URL path")
        require("\\" not in decoded and not any(p in {".", ".."} for p in decoded.split("/")), "unsafe URL path")
        require("%" not in decoded, "nested URL escapes refused")
    except ValueError as exc:
        raise ContractError("invalid URL") from exc
    return value


def unique(values, label="identities"):
    values = list(values)
    require(len(values) == len(set(values)), "duplicate " + label)


def validate_hash_graph(nodes):
    """Check hash dependency closure. A -> B means A records B's hash."""
    unique(n["path"].casefold() for n in nodes)
    graph = {safe_path(n["path"]): n["depends_on"] for n in nodes}
    for dependencies in graph.values():
        unique(dependencies)
        require(all(d in graph for d in dependencies), "hash graph reference missing")
    visiting, done = set(), set()
    def visit(node):
        require(node not in visiting, "hash graph cycle or self-hash")
        if node in done:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        done.add(node)
    try:
        for node in graph:
            visit(node)
    except RecursionError as exc:
        raise ContractError("hash graph depth limit") from exc
    return nodes


def mirror_transports(value):
    """All explicitly named whole objects, in signed order."""
    return [*value["discs"], *value.get("bundles", [])]


def mirror_child_names(value):
    """Retain the three child lists and four-signature owner boundary."""
    names = {d["role"]: d["file"].rsplit(".", 1)[0] + "-SHA256SUMS"
             for d in value["discs"]}
    names["disc3"] = "deep32-" + value["release"][1:] + "-disc3-gpu-pack-SHA256SUMS"
    return names


def validate_mirror_capabilities(value, manifest, *, capability_bytes=None):
    """Check exact runtime coverage and only declared optional source media.

    Authentication belongs to the caller. Original bytes additionally enforce
    the capability descriptor digest; source companions are never selected
    implicitly.
    """
    validate_contract("mirrors", value)
    validate_contract("capability-manifest", manifest)
    require(value["format_version"] == 2, "bundle downloads require mirrors version 2")
    require(value["release"] == "v" + manifest["release"], "mirror/capability release mismatch")
    if capability_bytes is not None:
        import hashlib
        require(hashlib.sha256(capability_bytes).hexdigest() == value["capability_manifest"]["sha256"],
                "mirror/capability byte binding mismatch")
    declared = {b["id"]: b for b in manifest["bundles"]}
    runtime = {key for key, b in declared.items() if b["kind"] != "corresponding-source-build"}
    transported = {b["bundle_id"] for b in value["bundles"]}
    require(runtime <= transported <= set(declared), "mirror/capability bundle coverage mismatch")
    unique((b["size"], b["sha256"]) for b in value["bundles"] if b["bundle_id"] in runtime)
    import hashlib
    for bundle in value["bundles"]:
        require(bundle["bundle_sha256"] == hashlib.sha256(canonical(declared[bundle["bundle_id"]])).hexdigest(),
                "mirror/capability bundle identity mismatch")
    return value


def _mirrors(value):
    require(value["release"] == RELEASE, "unsupported release")
    require(value["citation"]["concept_doi"] == CONCEPT_DOI, "concept DOI mismatch")
    archive = value["archive"]
    item = "deep32-" + RELEASE[1:]
    require(archive["item"] == item and archive["details_url"] == "https://archive.org/details/" + item,
            "archive item mismatch")
    require(archive["torrent_url"].startswith("https://archive.org/download/" + item + "/"), "torrent item mismatch")
    for url in [archive["details_url"], archive["torrent_url"], *value["citation"].values()]:
        safe_url(url)
    try:
        dt.datetime.strptime(value["published_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ContractError("invalid publication date") from exc
    expected = {"disc1": "workstation", "disc2": "content"}
    if value["format_version"] == 1:
        expected["disc3"] = "gpu-pack"
    require({d["role"] for d in value["discs"]} == set(expected), "disc roles mismatch")
    unique(d["role"] for d in value["discs"])
    bundles = value.get("bundles", [])
    unique(d["bundle_id"] for d in bundles)
    required = {"core", "ingestion", *("embedder-" + card for card in
                ("turing", "ampere80", "ampere86", "ada", "hopper", "blackwell100", "blackwell120"))}
    require(value["format_version"] == 1 or required <= {d["bundle_id"] for d in bundles}, "required bundle transports missing")
    unique(d["file"].casefold() for d in mirror_transports(value))
    if value["format_version"] == 2:
        safe_path(value["capability_manifest"]["file"], basename=True)
        require(value["capability_manifest"]["file"].lower().endswith(".json") and
                value["capability_manifest"]["file"].casefold() not in
                {"mirrors.json", "asset-map.json", "assembly.json"}, "invalid capability asset")
    for disc in mirror_transports(value):
        safe_path(disc["file"], basename=True)
        if "role" in disc:
            require(disc["kind"] == expected[disc["role"]], "disc kind mismatch")
            stem = f"deep32-{RELEASE[1:]}-{disc['role']}-{disc['kind']}"
            names = {stem + "-amd64.img", stem + "-amd64.iso"} if disc["role"] == "disc1" else {stem + ".iso"}
        else:
            names = {f"deep32-{RELEASE[1:]}-disc3-gpu-pack-{disc['bundle_id']}.iso"}
        require(disc["file"] in names, "noncanonical transport filename")
        require(len(disc["chunk_sha256"]) == (disc["size"] + 67108863) // 67108864, "chunk count mismatch")
        unique(m["id"] for m in disc["mirrors"])
        unique(m["url"] for m in disc["mirrors"])
        for mirror in disc["mirrors"]:
            safe_url(mirror["url"])
        require(disc["mirrors"][0]["url"] == f"https://archive.org/download/{item}/{disc['file']}", "first mirror mismatch")


def _asset_map(value):
    unique(e["asset"].casefold() for e in value["entries"])
    destinations = []
    for entry in value["entries"]:
        safe_path(entry["asset"], basename=True)
        asset = entry["asset"]
        require(asset.casefold() not in {"asset-map.json", "sha256sums", "mirrors.json"} and
                not asset.casefold().endswith((".asc", ".sig", ".iso", ".img", ".tar", ".part")),
                "asset map cannot map trust roots, signatures or payloads")
        fixed = {"selftest-questions.json": "selftest/questions.json",
                 "selftest-reference.json": "selftest/reference.json",
                 "selftest-questions.schema.json": "selftest/questions.schema.json",
                 "selftest-reference.schema.json": "selftest/reference.schema.json",
                 "selftest-result.schema.json": "selftest/result.schema.json"}
        for key, owner in (("media_paths", "disc"), ("install", "role")):
            for row in entry[key]:
                safe_path(row["path"])
                if asset in fixed:
                    require(row["path"] == fixed[asset], "selftest asset destination mismatch")
                else:
                    require(not row["path"].startswith("selftest/"), "unknown selftest metadata mapping")
                destinations.append((key, row[owner], row["path"].casefold()))
    unique(destinations, "asset destinations")


def _doctor(value):
    checks = value["checks"]
    unique(c["id"] for c in checks)
    rows = {c["id"]: c for c in checks}
    for row in checks:
        require(all(dep in rows and rows[dep]["state"] != "OK" for dep in row["blocked_by"]), "invalid blocked check")
        if row["id"].startswith("tailnet-path."):
            path_kind = row["observed"].get("path_kind", "unknown")
            require(path_kind != "direct" or row["state"] == "OK", "direct peer path state mismatch")
            if path_kind == "relayed":
                require(row["state"] == "WARN" and row["fix"] ==
                        "Allow outbound UDP between the two machines so Tailscale can use a direct path; relayed links add tens of milliseconds to every search.",
                        "relayed peer path state/fix mismatch")
    validate_hash_graph([{"path": c["id"], "depends_on": c["blocked_by"]} for c in checks])
    counts = {state.lower(): sum(c["state"] == state for c in checks) for state in ("OK", "WARN", "FAIL", "SKIP")}
    require(counts == value["summary"], "doctor summary mismatch")
    exit_code = 2 if counts["fail"] or not value["complete"] else 1 if counts["warn"] else 0
    require(value["exit_code"] == exit_code, "doctor exit mismatch")
    pairs = {"workstation": {"openbsd"}, "forge": {"linux", "windows"}, "headend": {"linux", "windows", "macos"}}
    role, platform = value["role"], value["platform"]
    if role is None or platform is None:
        require(any(c["id"] in {"arguments", "installation"} and c["state"] == "FAIL" for c in checks), "undetermined role without failure")
    else:
        require(platform in pairs[role], "unsupported role/platform")


def _selftest_reference(value):
    import hashlib
    settings = json.dumps(value["settings"], sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    require(hashlib.sha256(settings).hexdigest() == value["settings_sha256"], "settings hash mismatch")
    count, nums, metrics = value["question_count"], value["numerators"], value["reference"]
    require(0 <= nums["hit_at_1"] <= nums["hit_at_3"] <= nums["hit_at_8"] <= count, "reference counts mismatch")
    for key in ("hit_at_1", "hit_at_3", "hit_at_8"):
        require(math.isclose(nums[key] / count, metrics[key], abs_tol=1e-12), "reference metric mismatch")
    require(0 <= nums["reciprocal_rank_sum_at_10"] <= count, "reference rank sum mismatch")
    require(math.isclose(nums["reciprocal_rank_sum_at_10"] / count, metrics["mrr_at_10"], abs_tol=1e-12), "reference MRR mismatch")


def _selftest_questions(value):
    unique(q["id"] for q in value["questions"])
    for question in value["questions"]:
        unique((label["doc_key"], label["exact_span"]) for label in question["labels"])


def _selftest_result(value):
    unique(c["check"] for c in value["checks"])
    unique(c["id"] for c in value["details"])
    require(value["completed_questions"] <= value["question_count"], "completed question count mismatch")
    if value["status"] != "INCOMPLETE":
        require(value["completed_questions"] == value["question_count"], "incomplete measured run")
        require(not any(c["state"] != "OK" for c in value["checks"]), "degraded measured run")
        metrics = value["measured"]
        require(metrics["hit_at_1"] <= metrics["hit_at_3"] <= metrics["hit_at_8"], "result hit metrics are not monotonic")
        require(metrics["hit_at_1"] <= metrics["mrr_at_10"], "result MRR below rank-one hits")
        if value["details"]:
            require(len(value["details"]) == value["question_count"], "details count mismatch")
            ranks = [r["first_relevant_rank"] for r in value["details"]]
            metrics = {f"hit_at_{k}": sum(r is not None and r <= k for r in ranks) / len(ranks) for k in (1, 3, 8)}
            metrics["mrr_at_10"] = sum(1 / r for r in ranks if r is not None) / len(ranks)
            require(all(math.isclose(metric, value["measured"][key], abs_tol=1e-12) for key, metric in metrics.items()),
                    "result details/metrics disagree")
        passed = all(value["reference"][key] - metric <= 0.02 + 1e-12 for key, metric in value["measured"].items())
        require((value["status"] == "PASS") == passed, "selftest tolerance verdict mismatch")


def _calibration(value):
    runs = value["runs"]
    unique(r["id"] for r in runs)
    require(len(runs) == 3 and all(r["complete"] for r in runs), "calibration needs three complete runs")
    require(len({r["ranks_sha256"] for r in runs}) == 1, "calibration ranks differ")
    _bindings(value["candidate_inputs"])
    _bindings([r["receipt"] for r in runs])


def _doctor_install(value):
    pairs = {"workstation": {"openbsd"}, "forge": {"linux", "windows"}, "headend": {"linux", "windows", "macos"}}
    require(value["platform"] in pairs[value["role"]], "unsupported receipt role/platform")
    for key, identity in (("services", "id"), ("models", "role"), ("ports", "id"),
                          ("routes", "id"), ("filesystems", "id"), ("credential_slots", "id")):
        unique(r[identity] for r in value[key])
    services = {s["id"] for s in value["services"]}
    require(all(p["service"] in services for p in value["ports"]), "unknown port service")
    for filesystem in value["filesystems"]:
        require(filesystem["safe_running_bytes"] <= filesystem["next_operation_bytes"], "receipt storage thresholds reversed")
    require(value["content"] is None or value["role"] == "workstation", "receipt content role mismatch")
    if value["role"] == "forge":
        require(value["capability_manifest_sha256"] is not None, "Forge capability binding missing")


def _subjects(subjects):
    unique(s["path"].casefold() for s in subjects)
    for subject in subjects:
        safe_path(subject["path"])
        unique(a["role"] for a in subject["aliases"])
        for alias in subject["aliases"]:
            require(alias["path"] == subject["path"], "alias must refer to the one stored subject")


def _bindings(rows):
    unique(row["path"].casefold() for row in rows)
    for row in rows:
        safe_path(row["path"])


def _sbom_input(value):
    _subjects(value["subjects"])
    roots = value["input_roots"]
    unique(r["id"] for r in roots)
    unique(r["path"].casefold() for r in roots)
    for root in roots:
        safe_path(root["path"])
        safe_path(root["manifest"]["path"])
        require(root["manifest"]["path"].startswith(root["path"] + "/"), "manifest outside declared input root")
    def rooted(path):
        safe_path(path)
        require(any(path.startswith(root["path"] + "/") for root in roots), "input outside declared roots")
    components = value["components"]
    unique(c["id"] for c in components)
    ids = {c["id"] for c in components}
    manifests = {r["manifest"]["path"]: r["manifest"]["sha256"] for r in roots}
    used = set()
    for subject in value["subjects"]:
        require(set(subject["components"]) <= ids, "unknown subject component")
        unique(subject["components"])
        used.update(subject["components"])
        _bindings(subject["input_manifests"])
        _bindings(subject["contents_sboms"])
        for binding in subject["input_manifests"]:
            require(manifests.get(binding["path"]) == binding["sha256"], "input manifest binding mismatch")
    require(used == ids, "orphan component")
    for component in components:
        if component["source_url"] != "NOASSERTION":
            safe_url(component["source_url"])
        _bindings(component["licence_evidence"])
        for binding in component["licence_evidence"]:
            rooted(binding["path"])
        if component["base_inventory"] is not None:
            rooted(component["base_inventory"]["path"])
        if component["purpose"] == "CONTAINER" and component["base_inventory"] is None:
            require(any(e["component"] == component["id"] and e["scope"] == "components"
                        for e in value["exceptions"]), "container base inventory missing")
    # Package dependencies may be circular; only hash-containing artefacts must
    # form a DAG. Keep package reference closure separate from the hash graph.
    for component in components:
        unique(component["dependencies"])
        require(set(component["dependencies"]) <= ids, "component dependency missing")
    for exception in value["exceptions"]:
        require(exception["component"] in ids, "exception component missing")
        safe_path(exception["approval"]["path"])
    unique((e["component"], e["scope"]) for e in value["exceptions"])
    for path in value["excluded_metadata"]:
        safe_path(path)
    unique(value["excluded_metadata"])


def _sbom_index(value):
    _subjects([row["subject"] for row in value["subjects"]])
    unique(row["sbom"]["asset"].casefold() for row in value["subjects"])
    for row in value["subjects"]:
        safe_path(row["sbom"]["asset"], basename=True)
        require(row["subject"]["path"] != row["sbom"]["asset"], "subject cannot be its SBOM")
        _bindings(row["contents"])
        destinations = []
        for location in row["sbom"]["media_paths"]:
            safe_path(location["path"])
            destinations.append((location["disc"], location["path"].casefold()))
        unique(destinations)
        if "partial" in row["coverage"].values():
            require(row["gap"] is not None, "partial SBOM coverage needs a gap record")
        if row["gap"] is not None:
            safe_path(row["gap"]["path"])


def _sbom_gap(value):
    safe_path(value["subject_path"])
    unique((g["component"], g["scope"]) for g in value["gaps"])
    for gap in value["gaps"]:
        if gap["approval"] is not None:
            safe_path(gap["approval"]["path"])


def validate_selftest_bundle(questions_bytes, reference):
    """Check exact question-byte binding without executing any search."""
    import hashlib
    questions = strict_loads(questions_bytes)
    validate_contract("selftest-questions", questions)
    validate_contract("selftest-reference", reference)
    require(hashlib.sha256(questions_bytes).hexdigest() == reference["questions_sha256"], "question bytes differ")
    for key in ("bundle_id", "release", "fixture_only"):
        require(questions[key] == reference[key], "selftest bundle identity mismatch")
    require(len(questions["questions"]) == reference["question_count"], "question count differs")
    unique(q["id"] for q in questions["questions"])
    return questions


def validate_subject_agreement(left, right):
    """Require exact coverage of one frozen subject inventory by another."""
    _subjects(left)
    _subjects(right)
    require({s["path"]: s for s in left} == {s["path"]: s for s in right}, "subject inventories disagree")
    return left


def _reproducibility(value):
    _subjects([row["subject"] for row in value["subjects"]])
    for row in value["subjects"]:
        # Missing evidence is explicit only for an unproven subject.
        if row["status"] != "not-proven":
            require(all(row[key] is not None for key in
                        ("input_manifest_sha256", "recipe_sha256", "toolchain_sha256")),
                    "Proven and reused subjects require recorded evidence hashes.")
        for key in ("receipt", "provenance"):
            if row[key] is not None:
                safe_path(row[key]["path"])
        runs = row["runs"]
        unique(r["id"] for r in runs)
        for run in runs:
            require(run["environment"]["source_date_epoch"] == value["source_date_epoch"], "run epoch mismatch")
            unique(t["name"] for t in run["tool_versions"])
        if row["status"] == "proven":
            require(row["scope"] != "unchanged-upstream-input", "reuse is not a rebuild")
            require(len(runs) == 2 and all(r["clean"] for r in runs), "proof needs two distinct clean runs")
            expected = {k: row["subject"][k] for k in ("size", "sha256")}
            require(all(r["output"] == expected for r in runs) and row["byte_comparison"] is True,
                    "proof output byte comparison mismatch")
            require(row["receipt"] is not None and row["reason"] is None, "proof receipt missing or contradictory")
        elif row["status"] == "reused":
            require(row["scope"] == "unchanged-upstream-input" and not runs and row["byte_comparison"] is None,
                    "reuse cannot claim build comparison")
            require(row["provenance"] is not None, "reuse provenance missing")
        else:
            require(row["reason"] is not None, "unproven status needs a precise reason")
            if runs:
                require(row["receipt"] is not None, "observed unproven runs need a receipt")
                expected = {k: row["subject"][k] for k in ("size", "sha256")}
                require(any(r["output"] == expected for r in runs), "no observed output matches subject")
            if row["byte_comparison"] is not None:
                require(len(runs) == 2 and row["byte_comparison"] == (runs[0]["output"] == runs[1]["output"]),
                        "observed comparison contradicts outputs")


def validate_contract_set(documents):
    """Check agreement across supplied contract kinds, without authenticating.

    The caller must authenticate all input bytes through the signed root first.
    A partial set remains useful for bounded job handoffs; missing release
    artefacts are enforced by the release-producing job, not inferred here.
    """
    for kind, value in documents.items():
        validate_contract(kind, value)
    if "selftest-reference" in documents and "selftest-calibration" in documents:
        import hashlib
        reference, calibration = documents["selftest-reference"], documents["selftest-calibration"]
        for key in ("bundle_id", "release", "fixture_only", "questions_sha256", "question_count",
                    "generation", "corpus_fingerprint", "models", "settings_sha256"):
            require(reference[key] == calibration[key], "calibration/reference binding mismatch")
        request = json.dumps(reference["request"], sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        require(hashlib.sha256(request).hexdigest() == calibration["request_sha256"], "calibration request binding mismatch")
    if "mirrors" in documents and "capability-manifest" in documents:
        validate_mirror_capabilities(documents["mirrors"], documents["capability-manifest"])
    inventories = []
    if "sbom-input" in documents:
        inventories.append(documents["sbom-input"]["subjects"])
    for kind in ("sbom-index", "reproducibility"):
        if kind in documents:
            inventories.append([row["subject"] for row in documents[kind]["subjects"]])
    for inventory in inventories[1:]:
        validate_subject_agreement([{k: s[k] for k in ("kind", "path", "size", "sha256", "aliases")}
                                    for s in inventories[0]], inventory)
    if "mirrors" in documents and inventories:
        by_path = {s["path"]: s for s in inventories[0]}
        discs = mirror_transports(documents["mirrors"])
        require({s["path"] for s in inventories[0] if s["kind"] == "disc"} == {d["file"] for d in discs},
                "mirror/SBOM disc coverage mismatch")
        for disc in discs:
            subject = by_path[disc["file"]]
            require(all(subject[k] == disc[k] for k in ("size", "sha256")), "mirror/SBOM subject binding mismatch")
    if "asset-map" in documents and "sbom-index" in documents:
        assets = {e["asset"]: e for e in documents["asset-map"]["entries"]}
        for row in documents["sbom-index"]["subjects"]:
            sbom = row["sbom"]
            require(sbom["asset"] in assets and assets[sbom["asset"]]["media_paths"] == sbom["media_paths"],
                    "SBOM asset mapping mismatch")
    return documents


def validate_contract(kind, value):
    require(re.fullmatch(r"[a-z][a-z0-9-]*", kind), "invalid contract kind")
    schema_name = "mirrors-v1" if kind == "mirrors" and isinstance(value, dict) and value.get("format_version") == 1 else kind
    schema = strict_load(CONTRACTS / "schemas" / (schema_name + ".schema.json"))
    validate_schema(value, schema)
    require(len(canonical(value)) <= CONTRACT_LIMITS.get(kind, MAX_JSON_BYTES), "contract exceeds byte limit")
    if kind == "capability-manifest":
        import importlib.util
        spec = importlib.util.spec_from_file_location("deep32_capability_contract", CONTRACTS.parent / "capabilities.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module.validate_manifest(value)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        return value
    checks = {"mirrors": _mirrors, "asset-map": _asset_map, "doctor-output": _doctor,
              "selftest-reference": _selftest_reference, "selftest-result": _selftest_result,
              "hash-graph": lambda v: validate_hash_graph(v["nodes"]),
              "doctor-install": _doctor_install, "sbom-input": _sbom_input,
              "sbom-index": _sbom_index, "reproducibility": _reproducibility,
              "selftest-calibration": _calibration, "selftest-questions": _selftest_questions,
              "sbom-gap": _sbom_gap}
    if kind in checks:
        checks[kind](value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind")
    parser.add_argument("document", type=Path)
    args = parser.parse_args(argv)
    try:
        load_contract(args.kind, args.document)
    except (ContractError, OSError) as exc:
        print("Contract refused: " + (str(exc) if isinstance(exc, ContractError) else "input unavailable"), file=sys.stderr)
        return 2
    print("Contract valid (authentication required separately)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
