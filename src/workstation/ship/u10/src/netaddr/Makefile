CC ?= cc
AR ?= ar
PYTHON ?= python3
CFLAGS ?= -O2
CFLAGS += -std=c99 -Wall -Wextra -Werror -fstack-protector-strong

OBJS = netaddr.o nettext.o netpolicy.o netplan.o
TESTDIR = ../../tests/netaddr
REFDIR ?= $(TESTDIR)/ref

.PHONY: all test test-differential clean
all: libd32netaddr.a

$(OBJS): d32netaddr.h

.c.o:
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -c $< -o $@

libd32netaddr.a: $(OBJS)
	$(AR) rcs $@ $(OBJS)

libd32netaddr.so: $(OBJS)
	$(CC) $(LDFLAGS) -shared -o $@ $(OBJS)

test-exhaustive: $(TESTDIR)/test_exhaustive.c libd32netaddr.a
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $(LDFLAGS) -o $@ $(TESTDIR)/test_exhaustive.c libd32netaddr.a

test: test-exhaustive libd32netaddr.so
	./test-exhaustive
	$(PYTHON) -B $(TESTDIR)/test_vectors.py

test-differential: libd32netaddr.so
	$(PYTHON) -B "$(REFDIR)/differential.py" --adapter $(TESTDIR)/t10_adapter.py

clean:
	rm -f $(OBJS) libd32netaddr.a libd32netaddr.so test-exhaustive
