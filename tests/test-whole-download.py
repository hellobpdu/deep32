"""Offline synthetic J03 tests. All transport is fake; no sockets are opened."""
import argparse
import contextlib
import copy
from email.message import Message
import errno
import functools
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("download_under_test", ROOT / "src/release-tools/deep32_download.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
SCRATCH = None
SCENARIOS = 0
FINGERPRINT = "A" * 40
TORRENT = "https://archive.org/download/deep32-0.1.0-alpha.2/invented.torrent"
NAME = "deep32-0.1.0-alpha.2-disc2-content.iso"


def digest_repeated(digest, value, length):
    block = bytes([value]) * min(d.BUFFER, length)
    while length:
        piece = block[:min(len(block), length)]
        digest.update(piece)
        length -= len(piece)


@functools.lru_cache(maxsize=8)
def hashes(size):
    whole, chunks = hashlib.sha256(), []
    offset = 0
    while offset < size:
        length = min(d.CHUNK_BYTES, size - offset)
        value, chunk = 97 + offset // d.CHUNK_BYTES, hashlib.sha256()
        digest_repeated(whole, value, length)
        digest_repeated(chunk, value, length)
        chunks.append(chunk.hexdigest())
        offset += length
    return whole.hexdigest(), chunks


def descriptor(size=26, mirrors=1):
    whole, chunks = hashes(size)
    return {"file": NAME, "role": "disc2", "kind": "content", "size": size, "sha256": whole,
            "chunk_bytes": d.CHUNK_BYTES, "chunk_sha256": list(chunks),
            "mirrors": [{"id": "mirror-" + chr(97 + n),
                         "url": "https://archive.org/download/deep32-0.1.0-alpha.2/" + NAME if n == 0 else
                         "https://mirror%d.deep32.org/" % n + NAME} for n in range(mirrors)]}


class Response:
    def __init__(self, status=200, headers=(), body=b"", *, limit=None, fail_at=None,
                 failure=None, callback=None):
        self.status, self.headers = status, Message()
        for key, value in headers:
            self.headers[key] = value
        self.body = io.BytesIO(body) if isinstance(body, bytes) else body
        self.limit, self.fail_at = limit, fail_at
        self.failure, self.callback = failure, callback
        self.read_calls, self.read_bytes, self.max_read, self.closed = 0, 0, 0, False

    def read(self, count):
        self.read_calls += 1
        self.max_read = max(self.max_read, count)
        if self.callback:
            self.callback()
        if self.fail_at is not None:
            if self.read_bytes >= self.fail_at:
                raise self.failure or OSError("SYNTHETIC NETWORK ERROR SECRET SENTINEL")
            count = min(count, self.fail_at - self.read_bytes)
        if self.limit:
            count = min(count, self.limit)
        result = self.body.read(count)
        self.read_bytes += len(result)
        return result

    def close(self):
        self.closed = True


class Pattern:
    """Invented bytes without allocating an image-sized in-memory response."""
    def __init__(self, size, start=0, *, corrupt=None):
        self.size, self.offset, self.corrupt = size, start, corrupt

    def read(self, count):
        count = min(count, self.size - self.offset, d.CHUNK_BYTES - self.offset % d.CHUNK_BYTES)
        if count <= 0:
            return b""
        result = bytes([97 + self.offset // d.CHUNK_BYTES]) * count
        if self.corrupt is not None and self.offset <= self.corrupt < self.offset + count:
            at = self.corrupt - self.offset
            result = result[:at] + b"!" + result[at + 1:]
        self.offset += count
        return result


def good(disc, offset=0, **kwargs):
    headers = [("Content-Length", str(disc["size"] - offset))]
    if offset:
        headers += [("Content-Range", "bytes %d-%d/%d" % (offset, disc["size"] - 1, disc["size"]))]
    return Response(206 if offset else 200, headers, Pattern(disc["size"], offset), **kwargs)


class Opener:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, **kwargs):
        self.calls.append((url, headers, kwargs))
        if not self.responses:
            raise AssertionError("hidden retry or unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FixtureFS(d.FileSystem):
    """Real local files/locks/publication; fake ownership and free-space probes."""
    def __init__(self):
        self.budgets, self.capabilities = [], 0

    def private_directory(self, value):
        return self.path(value, directory=True)

    def available(self, folder):
        self.budgets.append(folder)
        return 2 * d.MAX_SIZE

    def capability(self, folder, size):
        self.capabilities += 1
        return super().capability(folder, size)


class Crash(BaseException):
    pass


class DownloadTests(unittest.TestCase):
    def setUp(self):
        network_guard = mock.patch("socket.create_connection", side_effect=AssertionError("Offline suite attempted network access"))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "private destination with spaces"
        self.folder.mkdir(mode=0o700)
        self.fs = FixtureFS()

    def scenario(self):
        global SCENARIOS
        SCENARIOS += 1

    def fetch(self, disc, opener, fs=None):
        self.scenario()
        return d.fetch_whole(disc, self.folder, release="v0.1.0-alpha.2", revision=2,
            fingerprint=FINGERPRINT, torrent_url=TORRENT, opener=opener, filesystem=fs or self.fs)

    def partial(self, disc):
        return self.folder / (disc["file"] + ".download")

    def write_pattern(self, target, size, *, corrupt=None):
        with target.open("wb") as handle:
            source = Pattern(size, corrupt=corrupt)
            while block := source.read(d.BUFFER):
                handle.write(block)

    def assert_partial(self, disc, size):
        self.assertFalse((self.folder / disc["file"]).exists())
        self.assertEqual(self.partial(disc).stat().st_size, size)
        self.assertEqual([p.name for p in self.folder.iterdir() if not p.name.startswith(".")], [disc["file"] + ".download"])

    def test_full_200_one_inode_and_no_second_payload(self):
        disc, seen, peak = descriptor(), [], []
        original_link = os.link
        def link(source, target, *args, **kwargs):
            original_link(source, target, *args, **kwargs)
            if str(source).endswith(".download"):
                a, b = Path(source).stat(), Path(target).stat()
                seen.append((a.st_ino, b.st_ino, a.st_nlink))
        def allocation():
            payloads = [p for p in self.folder.iterdir() if not p.name.startswith(".")]
            peak.append(sum(p.stat().st_size for p in payloads))
            self.assertEqual([p.name for p in payloads], [NAME + ".download"])
        response = good(disc, callback=allocation)
        opener = Opener(response)
        with mock.patch.object(d.os, "link", side_effect=link):
            report = self.fetch(disc, opener)
        self.assertEqual(report["status"], "downloaded")
        self.assertEqual(report["mirror_id"], "mirror-a")
        self.assertIn("Verified " + NAME, report["message"])
        self.assertEqual(report["torrent_url"], TORRENT)
        self.assertEqual(seen[0][0], seen[0][1])
        self.assertEqual(seen[0][2], 2)
        self.assertLessEqual(max(peak), disc["size"])
        self.assertFalse(self.partial(disc).exists())
        self.assertEqual((self.folder / NAME).read_bytes(), b"a" * 26)
        self.assertEqual((self.folder / NAME).stat().st_nlink, 1)
        self.assertEqual(opener.calls[0][1], {"Accept-Encoding": "identity", "Connection": "close"})
        self.assertEqual(opener.calls[0][2], {"connect_timeout": 30, "read_timeout": 60})
        self.assertTrue(response.closed)
        self.assertLessEqual(response.max_read, d.BUFFER)

    def test_exact_206_from_zero(self):
        disc = descriptor()
        response = Response(206, [("Content-Range", "bytes 0-25/26")], b"a" * 26)
        self.fetch(disc, Opener(response))
        self.assertTrue(response.closed)

    def test_response_without_length_uses_bounded_close_framing(self):
        self.fetch(descriptor(), Opener(Response(body=b"a" * 26, limit=3)))

    def test_strict_chunked_body_and_extensions(self):
        encoded = b"a;tag=word\r\n" + b"a" * 10 + b"\r\n10;tag=\"invented\\\"value\"\r\n" + b"a" * 16 + b"\r\n0\r\nX-Invented: yes\r\n\r\n"
        response = Response(headers=[("Transfer-Encoding", "chunked")], body=encoded, limit=3)
        self.fetch(descriptor(), Opener(response))
        self.assertEqual((self.folder / NAME).read_bytes(), b"a" * 26)

    def test_bad_headers_never_read_body(self):
        cases = [
            (206, []), (206, [("Content-Range", "bytes 1-25/26")]),
            (206, [("Content-Range", "bytes 0-24/26")]), (206, [("Content-Range", "bytes 0-25/27")]),
            (206, [("Content-Range", "bytes 0-25/*")]), (206, [("Content-Range", "bytes 0-25/26,0-1/26")]),
            (206, [("Content-Range", "bytes 0-25/26")] * 2), (200, [("Content-Range", "bytes 0-25/26")]),
            (200, [("Content-Length", "26")] * 2), (200, [("Content-Length", "26"), ("Content-Length", "27")]),
            (200, [("Content-Length", "26, 26")]), (200, [("Content-Length", "-26")]),
            (200, [("Content-Length", "2.6e1")]), (200, [("Content-Length", "25")]),
            (200, [("Content-Length", "0")]), (200, [("Content-Length", "9" * 100)]),
            (200, [("Transfer-Encoding", "gzip")]), (200, [("Transfer-Encoding", "chunked"), ("Content-Length", "26")]),
            (200, [("Transfer-Encoding", "chunked")] * 2), (200, [("Content-Encoding", "gzip")]),
            (200, [("Content-Encoding", "br")]), (200, [("Content-Encoding", "identity")] * 2),
            (200, [("Content-Type", "Multipart/byteranges; boundary=invented")]),
            (200, [("Content-Type", "application/octet-stream")] * 2),
            (200, [("Content-Encoding", "identity\t")]),
            (416, []), (500, []), (429, []), (304, []), (99, []), (True, [])]
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                response = Response(status, headers, b"untrusted body")
                opener = Opener(response)
                with self.assertRaises(d.TransferError):
                    self.fetch(descriptor(), opener)
                self.assertEqual(response.read_calls, 0)
                self.assertTrue(response.closed)
                self.assert_partial(descriptor(), 0)

    def test_header_rejection_advances_once_then_honest(self):
        disc = descriptor(mirrors=2)
        bad = Response(206, [("Content-Range", "bytes 0-25/27")], b"untrusted")
        opener = Opener(bad, good(disc))
        result = self.fetch(disc, opener)
        self.assertEqual(result["mirror_id"], "mirror-b")
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(bad.read_calls, 0)

    def test_body_failures_discard_unauthenticated_tail(self):
        responses = [Response(body=b"a" * 25), Response(body=b"a" * 27), Response(body=b"!" * 26),
                     Response(body=b"a" * 26, fail_at=13),
                     Response(headers=[("Content-Length", "26")], body=b"a" * 27)]
        for response in responses:
            with self.subTest(response=response):
                with self.assertRaises(d.TransferError) as error:
                    self.fetch(descriptor(), Opener(response))
                self.assertNotIn("SECRET SENTINEL", str(error.exception))
                self.assert_partial(descriptor(), 0)
                self.assertTrue(response.closed)

    def test_malformed_chunked_framing(self):
        bodies = [b"-1\r\n", b"g\r\n", b"1\na\r\n0\r\n\r\n", b"1\r\naX0\r\n\r\n",
                  b"1\r\n", b"1\r\na\r\n", b"1" * 4096, b"1;bad=\r\na\r\n0\r\n\r\n",
                  b"1a\r\n" + b"a" * 26 + b"\r\n0\r\nContent-Length: 26\r\n\r\n",
                  b"1a\r\n" + b"a" * 26 + b"\r\n0\r\ninvalid trailer\r\n\r\n",
                  b"1a\r\n" + b"a" * 26 + b"\r\n0\r\n\r\nEXCESS",
                  b"1b\r\n" + b"a" * 27 + b"\r\n0\r\n\r\n",
                  b"1a\r\n" + b"a" * 26 + b"\r\n0\r\n" + b"X: a\r\n" * 65 + b"\r\n"]
        for body in bodies:
            with self.subTest(body=body[:50]):
                response = Response(headers=[("Transfer-Encoding", "chunked")], body=body)
                with self.assertRaises(d.TransferError):
                    self.fetch(descriptor(), Opener(response))
                self.assert_partial(descriptor(), 0)
                self.assertLessEqual(response.max_read, d.BUFFER)

    def test_existing_final_reused_without_transfer_or_rewrite(self):
        disc = descriptor()
        final = self.folder / NAME
        final.write_bytes(b"a" * 26)
        before = final.stat()
        opener = Opener()
        report = self.fetch(disc, opener)
        self.assertEqual(report["status"], "local")
        self.assertEqual(opener.calls, [])
        self.assertEqual((before.st_ino, before.st_mtime_ns), (final.stat().st_ino, final.stat().st_mtime_ns))
        self.assertFalse(self.partial(disc).exists())

    def test_corrupt_final_refused_without_touching_it(self):
        final = self.folder / NAME
        final.write_bytes(b"unrelated local data")
        with self.assertRaisesRegex(d.LocalError, "remove it explicitly"):
            self.fetch(descriptor(), Opener())
        self.assertEqual(final.read_bytes(), b"unrelated local data")

    def test_full_partial_verified_and_published_without_http(self):
        disc = descriptor()
        self.partial(disc).write_bytes(b"a" * 26)
        inode = self.partial(disc).stat().st_ino
        self.assertEqual(self.fetch(disc, Opener())["status"], "local")
        self.assertEqual((self.folder / NAME).stat().st_ino, inode)

    def test_oversized_partial_is_preserved_and_refused(self):
        disc = descriptor()
        self.partial(disc).write_bytes(b"unrelated" * 100)
        before = self.partial(disc).read_bytes()
        with self.assertRaisesRegex(d.LocalError, "exceeds its signed size"):
            self.fetch(disc, Opener())
        self.assertEqual(self.partial(disc).read_bytes(), before)

    def test_partial_short_chunk_is_truncated_before_request(self):
        disc = descriptor()
        self.partial(disc).write_bytes(b"untrusted short prefix")
        def inspect():
            self.assertLessEqual(self.partial(disc).stat().st_size, disc["size"])
        opener = Opener(good(disc, callback=inspect))
        self.fetch(disc, opener)
        self.assertNotIn("Range", opener.calls[0][1])

    def test_multi_chunk_interrupt_then_resume_exact_range(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        first = good(disc, fail_at=d.CHUNK_BYTES + 5)
        second = good(disc, d.CHUNK_BYTES)
        opener = Opener(first, second)
        report = self.fetch(disc, opener)
        self.assertEqual(opener.calls[1][1]["Range"], "bytes=%d-" % d.CHUNK_BYTES)
        self.assertEqual(report["resumed_bytes"], d.CHUNK_BYTES)
        self.assertIn("resumed verified bytes", report["message"])
        self.assertEqual(report["mirror_id"], "mirror-b")
        self.assertLessEqual(max(first.max_read, second.max_read), d.BUFFER)
        with (self.folder / NAME).open("rb") as handle:
            self.assertEqual(hashes(disc["size"])[0], hashlib.file_digest(handle, "sha256").hexdigest())

    def test_first_corrupt_last_honest_never_poisoned(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        first = Response(body=Pattern(disc["size"], corrupt=1))
        opener = Opener(first, good(disc))
        report = self.fetch(disc, opener)
        self.assertNotIn("Range", opener.calls[1][1])
        self.assertEqual(report["resumed_bytes"], 0)

    def test_second_corrupt_chunk_retains_first_authenticated_chunk(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        opener = Opener(Response(body=Pattern(disc["size"], corrupt=d.CHUNK_BYTES + 2)), good(disc, d.CHUNK_BYTES))
        self.fetch(disc, opener)
        self.assertEqual(opener.calls[1][1]["Range"], "bytes=%d-" % d.CHUNK_BYTES)

    def test_prefix_scan_detects_changed_first_and_second_chunks(self):
        disc = descriptor(d.CHUNK_BYTES + 11)
        for corrupt, prefix in [(1, 0), (d.CHUNK_BYTES + 2, d.CHUNK_BYTES)]:
            with self.subTest(corrupt=corrupt):
                self.write_pattern(self.partial(disc), disc["size"], corrupt=corrupt)
                with self.partial(disc).open("r+b", buffering=0) as handle:
                    self.assertEqual(d.resume_prefix(handle, disc, filesystem=self.fs), prefix)
                self.assert_partial(disc, prefix)
                self.scenario()

    def test_ignored_range_does_not_read_and_advances_at_zero(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        self.write_pattern(self.partial(disc), d.CHUNK_BYTES)
        ignored = good(disc)
        opener = Opener(ignored, good(disc))
        report = self.fetch(disc, opener)
        self.assertIn("Range", opener.calls[0][1])
        self.assertNotIn("Range", opener.calls[1][1])
        self.assertEqual(ignored.read_calls, 0)
        self.assertEqual(report["resumed_bytes"], 0)
        self.assertGreaterEqual(len(self.fs.budgets), 4)

    def test_last_ignored_range_leaves_zero_and_retry_instruction(self):
        disc = descriptor(d.CHUNK_BYTES + 11)
        self.write_pattern(self.partial(disc), d.CHUNK_BYTES)
        ignored = good(disc)
        with self.assertRaisesRegex(d.TransferError, "retry the same command"):
            self.fetch(disc, Opener(ignored))
        self.assertEqual(ignored.read_calls, 0)
        self.assert_partial(disc, 0)

    def test_wrong_resumed_range_and_416_preserve_signed_prefix(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        self.write_pattern(self.partial(disc), d.CHUNK_BYTES)
        opener = Opener(Response(206, [("Content-Range", "bytes 0-10/11")]), Response(416))
        with self.assertRaises(d.TransferError):
            self.fetch(disc, opener)
        self.assert_partial(disc, d.CHUNK_BYTES)
        self.assertEqual(len(opener.calls), 2)

    def test_keyboard_interrupt_retains_only_complete_chunks(self):
        disc = descriptor(d.CHUNK_BYTES + 11)
        response = good(disc, fail_at=d.CHUNK_BYTES + 5, failure=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.fetch(disc, Opener(response))
        self.assert_partial(disc, d.CHUNK_BYTES)
        self.assertTrue(response.closed)

    def test_abrupt_crash_tail_is_repaired_on_next_invocation(self):
        disc = descriptor(d.CHUNK_BYTES + 11)
        with self.assertRaises(Crash):
            self.fetch(disc, Opener(good(disc, fail_at=d.CHUNK_BYTES + 5, failure=Crash())))
        self.assert_partial(disc, d.CHUNK_BYTES + 5)
        opener = Opener(good(disc, d.CHUNK_BYTES))
        self.fetch(disc, opener)
        self.assertEqual(opener.calls[0][1]["Range"], "bytes=%d-" % d.CHUNK_BYTES)

    def test_contradictory_whole_digest_tries_each_mirror_once(self):
        disc = descriptor(mirrors=3)
        disc["sha256"] = "0" * 64
        opener = Opener(*(good(disc) for _ in range(3)))
        with self.assertRaises(d.TransferError) as caught:
            self.fetch(disc, opener)
        self.assertEqual(len(opener.calls), 3)
        self.assertTrue(all("Range" not in call[1] for call in opener.calls))
        self.assertIn("index revision 2", str(caught.exception))
        self.assertIn(FINGERPRINT, str(caught.exception))
        self.assertIn("corrected re-signed metadata", str(caught.exception))
        self.assert_partial(disc, 0)

    def test_full_partial_contradiction_restarts_from_zero(self):
        disc = descriptor()
        disc["sha256"] = "0" * 64
        self.partial(disc).write_bytes(b"a" * 26)
        opener = Opener(good(disc))
        with self.assertRaises(d.TransferError):
            self.fetch(disc, opener)
        self.assertNotIn("Range", opener.calls[0][1])
        self.assert_partial(disc, 0)

    def test_all_sixteen_mirrors_bounded_and_errors_redacted(self):
        disc = descriptor(mirrors=16)
        opener = Opener(*(OSError("SYNTHETIC SECRET SENTINEL") for _ in range(16)))
        with self.assertRaises(d.TransferError) as caught:
            self.fetch(disc, opener)
        self.assertEqual(len(opener.calls), 16)
        self.assertEqual(len(caught.exception.attempts), 16)
        self.assertNotIn("SENTINEL", str(caught.exception))
        self.assertIn(TORRENT, str(caught.exception))
        self.assert_partial(disc, 0)

    def test_redirect_limit_success_and_actual_serving_host(self):
        responses = [Response(302, [("Location", "https://cdn%d.deep32.org/image" % n)]) for n in range(5)]
        responses.append(good(descriptor()))
        opener = Opener(*responses)
        result = self.fetch(descriptor(), opener)
        self.assertEqual(len(opener.calls), 6)
        self.assertEqual(result["host"], "cdn4.deep32.org")
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(response.read_calls == 0 for response in responses[:-1]))

    def test_sixth_redirect_is_closed_without_seventh_request(self):
        opener = Opener(*(Response(302, [("Location", "https://cdn%d.deep32.org/image" % n)]) for n in range(6)))
        with self.assertRaises(d.TransferError):
            self.fetch(descriptor(), opener)
        self.assertEqual(len(opener.calls), 6)

    def test_unsafe_redirects_refused_before_contact(self):
        locations = ["http://cdn.deep32.org/image", "https://user:pass@cdn.deep32.org/image",
            "https://cdn.deep32.org/image?token=SYNTHETIC-SENTINEL", "https://127.0.0.1/image",
            "https://10.0.0.1/image", "https://localhost/image", "file:///image", "ftp://cdn.deep32.org/image",
            "https://cdn.deep32.org/a\\b", "https://cdn.deep32.org/%xx", "https://cdn.deep32.org/%2e%2e/image",
            "https://cdn.deep32.org/%252e/image", "https://cdn.deep32.org/image#fragment", "https://cdn.deep32.org:99999/image"]
        for location in locations:
            with self.subTest(location=location):
                response, opener = Response(302, [("Location", location)]), None
                opener = Opener(response)
                with self.assertRaises(d.TransferError) as caught:
                    self.fetch(descriptor(), opener)
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(response.read_calls, 0)
                self.assertTrue(response.closed)
                self.assertNotIn("SENTINEL", str(caught.exception))

    def test_redirect_loop_and_duplicate_or_missing_location(self):
        for headers in [[], [("Location", descriptor()["mirrors"][0]["url"])], [("Location", "/a"), ("Location", "/b")]]:
            with self.subTest(headers=headers):
                opener = Opener(Response(302, headers))
                with self.assertRaises(d.TransferError):
                    self.fetch(descriptor(), opener)
                self.assertEqual(len(opener.calls), 1)

    def test_relative_redirect_and_signed_http_upgrade(self):
        disc = descriptor()
        disc["mirrors"][0]["url"] = "http://archive.org/invented"
        opener = Opener(Response(307, [("Location", "/other")]),
                        Response(308, [("Location", "https://cdn.deep32.org/image")]), good(disc))
        self.fetch(disc, opener)
        self.assertEqual(opener.calls[1][0], "http://archive.org/other")
        self.assertEqual(opener.calls[2][0], "https://cdn.deep32.org/image")

    def test_storage_plan_counts_only_remaining_and_reserve(self):
        disc = descriptor(d.CHUNK_BYTES + 11)
        plan = d.whole_storage_plan(disc, self.folder, d.CHUNK_BYTES, filesystem=self.fs)
        self.assertEqual(plan["needed"], 11 + 16 * 1024 * 1024)
        self.assertEqual(plan["scope"], "download-only")
        self.assertEqual(plan["payload_bytes"], 11)
        self.assertTrue(plan["ready"])
        self.scenario()

    def test_whole_descriptor_above_legacy_part_cap_is_not_split_or_capped(self):
        disc = descriptor()
        disc["size"] = 2_000_000_123
        disc["sha256"] = "0" * 64
        disc["chunk_sha256"] = ["0" * 64] * ((disc["size"] + d.CHUNK_BYTES - 1) // d.CHUNK_BYTES)
        plan = d.whole_storage_plan(disc, self.folder, filesystem=self.fs)
        self.assertEqual(plan["needed"], disc["size"] + d.RESERVE)
        response = Response(headers=[("Content-Length", str(disc["size"]))],
                            body=Pattern(disc["size"]), fail_at=0)
        opener = Opener(response)
        with self.assertRaises(d.TransferError):
            self.fetch(disc, opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(response.read_calls, 1)
        self.assert_partial(disc, 0)

    def test_full_disk_stops_before_any_request(self):
        self.fs.available = lambda _: 0
        opener = Opener()
        with self.assertRaisesRegex(d.LocalError, "Insufficient local storage"):
            self.fetch(descriptor(), opener)
        self.assertEqual(opener.calls, [])

    def test_restart_rebudgets_and_stops_locally(self):
        disc = descriptor(d.CHUNK_BYTES + 11, mirrors=2)
        self.write_pattern(self.partial(disc), d.CHUNK_BYTES)
        self.fs.available = lambda _: d.RESERVE + 11
        ignored = good(disc)
        opener = Opener(ignored)
        with self.assertRaises(d.LocalError):
            self.fetch(disc, opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(ignored.read_calls, 0)
        self.assert_partial(disc, 0)

    def test_local_write_enospc_stops_without_mirror_retry(self):
        disc = descriptor(mirrors=2)
        original = self.fs.open_payload
        class BrokenWriter:
            def __init__(self, handle):
                self.handle = handle
            def __getattr__(self, key):
                return getattr(self.handle, key)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def write(self, _):
                self.handle.write(b"a" * 4)
                raise OSError(errno.ENOSPC, "SYNTHETIC LOCAL DISK ERROR")
        self.fs.open_payload = lambda path, **kwargs: BrokenWriter(original(path, **kwargs)) if str(path).endswith(".download") else original(path, **kwargs)
        opener = Opener(good(disc))
        with self.assertRaises(d.LocalError):
            self.fetch(disc, opener)
        self.assertEqual(len(opener.calls), 1)
        self.assert_partial(disc, 0)

    def test_unsupported_publication_stops_before_request(self):
        self.fs.capability = mock.Mock(side_effect=d.LocalError("SYNTHETIC file-size limit or hard-link failure"))
        opener = Opener()
        with self.assertRaises(d.LocalError):
            self.fetch(descriptor(), opener)
        self.assertEqual(opener.calls, [])

    def test_atomic_publication_race_never_overwrites(self):
        original = self.fs.publish
        def race(partial, final, handle):
            final.write_bytes(b"unrelated concurrent final")
            original(partial, final, handle)
        self.fs.publish = race
        with self.assertRaisesRegex(d.LocalError, "not overwritten"):
            self.fetch(descriptor(), Opener(good(descriptor())))
        self.assertEqual((self.folder / NAME).read_bytes(), b"unrelated concurrent final")
        self.assertEqual(self.partial(descriptor()).read_bytes(), b"a" * 26)

    def test_kill_after_hardlink_is_recovered_only_for_exact_two_names(self):
        disc = descriptor()
        self.partial(disc).write_bytes(b"a" * 26)
        final = self.folder / NAME
        os.link(self.partial(disc), final)
        before = final.stat().st_ino
        report = self.fetch(disc, Opener())
        self.assertEqual(report["status"], "local")
        self.assertEqual(final.stat().st_ino, before)
        self.assertEqual(final.stat().st_nlink, 1)
        self.assertFalse(self.partial(disc).exists())

    def test_crash_recovery_refuses_extra_link_and_corrupt_bytes(self):
        disc = descriptor()
        final = self.folder / NAME
        self.partial(disc).write_bytes(b"!" * 26)
        os.link(self.partial(disc), final)
        with self.assertRaises(d.LocalError):
            self.fetch(disc, Opener())
        self.assertTrue(self.partial(disc).exists())
        self.assertEqual(final.stat().st_nlink, 2)
        other = self.folder / "unrelated-third-link"
        os.link(final, other)
        with self.assertRaises(d.LocalError):
            self.fetch(disc, Opener())
        self.assertEqual(other.stat().st_nlink, 3)

    def test_payload_identity_change_is_local_failure(self):
        original, checks = self.fs.recheck, [0]
        def changed(path, handle, **kwargs):
            if str(path).endswith(".download"):
                checks[0] += 1
                if checks[0] >= 6:
                    raise d.LocalError("SYNTHETIC identity changed")
            return original(path, handle, **kwargs)
        self.fs.recheck = changed
        opener = Opener(good(descriptor(mirrors=2)))
        with self.assertRaises(d.LocalError):
            self.fetch(descriptor(mirrors=2), opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertFalse((self.folder / NAME).exists())

    def test_replaced_partial_path_never_changes_replacement_bytes(self):
        disc, replaced = descriptor(), [False]
        def replace():
            if not replaced[0]:
                self.partial(disc).unlink()
                self.partial(disc).write_bytes(b"unrelated replacement bytes")
                replaced[0] = True
        opener = Opener(good(disc, callback=replace))
        with self.assertRaises(d.LocalError):
            self.fetch(disc, opener)
        self.assertEqual(self.partial(disc).read_bytes(), b"unrelated replacement bytes")
        self.assertFalse((self.folder / NAME).exists())

    def test_existing_payload_hard_links_refused_without_changes(self):
        for suffix in ["", ".download"]:
            target = self.folder / (NAME + suffix)
            other = self.folder / "unrelated"
            target.write_bytes(b"a" * 26)
            os.link(target, other)
            with self.assertRaises(d.LocalError):
                self.fetch(descriptor(), Opener())
            self.assertEqual(other.read_bytes(), b"a" * 26)
            other.unlink()
            target.unlink()

    def test_special_files_and_reparse_points_refused(self):
        for mode, links, attributes in [(stat.S_IFLNK, 1, 0), (stat.S_IFIFO, 1, 0),
                                        (stat.S_IFDIR, 1, 0), (stat.S_IFREG, 2, 0), (stat.S_IFREG, 1, 0x400)]:
            with self.subTest(mode=mode, links=links, attributes=attributes):
                with self.assertRaises(d.LocalError):
                    d._regular(SimpleNamespace(st_mode=mode, st_nlink=links, st_file_attributes=attributes))
                self.scenario()

    def test_linked_ancestor_refused_before_payload_open(self):
        real = Path.lstat
        def linked(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path == self.folder:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            return info
        with mock.patch.object(Path, "lstat", linked):
            with self.assertRaises(d.LocalError):
                self.fetch(descriptor(), Opener())

    def test_exclusive_os_lock_is_not_a_pid_file(self):
        path = self.folder / ".synthetic-lock"
        with self.fs.lock(path):
            with self.assertRaises(d.LocalError):
                with self.fs.lock(path):
                    self.fail("second lock succeeded")
        with self.fs.lock(path):
            pass
        self.assertEqual(path.stat().st_size, 1)
        self.scenario()

    def test_windows_private_acl_parser(self):
        owner = "S-1-5-21-101-202-303-1001"
        d._private_sddl("O:" + owner + "D:P(A;;FA;;;" + owner + ")(A;;FA;;;SY)(A;;FR;;;BU)", owner)
        self.scenario()
        cases = ["O:BAD" + "D:P(A;;FA;;;" + owner + ")", "O:" + owner + "D:NO_ACCESS_CONTROL",
                 "O:" + owner + "D:P(A;;FA;;;BU)", "O:" + owner + "D:P(A;;0x2;;;WD)",
                 "O:" + owner + "D:P(A;;SD;;;BU)", "O:" + owner + "D:P(OA;;FR;;;BU)"]
        for sddl in cases:
            with self.subTest(sddl=sddl):
                with self.assertRaises(d.LocalError):
                    d._private_sddl(sddl, owner)
                self.scenario()

    def test_native_private_directory_and_filesystem_adapter(self):
        if os.name == "nt":
            settings_spec = importlib.util.spec_from_file_location("synthetic_settings_acl", ROOT / "src/release-tools/deep32_settings.py")
            settings = importlib.util.module_from_spec(settings_spec)
            settings_spec.loader.exec_module(settings)
            settings.windows_security(self.folder)
        fs = d.FileSystem()
        report = self.fetch(descriptor(), Opener(good(descriptor())), fs=fs)
        self.assertEqual(report["status"], "downloaded")

    def test_local_readonly_verification_has_no_writes(self):
        disc = descriptor()
        final = self.folder / NAME
        final.write_bytes(b"a" * 26)
        before = sorted(p.name for p in self.folder.iterdir())
        with mock.patch.object(self.fs, "open_fd", wraps=self.fs.open_fd) as opened:
            self.assertEqual(d.verify_local_image(disc, final, filesystem=self.fs), str(final))
        self.assertTrue(all(not call.args[1] & (os.O_CREAT | os.O_RDWR | os.O_WRONLY) for call in opened.call_args_list))
        self.assertTrue(all(call.kwargs["writable"] is False for call in opened.call_args_list))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)
        self.scenario()

    def test_bad_local_image_and_wrong_basename_refused(self):
        wrong = self.folder / "invented.iso"
        wrong.write_bytes(b"a" * 26)
        with self.assertRaises(d.LocalError):
            d.verify_local_image(descriptor(), wrong, filesystem=self.fs)
        final = self.folder / NAME
        final.write_bytes(b"!" * 26)
        with self.assertRaises(d.LocalError):
            d.verify_local_image(descriptor(), final, filesystem=self.fs)
        self.scenario()

    def test_descriptor_and_context_defences_precede_files_or_http(self):
        cases = []
        for key, value in [("file", "../unsafe.iso"), ("file", "CON.iso"), ("size", True), ("size", 1.0),
                           ("size", d.MAX_SIZE + 1), ("chunk_bytes", 1), ("chunk_sha256", []), ("sha256", "X" * 64),
                           ("mirrors", []), ("mirrors", descriptor(mirrors=16)["mirrors"] + [descriptor()["mirrors"][0]])]:
            item = descriptor()
            item[key] = value
            cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(d.DownloadError):
                    self.fetch(item, Opener())
        item = descriptor()
        item["mirrors"][0]["url"] = "https://user:SYNTHETIC@archive.org/image"
        with self.assertRaises(d.DownloadError):
            self.fetch(item, Opener())
        for context in [{"fingerprint": "wrong"}, {"revision": True}, {"release": "release\n"},
                        {"torrent_url": "https://archive.org/image?secret=SYNTHETIC"}]:
            kwargs = {"release": "v0.1.0-alpha.2", "revision": 2, "fingerprint": FINGERPRINT,
                      "torrent_url": TORRENT, "opener": Opener(), "filesystem": self.fs}
            kwargs.update(context)
            with self.assertRaises(d.DownloadError):
                d.fetch_whole(descriptor(), self.folder, **kwargs)
            self.scenario()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_default_http_opener_has_no_proxy_or_automatic_redirect_handler(self):
        connection = mock.Mock()
        raw = io.BytesIO(b"a" * 26)
        response = SimpleNamespace(status=200, headers=Message(), fp=raw, close=mock.Mock())
        connection.getresponse.return_value = response
        with mock.patch.object(d.ssl, "create_default_context", return_value="normal certificate validation") as tls:
            with mock.patch.object(d.http.client, "HTTPSConnection", return_value=connection) as constructor:
                result = d.open_http("https://archive.org/invented", {"Accept-Encoding": "identity", "Connection": "close"})
                constructor.assert_called_once_with("archive.org", None, timeout=30, context="normal certificate validation")
                tls.assert_called_once_with()
                connection.sock.settimeout.assert_called_once_with(60)
                connection.request.assert_called_once_with("GET", "/invented", headers={"Accept-Encoding": "identity", "Connection": "close"})
                self.assertEqual(result.read(27), b"a" * 26)
                result.close()
                connection.close.assert_called_once_with()
        self.scenario()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmp", required=True)
    args = parser.parse_args()
    SCRATCH = Path(args.tmp)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if list(SCRATCH.iterdir()):
        raise SystemExit("The suite scratch directory must be empty")
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(unittest.defaultTestLoader.loadTestsFromTestCase(DownloadTests))
    print("SCENARIOS=%d TESTS=%d FAILURES=%d ERRORS=%d SKIPPED=%d" %
          (SCENARIOS, result.testsRun, len(result.failures), len(result.errors), len(result.skipped)))
    raise SystemExit(not result.wasSuccessful())
