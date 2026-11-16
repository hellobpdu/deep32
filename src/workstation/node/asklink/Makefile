# asklink: D32U/1 node side (d32-chatd, d32-chatctl) for the Deep32 Workstation.
# Build on OpenBSD 8.0 with base cc and make:  make && make test
CC?=		cc
CFLAGS+=	-std=c99 -O2 -g -Wall -Wextra -Werror -Wformat=2 \
		-Werror=format-nonliteral -Wshadow -Wpointer-arith \
		-Wmissing-prototypes -Wstrict-prototypes -Wno-unused-parameter \
		-fstack-protector-strong
LDADD=		-ltls -lssl -lcrypto -lutil

COMMON=		src/frame.o src/sas.o src/util.o src/json.o src/msg.o src/bip39.o
DAEMON=		src/chatd.o src/link.o src/ui.o src/edge.o src/log.o src/proc.o
CTL=		src/chatctl.o src/log.o src/ident.o

all: d32-chatd d32-chatctl tests/test_codec tests/tcpfreeze

src/bip39.c: vendor/bip39-english.txt gen-bip39.sh
	sh gen-bip39.sh vendor/bip39-english.txt > src/bip39.c.tmp
	mv src/bip39.c.tmp src/bip39.c

.c.o:
	${CC} ${CFLAGS} -c $< -o $@

d32-chatd: ${COMMON} ${DAEMON} src/ident.o
	${CC} ${LDFLAGS} -o $@ ${COMMON} ${DAEMON} src/ident.o ${LDADD}

d32-chatctl: ${COMMON} ${CTL}
	${CC} ${LDFLAGS} -o $@ ${COMMON} ${CTL} ${LDADD}

# Test-only control tool: the wscons console check is compiled out so a
# scripted harness can type the words over SSH.  Never installed.
d32-chatctl-test: ${COMMON} src/chatctl-test.o src/log.o src/ident.o
	${CC} ${LDFLAGS} -o $@ ${COMMON} src/chatctl-test.o src/log.o \
	    src/ident.o ${LDADD}

src/chatctl-test.o: src/chatctl.c
	${CC} ${CFLAGS} -DD32_TEST_NO_CONSOLE -c src/chatctl.c -o $@

tests/test_codec: ${COMMON} tests/test_codec.o
	${CC} ${LDFLAGS} -o $@ ${COMMON} tests/test_codec.o ${LDADD}

tests/tcpfreeze: tests/tcpfreeze.o
	${CC} ${LDFLAGS} -o $@ tests/tcpfreeze.o

test: tests/test_codec
	./tests/test_codec tests/spec

clean:
	rm -f src/*.o tests/*.o src/bip39.c d32-chatd d32-chatctl \
	    d32-chatctl-test tests/test_codec tests/tcpfreeze

${COMMON} ${DAEMON} src/chatctl.o src/chatctl-test.o src/ident.o \
    tests/test_codec.o: src/d32u.h src/json.h src/msg.h src/chatd.h \
    src/edge.h
