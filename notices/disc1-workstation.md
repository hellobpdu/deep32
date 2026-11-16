# Disc 1 workstation: third-party notices and source directions

Disc 1 contains OpenBSD 8.0 base and X Window System sets, 131 OpenBSD packages, and Deep32 packs containing 56 installed Python distribution records across the engine and gateway environments. The tables below follow the extracted disc 1 component manifest. The Python records cover 42 distinct distribution names; a distribution installed in both environments has a separate row for each installation.

Upstream components retain their own copyright notices and licence terms. Deep32's licences do not replace those terms.

## OpenBSD 8.0 base and sets

OpenBSD states: "OpenBSD strives to provide code that can be freely used, copied, modified, and distributed by anyone and for any purpose." Its base contains ISC and BSD licensed code and GPL components, including GCC with supporting binutils and libraries. The X Window System (xenocara) contains MIT/X11 and other terms; fonts have their own licences. The individual copyright notices and licence texts apply. OpenBSD's licence policy is at https://www.openbsd.org/policy.html .

The manifest records the following 13 release entries, including kernels and release metadata as well as set archives:

- `BUILDINFO`
- `INSTALL.amd64`
- `SHA256.sig`
- `base80.tgz`
- `bsd`
- `bsd.mp`
- `bsd.rd`
- `man80.tgz`
- `site80.tgz`
- `xbase80.tgz`
- `xfont80.tgz`
- `xserv80.tgz`
- `xshare80.tgz`

OpenBSD 8.0 sources are available from the official release directory:

https://cdn.openbsd.org/pub/OpenBSD/8.0/

- Base source: https://cdn.openbsd.org/pub/OpenBSD/8.0/src.tar.gz
- Kernel source: https://cdn.openbsd.org/pub/OpenBSD/8.0/sys.tar.gz
- Ports tree: https://cdn.openbsd.org/pub/OpenBSD/8.0/ports.tar.gz
- X Window System source: https://cdn.openbsd.org/pub/OpenBSD/8.0/xenocara.tar.gz

## OpenBSD packages

For every package in this table, use the OpenBSD 8.0 ports tree above at the recorded port path, together with its upstream distfiles from OpenBSD distfiles mirrors. Port paths, including any comma suffix, are reproduced as recorded. Specific distfile filenames, hashes and per-package mirror URLs are not stated in the component manifest.

Package licence identifiers are not stated in the manifest. The licence paths below are reproduced from its `licence_files` field: 20 packages have recorded paths and 111 have none recorded. "Not stated in manifest" means no path was recorded, rather than that the package has no licence. Listed paths are metadata, not a verification of their contents; some Node.js entries are licence-related JavaScript files.

