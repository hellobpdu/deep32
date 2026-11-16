PROG= d32capdlg
SRCS= d32capdlg.c policy.c
NOMAN=
BINDIR?= /usr/local/d32/ship/bin
LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
DTBASE?= ${LOCALBASE}/dt
COREDIR?= ${.CURDIR}/../lib/core
UIDIR?= ${.CURDIR}/../lib/ui
D32UI_DTHELP?= auto

CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CPPFLAGS+= -I${COREDIR} -I${UIDIR} -I${LOCALBASE}/include -I${X11BASE}/include
LDADD+= ${UIDIR}/libd32ui.a ${COREDIR}/libd32core.a
LDADD+= -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lX11
DPADD+= ${UIDIR}/libd32ui.a ${COREDIR}/libd32core.a
CLEANFILES+= policy-check policy-check.o d32-capture capture.o

.if ${D32UI_DTHELP} == "auto"
HAVE_DTHELP!= if test -r "${DTBASE}/include/Dt/HelpDialog.h" && \
	ls ${DTBASE}/lib/libDtHelp.so* ${DTBASE}/lib/libDtSvc.so* ${DTBASE}/lib/libtt.so* >/dev/null 2>&1; \
	then echo yes; else echo no; fi
.else
HAVE_DTHELP= ${D32UI_DTHELP}
.endif
.if ${HAVE_DTHELP} == "yes"
LDADD+= -L${DTBASE}/lib -Wl,-rpath,${DTBASE}/lib -lDtHelp -lDtSvc -ltt
.endif

.include <bsd.prog.mk>

${COREDIR}/libd32core.a:
	${MAKE} -C ${COREDIR} libd32core.a

${UIDIR}/libd32ui.a: ${COREDIR}/libd32core.a
	${MAKE} -C ${UIDIR} libd32ui.a

check: policy-check
	./policy-check

d32-capture: capture.o policy.o ${COREDIR}/libd32core.a
	${CC} ${LDFLAGS} -o ${.TARGET} capture.o policy.o ${COREDIR}/libd32core.a -lpcap

policy-check: policy-check.o policy.o ${COREDIR}/libd32core.a
	${CC} ${LDFLAGS} -o ${.TARGET} policy-check.o policy.o ${COREDIR}/libd32core.a
