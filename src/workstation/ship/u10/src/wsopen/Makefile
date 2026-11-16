COREDIR?= ${.CURDIR}/../lib/core
CORELIB?= ${COREDIR}/libd32core.a
TESTDIR?= ${.CURDIR}/../../tests/ws

CFLAGS+= -std=c99 -Wall -Wextra -Werror -fstack-protector-strong
CPPFLAGS+= -I${COREDIR} -I${.CURDIR}

SENDER= wsopen.o common.o
RECEIVER= wsrun.o spool.o common.o
TESTS= d32-wsopen-mock d32-wsopen-test-absent wireshark-run-mock \
	wireshark-run-test-gated wireshark-run-test-small mock-doas make-fixtures
TESTOBJS= common_test.o wsopen_mock.o wsopen_absent.o spool_small.o mock_doas.o mock_viewer.o

.PHONY: all test clean
all: d32-wsopen wireshark-run-core

${SENDER} ${RECEIVER} viewer_gated.o viewer_native.o: ws41.h

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

d32-wsopen: ${SENDER} ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} ${SENDER} ${CORELIB}

wireshark-run-core: ${RECEIVER} viewer_native.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} ${RECEIVER} viewer_native.o ${CORELIB}

common_test.o: common.c ws41.h
	${CC} ${CPPFLAGS} ${CFLAGS} -DWS41_TEST_NO_SYSLOG -c common.c -o ${.TARGET}

wsopen_mock.o: wsopen.c ws41.h
	${CC} ${CPPFLAGS} ${CFLAGS} -DD32_WS_TEST_PEER \
	    -DD32_WS_DOAS='"${.OBJDIR}/mock-doas"' -DD32_WS_RUN='"${.OBJDIR}/wireshark-run-mock"' \
	    -c wsopen.c -o ${.TARGET}

wsopen_absent.o: wsopen.c ws41.h
	${CC} ${CPPFLAGS} ${CFLAGS} -DD32_WS_DOAS='"${.OBJDIR}/mock-doas"' \
	    -DD32_WS_RUN='"${.OBJDIR}/intentionally-absent-runner"' -c wsopen.c -o ${.TARGET}

spool_small.o: spool.c ws41.h
	${CC} ${CPPFLAGS} ${CFLAGS} -DWS_MAX_BYTES=1024U -DD32_WS_TIMEOUT_MS=300U \
	    -c spool.c -o ${.TARGET}

mock_doas.o: ${TESTDIR}/mock_doas.c
	${CC} ${CPPFLAGS} ${CFLAGS} -DD32_WS_MOCK_RUN='"${.OBJDIR}/wireshark-run-mock"' \
	    -c ${TESTDIR}/mock_doas.c -o ${.TARGET}

mock_viewer.o: ${TESTDIR}/mock_viewer.c ws41.h
	${CC} ${CPPFLAGS} ${CFLAGS} -c ${TESTDIR}/mock_viewer.c -o ${.TARGET}

mock-doas: mock_doas.o
	${CC} ${LDFLAGS} -o ${.TARGET} mock_doas.o

make-fixtures: ${TESTDIR}/gen/make_fixtures.c
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${TESTDIR}/gen/make_fixtures.c

d32-wsopen-mock: wsopen_mock.o common_test.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} wsopen_mock.o common_test.o ${CORELIB}

d32-wsopen-test-absent: wsopen_absent.o common_test.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} wsopen_absent.o common_test.o ${CORELIB}

wireshark-run-mock: wsrun.o spool.o mock_viewer.o common_test.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} wsrun.o spool.o mock_viewer.o common_test.o ${CORELIB}

wireshark-run-test-small: wsrun.o spool_small.o mock_viewer.o common_test.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} wsrun.o spool_small.o mock_viewer.o common_test.o ${CORELIB}

wireshark-run-test-gated: wsrun.o spool.o viewer_gated.o common_test.o ${CORELIB}
	${CC} ${LDFLAGS} -o ${.TARGET} wsrun.o spool.o viewer_gated.o common_test.o ${CORELIB}

test: all ${TESTS}
	/bin/sh ${TESTDIR}/run.sh "${.OBJDIR}"

clean:
	rm -f ${SENDER} ${RECEIVER} viewer_gated.o viewer_native.o ${TESTOBJS} ${TESTS} d32-wsopen wireshark-run-core
