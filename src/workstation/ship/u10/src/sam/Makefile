LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
DTBASE?= ${LOCALBASE}/dt
COREDIR= ../lib/core
UIDIR= ../lib/ui
CORELIB= ${COREDIR}/libd32core.a
UILIB= ${UIDIR}/libd32ui.a
CATALOG= libsamcatalog.a
D32UI_DTHELP?= auto

CPPFLAGS+= -I. -I${COREDIR} -I${UIDIR} -I${LOCALBASE}/include -I${X11BASE}/include
CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
UI_LIBS= -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lX11

.if ${D32UI_DTHELP} == "auto"
HAVE_DTHELP!= if test -r "${DTBASE}/include/Dt/HelpDialog.h" && \
	ls ${DTBASE}/lib/libDtHelp.so* ${DTBASE}/lib/libDtSvc.so* ${DTBASE}/lib/libtt.so* >/dev/null 2>&1; \
	then echo yes; else echo no; fi
.else
HAVE_DTHELP= ${D32UI_DTHELP}
.endif
.if ${HAVE_DTHELP} == "yes"
UI_LIBS+= -L${DTBASE}/lib -Wl,-rpath,${DTBASE}/lib -lDtHelp -lDtSvc -ltt
.endif

.PHONY: all test help clean
all: sam

sam: main.o runtime.o gui.o prefs.o ${CATALOG} ${UILIB} ${CORELIB} Makefile
	${CC} ${LDFLAGS} -o $@ main.o runtime.o gui.o prefs.o ${CATALOG} ${UILIB} ${CORELIB} ${UI_LIBS}

main.o runtime.o gui.o prefs.o: sam.h catalog.h
main.o: ${COREDIR}/d32_io.h
gui.o: ${UIDIR}/d32ui.h
runtime.o prefs.o: ${COREDIR}/d32core.h

${CATALOG}: catalog.mk catalog.h catalog.c catalog_exec.c catalog_system.c
	${MAKE} -f catalog.mk libsamcatalog.a

${UILIB}:
	${MAKE} -C ${UIDIR} D32UI_DTHELP=${HAVE_DTHELP} libd32ui.a

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

sam-prefs-test: tests/prefs_test.c prefs.o ${CATALOG} ${CORELIB}
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o $@ tests/prefs_test.c prefs.o ${CATALOG} ${CORELIB}

sam-runtime-test: tests/runtime_test.c runtime.o prefs.o ${CATALOG} ${CORELIB}
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o $@ tests/runtime_test.c runtime.o prefs.o ${CATALOG} ${CORELIB}

test: sam sam-prefs-test sam-runtime-test
	${MAKE} -f catalog.mk test
	./sam-prefs-test
	./sam-runtime-test

help: help/D32Sam.sdl

help/D32Sam.sdl: help/D32Sam.htg
	cd help && PATH="${DTBASE}/bin:$$PATH" ${DTBASE}/bin/dthelptag D32Sam.htg

clean:
	rm -f sam sam-prefs-test sam-runtime-test main.o runtime.o gui.o prefs.o \
	    help/D32Sam.sdl help/D32Sam.err
