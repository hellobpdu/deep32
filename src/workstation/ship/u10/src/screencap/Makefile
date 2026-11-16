PROG= d32-screencap
SRCS= ui.c output.c select.c policy.c grab.c png.c
MAN=

LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
COREDIR?= ${.CURDIR}/../lib/core
UIDIR?= ${.CURDIR}/../lib/ui
CORELIB= ${COREDIR}/libd32core.a
UILIB= ${UIDIR}/libd32ui.a

CPPFLAGS+= -I${COREDIR} -I${UIDIR} -I${LOCALBASE}/include -I${X11BASE}/include
CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
DPADD+= ${UILIB} ${CORELIB}
LDADD+= ${UILIB} ${CORELIB} -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lXrandr -lXRes -lX11 -lz
CLEANFILES+= fixture test-png test-policy test-grab
.MAIN: all

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

${UILIB}:
	${MAKE} -C ${UIDIR} libd32ui.a

fixture: fixture.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${.ALLSRC} -L${X11BASE}/lib -lX11

test-png: test_png.c png.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${.ALLSRC} -lz

test-policy: test_policy.c policy.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${.ALLSRC} -L${X11BASE}/lib -lXRes -lX11

test-grab: test_grab.c grab.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${.ALLSRC} -L${X11BASE}/lib -lXrandr -lX11

.include <bsd.prog.mk>