| Package name-version | OpenBSD 8.0 source port path | Description | Licence files installed with the package (manifest paths) |
| --- | --- | --- | --- |
| `abseil-cpp-20260817.0` | `devel/abseil-cpp` | abseil common libraries (C++) | not stated in manifest |
| `adwaita-icon-theme-50.0` | `x11/gnome/adwaita-icon-theme` | base icon theme for GNOME | `share/licenses/adwaita-icon-theme/COPYING`; `share/licenses/adwaita-icon-theme/COPYING_CCBYSA3`; `share/licenses/adwaita-icon-theme/COPYING_LGPL` |
| `adwaita-icon-theme-legacy-46.2p0` | `x11/gnome/adwaita-icon-theme-legacy` | legacy icon theme for GNOME | not stated in manifest |
| `age-1.3.2` | `security/age` | simple, modern and secure file encryption tool | not stated in manifest |
| `aom-3.14.1p0` | `multimedia/aom` | Alliance for Open Media AV1 video codec | not stated in manifest |
| `at-spi2-core-2.60.6` | `x11/gnome/at-spi2-core` | service interface for assistive technologies | not stated in manifest |
| `brotli-1.2.0` | `archivers/brotli` | generic lossless compressor | not stated in manifest |
| `bzip2-1.0.8p0` | `archivers/bzip2` | block-sorting file compressor, unencumbered | not stated in manifest |
| `cairo-1.18.6` | `graphics/cairo` | vector graphics library | not stated in manifest |
| `cde-2.5.4pre0` | `x11/cde` | Common Desktop Environment | `copyright`; `share/doc/cde/COPYING` |
| `curl-8.22.0` | `net/curl` | transfer files with FTP, HTTP, HTTPS, etc. | not stated in manifest |
| `dav1d-1.5.4` | `multimedia/dav1d` | small and fast AV1 decoder | not stated in manifest |
| `dbus-1.16.2p1v0` | `x11/dbus,-main` | message bus system | not stated in manifest |
| `dconf-0.40.0p2` | `devel/dconf` | configuration backend system | not stated in manifest |
| `desktop-file-utils-0.28p0` | `devel/desktop-file-utils` | utilities for dot.desktop entries | not stated in manifest |
| `docopt.cpp-0.6.3` | `devel/docopt.cpp` | command-line interface description language, in c++ | not stated in manifest |
| `epoll-shim-0.0.20240608` | `devel/epoll-shim` | small epoll implementation using kqueue | not stated in manifest |
| `ffmpeg-9.0.2v1` | `graphics/ffmpeg` | audio/video converter and streamer | not stated in manifest |
| `firefox-esr-153.4.0` | `www/firefox-esr` | Firefox web browser, Extended Support Release | not stated in manifest |
| `flock-20110525p1` | `sysutils/flock` | flock(2) manager for shell scripts | not stated in manifest |
| `freeglut-3.6.0` | `graphics/freeglut` | open source alternative to glut | not stated in manifest |
| `fribidi-1.0.17` | `devel/fribidi` | library implementing the Unicode Bidirectional Algorithm | not stated in manifest |
| `gdk-pixbuf-2.44.8` | `graphics/gdk-pixbuf2` | image data transformation library | not stated in manifest |
| `gettext-runtime-1.0` | `devel/gettext,-runtime` | GNU gettext runtime libraries and programs | not stated in manifest |
| `giflib-6.1.3p0` | `graphics/giflib` | tools and library routines for working with GIF images | not stated in manifest |
| `glew-2.3.1` | `graphics/glew` | GL Extension Wrangler library | not stated in manifest |
| `glfw-3.4p2` | `graphics/glfw` | open source, multi-platform library for creating windows | not stated in manifest |
| `glib2-2.88.3p1` | `devel/glib2` | low level core library | not stated in manifest |
| `gmake-4.4.1p0` | `devel/gmake` | GNU make | not stated in manifest |
| `gmp-6.3.0p0` | `devel/gmp,-main` | library for arbitrary precision arithmetic | not stated in manifest |
| `gnupg-2.5.24p0v0` | `security/gnupg` | GNU privacy guard - a free PGP replacement | not stated in manifest |
| `gnutls-3.8.13p1` | `security/gnutls` | GNU Transport Layer Security library | not stated in manifest |
| `graphite2-1.3.15` | `graphics/graphite2` | rendering for complex writing systems | not stated in manifest |
| `gsm-1.0.24` | `audio/gsm` | GSM audio codec library and converter | not stated in manifest |
| `gtest-1.18.0` | `devel/gtest` | C++ unit test framework | not stated in manifest |
| `gtk+3-3.24.52` | `x11/gtk+3,-main` | multi-platform graphical toolkit | not stated in manifest |
| `gtk4-update-icon-cache-4.22.5` | `x11/gtk+4,-guic` | gtk+ icon theme caching utility | not stated in manifest |
| `gumbo-0.14.0` | `www/gumbo` | HTML5 parsing library in pure C99 | not stated in manifest |
| `harfbuzz-14.5.0` | `devel/harfbuzz,-main` | text shaping library | not stated in manifest |
| `hicolor-icon-theme-0.18` | `x11/hicolor-icon-theme` | fallback theme of the icon theme specification | not stated in manifest |
| `icu4c-78.3v0` | `textproc/icu4c,-main` | International Components for Unicode | `share/icu/78.3/LICENSE` |
| `jasper-4.2.9` | `graphics/jasper` | reference implementation of JPEG-2000 | not stated in manifest |
| `jbig2dec-0.19p0` | `graphics/jbig2dec` | decoder for JBIG2 monochrome hi-res image compression format | not stated in manifest |
| `jpeg-3.2.0v0` | `graphics/jpeg` | SIMD-accelerated JPEG codec replacement of libjpeg | `share/doc/libjpeg-turbo/LICENSE.md` |
| `kiwix-tools-3.8.1` | `www/kiwix/kiwix-tools` | command-line Kiwix tools | not stated in manifest |
| `ksh93-1.0.10p0v0` | `shells/ksh93,-main` | AT&T Korn Shell | not stated in manifest |
| `lame-3.101` | `audio/lame` | lame ain't an MP3 encoder | not stated in manifest |
| `lcms2-2.18pl20260420` | `graphics/lcms2` | color management library | not stated in manifest |
| `lerc-4.2.0` | `graphics/lerc` | Limited Error Raster Compression | not stated in manifest |
| `libass-0.17.5` | `multimedia/libass` | portable ASS/SSA subtitle renderer | not stated in manifest |
| `libassuan-3.0.2p0` | `security/libassuan` | IPC library used by GnuPG and gpgme | not stated in manifest |
| `libb2-0.98.1v0` | `security/libb2` | library providing BLAKE2b, BLAKE2s, BLAKE2bp, BLAKE2sp | not stated in manifest |
| `libcares-1.34.8` | `net/libcares` | asynchronous resolver library | not stated in manifest |
| `libffi-3.8.0` | `devel/libffi` | Foreign Function Interface | not stated in manifest |
| `libgcrypt-1.12.4` | `security/libgcrypt` | crypto library based on code used in GnuPG | not stated in manifest |
| `libgpg-error-1.61` | `security/libgpg-error` | error codes for GnuPG related software | not stated in manifest |
| `libiconv-1.19` | `converters/libiconv` | character set conversion library | not stated in manifest |
| `libidn2-2.3.0p0` | `devel/libidn2` | implementation of IDNA2008 internationalized domain names | not stated in manifest |
| `libkiwix-14.2.0` | `www/kiwix/libkiwix` | common code base for all Kiwix ports | not stated in manifest |
| `libksba-1.8.1` | `security/libksba` | X.509 library | not stated in manifest |
| `libmicrohttpd-1.0.10` | `www/libmicrohttpd` | tiny embeddable httpd library written in C | not stated in manifest |
| `libogg-1.3.6p0` | `audio/libogg` | Ogg bitstream library | not stated in manifest |
| `librsvg-2.63.2v0` | `x11/gnome/librsvg` | SAX-based render library for SVG files | not stated in manifest |
| `libsamplerate-0.2.2p0` | `audio/libsamplerate` | audio sample rate conversion library | `share/doc/libsamplerate/license.md` |
| `libsecret-0.21.8.2` | `x11/gnome/libsecret` | library for storing and retrieving passwords and secrets | not stated in manifest |
| `libtasn1-4.21.0` | `security/libtasn1` | Abstract Syntax Notation One structure parser library | not stated in manifest |
| `libtheora-1.2.0v0` | `multimedia/libtheora` | open video codec | not stated in manifest |
| `libunbound-1.26.1` | `net/libunbound` | validating DNS resolver library | not stated in manifest |
| `libunibreak-7.0` | `textproc/libunibreak` | implementation of the line and word breaking | not stated in manifest |
| `libunistring-0.9.7` | `converters/libunistring` | manipulate Unicode strings | not stated in manifest |
| `libusb1-1.0.30` | `devel/libusb1` | library for USB device access from userspace | not stated in manifest |
| `libuv-1.52.1p0` | `devel/libuv` | multi-platform library for asynchronous I/O | not stated in manifest |
| `libv4l-1.24.1` | `multimedia/libv4l` | libv4l userspace library | not stated in manifest |
| `libvidstab-1.1.0` | `multimedia/libvidstab` | video stabilization library | not stated in manifest |
| `libvorbis-1.3.7` | `audio/libvorbis` | audio compression codec library | not stated in manifest |
| `libvpx-1.16.0p1v0` | `multimedia/libvpx` | Google VP8/VP9 video codec | not stated in manifest |
| `libwebp-1.6.0p2` | `graphics/libwebp` | Google WebP image format conversion tool | not stated in manifest |
| `libxkbcommon-1.13.2` | `x11/xkbcommon` | library to handle keyboard descriptions | not stated in manifest |
| `libxml-2.15.4` | `textproc/libxml` | XML parsing library | not stated in manifest |
| `libzim-9.5.1` | `archivers/libzim` | reference implementation of the ZIM specification | not stated in manifest |
| `lmdb-0.9.33` | `databases/lmdb` | Symas Lightning Memory-Mapped Database | not stated in manifest |
| `lz4-1.10.0` | `archivers/lz4` | fast BSD-licensed data compression | not stated in manifest |
| `lzo2-2.10p2` | `archivers/lzo2` | portable speedy lossless data compression library | `share/doc/lzo2/COPYING` |
| `motif-2.3.8p3` | `x11/motif` | Motif toolkit | not stated in manifest |
| `mpg123-1.33.7` | `audio/mpg123` | fast console MPEG audio player and decoder library | not stated in manifest |
| `mupdf-1.28.5` | `textproc/mupdf` | graphic library, pdf parser, viewer and utilities | `share/doc/mupdf/COPYING` |
| `nedit-5.7p1v0` | `editors/nedit` | a fast, compact Motif/X11 plain text editor | not stated in manifest |
| `nettle-4.0p0` | `security/nettle` | cryptographic library | not stated in manifest |
| `nghttp2-1.70.0` | `www/nghttp2` | library for HTTP/2 | not stated in manifest |
| `nghttp3-1.18.0` | `www/nghttp3` | implementation of HTTP/3 | not stated in manifest |
| `ngtcp2-1.25.0` | `net/ngtcp2` | implementation of the RFC 9000 QUIC protocol | not stated in manifest |
| `node-24.21.0v0` | `lang/node` | JavaScript runtime built on Chrome's V8 JavaScript engine | `lib/node_modules/undici/LICENSE`; `lib/node_modules/undici/lib/web/fetch/LICENSE`; `lib/node_modules/@gar/promise-retry/LICENSE`; `lib/node_modules/@npmcli/package-json/lib/license.js`; `lib/node_modules/balanced-match/LICENSE.md`; `lib/node_modules/brace-expansion/LICENSE`; `lib/node_modules/walk-up-path/LICENSE`; `lib/node_modules/exponential-backoff/LICENSE`; `lib/node_modules/tinyglobby/LICENSE`; `lib/node_modules/tinyglobby/node_modules/fdir/LICENSE`; `lib/node_modules/tinyglobby/node_modules/picomatch/LICENSE`; `lib/node_modules/@npmcli/node-gyp/LICENSE`; `lib/node_modules/minizlib/LICENSE`; `lib/node_modules/minimatch/LICENSE.md`; `lib/node_modules/chownr/LICENSE.md`; `lib/node_modules/glob/LICENSE.md`; `lib/node_modules/yallist/LICENSE.md`; `lib/node_modules/@npmcli/redact/LICENSE`; `lib/node_modules/agent-base/LICENSE`; `lib/node_modules/https-proxy-agent/LICENSE`; `lib/node_modules/socks-proxy-agent/LICENSE`; `lib/node_modules/binary-extensions/license`; `lib/node_modules/diff/LICENSE`; `lib/node_modules/http-proxy-agent/LICENSE`; `lib/node_modules/ip-address/LICENSE`; `lib/node_modules/minipass-collect/LICENSE`; `lib/node_modules/ci-info/LICENSE`; `lib/node_modules/node-gyp/gyp/pylib/packaging/LICENSE`; `lib/node_modules/node-gyp/gyp/pylib/packaging/LICENSE.APACHE`; `lib/node_modules/node-gyp/gyp/pylib/packaging/LICENSE.BSD`; `lib/node_modules/@sigstore/bundle/LICENSE`; `lib/node_modules/@sigstore/core/LICENSE`; `lib/node_modules/@sigstore/protobuf-specs/LICENSE`; `lib/node_modules/@sigstore/sign/LICENSE`; `lib/node_modules/@sigstore/tuf/LICENSE`; `lib/node_modules/sigstore/LICENSE`; `lib/node_modules/@tufjs/canonical-json/LICENSE`; `lib/node_modules/@tufjs/models/LICENSE`; `lib/node_modules/tuf-js/LICENSE`; `lib/node_modules/common-ancestor-path/LICENSE.md`; `lib/node_modules/isexe/LICENSE.md`; `lib/node_modules/lru-cache/LICENSE.md`; `lib/node_modules/minipass-flush/LICENSE.md`; `lib/node_modules/minipass/LICENSE.md`; `lib/node_modules/path-scurry/LICENSE.md`; `lib/node_modules/tar/LICENSE.md`; `lib/node_modules/signal-exit/LICENSE.txt`; `lib/node_modules/postcss-selector-parser/LICENSE-MIT`; `lib/node_modules/minipass-pipeline/node_modules/minipass/LICENSE`; `lib/node_modules/@npmcli/arborist/bin/license.js`; `lib/node_modules/graceful-fs/LICENSE`; `lib/node_modules/ssri/LICENSE.md`; `lib/node_modules/@npmcli/arborist/LICENSE.md`; `lib/node_modules/@npmcli/fs/LICENSE.md`; `lib/node_modules/@npmcli/map-workspaces/LICENSE.md`; `lib/node_modules/@npmcli/query/LICENSE`; `lib/node_modules/npm-profile/LICENSE.md`; `lib/node_modules/npm-registry-fetch/LICENSE.md`; `lib/node_modules/parse-conflict-json/LICENSE.md`; `lib/node_modules/@npmcli/config/LICENSE`; `lib/node_modules/@npmcli/installed-package-contents/LICENSE`; `lib/node_modules/@npmcli/metavuln-calculator/LICENSE`; `lib/node_modules/@npmcli/run-script/LICENSE`; `lib/node_modules/bin-links/LICENSE`; `lib/node_modules/npm-normalize-package-bin/LICENSE`; `lib/node_modules/npm-package-arg/LICENSE`; `lib/node_modules/libnpmfund/LICENSE`; `lib/node_modules/@npmcli/fs/lib/cp/LICENSE`; `lib/node_modules/@npmcli/git/LICENSE`; `lib/node_modules/@npmcli/promise-spawn/LICENSE`; `lib/node_modules/@npmcli/name-from-folder/LICENSE`; `lib/node_modules/@npmcli/package-json/LICENSE`; `lib/node_modules/abbrev/LICENSE`; `lib/node_modules/env-paths/license`; `lib/node_modules/aproba/LICENSE`; `lib/node_modules/read-cmd-shim/LICENSE`; `lib/node_modules/init-package-json/LICENSE.md`; `lib/node_modules/cacache/LICENSE.md`; `lib/node_modules/npm-audit-report/LICENSE`; `lib/node_modules/npm-pick-manifest/LICENSE.md`; `lib/node_modules/@isaacs/fs-minipass/LICENSE`; `lib/node_modules/fs-minipass/LICENSE`; `lib/node_modules/ignore-walk/LICENSE`; `lib/node_modules/ini/LICENSE`; `lib/node_modules/json-stringify-nice/LICENSE`; `lib/node_modules/minipass-pipeline/LICENSE`; `lib/node_modules/minipass-pipeline/node_modules/yallist/LICENSE`; `lib/node_modules/minipass-sized/LICENSE`; `lib/node_modules/mute-stream/LICENSE`; `lib/node_modules/nopt/LICENSE`; `lib/node_modules/npm-packlist/LICENSE`; `lib/node_modules/read/LICENSE`; `lib/node_modules/semver/LICENSE`; `lib/node_modules/which/LICENSE`; `lib/node_modules/cidr-regex/LICENSE`; `lib/node_modules/is-cidr/LICENSE`; `lib/node_modules/cmd-shim/LICENSE`; `lib/node_modules/npm-bundled/LICENSE`; `lib/node_modules/treeverse/LICENSE`; `lib/node_modules/corepack/LICENSE.md`; `lib/node_modules/debug/LICENSE`; `lib/node_modules/cssesc/LICENSE-MIT.txt`; `lib/node_modules/fastest-levenshtein/LICENSE.md`; `lib/node_modules/hosted-git-info/LICENSE`; `lib/node_modules/http-cache-semantics/LICENSE`; `lib/node_modules/iconv-lite/LICENSE`; `lib/node_modules/json-parse-even-better-errors/LICENSE.md`; `lib/node_modules/jsonparse/LICENSE`; `lib/node_modules/just-diff-apply/LICENSE`; `lib/node_modules/just-diff/LICENSE`; `lib/node_modules/libnpmaccess/LICENSE`; `lib/node_modules/libnpmorg/LICENSE`; `lib/node_modules/libnpmpack/LICENSE`; `lib/node_modules/libnpmpublish/LICENSE`; `lib/node_modules/libnpmsearch/LICENSE`; `lib/node_modules/libnpmteam/LICENSE`; `lib/node_modules/libnpmdiff/LICENSE`; `lib/node_modules/libnpmexec/LICENSE`; `lib/node_modules/make-fetch-happen/LICENSE`; `lib/node_modules/minipass-fetch/LICENSE`; `lib/node_modules/ms/license.md`; `lib/node_modules/negotiator/LICENSE`; `lib/node_modules/node-gyp/gyp/LICENSE`; `lib/node_modules/node-pledge/LICENSE`; `lib/node_modules/npm-install-checks/LICENSE`; `lib/node_modules/npm/LICENSE`; `lib/node_modules/chalk/license`; `lib/node_modules/p-map/license`; `lib/node_modules/supports-color/license`; `lib/node_modules/pacote/LICENSE`; `lib/node_modules/proc-log/LICENSE`; `lib/node_modules/proggy/LICENSE`; `lib/node_modules/qrcode-terminal/LICENSE`; `lib/node_modules/safer-buffer/LICENSE`; `lib/node_modules/smart-buffer/LICENSE`; `lib/node_modules/socks/LICENSE`; `lib/node_modules/spdx-expression-parse/LICENSE`; `lib/node_modules/tiny-relative-date/LICENSE.md`; `lib/node_modules/util-deprecate/LICENSE`; `lib/node_modules/validate-npm-package-name/LICENSE`; `lib/node_modules/write-file-atomic/LICENSE.md`; `lib/node_modules/archy/LICENSE`; `lib/node_modules/text-table/LICENSE`; `lib/node_modules/node-gyp/LICENSE`; `lib/node_modules/npm-user-validate/LICENSE`; `lib/node_modules/@isaacs/string-locale-compare/LICENSE`; `lib/node_modules/libnpmversion/LICENSE`; `lib/node_modules/promise-all-reject-late/LICENSE`; `lib/node_modules/promise-call-limit/LICENSE`; `lib/node_modules/promzard/LICENSE` |
| `npth-1.8` | `devel/npth` | new GNU Portable Threads Library | not stated in manifest |
| `nspr-4.40` | `devel/nspr` | Netscape Portable Runtime | not stated in manifest |
| `nss-3.130` | `security/nss` | libraries to support development of security-enabled apps | not stated in manifest |
| `openjp2-2.5.4` | `graphics/openjp2` | open-source JPEG 2000 codec library | not stated in manifest |
| `openssl-3.5.9v0` | `security/openssl/3.5` | TLS/SSL library and tools | not stated in manifest |
| `opus-1.6.1` | `audio/opus` | IETF audio codec | not stated in manifest |
| `p11-kit-0.26.5` | `security/p11-kit` | library for loading and enumerating PKCS#11 modules | not stated in manifest |
| `pango-1.58.2` | `devel/pango` | library for layout and rendering of text | not stated in manifest |
| `pcre2-10.48` | `devel/pcre2` | perl-compatible regular expression library, version 2 | `share/doc/pcre2/LICENCE.md`; `share/doc/pcre2/COPYING` |
| `pinentry-1.3.3` | `security/pinentry,-main` | PIN or passphrase entry dialog (ncurses interface) | not stated in manifest |
| `plan-1.10.1p5` | `misc/plan` | X11/Motif schedule planner with calendar | not stated in manifest |
| `png-1.6.59` | `graphics/png` | library for manipulating PNG images | not stated in manifest |
| `pugixml-1.16` | `textproc/pugixml` | light-weight, simple and fast XML parser for C++ | not stated in manifest |
| `py3-cffi-2.1.1` | `devel/py-cffi` | Foreign Function Interface for Python calling C code | `lib/python3.14/site-packages/cffi-2.1.1.dist-info/licenses/LICENSE` |
| `py3-cparser-3.0` | `devel/py-cparser` | C parser in pure Python | `lib/python3.14/site-packages/pycparser-3.0.dist-info/licenses/LICENSE` |
| `py3-cryptography-49.0.0p0` | `security/py-cryptography` | cryptographic recipes and primitives for Python | `lib/python3.14/site-packages/cryptography-49.0.0.dist-info/licenses/LICENSE`; `lib/python3.14/site-packages/cryptography-49.0.0.dist-info/licenses/LICENSE.APACHE`; `lib/python3.14/site-packages/cryptography-49.0.0.dist-info/licenses/LICENSE.BSD` |
| `py3-packaging-26.3` | `sysutils/py-packaging` | core utilities for Python packaging | `lib/python3.14/site-packages/packaging-26.3.dist-info/licenses/LICENSE`; `lib/python3.14/site-packages/packaging-26.3.dist-info/licenses/LICENSE.APACHE`; `lib/python3.14/site-packages/packaging-26.3.dist-info/licenses/LICENSE.BSD` |
| `py3-rpds-py-0.30.0p0` | `devel/py-rpds-py` | Python bindings for Rust's persistent data structures (rpds) | `lib/python3.14/site-packages/rpds_py-0.30.0.dist-info/licenses/LICENSE` |
| `python-3.14.7p0` | `lang/python/3,-main` | interpreted object-oriented programming language | `lib/python3.14/LICENSE.txt` |
| `re2-20251105p1` | `textproc/re2` | non-backtracking regular expression engine | not stated in manifest |
| `sdl2-2.32.10p1` | `devel/sdl2` | cross-platform multimedia library | not stated in manifest |
| `shared-mime-info-2.5.1` | `misc/shared-mime-info` | shared mime database for desktops | not stated in manifest |
| `simdutf-9.2.1` | `textproc/simdutf` | unicode validator and transcoder with SIMD | not stated in manifest |
| `speex-1.2.1` | `audio/speex` | patent-free speech codec | not stated in manifest |
| `speexdsp-1.2.1` | `audio/speexdsp` | speech processing DSP library | not stated in manifest |
| `sqlite3-3.53.2` | `databases/sqlite3` | embedded SQL implementation | not stated in manifest |
| `svt-av1-4.0.1` | `multimedia/svt-av1` | scalable AV1 encoder | not stated in manifest |
| `tcl-8.6.18` | `lang/tcl/8.6` | Tool Command Language | `lib/tcl/tcl8.6/man/license.terms` |
| `tiff-4.7.2` | `graphics/tiff` | tools and library routines for working with TIFF images | `share/doc/tiff/manual/project/license.html`; `share/doc/tiff/LICENSE.md`; `share/doc/tiff/manual/_sources/project/license.rst.txt` |
| `wayland-1.26.0` | `wayland/wayland` | Display server protocol | not stated in manifest |
| `x264-20250909` | `multimedia/x264` | free H.264/MPEG-4 AVC encoder | not stated in manifest |
| `x265-4.3` | `multimedia/x265` | free H.265/HEVC encoder | not stated in manifest |
| `xapian-core-2.0.0` | `databases/xapian-core` | search engine library | not stated in manifest |
| `xdg-utils-1.2.1` | `devel/xdg-utils` | utilities to assist desktop integration tasks | not stated in manifest |
| `xv-5.2.0` | `graphics/xv` | X11 image display and modification tool | `share/doc/xv/copyright.h` |
| `xvidcore-1.3.7` | `multimedia/xvidcore` | ISO MPEG-4 compliant video codec | not stated in manifest |
| `xz-5.8.4` | `archivers/xz` | library and tools for XZ and LZMA compressed files | `share/doc/xz/COPYING.GPLv2`; `share/doc/xz/COPYING`; `share/doc/xz/COPYING.0BSD` |
| `zimg-3.0.6` | `graphics/zimg` | scaling, colorspace conversion, and dithering library | `share/doc/zimg/COPYING` |
| `zstd-1.5.7p0` | `archivers/zstd` | zstandard fast real-time compression algorithm | not stated in manifest |

