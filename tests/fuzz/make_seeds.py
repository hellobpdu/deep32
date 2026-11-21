# SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
# SPDX-License-Identifier: Apache-2.0
"""Write the seed corpora in tests/fuzz/corpus/TARGET/.

Every seed is built here: no file from a disc, from another project or from the internet is used. Each one is a
small, valid input made from the format's published layout, except the seed-finding-* inputs, which reproduce the
findings in README.md section 5. The fuzzers mutate these, so each seed aims to reach one branch of
its parser (a compression method, a header size, a message kind). Run it from any directory:

    python3 -I -B tests/fuzz/make_seeds.py

It replaces the files named seed-* and leaves any other file in the corpus folders alone.
"""

import json
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"


# ---- DIB (Windows bitmaps and icons) -------------------------------------------------------------------------

def info_header(w, h, bpp, comp=0, colors=0, size=40):
    head = struct.pack("<IiiHHIIiiII", size, w, h, 1, bpp, comp, 0, 2835, 2835, colors, 0)
    return head + bytes(size - 40)


def rows(w, h, bpp, value):
    stride = (w * bpp + 31) // 32 * 4
    return b"".join(bytes((value(x, y) & 0xFF for x in range(stride))) for y in range(h))


def with_file_header(dib, pixel_offset):
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + pixel_offset) + dib


def palette(n):
    return b"".join(bytes((i * 7 % 256, i * 13 % 256, i * 29 % 256, 0)) for i in range(n))


