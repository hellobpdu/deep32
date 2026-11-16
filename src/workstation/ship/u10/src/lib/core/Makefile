LIB= d32core
SRCS= d32_error.c d32_exec.c d32_io.c d32_json.c d32_log.c d32_prefs.c
NOPIC=
NOPROFILE=

CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CPPFLAGS+= -I${.CURDIR}

TESTDIR= ${.CURDIR}/../../../tests/lib/core
TESTS= test-core test-io test-json test-prefs
TESTFLAGS_prefs= -pthread
CLEANFILES+= ${TESTS}

.include <bsd.lib.mk>

.for name in core io json prefs
test-${name}: ${TESTDIR}/test_${name}.c lib${LIB}.a
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} \
	    ${TESTDIR}/test_${name}.c lib${LIB}.a ${TESTFLAGS_${name}}
.endfor

.PHONY: test
test: ${TESTS}
	./test-core
	./test-io ${TESTDIR}/corpus/io
	./test-json ${TESTDIR}/corpus/json
	./test-prefs ${TESTDIR}/corpus/prefs
