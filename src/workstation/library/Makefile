# Deep32 Library. GNU make (gmake on OpenBSD). C99, no warnings allowed. Build 3 (W0-F0): every directory is a wildcard,
# rooms and kinds register themselves (generated module lists), and tests are found by name.
#   gmake                 build the application and the helpers (e26-lib, e26-render, d32library-ask)
#   gmake test            every tests/test_*.c (test_unit: SHA-256, DIB/RLE, screen files,
#                         taxonomy, article; test_registry: the registry
#                         lint and the notice fallback; test_core: settings, events, timer, history, spawn, helpers)
#   gmake lint            the registry lint alone (every kind, action, source and key the screens name resolves)
# OBJ defaults to ./build; on the build VM use OBJ=/build/pobj/<lane> (the shared RAM disk).
#
# Adding a room needs new files only (docs/F0-HANDOFF.md): src/app/room_<room>.c and kind_<x>.c (each defines
# void e26_mod_<file>(void)), screens/<room>.scr and .keys, src/lib/q_<room>.c (void e26_q_<room>(void)),
# src/render/op_<x>.c (void e26_op_<x>(void)), tests/test_<room>.c, tools/render/<room>.sh.

OBJ    ?= build
CC     ?= cc
X11    ?= /usr/X11R6
LOCAL  ?= /usr/local
CFLAGS ?= -O2
CFLAGS += -std=c99 -Wall -Wextra -Werror -pedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE
XCFLAGS = -I$(LOCAL)/include -I$(X11)/include
XLIBS   = -L$(LOCAL)/lib -L$(X11)/lib -Wl,-rpath,$(LOCAL)/lib -Wl,-rpath,$(X11)/lib -lXm -lXt -lXrender -lXinerama -lXrandr -lXext -lX11
SNDIO  ?= 1
ifeq ($(SNDIO),1)
SNDDEF  = -DHAVE_SNDIO
SNDLIB  = -lsndio
endif
SYSLIBS = -lutil -lm

# The helpers' libraries, declared ahead for W1-DATA, W1-IMAGE and W2-READER: 1 when the system has them. A module
# that uses one tests the HAVE_ flag; the helper links the library only when the flag is 1.
PKGCONF ?= pkg-config
HAVE_SQLITE3 ?= $(shell $(PKGCONF) --exists sqlite3 2>/dev/null && echo 1)
HAVE_LIBZIM  ?= $(shell $(PKGCONF) --exists libzim 2>/dev/null && echo 1)
HAVE_LIBWEBP ?= $(shell $(PKGCONF) --exists libwebp 2>/dev/null && echo 1)
HAVE_MUPDF   ?= $(shell test -f $(LOCAL)/include/mupdf/fitz.h && echo 1)
# W1-DATA: e26-lib links SQLite (E26_LIB_SQLITE) and, through two small C++ files (src/lib/*.cpp), libzim and ICU
# (E26_LIB_ZIM); the UI process links neither.
# u6-data: libzstd (E26_LIB_ZSTD) for storage revision 2 packs (src/lib/storage.c, h2-index SIZE-PASS.md).
HAVE_ZSTD    ?= $(shell $(PKGCONF) --exists libzstd 2>/dev/null && echo 1)
LIBDEFS    = $(if $(HAVE_SQLITE3),-DHAVE_SQLITE3 -DE26_LIB_SQLITE $(shell $(PKGCONF) --cflags sqlite3)) $(if $(HAVE_LIBZIM),-DHAVE_LIBZIM -DE26_LIB_ZIM)              $(if $(and $(HAVE_SQLITE3),$(HAVE_ZSTD)),-DE26_LIB_ZSTD $(shell $(PKGCONF) --cflags libzstd))
LIBLIBS    = $(if $(HAVE_SQLITE3),$(shell $(PKGCONF) --libs sqlite3)) $(if $(HAVE_LIBZIM),$(shell $(PKGCONF) --libs libzim icu-uc))              $(if $(and $(HAVE_SQLITE3),$(HAVE_ZSTD)),$(shell $(PKGCONF) --libs libzstd))
ifeq ($(origin CXX),default)
CXX        = c++
endif
LIBCXXFLAGS = -std=c++17 -O2 -Wall -Wextra -Werror $(LIBDEFS) $(patsubst -I%,-isystem %,$(shell $(PKGCONF) --cflags libzim icu-uc 2>/dev/null))
LIBLD      = $(if $(HAVE_LIBZIM),$(CXX),$(CC))
RENDERDEFS = $(if $(HAVE_LIBWEBP),-DHAVE_LIBWEBP $(shell $(PKGCONF) --cflags libwebp)) $(if $(HAVE_MUPDF),-DHAVE_MUPDF -I$(LOCAL)/include)
RENDERLIBS = $(if $(HAVE_LIBWEBP),$(shell $(PKGCONF) --libs libwebp)) $(if $(HAVE_MUPDF),-L$(LOCAL)/lib -lmupdf)

