# Fuzzing the C code

These are the fuzz targets for the C parsers in the Workstation's source: libFuzzer harnesses built with
AddressSanitizer and UndefinedBehaviorSanitizer on Linux, compiled straight from the files under `src/workstation`.
This page is for anyone who wants to run them, add a target or check what they found.

<sub>Applies to 0.1.0-alpha.2 · <a href="../README.md">Tests</a></sub>

## 1. What is fuzzed

The Workstation runs on OpenBSD, which has neither AddressSanitizer nor libFuzzer, so the targets build on Linux. Each
one compiles its harness in `targets/` together with the sources it tests, in place: nothing under `src/workstation`
is copied or changed. Code that needs Motif, X11, sndio or OpenBSD's `pledge` and `unveil` stays out; the few OpenBSD
library functions the Ask link's parsers call have portable versions in `shims/`.

| Target | Code under test | What the harness checks besides memory safety |
|---|---|---|
| `appjson` | `library/src/app/json.c` | Every member of an accepted tree can be looked up |
| `asklink` | `node/asklink/src/frame.c`, `json.c`, `msg.c`, `util.c`, `sas.c` | Frames re-encode to the same bytes; every message schema; JSON and quoted text survive a write and a re-read; a pairing code verifies when typed in either case |
| `b64` | `library/src/common/b64.c` | Accepted text is canonical (it encodes back unchanged); any bytes survive encode and decode |
| `d32json` | `ship/u10/src/lib/core/d32_json.c` | Lookups find every member; views stay inside the input; the canonical writer's output parses and writes back to the same bytes |
| `d32vec` | `node/d32vec.c` | The SSE and plain C dot products give the same bits for every input (NaN for NaN) |
| `d32ww` | `library/src/common/d32ww.c` | Every entry of an accepted word wheel reads inside the file; every key in a search range starts with the folded query, and the key before the range sorts below it |
| `dib` | `library/src/import/dib.c` | Decoded size matches the header; bitmap, icon and system-palette decodes |
| `e26j` | `library/src/common/e26j.c` | The parser and the validator agree; accepted trees survive the writer; the 64-bit integer readers agree; base64 is canonical |
| `e26p` | `library/src/common/e26p.c` | Every frame the reader accepts writes back byte for byte |
| `imgscale` | `library/src/common/imgscale.c` | Fit and crop sizes stay inside the box and the picture; every resampling filter and view |
| `json8` | `library/src/common/json8.c` | Nesting stays within 64 levels; lookups find every member |
| `kwaj` | `library/src/import/kwaj.c` | Output stays within the 256 MiB bound; nothing is returned on an error |
| `netaddr` | `ship/u10/src/netaddr/*.c` | Addresses format and parse back to themselves; split, cover and planner results stay inside their parent |
| `pnm` | `library/src/common/image.c` | Pixels and the SHA-256 identity survive a write and a re-read |
| `rfctext` | `library/src/common/rfctext.c` | Every block has text, a known kind and a terminated heading number |
| `text8` | `library/src/common/text8.c` | UTF-8 decodes and re-encodes to the same bytes; mapped text stays inside its buffer |
| `wav` | `library/src/import/wav.c` | Decoded audio written as 16-bit PCM decodes again to the same samples |

All paths in the table are under `src/workstation/`.

Assertions are always on. `FUZZ_ASSERT` (in `fuzz.h`) does not depend on `NDEBUG`, `build.sh` never defines `NDEBUG`,
and UndefinedBehaviorSanitizer is built with `-fno-sanitize-recover=all`, so undefined behavior stops the program the
way a memory error does.

## 2. Running the targets

**Procedure 1. Build and fuzz on Linux**

1. Install clang with its libFuzzer and sanitizer runtimes (I use clang 18). On Debian or Ubuntu:

   ```sh
   sudo apt-get install clang libclang-rt-dev
   ```

2. From the repository's root, build every target, replay the seeds once, then fuzz each target for two minutes:

   ```sh
   sh tests/fuzz/build.sh
   sh tests/fuzz/run.sh replay
   sh tests/fuzz/run.sh fuzz 120
   ```

   Name targets after the command to build or run only those, for example `sh tests/fuzz/run.sh fuzz 600 dib wav`.
   Everything goes to `tests/fuzz/out/` (or `$OUT`): the programs, a working corpus per target, logs and crash inputs.

3. **Verify.** The last line of each `run.sh` command reports success:

   ```text
   run.sh: all passed
   ```

**Procedure 2. Reproduce a crash**

1. Build the plain driver for the target, which runs inputs once without libFuzzer and works with gcc as well:

   ```sh
   sh tests/fuzz/build.sh replay wav
   ```

