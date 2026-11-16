CC ?= cc
LOCALBASE ?= /usr/local
X11BASE ?= /usr/X11R6
CPPFLAGS += -Icore
CFLAGS ?= -O2
WARNINGS = -std=c99 -Wall -Wextra -Werror -Wpedantic
XM_CPPFLAGS = -I$(LOCALBASE)/include -I$(X11BASE)/include -I$(X11BASE)/include/freetype2
XM_LDLIBS = -L$(LOCALBASE)/lib -L$(X11BASE)/lib \
	-Wl,-rpath,$(LOCALBASE)/lib -Wl,-rpath,$(X11BASE)/lib -lXm -lXt -lXext -lXft -lXrandr -lX11 -lsndio
SESSION_UI ?= ../ui
ASK_SOUND ?= ../sound
D32_SRC ?= ../../ship/u10/src
D32_CORE = $(D32_SRC)/lib/core
D32_SOUND = $(D32_SRC)/sound/sndd
XM_CPPFLAGS += -I$(SESSION_UI) -I$(ASK_SOUND) -I$(D32_CORE) -I$(D32_SOUND)
CORE = core/ev.c core/doc.c core/lay.c core/copy.c
GUI = xm/app.c xm/child.c $(SESSION_UI)/view.c $(SESSION_UI)/session_widgets.c $(ASK_SOUND)/ask_sound.c
SHARED = $(D32_SOUND)/sound.c $(D32_CORE)/d32_error.c $(D32_CORE)/d32_exec.c \
	$(D32_CORE)/d32_io.c $(D32_CORE)/d32_log.c $(D32_CORE)/d32_prefs.c
BUILD ?= build

all: $(BUILD)/d32-askw

$(BUILD)/d32-askw: $(CORE) $(GUI) $(SHARED) core/ask_core.h core/doc.h xm/child.h xm/view.h Makefile
	mkdir -p $(BUILD)
	$(CC) $(CPPFLAGS) -Ixm $(XM_CPPFLAGS) $(CFLAGS) $(WARNINGS) -o $@ $(CORE) $(GUI) $(SHARED) $(LDFLAGS) $(XM_LDLIBS)

$(BUILD)/test_core: $(CORE) core/ask_core.h core/doc.h tests/test_core.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) -o $@ $(CORE) tests/test_core.c $(LDFLAGS)

test: $(BUILD)/test_core
	$(BUILD)/test_core

.PHONY: all test
