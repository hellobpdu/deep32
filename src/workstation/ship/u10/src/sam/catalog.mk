CORE= ../lib/core
CORELIB= ${CORE}/libd32core.a
CATALOG_OBJS= catalog.o catalog_system.o catalog_exec.o

CPPFLAGS+= -I${CORE}
CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong

.PHONY: all test clean
all: libsamcatalog.a

libsamcatalog.a: ${CATALOG_OBJS}
	${AR} crs $@ ${CATALOG_OBJS}

${CATALOG_OBJS} catalog_test.o: catalog.h
catalog.o catalog_exec.o catalog_test.o: ${CORE}/d32core.h

${CORELIB}:
	${MAKE} -C ${CORE} libd32core.a

catalog_test: catalog_test.o libsamcatalog.a ${CORELIB}
	${CC} ${LDFLAGS} -o $@ catalog_test.o libsamcatalog.a ${CORELIB}

test: catalog_test
	./catalog_test

clean:
	rm -f libsamcatalog.a catalog_test ${CATALOG_OBJS} catalog_test.o
