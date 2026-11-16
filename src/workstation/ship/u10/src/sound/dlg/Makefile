PROG= d32sound
SRCS= d32sound.c sound.c
NOMAN=
BINDIR?= /usr/local/d32/ship/bin
LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
COREDIR?= ${.CURDIR}/../../lib/core
UIDIR?= ${.CURDIR}/../../lib/ui
SOUNDDIR= ${.CURDIR}/../sndd

.PATH: ${SOUNDDIR}
CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CPPFLAGS+= -I${COREDIR} -I${UIDIR} -I${SOUNDDIR} -I${LOCALBASE}/include -I${X11BASE}/include
LDADD+= ${UIDIR}/libd32ui.a ${COREDIR}/libd32core.a
LDADD+= -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lX11 -lsndio
DPADD+= ${UIDIR}/libd32ui.a ${COREDIR}/libd32core.a

.include <bsd.prog.mk>

${COREDIR}/libd32core.a:
	${MAKE} -C ${COREDIR} libd32core.a

${UIDIR}/libd32ui.a: ${COREDIR}/libd32core.a
	${MAKE} -C ${UIDIR} libd32ui.a
