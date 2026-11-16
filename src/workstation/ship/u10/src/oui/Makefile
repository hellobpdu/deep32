CORE= ../lib/core
CORELIB= ${CORE}/libd32core.a
DATA= ../../data/oui
TESTDIR= ../../tests/oui

CPPFLAGS+= -I. -I${CORE}
CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong

.PHONY: all test clean
all: d32ouiq oui-build

libouidb.a: ouidb.o oui_common.o
	${AR} crs $@ ouidb.o oui_common.o

d32ouiq: d32ouiq.o libouidb.a ${CORELIB}
	${CC} ${LDFLAGS} -o $@ d32ouiq.o libouidb.a ${CORELIB}

oui-build: oui-build.o libouidb.a ${CORELIB}
	${CC} ${LDFLAGS} -o $@ oui-build.o libouidb.a ${CORELIB}

ouidb.o oui_common.o: oui.h oui_internal.h
d32ouiq.o: oui.h oui_internal.h ${CORE}/d32_io.h ${CORE}/d32_json.h
oui-build.o: ${DATA}/oui-build.c oui.h oui_internal.h ${CORE}/d32_io.h
	${CC} ${CPPFLAGS} ${CFLAGS} -c -o $@ ${DATA}/oui-build.c

${CORELIB}:
	${MAKE} -C ${CORE}

test: all
	python3 -B ${TESTDIR}/test_oui.py --builder ./oui-build --query ./d32ouiq

clean:
	rm -f d32ouiq oui-build libouidb.a d32ouiq.o oui-build.o ouidb.o oui_common.o
