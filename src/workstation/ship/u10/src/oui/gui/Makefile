LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
DTBASE?= ${LOCALBASE}/dt
COREDIR= ../../lib/core
UIDIR= ../../lib/ui
OUIDIR= ..
CORELIB= ${COREDIR}/libd32core.a
UILIB= ${UIDIR}/libd32ui.a
OUILIB= ${OUIDIR}/libouidb.a
D32UI_DTHELP?= auto

CPPFLAGS+= -I${COREDIR} -I${UIDIR} -I${OUIDIR} -I${LOCALBASE}/include -I${X11BASE}/include
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

.PHONY: all help test-tools clean
all: d32oui

d32oui: d32oui.o batch.o provision.o ${OUILIB} ${UILIB} ${CORELIB} Makefile
	${CC} ${LDFLAGS} -o $@ d32oui.o batch.o provision.o ${OUILIB} ${UILIB} ${CORELIB} ${UI_LIBS}

d32oui.o batch.o: batch.h result.h ${OUIDIR}/oui.h ${UIDIR}/d32ui.h
d32oui.o provision.o: provision.h ${COREDIR}/d32core.h
batch.o: ${COREDIR}/d32_io.h

${OUILIB}:
	${MAKE} -C ${OUIDIR} libouidb.a

${UILIB}:
	${MAKE} -C ${UIDIR} D32UI_DTHELP=${HAVE_DTHELP} libd32ui.a

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

help: help/D32oui.sdl

help/D32oui.sdl: help/D32oui.htg
	cd help && PATH="${DTBASE}/bin:$$PATH" ${DTBASE}/bin/dthelptag D32oui.htg

test-tools: tests/primary

tests/primary: tests/primary.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o $@ tests/primary.c -L${X11BASE}/lib -lXtst -lX11

clean:
	rm -f d32oui d32oui.o batch.o provision.o help/D32oui.sdl help/D32oui.err tests/primary