# ---- sources, by directory (every .c file; the ones holding main() are named)
COMMON_SRC = $(wildcard src/common/*.c)
IMPORT_SRC = $(filter-out src/import/import.c,$(wildcard src/import/*.c))
APP_SRC    = $(filter-out src/app/app.c,$(wildcard src/app/*.c))
LIB_SRC    = $(filter-out src/lib/main.c,$(wildcard src/lib/*.c))
LIB_CXX_SRC = $(if $(HAVE_LIBZIM),$(wildcard src/lib/*.cpp))
RENDER_SRC = $(filter-out src/render/main.c,$(wildcard src/render/*.c))
ASK_SRC    = $(filter-out src/ask/main.c,$(wildcard src/ask/*.c))

COMMON_OBJ = $(patsubst src/common/%.c,$(OBJ)/common/%.o,$(COMMON_SRC))
IMPORT_OBJ = $(patsubst src/import/%.c,$(OBJ)/import/%.o,$(IMPORT_SRC))
APP_OBJ    = $(patsubst src/app/%.c,$(OBJ)/app/%.o,$(APP_SRC)) $(OBJ)/app/modules.gen.o
LIB_OBJ    = $(patsubst src/lib/%.c,$(OBJ)/lib/%.o,$(LIB_SRC)) $(OBJ)/lib/lib_modules.gen.o \
             $(patsubst src/lib/%.cpp,$(OBJ)/lib/%.o,$(LIB_CXX_SRC))
RENDER_OBJ = $(patsubst src/render/%.c,$(OBJ)/render/%.o,$(RENDER_SRC)) $(OBJ)/render/render_modules.gen.o
ASK_OBJ    = $(patsubst src/ask/%.c,$(OBJ)/ask/%.o,$(ASK_SRC))

# one archive of everything but the programs' main()s: the programs and every test link what they use from it
ARCHIVE = $(OBJ)/libe26.a

PROGRAMS = $(OBJ)/d32library $(OBJ)/e26-lib $(OBJ)/e26-render $(OBJ)/d32library-ask
all: $(PROGRAMS)

# ---- the generated module lists: a module is a file; its register function is named after it
APP_MODULES    = $(sort $(patsubst room_documents,room_cisco,$(basename $(notdir $(wildcard src/app/kind_*.c src/app/room_*.c src/app/mod_*.c)))))
LIB_MODULES    = $(sort $(patsubst documents,cisco,$(patsubst q_%,%,$(basename $(notdir $(wildcard src/lib/q_*.c))))))
RENDER_MODULES = $(sort $(patsubst op_%,%,$(basename $(notdir $(wildcard src/render/op_*.c)))))
# each list is rewritten only when it changes, so a module added or removed regenerates its .gen.c
$(shell mkdir -p $(OBJ)/common $(OBJ)/import $(OBJ)/app $(OBJ)/lib $(OBJ)/render $(OBJ)/ask)
$(shell echo '$(APP_MODULES)' | cmp -s - $(OBJ)/app/modules.list || echo '$(APP_MODULES)' > $(OBJ)/app/modules.list)
$(shell echo '$(LIB_MODULES)' | cmp -s - $(OBJ)/lib/modules.list || echo '$(LIB_MODULES)' > $(OBJ)/lib/modules.list)
$(shell echo '$(RENDER_MODULES)' | cmp -s - $(OBJ)/render/modules.list || echo '$(RENDER_MODULES)' > $(OBJ)/render/modules.list)

# gen <name> <prefix> <modules...>: a C file declaring and calling <prefix><module>() for each module, and listing them
define gen
	{ echo '/* generated by the Makefile from the module files: do not edit */'; \
	  for m in $(3); do echo "void $(2)$$m(void);"; done; \
	  echo 'extern const char *const $(1)_names[];'; \
	  echo 'extern const int $(1)_count;'; \
	  echo 'void $(1)_register(void);'; \
	  echo 'const char *const $(1)_names[] = {'; for m in $(3); do echo "	\"$$m\","; done; echo '	0 };'; \
	  echo 'const int $(1)_count = $(words $(3));'; \
	  echo 'void $(1)_register(void)'; echo '{'; for m in $(3); do echo "	$(2)$$m();"; done; echo '}'; } > $@.tmp && mv $@.tmp $@
endef
$(OBJ)/app/modules.gen.c: $(OBJ)/app/modules.list
	$(call gen,e26_modules,e26_mod_,$(APP_MODULES))
$(OBJ)/lib/lib_modules.gen.c: $(OBJ)/lib/modules.list
	$(call gen,e26_lib_modules,e26_q_,$(LIB_MODULES))
$(OBJ)/render/render_modules.gen.c: $(OBJ)/render/modules.list
	$(call gen,e26_render_modules,e26_op_,$(RENDER_MODULES))

# ---- compiling
HDRS = $(wildcard src/app/*.h src/common/*.h)
$(OBJ)/lib/q_finder.o: src/lib/cisco_types.inc
$(OBJ)/common/%.o: src/common/%.c $(HDRS)
	$(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/import/kwaj.o: src/import/kwaj.c $(wildcard src/import/*.h)
	$(CC) $(CFLAGS) -Wno-pedantic -c $< -o $@
$(OBJ)/import/%.o: src/import/%.c $(wildcard src/import/*.h) $(HDRS)
	$(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/app/sound.o: src/app/sound.c $(HDRS)
	$(CC) $(CFLAGS) $(XCFLAGS) $(SNDDEF) -c $< -o $@
$(OBJ)/app/%.gen.o: $(OBJ)/app/%.gen.c
	$(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/app/%.o: src/app/%.c $(HDRS)
	$(CC) $(CFLAGS) $(XCFLAGS) -c $< -o $@
$(OBJ)/lib/%.gen.o: $(OBJ)/lib/%.gen.c
	$(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/lib/%.o: src/lib/%.c $(HDRS) $(wildcard src/lib/*.h)
	$(CC) $(CFLAGS) $(LIBDEFS) -c $< -o $@
$(OBJ)/lib/%.o: src/lib/%.cpp $(wildcard src/lib/*.h)
	$(CXX) $(LIBCXXFLAGS) -c $< -o $@
$(OBJ)/render/%.gen.o: $(OBJ)/render/%.gen.c
	$(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/render/%.o: src/render/%.c $(HDRS)
	$(CC) $(CFLAGS) $(RENDERDEFS) -c $< -o $@
$(OBJ)/ask/%.o: src/ask/%.c $(HDRS)
	$(CC) $(CFLAGS) -c $< -o $@

$(ARCHIVE): $(COMMON_OBJ) $(IMPORT_OBJ) $(APP_OBJ)
	rm -f $@
	ar rcs $@ $^

# e26-lib's modules without its main(), for the tests that drive a named query in process (tests/test_search.c)
LIBARCHIVE = $(OBJ)/libe26lib.a
$(LIBARCHIVE): $(LIB_OBJ)
	rm -f $@
	ar rcs $@ $^

# ---- programs
$(OBJ)/d32library: $(OBJ)/app/app.o $(ARCHIVE)
	$(CC) -o $@ $(OBJ)/app/app.o $(ARCHIVE) $(XLIBS) $(SNDLIB) $(SYSLIBS)
$(OBJ)/e26-lib: $(OBJ)/lib/main.o $(LIB_OBJ) $(ARCHIVE)
	$(LIBLD) -o $@ $(OBJ)/lib/main.o $(LIB_OBJ) $(ARCHIVE) $(LIBLIBS) $(SYSLIBS)
$(OBJ)/e26-render: $(OBJ)/render/main.o $(RENDER_OBJ) $(ARCHIVE)
	$(CC) -o $@ $(OBJ)/render/main.o $(RENDER_OBJ) $(ARCHIVE) $(RENDERLIBS) $(SYSLIBS)
$(OBJ)/d32library-ask: $(OBJ)/ask/main.o $(ASK_OBJ) $(ARCHIVE)
	$(CC) -o $@ $(OBJ)/ask/main.o $(ASK_OBJ) $(ARCHIVE) $(SYSLIBS)

# ---- tests: every tests/test_<name>.c is a test program
UNIT_TESTS = $(basename $(notdir $(wildcard tests/test_*.c)))
$(OBJ)/test_%: tests/test_%.c $(LIBARCHIVE) $(ARCHIVE) $(HDRS)
	$(CC) $(CFLAGS) $(XCFLAGS) $(LIBDEFS) -o $@ $< $(LIBARCHIVE) $(ARCHIVE) $(XLIBS) $(SNDLIB) $(LIBLIBS) $(SYSLIBS)

TAXONOMY ?=
# each test runs from the tree's root with the taxonomy (when given) and knows the object directory (E26_OBJ)
test: all $(addprefix $(OBJ)/,$(UNIT_TESTS))
	@rc=0; for t in $(UNIT_TESTS); do echo "== $$t"; E26_OBJ=$(OBJ) $(OBJ)/$$t . $(TAXONOMY) || rc=1; done; exit $$rc

lint: $(OBJ)/test_registry
	E26_OBJ=$(OBJ) $(OBJ)/test_registry . $(TAXONOMY)

clean:
	rm -rf $(OBJ)

.PHONY: all test lint clean
.SECONDARY:
