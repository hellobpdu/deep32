#!/usr/bin/env python3
"""Build pinned registry generations offline. This tool is never installed."""

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
import zlib


NS = "{http://www.iana.org/assignments}"
MAGIC = b"D32REG1\0"
SCHEMA = 1
CONVERTER_REV = "t12-1"
HEADER = struct.Struct(">8sIIII")
RECORD = struct.Struct(">BBBB16s16sIIIIIII")
GEO_RECORD = struct.Struct(">B3s16s16s")
INTERVAL, SCOPE, RELATIVE, GROUP_ID = range(4)
NO_SCOPE = 255
MAX_RECORDS = 2_000_000
MAX_SOURCE = 128 * 1024 * 1024
MAX_STRINGS = 8 * 1024 * 1024
DATASET_IDS = {"iana": 1, "mcast": 2, "nro": 3, "geo": 4}
LICENSES = {"iana": "CC0-1.0", "mcast": "CC0-1.0", "nro": "HELD-Q3", "geo": "CC-BY-4.0"}
IANA_ROOT = "https://www.iana.org/assignments/"
NRO_ROOT = "https://ftp.ripe.net/pub/stats/ripencc/nro-stats/"
GEO_ROOT = "https://download.db-ip.com/free/"
SOURCES = {
    "iana": ("iana-ipv4-special-registry", "iana-ipv6-special-registry"),
    "mcast": ("multicast-addresses", "ipv6-multicast-addresses"),
    "nro": ("nro-delegated-stats",),
    "geo": ("dbip-country-lite",),
}


@dataclass(frozen=True)
class Source:
    name: str
    date: str
    digest: str
    url: str


@dataclass(frozen=True, order=True, slots=True)
class Row:
    family: int
    kind: int
    first: int
    last: int
    flags: int = 0
    scope: int = NO_SCOPE
    name: str = ""
    registry: str = ""
    status: str = ""
    country: str = ""
    date: str = ""
    reference: str = ""
    detail: str = ""


def validate_date(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"noncanonical date: {value}")
    return value


