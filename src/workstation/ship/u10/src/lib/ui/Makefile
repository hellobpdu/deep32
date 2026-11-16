LIB= d32ui
SRCS= d32ui.c d32ui_chart.c d32ui_dialog.c d32ui_job.c d32ui_menu.c
NOPIC=
NOPROFILE=

LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
DTBASE?= ${LOCALBASE}/dt
COREDIR?= ${.CURDIR}/../core
CORELIB?= ${COREDIR}/libd32core.a
D32UI_DTHELP?= auto

CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CPPFLAGS+= -I${COREDIR} -I${LOCALBASE}/include -I${X11BASE}/include
UI_LIBS= -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lX11

.if ${D32UI_DTHELP} == "auto"
HAVE_DTHELP!= if test -r "${DTBASE}/include/Dt/HelpDialog.h" && \
	ls ${DTBASE}/lib/libDtHelp.so* ${DTBASE}/lib/libDtSvc.so* ${DTBASE}/lib/libtt.so* >/dev/null 2>&1; \
	then echo yes; else echo no; fi
.else
HAVE_DTHELP= ${D32UI_DTHELP}
.endif
.if ${HAVE_DTHELP} == "yes"
CPPFLAGS+= -DD32UI_WITH_DTHELP -I${DTBASE}/include
UI_LIBS+= -L${DTBASE}/lib -Wl,-rpath,${DTBASE}/lib -lDtHelp -lDtSvc -ltt
.endif

CLEANFILES+= d32ui-sample sample.o

.include <bsd.lib.mk>

all: d32ui-sample

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

d32ui-sample: sample.o lib${LIB}.a ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} sample.o lib${LIB}.a ${CORELIB} ${UI_LIBS}