## MuPDF 1.28.5

MuPDF 1.28.5 is under AGPL-3.0. Its recorded package licence file is `share/doc/mupdf/COPYING`. Corresponding source:

https://mupdf.com/downloads/archive/mupdf-1.28.5-source.tar.gz

The component manifest records the OpenBSD 8.0 port as `textproc/mupdf`; use that port together with its upstream distfiles as described above.

Deep32's PDF renderer `e26-render` links `libmupdf`. The renderer's own source is in this repository at `src/workstation/library/src/render/`, with its build rules in `src/workstation/library/Makefile`, under AGPL-3.0-or-later.

## xv 5.2.0

The following is the complete, unmodified copyright and licence text recorded for `share/doc/xv/copyright.h`:

```text
/* Copyright Notice
 * ================
 * Copyright 1989, 1994 by John Bradley
 *
 * Permission to copy and distribute XV in its entirety, for non-commercial
 * purposes, is hereby granted without fee, provided that this license
 * information and copyright notice appear unmodified in all copies.
 *
 * Note that distributing XV 'bundled' in with any product is considered
 * to be a 'commercial purpose'.
 *
 * Also note that any copies of XV that are distributed must be built
 * and/or configured to be in their 'unregistered copy' mode, so that it
 * is made obvious to the user that XV is shareware, and that they should
 * consider donating, or at least reading this License Info.
 *
 * The software may be modified for your own purposes, but modified
 * versions may not be distributed without prior consent of the author.
 *
 * This software is provided 'as-is', without any express or implied
 * warranty.  In no event will the author be held liable for any damages
 * arising from the use of this software.
 *
 * If you would like to do something with XV that this copyright
 * prohibits (such as distributing it with a commercial product,
 * using portions of the source in some other program, etc.), please
 * contact the author (preferably via email).  Arrangements can
 * probably be worked out.
 *
 * XV is shareware for PERSONAL USE only.  You may use XV for your own
 * amusement, and if you find it nifty, useful, generally cool, or of
 * some value to you, your non-deductable donation would be greatly
 * appreciated.  $25 is the suggested donation, though, of course,
 * larger donations are quite welcome.  Folks who donate $25 or more
 * can receive a Real Nice bound copy of the XV manual for no extra
 * charge.
 *
 * Commercial, government, and institutional users must register their
 * copies of XV, for the price of $25 per workstation/X terminal or per
 * XV user, whichever is less.  Note that it does NOT say 'simultaneous user',
 * but rather, the total number of people who use XV on any sort of
 * recurring basis. Site licenses are available (and recommended) for those
 * who wish to run XV on a large (>10) number of machines.
 * Contact the author for more details.
 *
 * The author may be contacted via:
 *
 *    Email:    bradley@cis.upenn.edu       (preferred!)
 *
 *    FAX:      (610) 520-2042
 *
 *    US Mail:  John Bradley
 *              1053 Floyd Terrace
 *              Bryn Mawr, PA  19010
 *
 * The author may not be contacted by (voice) phone.  Please don't try.
 *
 */

/*
 * Portions copyright 2000-2007 by Greg Roelofs and contributors:
 *
 *   Andrey A. Chernov [ache]
 *     (http://cvsweb.freebsd.org/ports/graphics/xv/files/patch-ab)
 *   Andreas Dilger (adilger clusterfs.com)
 *   Alexander Lehmann (lehmann usa.net)
 *   Alexey Spiridonov (http://www-math.mit.edu/~lesha/)
 *   Anthony Thyssen (http://www.cit.gu.edu.au/~anthony/)
 *   Bruno Rohee (http://bruno.rohee.com/)
 *   David A. Clunie (http://www.dclunie.com/xv-pcd.html)
 *   Erling A. Jacobsen (linuxcub email.dk)
 *   Egmont Koblinger (egmont users.sourceforge.net)
 *   Fabian Greffrath (fabian debian-unofficial.org)
 *   Greg Roelofs (http://pobox.com/~newt/greg_contact.html)
 *   Guido Vollbeding (http://sylvana.net/guido/)
 *   IKEMOTO Masahiro (ikeyan airlab.cs.ritsumei.ac.jp)
 *   John Cooper (john.cooper third-harmonic.com)
 *   John C. Elliott (http://www.seasip.demon.co.uk/ZX/zxdload.html)
 *   John D. Baker (http://mylinuxisp.com/~jdbaker/)
 *   Jörgen Grahn (jgrahn algonet.se)
 *   John H. Bradley, of course (http://www.trilon.com/xv/)
 *   Jean-Pierre Demailly (http://www-fourier.ujf-grenoble.fr/~demailly/)
 *   John Rochester (http://www.freebsd.org/cgi/query-pr.cgi?pr=2920)
 *   (also http://cvsweb.freebsd.org/ports/graphics/xv/files/patch-af, -ag)
 *   James Roberts Kirkpatrick (uwyo.edu)
 *   Joe Zbiciak (http://spatula-city.org/~im14u2c/)
 *   Kyoichiro Suda (http://www.coara.or.jp/~sudakyo/XV_jp.html)
 *   Landon Curt "chongo" Noll (http://www.isthe.com/chongo/)
 *   Larry Jones (lawrence.jones ugs.com)
 *   Peter Jordan (http://www.ibiblio.org/pub/Linux/apps/graphics/viewers/X/)
 *   Pawel S. Veselov (http://manticore.2y.net/wbmp.html)
 *   Ross Combs (rocombs cs.nmsu.edu)
 *   Robin Humble (http://www.cita.utoronto.ca/~rjh/)
 *   Sean Borman (http://www.nd.edu/~sborman/software/xvwheelmouse.html)
 *   TenThumbs (tenthumbs cybernex.net)
 *   Scott B. Marovich (formerly marovich hpl.hp.com)
 *   Tim Adye (http://hepwww.rl.ac.uk/Adye/xv-psnewstyle.html)
 *   Tim Ramsey (tar pobox.com)
 *   Tetsuya INOUE (tin329 chino.it.okayama-u.ac.jp)
 *   Tavis Ormandy (taviso gentoo.org)
 *   Werner Fink (http://www.suse.de/~werner/)
 *
 * Portions copyright 2022 by the following contributors:
 *   Michael D. Adams (http://github.com/mdadams)
 *
 * Other credits are as listed on the XV Downloads page or in the respective
 * patches (e.g., the jp-extension patches or within the PNG patch).
 *
 */
```

