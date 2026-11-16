LIB= d32regdata
SRCS= d32_regdata.c
NOPIC=
NOPROFILE=

CFLAGS+= -std=gnu99 -Wall -Wextra -Werror -O2 -fstack-protector-strong
CLEANFILES+= test-regdata

.include <bsd.lib.mk>

test-regdata: test_regdata.c lib${LIB}.a
	${CC} ${CPPFLAGS} ${CFLAGS} ${LDFLAGS} -o ${.TARGET} ${.CURDIR}/test_regdata.c lib${LIB}.a

.PHONY: test
test: test-regdata
	python3 -B ${.CURDIR}/test_build.py ./test-regdata