def dib_seeds():
    seeds = {}
    # 1 bpp, 8 x 2, two colors, with the file header
    dib = info_header(8, 2, 1) + palette(2)
    seeds["1bpp"] = with_file_header(dib + rows(8, 2, 1, lambda x, y: 0xA5 if x == 0 else 0), len(dib))
    # 4 bpp, odd width, 16 colors, no file header (as a resource stores it)
    seeds["4bpp-odd"] = info_header(5, 3, 4) + palette(16) + rows(5, 3, 4, lambda x, y: (x * 16 + y) & 0xFF)
    # 8 bpp, full 256-entry table (the system-palette path), top-down
    dib = info_header(4, -4, 8) + palette(256)
    seeds["8bpp-topdown"] = with_file_header(dib + rows(4, 4, 8, lambda x, y: x * 60 + y), len(dib))
    # 8 bpp with a short palette: indices past it count as undefined pixels
    seeds["8bpp-short-palette"] = info_header(4, 2, 8, colors=3) + palette(3) + rows(4, 2, 8, lambda x, y: x)
    # 16 bpp default 5-5-5, and 5-6-5 through BI_BITFIELDS
    seeds["16bpp"] = info_header(3, 2, 16) + rows(3, 2, 16, lambda x, y: (x * 37 + y) & 0xFF)
    masks = struct.pack("<III", 0xF800, 0x07E0, 0x001F)
    seeds["16bpp-bitfields"] = info_header(3, 2, 16, comp=3) + masks + rows(3, 2, 16, lambda x, y: x * 11 + y)
    # 24 bpp bottom-up, with the file header
    dib = info_header(3, 3, 24)
    seeds["24bpp"] = with_file_header(dib + rows(3, 3, 24, lambda x, y: x * 20 + y * 3), len(dib))
    # 32 bpp, BITMAPV4HEADER (108 bytes) with its masks inside the header
    v4 = bytearray(info_header(2, 2, 32, comp=3, size=108))
    v4[40:52] = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    seeds["32bpp-v4"] = bytes(v4) + rows(2, 2, 32, lambda x, y: x * 50 + y)
    # BITMAPCOREHEADER (12 bytes), 8 bpp with a 3-byte palette, with the file header
    core = struct.pack("<IHHHH", 12, 4, 2, 1, 8) + b"".join(bytes((i, 255 - i, i // 2)) for i in range(256))
    seeds["core-8bpp"] = with_file_header(core + rows(4, 2, 8, lambda x, y: x + 4 * y), len(core))
    # RLE8: an encoded run, an absolute run, a delta, end of line, end of bitmap
    rle8 = bytes((3, 1, 0, 3, 2, 3, 4, 0, 0, 0)) + bytes((0, 2, 1, 1)) + bytes((2, 5, 0, 1))
    seeds["rle8"] = info_header(8, 3, 8, comp=1, colors=8) + palette(8) + rle8
    # RLE4, odd width: a run that encodes the unused last nibble, an absolute run, end of bitmap
    rle4 = bytes((6, 0x12, 0, 0)) + bytes((0, 3, 0x34, 0x50, 0, 1))
    seeds["rle4-odd"] = info_header(5, 2, 4, comp=2, colors=16) + palette(16) + rle4
    # an icon image: 4 bpp 4 x 4, height doubled for the XOR and AND masks
    xor = rows(4, 4, 4, lambda x, y: 0x12)
    andmask = rows(4, 4, 1, lambda x, y: 0x90 if x == 0 else 0)
    seeds["icon-4bpp"] = info_header(4, 8, 4) + palette(16) + xor + andmask
    return seeds


# ---- KWAJ ----------------------------------------------------------------------------------------------------

KWAJ_MAGIC = b"KWAJ\x88\xf0\x27\xd1"


def kwaj(method, payload, flags=0, extra=b""):
    return KWAJ_MAGIC + struct.pack("<HHH", method, 14 + len(extra), flags) + extra + payload


class BitWriter:
    """MSB-first bit packing, as the KWAJ method 3 reader takes it."""

    def __init__(self):
        self.bits = []

    def put(self, value, n):
        self.bits.extend((value >> (n - 1 - i)) & 1 for i in range(n))

    def bytes(self):
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def lzh_stream(text):
    """Method 3 with the five tables of kind 0 (fixed equal-length codes): a literal run, then a match."""
    w = BitWriter()
    for _ in range(5):
        w.put(0, 4)
    w.put(0, 4)
    w.put(0, 4)                  # table 0, symbol 0: a run of literals follows
    w.put(len(text) - 1, 5)      # table 2: run length - 1
    for c in text:
        w.put(c, 8)              # table 4: the literals
    w.put(3, 4)                  # table 1 (after a short run): a match of 3 + 2 bytes
    w.put(0, 6)                  # table 3: distance high bits
    w.put(len(text), 6)          # distance low bits: back to the start
    return w.bytes()


def kwaj_seeds():
    text = b"Deep32 KWAJ seed text, Deep32 KWAJ seed text.\n"
    stored_extra = struct.pack("<I", len(text)) + b"SEED\0" + b"TXT\0"
    seeds = {
        "stored": kwaj(0, text, flags=1 | 8 | 16, extra=stored_extra),
        "xor": kwaj(1, bytes(c ^ 0xFF for c in text)),
        "header-fields": kwaj(0, text, flags=2 | 4 | 32,
                              extra=b"\x01\x02" + struct.pack("<H", 3) + b"abc" + struct.pack("<H", 4) + b"note"),
    }
    # method 2 (SZDD LZSS): three literals, then a 9-byte copy from the window position they were written at
    seeds["lzss"] = kwaj(2, bytes((0x07,)) + b"ABC" + bytes((0xEE, 0xF6)))
    hello = b"HELLO KWAJ"
    seeds["lzh"] = kwaj(3, lzh_stream(hello), flags=1, extra=struct.pack("<I", len(hello) + 5))
    for name, level in (("mszip-stored", 0), ("mszip-deflate", 9)):
        c = zlib.compressobj(level, zlib.DEFLATED, -15)
        raw = c.compress(text * 3) + c.flush(zlib.Z_FINISH)
        seeds[name] = kwaj(4, struct.pack("<H", len(raw) + 2) + b"CK" + raw)
    return seeds


# ---- RIFF WAVE -----------------------------------------------------------------------------------------------

def riff(chunks):
    body = b"WAVE" + b"".join(cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")
                              for cid, data in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_seeds():
    seeds = {}
    pcm16 = b"".join(struct.pack("<hh", i * 1000, -i * 1000) for i in range(8))
    seeds["pcm16-stereo"] = riff([(b"fmt ", struct.pack("<HHIIHH", 1, 2, 22050, 88200, 4, 16)), (b"data", pcm16)])
    seeds["pcm8-mono"] = riff([(b"fmt ", struct.pack("<HHIIHH", 1, 1, 11025, 11025, 1, 8)),
                               (b"data", bytes(range(100, 140, 5)))])
    # WAVE ADPCM (format 2): 15-byte blocks of 18 samples, the seven standard coefficient pairs, a fact chunk
    coefs = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232)]
    ext = struct.pack("<HH", 18, len(coefs)) + b"".join(struct.pack("<hh", a, b) for a, b in coefs)
    fmt = struct.pack("<HHIIHHH", 2, 1, 8000, 4000, 15, 4, len(ext)) + ext
    block = struct.pack("<Bhhh", 1, 16, 100, 50) + bytes((0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0))
    seeds["msadpcm"] = riff([(b"fmt ", fmt), (b"fact", struct.pack("<I", 30)), (b"data", block * 2)])
    # IMA ADPCM (format 0x11): 12-byte mono blocks of 17 samples
    fmt = struct.pack("<HHIIHHHH", 0x11, 1, 8000, 4000, 12, 4, 2, 17)
    block = struct.pack("<hBB", -200, 10, 0) + bytes((0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF))
    seeds["ima-mono"] = riff([(b"fmt ", fmt), (b"data", block * 2)])
    fmt = struct.pack("<HHIIHHHH", 0x11, 2, 8000, 8000, 24, 4, 2, 17)
    block = struct.pack("<hBBhBB", 300, 20, 0, -300, 30, 0) + bytes(range(16))
    seeds["ima-stereo"] = riff([(b"fmt ", fmt), (b"data", block)])
    # finding F-2: 8-bit PCM below the midpoint (a left shift of a negative value in wav_decode)
    seeds["finding-F2"] = riff([(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)), (b"data", b"\x00\x7f")])
    # finding F-3: WAVE ADPCM with one sample per block; three 7-byte blocks overrun the sample buffer
    fmt = struct.pack("<HHIIHHHH", 2, 1, 8000, 4000, 7, 4, 2, 1)
    seeds["finding-F3"] = riff([(b"fmt ", fmt), (b"data", struct.pack("<Bhhh", 0, 16, 0x1234, 0x5678) * 3)])
    # finding F-5: WAVE ADPCM codes that keep tripling the step size until the long multiplication overflows
    fmt = struct.pack("<HHIIHH", 2, 1, 8000, 4000, 47, 4)
    seeds["finding-F5"] = riff([(b"fmt ", fmt), (b"data", struct.pack("<Bhhh", 0, 16, 0, 0) + b"\x88" * 40)])
    return seeds


# ---- Netpbm --------------------------------------------------------------------------------------------------

def pnm_seeds():
    return {
        "p6": b"P6\n2 2\n255\n" + bytes(range(12)),
        "p6-comment": b"P6 # a comment\n3 1\n# another\n255\n" + bytes(range(40, 49)),
        "p7-rgba": (b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
                    + bytes((1, 2, 3, 128, 4, 5, 6, 0))),
        "p7-rgb": b"P7\n# rgb\nWIDTH 1\nHEIGHT 2\nDEPTH 3\nMAXVAL 255\nTUPLTYPE RGB\nENDHDR\n" + bytes(range(6)),
    }


# ---- the scaler ----------------------------------------------------------------------------------------------

def imgscale_seeds():
    def params(sw, sh, bw, bh, mode, filt, zoom, x, y, pad, bg):
        return bytes((sw - 1, sh - 1, bw - 1, bh - 1, mode, filt, zoom & 255, zoom >> 8, x, y, pad)) + bytes(bg) + b"\0"
    pixels = bytes((i * 37) & 255 for i in range(4 * 64))
    return {
        "shrink-fit": params(16, 12, 5, 5, 0, 0, 50, 0, 0, 0, (255, 255, 255, 255)) + pixels,
        "grow-bicubic": params(3, 4, 40, 30, 1, 2, 400, 2, 1, 3, (0, 0, 0, 255)) + pixels,
        "fitdown-alpha": params(8, 2, 9, 9, 2, 1, 1000, 5, 9, 1, (10, 20, 30, 0)) + pixels,
    }


# ---- text and JSON -------------------------------------------------------------------------------------------

def b64_seeds():
    return {"empty": b"", "one": b"Zg==", "two": b"Zm8=", "three": b"Zm9v", "bytes": b"AAECAwQFBgcICQ==",
            "alphabet": b"+/+/ABCDabcd0123"}


JSON_DOCS = {
    "object": '{"a":1,"b":[true,false,null],"c":"x\\u00e9\\ud83d\\ude00","d":{"e":-0.5e+10}}',
    "escapes": '{"k":"line\\nnext\\t\\"q\\"\\\\ \\/ \\b\\f\\r","k2":"\u00e9\u20ac\U0001F600"}',
    "integers": '{"big":18446744073709551615,"neg":-9223372036854775808,"zero":-0,"frac":1.25E-3}',
    "nested": "[" * 20 + "1" + "]" * 20,
    "array": ' [ 1 , 2.5 , "three" , [ ] , { } ] ',
    "scalar-string": '"just a string"',
    "scalar-number": "0",
    "empty-object": "{}",
}


def json_seeds():
    return {name: doc.encode("utf-8") for name, doc in JSON_DOCS.items()}


def appjson_seeds():
    seeds = json_seeds()
    # finding F-1: text that ends inside a literal; the reader compares "null" past the end of an unterminated buffer
    seeds["finding-F1"] = b"[nul"
    return seeds


def e26p_frame(body, req_id, kind):
    return b"E26P" + struct.pack(">HHIQI", 1, 0, len(body), req_id, kind) + body


def e26p_seeds():
    req = b'{"op":"status","args":{}}'
    resp = b'{"ok":true,"result":{"generation":"g1"}}'
    return {
        "request": e26p_frame(req, 1, 1),
        "response": e26p_frame(resp, 2, 2),
        "two-frames": e26p_frame(req, 3, 1) + e26p_frame(b"{}", 3, 4),
    }


# ---- word wheels ---------------------------------------------------------------------------------------------

def wheel(entries, flags=0, pad=0):
    """A D32WHEEL v1 file: header, entries, postings, jump table, string pool (the reader's checks, in order)."""
    pool = bytearray(b"\0")

    def s(text):
        off = len(pool)
        pool.extend(text.encode("utf-8") + b"\0")
        return off

    head_strings = [s("Seed wheel"), s("seed-catalogue"), s("0" * 64), s("2026-11-19T00:00:00Z")]
    entries = sorted(entries, key=lambda e: e[0].encode("utf-8"))
    ent = bytearray()
    postings = bytearray()
    first = 0
    for key, display, label, posts, kind in entries:
        ent += struct.pack("<IIIIIBBH", s(key), s(display), s(label), first, len(posts), kind, 1, 0)
        for p in posts:
            postings += struct.pack("<I", p)
        first += len(posts)
    n = len(entries)
    keys = [e[0].encode("utf-8") for e in entries]
    jump = bytearray()
    for b in range(256):
        jump += struct.pack("<I", next((i for i, k in enumerate(keys) if k[0] >= b), n))
    jump += struct.pack("<I", n)
    entries_off = 96
    postings_off = entries_off + len(ent)
    jump_off = postings_off + len(postings)
    strings_off = jump_off + len(jump)
    body = bytes(ent + postings + jump + pool)
    size = 96 + len(body)
    header = b"D32WHEEL" + struct.pack(
        "<14I4I", 1, 96, size, zlib.crc32(body), flags, pad, n, entries_off, 24, len(postings) // 4,
        postings_off, strings_off, len(pool), jump_off, *head_strings)
    header += bytes(96 - len(header))
    return header + body


def d32ww_seeds():
    entries = [
        ("bgp", "BGP", "Border Gateway Protocol", [7, 9], 33),
        ("ospf", "OSPF", "Open Shortest Path First", [4], 33),
        ("ospf version 2", "OSPF Version 2", "RFC 2328", [4], 1),
        ("rfc 2328", "RFC 2328", "OSPF Version 2", [4], 1),
        ("rfc 4271", "RFC 4271", "A Border Gateway Protocol 4 (BGP-4)", [7], 1),
        ("\u00e9t\u00e9", "\u00c9t\u00e9", "a UTF-8 key", [11], 33),
    ]
    padded = [("rfc 02328", "RFC 2328", "OSPF Version 2", [4], 1), ("rfc 04271", "RFC 4271", "BGP-4", [7], 1)]

    def prefix(flags, pad, query):
        q = query.encode("utf-8")
        return bytes((flags, pad, len(q))) + q

    return {
        "plain": prefix(0, 0, "OSPF") + wheel(entries),
        "padded": prefix(3, 5, "RFC2328") + wheel(padded, flags=3, pad=5),
        "empty-query": prefix(0x80, 0, "") + wheel(entries),
        "separators": prefix(0x81, 0, "the  ospf-") + wheel(entries, flags=1),
    }


def text8_seeds():
    samples = {
        "ascii": "Plain ASCII text, with digits 0123 and_underscores.",
        "latin1": "Caf\u00e9 cr\u00e8me br\u00fbl\u00e9e \u00a9 \u00bd",
        "cp1252": "\u20ac \u2014 \u2018quotes\u2019 \u201cdouble\u201d \u2026 \u2122 \u0152",
        "greek-math": "\u03b1\u03b2\u03b3 \u2264 \u2265 \u2192 \u221e \u2211",
        "other": "e\u0301 \u200b \U0001F600 \u4e2d\u6587",
    }
    seeds = {}
    for i, (name, text) in enumerate(samples.items()):
        seeds[name] = bytes((i & 3, 7 + i)) + text.encode("utf-8")
    seeds["invalid-utf8"] = bytes((3, 5)) + b"ok \xc0\xaf \xed\xa0\x80 \xf4\x90\x80\x80 \xff tail"
    return seeds


def rfctext_seeds():
    # A made-up document in the plain-text page layout: no text from a real RFC.
    page = (
        "RFC 9999                    Example Seed Protocol               November 2026\n"
        "\n"
        "1.  Introduction\n"
        "\n"
        "   This made-up document gives the classifier a heading, a paragraph\n"
        "   that continues on the next page, a definition and a figure. It\n"
        "\n"
        "Seed                         Informational                     [Page 1]\n"
        "\f\n"
        "RFC 9999                    Example Seed Protocol               November 2026\n"
        "\n"
        "   ends here after the page break.\n"
        "\n"
        "2.1.  Terms Used Here\n"
        "\n"
        "   Seed term\n"
        "      The text that explains the term, indented below it.\n"
        "\n"
        "      +--------+     +--------+\n"
        "      | node A |---->| node B |\n"
        "      +--------+     +--------+\n"
        "\n"
    )
    # finding F-4: a form feed at the start of a text line (a page break before a line that is not blank)
    hang = "1.  Introduction\n\n\fThe page starts here without a blank line.\n"
    return {"pages": page.encode("ascii"), "crlf": page.replace("\n", "\r\n").encode("ascii"),
            "finding-F4": hang.encode("ascii")}


# ---- the vector kernel ---------------------------------------------------------------------------------------

def d32vec_seeds():
    def floats(values):
        return b"".join(struct.pack("<f", v) for v in values)
    inf = float("inf")
    special = [0.0, -0.0, 1.0, -1.0, inf, -inf, 1e-45, 3.4e38, -2.5, 0.125, 1e-38, 7.0]
    return {
        "rows2-len17": bytes((1, 0x21)) + floats([(i % 7 - 3) * 0.25 for i in range(17 * 3)]),
        "rows1-len33": bytes((0, 0x10)) + floats([1.0 / (i + 1) for i in range(66)]),
        "special": bytes((3, 0x32)) + floats(special * 8),
        "nan": bytes((0, 0)) + floats([float("nan"), 1.0] * 16),
    }


# ---- the Ask link --------------------------------------------------------------------------------------------

def d32u_frame(ftype, channel, stream, flags, payload):
    return struct.pack(">IIBBH", len(payload), stream, ftype, channel, flags) + payload


def asklink_seeds():
    def msg(obj):
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")
    hello = msg({"proto": "D32U/1", "id": "relay-1", "version": 1, "versions": [1], "role": "relay",
                 "features": ["research-v1"], "nonce": "0123456789abcdef" * 4, "sessions": [],
                 "codex_version": "0.160.0", "signed_in": True,
                 "profiles": [{"name": "research", "models": ["model-a"], "efforts": ["low", "high"]}]})
    seeds = {
        "frame-hello": b"\x00" + d32u_frame(1, 0, 0, 0, hello),
        "frame-data": b"\x00" + d32u_frame(5, 1, 1, 1, msg({"op": "agent.delta", "session": "s1", "text": "hi"})),
        "frame-ping": b"\x00" + d32u_frame(9, 0, 0, 0, bytes(range(8))),
        "frame-window": b"\x00" + d32u_frame(6, 2, 3, 0, struct.pack(">I", 65536)),
        "hello": b"\x01" + hello,
        "hello-ack": b"\x02" + msg({"version": 1, "features": ["research-v1"], "paired": True}),
        "open-session": b"\x03" + msg({"session": "s-1_x"}),
        "open-empty": b"\x03{}",
        "event-delta": b"\x04" + msg({"op": "agent.final", "session": "s1", "text": "done \u00e9"}),
        "event-done": b"\x04" + msg({"op": "turn.completed", "session": "s1", "status": "completed",
                                     "usage": {"input_tokens": 12, "output_tokens": 34}}),
        "event-error": b"\x04" + msg({"op": "error", "session": "s1", "code": 3}),
        "event-status": b"\x04" + msg({"op": "status", "session": "s1", "code": "CONNECTED"}),
        "status": b"\x04" + msg({"code": "IDLE"}),
        "mcp": b"\x04" + msg({"body": {"jsonrpc": "2.0", "id": 1}, "mcp_session_id": "abc",
                              "protocol_version": "2025-06-18"}),
        "text": b"\x05hello\x07 \xc2\x85world\n\ttab \xe2\x82\xac",
        "hex": b"\x050123456789abcdef",
        "typed": b"\x06ABANDON ability   able\tabout ABOVE absent absorb abstract absurd abuse",
        "pins": b"\x07" + bytes(range(1, 33)) + bytes(range(33, 65)) + bytes(range(64)),
    }
    return seeds


# ---- the network calculator ----------------------------------------------------------------------------------

def netaddr_seeds():
    items = [
        (0, 0, "192.0.2.1"), (0, 1, "2001:db8::1"), (0, 0, "fe80::1%eth0"), (0, 0, "::ffff:192.0.2.1"),
        (0, 0, "224.0.0.251"), (0, 1, "ff02::1:ff00:1"),
        (1, 3, "192.0.2.0/24"), (1, 1, "10.0.0.0 255.0.0.0"), (1, 2, "2001:db8::/48"), (1, 0, "198.51.100.7/32"),
        (2, 0, "192.0.2.10-192.0.2.200"), (2, 0, "2001:db8::1 - 2001:db8::ff"), (2, 0, "10.0.0.0..10.0.3.255"),
        (3, 7, "0.0.0.255"), (3, 0, "255.255.252.0"), (3, 9, "0.255.0.255"),
        (4, 0, "seq 5 permit 10.0.0.0/8 ge 16 le 24\nseq 10 deny 0.0.0.0/0 le 32\n"),
        (4, 3, "seq 1 permit 2001:db8::/32 le 64\r\nseq 2 deny ::/0 ge 1\r\n"),
        (5, 0, "10.0.0.0/16\nc 24\nh 100 1\nr 10.0.5.0-10.0.5.255\np 10.0.9.0/24\nc 30\n"),
        (5, 0, "2001:db8::/56\nc 64\nh 1000 0\np 2001:db8:0:10::/60\n"),
    ]
    seeds = {f"{mode}-{i:02d}": bytes((mode, opt)) + text.encode("ascii") for i, (mode, opt, text) in enumerate(items)}
    seeds["6-mac"] = bytes((6, 0)) + bytes((0x00, 0x1B, 0x2C, 0x3D, 0x4E, 0x5F)) + b"239.1.2.0/24"
    return seeds


GENERATORS = {
    "appjson": appjson_seeds,
    "asklink": asklink_seeds,
    "b64": b64_seeds,
    "d32json": json_seeds,
    "d32vec": d32vec_seeds,
    "d32ww": d32ww_seeds,
    "dib": dib_seeds,
    "e26j": json_seeds,
    "e26p": e26p_seeds,
    "imgscale": imgscale_seeds,
    "json8": json_seeds,
    "kwaj": kwaj_seeds,
    "netaddr": netaddr_seeds,
    "pnm": pnm_seeds,
    "rfctext": rfctext_seeds,
    "text8": text8_seeds,
    "wav": wav_seeds,
}


def main():
    total = 0
    for target, make in GENERATORS.items():
        folder = CORPUS / target
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("seed-*"):
            old.unlink()
        for name, data in make().items():
            (folder / f"seed-{name}").write_bytes(data)
            total += 1
    print(f"make_seeds.py: {total} seeds in {len(GENERATORS)} folders under {CORPUS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