## Firefox ESR 153.4.0

Firefox ESR 153.4.0 is shipped as OpenBSD's unmodified package, under the Mozilla Public License 2.0 (MPL-2.0). Its recorded OpenBSD 8.0 port is `www/firefox-esr`; source directions are given above. Firefox and the Firefox logo are trademarks of the Mozilla Foundation.

## Python distributions in the Deep32 packs

All 56 records below are from `d32-node.tgz`. The engine environment uses Python 3.12 and the gateway environment uses Python 3.14. Each distribution retains its licence files in its `.dist-info` directory; individual licence-file paths are not stated in the component manifest.

The declared licence is reproduced from `License-Expression`, otherwise `License`, otherwise licence classifiers. If none is supplied, it is shown as "not stated in metadata". No licence names or expressions have been inferred or normalised.

| Distribution | Version | Environment | Declared licence | Metadata field |
| --- | --- | --- | --- | --- |
| `annotated-doc` | `0.0.5` | engine | MIT | License-Expression |
| `annotated-types` | `0.8.0` | engine | MIT | License-Expression |
| `anyio` | `4.15.1` | engine | MIT | License-Expression |
| `certifi` | `2026.7.22` | engine | MPL-2.0 | License |
| `click` | `8.5.0` | engine | BSD-3-Clause | License-Expression |
| `fastapi` | `0.141.1` | engine | MIT | License-Expression |
| `grpcio` | `1.84.0` | engine | Apache-2.0 | License-Expression |
| `h11` | `0.16.0` | engine | MIT | License |
| `h2` | `4.4.1` | engine | MIT | License-Expression |
| `hpack` | `4.2.0` | engine | MIT | License-Expression |
| `httpcore` | `1.0.9` | engine | BSD-3-Clause | License-Expression |
| `httptools` | `0.8.0` | engine | MIT | License-Expression |
| `httpx` | `0.28.1` | engine | BSD-3-Clause | License |
| `hyperframe` | `6.1.0` | engine | The MIT License (MIT) | License |
| `idna` | `3.20` | engine | BSD-3-Clause | License-Expression |
| `numpy` | `2.5.3` | engine | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | License-Expression |
| `portalocker` | `3.2.0` | engine | BSD-3-Clause | License-Expression |
| `protobuf` | `7.36.2` | engine | 3-Clause BSD License | License |
| `pydantic` | `2.13.5` | engine | MIT | License-Expression |
| `pydantic_core` | `2.46.5` | engine | MIT | License-Expression |
| `qdrant-client` | `1.19.1` | engine | Apache-2.0 | License |
| `regex` | `2026.9.10` | engine | Apache-2.0 AND CNRI-Python | License-Expression |
| `starlette` | `1.7.0` | engine | BSD-3-Clause | License-Expression |
| `typing-inspection` | `0.4.4` | engine | MIT | License-Expression |
| `typing_extensions` | `4.16.0` | engine | PSF-2.0 | License-Expression |
| `urllib3` | `2.8.0` | engine | MIT | License-Expression |
| `uvicorn` | `0.54.0` | engine | BSD-3-Clause | License-Expression |
| `uvloop` | `0.22.1` | engine | MIT License | License |
| `PyJWT` | `2.15.0` | gateway | MIT | License-Expression |
| `annotated-types` | `0.8.0` | gateway | MIT | License-Expression |
| `anyio` | `4.15.1` | gateway | MIT | License-Expression |
| `attrs` | `26.1.0` | gateway | MIT | License-Expression |
| `certifi` | `2026.7.22` | gateway | MPL-2.0 | License |
| `click` | `8.5.0` | gateway | BSD-3-Clause | License-Expression |
| `h11` | `0.16.0` | gateway | MIT | License |
| `httpcore` | `1.0.9` | gateway | BSD-3-Clause | License-Expression |
| `httpcore2` | `2.13.1` | gateway | BSD-3-Clause | License-Expression |
| `httpx` | `0.28.1` | gateway | BSD-3-Clause | License |
| `httpx2` | `2.13.1` | gateway | BSD-3-Clause | License-Expression |
| `idna` | `3.20` | gateway | BSD-3-Clause | License-Expression |
| `jsonschema` | `4.26.0` | gateway | MIT | License-Expression |
| `jsonschema-specifications` | `2025.9.1` | gateway | MIT | License-Expression |
| `mcp` | `2.2.0` | gateway | MIT | License |
| `mcp-types` | `2.2.0` | gateway | MIT | License |
| `opentelemetry-api` | `1.45.0` | gateway | Apache-2.0 | License-Expression |
| `pycparser` | `3.0` | gateway | BSD-3-Clause | License-Expression |
| `pydantic` | `2.13.5` | gateway | MIT | License-Expression |
| `pydantic_core` | `2.46.5` | gateway | MIT | License-Expression |
| `python-multipart` | `0.0.32` | gateway | Apache-2.0 | License-Expression |
| `referencing` | `0.37.0` | gateway | MIT | License-Expression |
| `sse-starlette` | `3.4.11` | gateway | BSD-3-Clause | License-Expression |
| `starlette` | `1.7.0` | gateway | BSD-3-Clause | License-Expression |
| `truststore` | `0.10.4` | gateway | MIT | License-Expression |
| `typing-inspection` | `0.4.4` | gateway | MIT | License-Expression |
| `typing_extensions` | `4.16.0` | gateway | PSF-2.0 | License-Expression |
| `uvicorn` | `0.54.0` | gateway | BSD-3-Clause | License-Expression |

For `uvloop` 0.22.1, `License` declares "MIT License"; its classifiers also name Apache and MIT. The table follows the declared field order; further licence terms are not stated in the manifest.

## Deep32 code and documentation

Deep32's own code is licensed under AGPL-3.0-or-later. Deep32's documentation is licensed under Creative Commons Attribution 4.0 International (CC BY 4.0). See `LICENSE` and `licenses/LICENSE-DOCS` at the repository root. Third-party material keeps its own terms.

## Trademarks and affiliation

Other companies' and projects' names are the trademarks of their owners, used only to describe compatibility. Deep32 is not affiliated with or endorsed by them.