2. Run it on the input. In a CI log, `run.sh` prints a crashing input in base64; paste that into `base64 -d` first:

   ```sh
   base64 -d > crash.bin
   tests/fuzz/out/replay-wav crash.bin
   ```

3. **Verify.** The sanitizer's report names the file and line. AddressSanitizer prints them only when it finds
   `llvm-symbolizer` on PATH; otherwise set `ASAN_SYMBOLIZER_PATH` to a versioned `llvm-symbolizer` or to binutils'
   `addr2line` (`run.sh` does this itself). Without a report, the driver ends with:

   ```text
   replay: 1 inputs ran
   ```

`FUZZ_STRICT=1 sh tests/fuzz/build.sh` builds the targets without the exceptions section 5 describes; with that
build, the `seed-finding-*` inputs reproduce the findings.

## 3. Seeds and corpora

`make_seeds.py` writes the seed corpora in `corpus/TARGET/`. Each seed is a small valid input built from the format's
published layout: a bitmap of each header size and compression, each KWAJ method, each WAVE encoding, a word wheel, a
D32U/1 message of each kind. The exceptions are the seeds named `seed-finding-*`, short inputs I wrote
that reproduce the findings in section 5. No file from a disc, another project or the internet is used.
`tests/fuzz/.gitattributes` keeps git from changing the seeds' bytes.

```sh
python3 -I -B tests/fuzz/make_seeds.py
```

The fuzzers only read `corpus/`; what they find goes to `out/corpus/TARGET/`. The dictionaries in `dict/` give
libFuzzer the JSON tokens, the D32U/1 message keys and the address syntax.

## 4. Continuous integration

[`.github/workflows/fuzz.yml`](../../.github/workflows/fuzz.yml) runs every target in its own job on `ubuntu-latest`:
build, replay the seeds, then fuzz for two minutes on every push and pull request to `main`, for thirty minutes
every Sunday, and for a chosen time when started by hand (each job stops after 75 minutes). A crash fails the job,
and the log holds the sanitizer's error and summary lines and the input in base64; Procedure 2 replays that input
for the full report. The workflow's token can only read the repository.

## 5. Findings

On 19 November 2026 I ran every target for 15 minutes with both sanitizers (section 6). The fuzzers found five defects.
All five are in source the signed images are built from, so each fix ships with the next release that rebuilds them,
planned for 0.1.0-alpha.3. I rated them on the scale [SECURITY.md](../../.github/SECURITY.md) uses.

| ID | Defect | Where | Severity |
|---|---|---|---|
| F-1 | Reads up to four bytes past the text when it ends inside `true`, `false` or `null` and is not NUL-terminated | `library/src/app/json.c`, lines 140 and 145 | Low |
| F-2 | Left shift of a negative value while decoding 8-bit PCM (undefined behavior in C99) | `library/src/import/wav.c`, line 58 | Low |
| F-3 | Writes past the sample buffer for WAVE ADPCM with one sample per block | `library/src/import/wav.c`, line 95 | Low |
| F-4 | Never returns when a page break comes directly before a line that is not blank | `library/src/common/rfctext.c`, the page-break loop at line 188 | Low |
| F-5 | Signed integer overflow in the WAVE ADPCM step size (undefined behavior in C99) | `library/src/import/wav.c`, line 107 | Low |

- **F-1.** The reader compares the literals with `strncmp`. The render client and the helpers pass NUL-terminated
  bodies, so they are safe. The taxonomy loader (`library/src/app/taxonomy.c`) allocates one byte more than the file
  but leaves that byte unset, so for a taxonomy file that ends inside a literal the reader compares that unset byte
  and, when it happens to continue the literal, up to three bytes past the end of the heap buffer. The taxonomy file
  is a local file the Library reads at start. Nothing is written.
- **F-2.** `(data[k] - 128) << 8` is negative for samples below 128. clang and gcc produce the intended value on the
  Workstation's hardware, but the C standard leaves the result undefined. The WAVE reader is not called by any
  program in this release (see F-3).
- **F-3.** When the fmt chunk says one sample per block, the buffer holds one frame per block, but each WAVE ADPCM
  block writes its two header samples before the decoder checks the count. A file of three blocks is enough to write
  past the end, by up to three 16-bit samples, with values taken from the file. No program in this release calls the
  WAVE reader: `wav_decode` is built into the Library's archive, and none of the four Library programs refers to it,
  so the linker leaves it out. It is rated low for that reason; in a program that imports WAVE files it would rate
  higher.