def read_pins(path, dataset):
    sources = []
    for line in path.read_text(encoding="ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        name, updated, digest, url = line.split("\t")
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError(f"{name}: invalid source SHA-256")
        validate_date(updated)
        if dataset == "nro":
            expected = f"{NRO_ROOT}{updated.replace('-', '')}/{name}"
        elif dataset == "geo":
            if not updated.endswith("-01"):
                raise ValueError(f"{name}: expected the first day of the source month")
            expected = f"{GEO_ROOT}{name}-{updated[:7]}.csv.gz"
        else:
            expected = f"{IANA_ROOT}{name}/{name}.xml"
        if url != expected:
            raise ValueError(f"{name}: unexpected source URL")
        sources.append(Source(name, updated, digest, url))
    if tuple(source.name for source in sources) != SOURCES[dataset]:
        raise ValueError(f"{dataset}: source pin roster/order mismatch")
    return sources


def read_source(root, source, dataset):
    if dataset == "geo":
        filename = f"{source.name}-{source.date[:7]}.csv.gz"
    else:
        filename = source.name if dataset == "nro" else source.name + ".xml"
    path = root / filename
    with path.open("rb") as stream:
        blob = stream.read(MAX_SOURCE + 1)
    if len(blob) > MAX_SOURCE or hashlib.sha256(blob).hexdigest() != source.digest:
        raise ValueError(f"{path.name}: source size or SHA-256 mismatch")
    return blob


def xml_field(node, tag, *, optional=False):
    found = node.find(NS + tag)
    if found is None:
        if not optional:
            raise ValueError(f"missing XML field: {tag}")
        return ""
    return " ".join("".join(found.itertext()).split())


def xml_root(source, blob):
    if b"<!DOCTYPE" in blob or b"<!ENTITY" in blob:
        raise ValueError(f"{source.name}: XML declarations are unsupported")
    root = ET.fromstring(blob)
    if root.tag != NS + "registry" or root.get("id") != source.name:
        raise ValueError(f"{source.name}: unexpected registry")
    if xml_field(root, "updated") != source.date:
        raise ValueError(f"{source.name}: source date mismatch")
    return root


def refs(node):
    return ", ".join(sorted({item.get("data", "").upper()
                             for item in node.iter(NS + "xref")
                             if item.get("type") == "rfc"
                             and re.fullmatch(r"rfc[0-9]+", item.get("data", ""))}))


def ip_interval(value, family):
    if "-" in value:
        first, last = (ipaddress.ip_address(part.strip()) for part in value.split("-", 1))
    else:
        network = ipaddress.ip_network(value, strict=True)
        first, last = network.network_address, network.broadcast_address
    if first.version != family or last.version != family or first > last:
        raise ValueError(f"address: invalid IPv{family} interval {value!r}")
    return int(first), int(last)


def special_rows(source, blob, family):
    root = xml_root(source, blob)
    registry = root.find(NS + "registry")
    if registry is None or registry.get("id") != source.name + "-1":
        raise ValueError(f"{source.name}: missing special-purpose table")
    fields = ("source", "destination", "forwardable", "global", "reserved")
    for record in registry.findall(NS + "record"):
        attributes = []
        for key in fields:
            value = xml_field(record, key)
            if value not in ("", "True", "False", "N/A"):
                raise ValueError(f"{source.name}: invalid {key} value")
            attributes.append(f"{key}={value.lower() or 'unknown'}")
        termination = xml_field(record, "termination", optional=True)
        if termination:
            attributes.append(f"termination={termination}")
        for address in xml_field(record, "address").split(","):
            first, last = ip_interval(address.strip(), family)
            yield Row(family, INTERVAL, first, last, name=xml_field(record, "name"),
                      registry=registry.get("id"), status="terminated" if termination else "active",
                      date=xml_field(record, "allocation"), reference=refs(record),
                      detail=" ".join(attributes))


def numeric_interval(value, base, maximum):
    parts = value.split("-")
    pattern = {10: r"[0-9]+", 16: r"(?:0[xX])?[0-9A-Fa-f]+"}[base]
    if len(parts) > 2 or any(not re.fullmatch(pattern, part) for part in parts):
        raise ValueError(f"numeric registry: invalid interval {value!r}")
    first, last = int(parts[0], base), int(parts[-1], base)
    if not first <= last <= maximum:
        raise ValueError(f"numeric registry: out of range interval {value!r}")
    return first, last


def mcast_status(name, title, flags):
    description = name.lower()
    for keyword, status in (("unassigned", "unassigned"), ("reserved", "reserved"),
                            ("invalid", "invalid"), ("private use", "private-use"),
                            ("experimental", "experimental")):
        if re.search(rf"\b{keyword}\b", description):
            return status
    if flags & 1 or "transient" in description or "Transient" in title:
        return "transient"
    if description == "variable scope allocation":
        return "variable-scope"
    return "assigned"


def mcast_ip(value, family):
    value = value.upper()
    if "X" in value:
        if family != 6 or any(not re.match(r"^FF[0-9A-F]X:", part)
                              or part.count("X") != 1 for part in value.split("-")):
            raise ValueError(f"multicast: invalid wildcard {value!r}")
        for scope in range(16):
            yield from mcast_ip(value.replace("X", f"{scope:X}"), family)
        return
    first, last = ip_interval(value, family)
    if family == 4 and first >> 28 == last >> 28 == 0xE:
        yield first, last, 0, NO_SCOPE
    elif family == 6 and first >> 112 == last >> 112 and first >> 120 == 0xFF:
        yield first, last, (first >> 116) & 0xF, (first >> 112) & 0xF
    else:
        raise ValueError(f"multicast: interval is outside a single multicast scope {value!r}")


def multicast_rows(source, blob, family):
    root = xml_root(source, blob)
    for registry in root.findall(NS + "registry"):
        identifier = registry.get("id")
        title = xml_field(registry, "title")
        if not identifier:
            raise ValueError("multicast: unnamed subregistry")
        for record in registry.findall(NS + "record"):
            name = xml_field(record, "description")
            updated = record.get("date", "")
            reference = refs(record)
            if identifier == "ipv6-scope":
                first, last = numeric_interval(xml_field(record, "value"), 16, 15)
                ranges = [(first, last, 0, NO_SCOPE)]
                kind = SCOPE
            elif identifier == "dynamic-multicast-group-ids":
                first, last = numeric_interval(xml_field(record, "value"), 16, 0xFFFFFFFF)
                ranges = [(first, last, 0, NO_SCOPE)]
                kind = GROUP_ID
            elif identifier == "multicast-addresses-13":
                first, last = numeric_interval(xml_field(record, "relative"), 10, 255)
                ranges = [(first, last, 0, NO_SCOPE)]
                kind = RELATIVE
            else:
                ranges = mcast_ip(xml_field(record, "addr"), family)
                kind = INTERVAL
            for first, last, flags, scope in ranges:
                yield Row(family, kind, first, last, flags, scope, name=name, registry=identifier,
                          status=mcast_status(name, title, flags), date=updated,
                          reference=reference, detail=title)


def nro_date(value):
    if value == "00000000":
        return ""
    if not re.fullmatch("[0-9]{8}", value):
        raise ValueError(f"NRO date: invalid value {value!r}")
    return validate_date(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def nro_rows(source, blob):
    header = None
    summaries = {}
    counts = Counter()
    last = {}
    types = {"asn", "ipv4", "ipv6"}
    registries = {"afrinic", "apnic", "arin", "iana", "lacnic", "ripencc"}
    for number, raw in enumerate(io.BytesIO(blob), 1):
        line = raw.decode("ascii").strip()
        if not line or line.startswith("#"):
            continue
        columns = line.split("|")
        try:
            if header is None:
                version, registry, serial, total, start, end, utc = columns
                if (version != "2" or registry != "nro" or nro_date(serial) != source.date
                        or nro_date(end) != source.date or nro_date(start) > source.date
                        or utc != "+0000" or not total.isdecimal()):
                    raise ValueError("invalid version/date header")
                header = int(total)
                continue
            if len(columns) == 6 and columns[-1] == "summary":
                registry, cc, kind, address, total, _ = columns
                if (counts or registry != "nro" or cc != "*" or address != "*"
                        or kind not in types or kind in summaries or not total.isdecimal()):
                    raise ValueError("invalid resource summary")
                summaries[kind] = int(total)
                continue
            rir, country, kind, start, count, allocated, status, *_ = columns
            if rir not in registries or (country and not re.fullmatch("[A-Z]{2}", country)):
                raise ValueError("invalid registry or country")
            if kind not in types or not count.isdecimal():
                raise ValueError("invalid resource type or value")
            if status not in {"allocated", "assigned", "available", "reserved"}:
                raise ValueError("invalid status")
            counts[kind] += 1
            if kind == "asn":
                if not start.isdecimal() or int(count) < 1 or int(start) + int(count) > 2**32:
                    raise ValueError("invalid ASN range")
                continue
            family = int(kind[-1])
            first = ipaddress.ip_address(start)
            value = int(count)
            if first.version != family:
                raise ValueError("address family mismatch")
            if family == 4:
                end = int(first) + value - 1
                if not 0 < value or end >= 2**32:
                    raise ValueError("IPv4 count overflows")
            else:
                network = ipaddress.IPv6Network((first, value), strict=True)
                end = int(network.broadcast_address)
            if int(first) <= last.get(family, -1):
                raise ValueError("unordered or overlapping range")
            last[family] = end
            yield Row(family, INTERVAL, int(first), end, registry=rir, status=status,
                      country=country, date=nro_date(allocated))
        except ValueError as exc:
            raise ValueError(f"NRO line {number}: {exc}") from exc
    if set(summaries) != types or header != sum(counts.values()) or summaries != dict(counts):
        raise ValueError("NRO header and resource totals do not match")


def geo_rows(blob):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as source:
            data = source.read(MAX_SOURCE + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("invalid DB-IP gzip snapshot") from exc
    if len(data) > MAX_SOURCE:
        raise ValueError("DB-IP expanded CSV exceeds source size bound")

    # Keep only the pending range per family so interleaving cannot hide overlap or gaps.
    pending = {}
    reader = csv.reader(io.TextIOWrapper(io.BytesIO(data), encoding="ascii", newline=""),
                        strict=True)
    try:
        for number, columns in enumerate(reader, 1):
            if number > MAX_RECORDS:
                raise ValueError("DB-IP source record count exceeds schema bound")
            start, end, country = columns
            first, last = ipaddress.ip_address(start), ipaddress.ip_address(end)
            if first.version != last.version or first > last or "%" in start or "%" in end:
                raise ValueError("invalid address interval")
            if country == "ZZ":
                country = ""
            elif not re.fullmatch("[A-Z]{2}", country):
                raise ValueError("invalid country code")
            family, first, last = first.version, int(first), int(last)
            previous = pending.get(family)
            if previous is not None:
                if first <= previous.last:
                    raise ValueError("unordered or overlapping range")
                if previous.last + 1 == first and previous.country == country:
                    first = previous.first
                else:
                    yield previous
            pending[family] = Row(family, INTERVAL, first, last,
                                  status="estimated" if country else "unknown", country=country)
    except (ValueError, csv.Error) as exc:
        raise ValueError(f"DB-IP CSV line {reader.line_num}: {exc}") from exc
    if not pending:
        raise ValueError("DB-IP CSV has no records")
    yield from pending.values()


def encode(dataset, rows):
    if not 0 < len(rows) <= MAX_RECORDS:
        raise ValueError("record count outside schema bounds")
    if dataset == "geo":
        records = bytearray()
        for row in rows:
            country = row.country.encode("ascii")
            if country and (country == b"ZZ" or not re.fullmatch(rb"[A-Z]{2}", country)):
                raise ValueError("geo record contains an invalid country code")
            records.extend(GEO_RECORD.pack(row.family, country, row.first.to_bytes(16, "big"),
                                           row.last.to_bytes(16, "big")))
        return HEADER.pack(MAGIC, SCHEMA, DATASET_IDS[dataset], len(rows), 0) + records

    strings = bytearray(b"\0")
    offsets = {"": 0}
    records = bytearray()
    for row in rows:
        pointers = []
        for value in (row.name, row.registry, row.status, row.country, row.date,
                      row.reference, row.detail):
            if value not in offsets:
                raw = value.encode("utf-8")
                if len(raw) > 1024 or any(c < 32 or c == 127 for c in raw):
                    raise ValueError("record contains an invalid or overlong display string")
                offsets[value] = len(strings)
                strings.extend(raw + b"\0")
                if len(strings) > MAX_STRINGS:
                    raise ValueError("string pool exceeds schema bound")
            pointers.append(offsets[value])
        records.extend(RECORD.pack(row.family, row.kind, row.flags, row.scope,
                                   row.first.to_bytes(16, "big"), row.last.to_bytes(16, "big"),
                                   *pointers))
    return (HEADER.pack(MAGIC, SCHEMA, DATASET_IDS[dataset], len(rows), len(strings))
            + records + strings)


def render(dataset, sources, root):
    rows = []
    for index, source in enumerate(sources):
        blob = read_source(root, source, dataset)
        if dataset == "nro":
            rows.extend(nro_rows(source, blob))
        elif dataset == "geo":
            rows.extend(geo_rows(blob))
        elif dataset == "iana":
            rows.extend(special_rows(source, blob, 4 + 2 * index))
        else:
            rows.extend(multicast_rows(source, blob, 4 + 2 * index))
    rows.sort()
    output = encode(dataset, rows)
    count = len(rows)
    # Multiple source blobs use an ordered fingerprint of their individual digests.
    digest = (sources[0].digest if len(sources) == 1 else
              hashlib.sha256(b"".join(bytes.fromhex(s.digest) for s in sources)).hexdigest())
    snapshot = max(s.date for s in sources)
    gen = f"{snapshot.replace('-', '')}-{digest[:16]}"
    manifest = dict(dataset=dataset, gen=gen, source_url=",".join(s.url for s in sources),
                    source_date=snapshot, source_sha256=digest, license=LICENSES[dataset],
                    converter_rev=CONVERTER_REV, schema=SCHEMA, records=count,
                    output_sha256=hashlib.sha256(output).hexdigest())
    return output, manifest


def manifest_bytes(values):
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode("ascii")


def publish(output, manifest, root):
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest["gen"]
    if target.exists() or target.is_symlink():
        raise ValueError(f"generation already exists: {target}")
    with tempfile.TemporaryDirectory(prefix=".build-", dir=root) as temp:
        scratch = Path(temp) / "generation"
        scratch.mkdir()
        (scratch / "registry.d32r").write_bytes(output)
        (scratch / "MANIFEST").write_bytes(manifest_bytes(manifest))
        os.rename(scratch, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=DATASET_IDS)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path)
    action.add_argument("--local-check", action="store_true",
                        help="validate and hash without writing release output")
    args = parser.parse_args()
    try:
        if args.out and LICENSES[args.dataset] == "HELD-Q3":
            raise ValueError("NRO redistribution is held for Q3; use --local-check")
        pins = read_pins(args.pins, args.dataset)
        output, manifest = render(args.dataset, pins, args.sources)
        if args.out:
            print(publish(output, manifest, args.out))
        print(manifest_bytes(manifest).decode("ascii"), end="")
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.exit(1, f"{args.dataset}: {exc}\n")


if __name__ == "__main__":
    main()
