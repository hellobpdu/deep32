PROG= d32sndd
SRCS= d32sndd.c sound.c
NOMAN=
BINDIR?= /usr/local/d32/ship/bin
X11BASE?= /usr/X11R6
COREDIR?= ${.CURDIR}/../../lib/core

CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CPPFLAGS+= -I${COREDIR} -I${X11BASE}/include
LDADD+= ${COREDIR}/libd32core.a -L${X11BASE}/lib -lX11
DPADD+= ${COREDIR}/libd32core.a
CLEANFILES+= sound-check sound-check.o

.include <bsd.prog.mk>

${COREDIR}/libd32core.a:
	${MAKE} -C ${COREDIR} libd32core.a

check: sound-check
	./sound-check

sound-check: sound-check.o sound.o ${COREDIR}/libd32core.a
	${CC} ${LDFLAGS} -o ${.TARGET} sound-check.o sound.o ${COREDIR}/libd32core.a