- **F-4.** The first pass marks a line that follows a form feed or a removed page footer or header. If that line is
  not blank, the page-break loop sets its index back to the same line and runs forever. A form feed at the start of a
  text line is enough. The Library's article reader (`library/src/app/article.c`) passes article files in this format
  to the classifier, so such a file stops the Library's window from responding.
- **F-5.** The WAVE ADPCM step size has no upper bound: codes that raise it keep multiplying it, and after a few
  dozen samples the `long` multiplication overflows. In practice the value wraps and the decoded samples are clamped,
  so the result is noise. Like F-3, it is in the WAVE reader, which no program in this release calls.

The default build makes narrow exceptions so that fuzzing goes on past these defects: it leaves the shift and
signed-overflow checks out of `wav.c` alone (F-2, F-5), with AddressSanitizer still on, and the `appjson`, `wav` and
`rfctext` harnesses step around the inputs of F-1, F-3 and F-4 exactly (each harness says how). `FUZZ_STRICT=1`
removes every exception, and the `seed-finding-*` inputs then reproduce all five reports.

## 6. Results

Measured on 19 November 2026: Ubuntu 24.04 on x86-64 with 12 cores, clang 18.1.3, up to nine targets at a time at the
lowest scheduling priority, beside other work. Each target first replayed its seeds and ran for up to one minute; that
pass found F-1, F-3 and F-4 and stopped those targets early, and the 8-bit PCM seed showed F-2. With the exceptions of
section 5 in place, every target then ran for 15 minutes from the corpus it had built. The `wav` target stopped on
F-5 in its first second and ran its 15 minutes once F-5 had its exception. Executions and coverage are libFuzzer's
figures at the end of each 15-minute run; coverage counts the instrumented code edges reached in the harness and the
code under test.

| Target | Executions in 15 minutes | Coverage (edges) | Found |
|---|---:|---:|---|
| `appjson` | 7,191,772 | 145 | F-1 in the first pass |
| `asklink` | 15,318,207 | 671 | Nothing |
| `b64` | 62,902,280 | 91 | Nothing |
| `d32json` | 35,242,007 | 351 | Nothing |
| `d32vec` | 22,465,767 | 51 | Nothing |
| `d32ww` | 35,307,102 | 240 | Nothing |
| `dib` | 70,477 | 218 | Nothing |
| `e26j` | 12,436,906 | 468 | Nothing |
| `e26p` | 2,966,189 | 51 | Nothing |
| `imgscale` | 207,855 | 169 | Nothing |
| `json8` | 7,217,007 | 222 | Nothing |
| `kwaj` | 29,876,887 | 232 | Nothing |
| `netaddr` | 19,206,823 | 806 | Nothing |
| `pnm` | 11,199,170 | 99 | Nothing |
| `rfctext` | 2,327,651 | 225 | F-4 in the first pass |
| `text8` | 997,903 | 172 | Nothing |
| `wav` | 13,948,610 | 158 | F-2 on the seeds, F-3 in the first pass, F-5 at the start of the second |
| **All** | **279 million** | | Five defects |

The rates differ almost a thousandfold: `dib` spends most of its time on large bitmaps (the decoder accepts up to 40
million pixels) and `imgscale` on large enlargements, while `b64` and `d32ww` check small inputs. The slowest `dib`
input, 90 bytes of RLE4 that describe a 7,941 by 4,098 bitmap, took between 4 and 11 seconds in the fuzzing build,
depending on the load. That is within the decoder's limit, and I don't count it as a defect.

## 7. Not covered yet

- The Library's interface, renderer and search helpers beyond the parsers above: they need Motif, X11, MuPDF, SQLite
  or libzim, and need test builds of their own.
- The Ask link's TLS, process and terminal code, and the desktop tools' interface code.
- The Python parsers: the pairing wire format, the contract validator and the knowledge-base bundle checks. Atheris
  harnesses for them are planned ([docs/ROADMAP.md](../../docs/ROADMAP.md#52-dynamic-analysis)).

---

<p align="center"><sub><a href="../../README.md">Overview</a> · <a href="../../INSTALL.md">Installation</a> · <a href="../../docs/HARDWARE.md">Hardware</a> · <a href="../../docs/OPTIONAL-FEATURES.md">Optional features</a> · <a href="../../docs/tr/README.md">Design</a> · <a href="../../docs/DECISIONS.md">Decision record</a> · <a href="../../docs/contracts/README.md">Contracts</a> · <a href="../../adapters/README.md">Agent adapters</a> · <a href="../../.github/SECURITY.md">Security</a> · <a href="../../CHANGELOG.md">Release notes</a></sub></p>
