LOCALBASE?= /usr/local
X11BASE?= /usr/X11R6
DTBASE?= ${LOCALBASE}/dt
D32UI_DTHELP?= auto
COREDIR?= ${.CURDIR}/../lib/core
UIDIR?= ${.CURDIR}/../lib/ui
NETDIR?= ${.CURDIR}/../netaddr
REGDIR?= ${.CURDIR}/../regdata
DATAROOT?= /usr/local/d32/share/data
FRAME_PROOF?= no
CORELIB= ${COREDIR}/libd32core.a
UILIB= ${UIDIR}/libd32ui.a
NETLIB= ${NETDIR}/libd32netaddr.a
REGLIB= ${REGDIR}/libd32regdata.a

CPPFLAGS+= -I${COREDIR} -I${UIDIR} -I${NETDIR} -I${REGDIR} -I${LOCALBASE}/include -I${X11BASE}/include
CPPFLAGS+= -DD32_CALC_DATA_ROOT='"${DATAROOT}"'
.if ${FRAME_PROOF} == "yes"
CPPFLAGS+= -DD32_CALC_FRAME_PROOF
.endif
CFLAGS+= -std=c99 -Wall -Wextra -Werror -fstack-protector-strong
UI_LIBS= -L${LOCALBASE}/lib -L${X11BASE}/lib -lXm -lXt -lX11
.if ${D32UI_DTHELP} == "auto"
HAVE_DTHELP!= if test -r "${DTBASE}/include/Dt/HelpDialog.h" && \
	ls ${DTBASE}/lib/libDtHelp.so* ${DTBASE}/lib/libDtSvc.so* ${DTBASE}/lib/libtt.so* >/dev/null 2>&1; \
	then echo yes; else echo no; fi
.else
HAVE_DTHELP= ${D32UI_DTHELP}
.endif
.if ${HAVE_DTHELP} == "yes"
UI_LIBS+= -L${DTBASE}/lib -Wl,-rpath,${DTBASE}/lib -lDtHelp -lDtSvc -ltt
.endif
OBJS= main.o base_panel.o base_convert.o address_page.o plan_page.o plan_io.o \
	policy_page.o registry_data.o registry_page.o

.PHONY: all test help clean
all: d32calc

main.o: base_panel.h calc.h
base_panel.o: base_panel.h base_convert.h
base_convert.o test_base_convert.o: base_convert.h
address_page.o policy_page.o: calc.h
plan_page.o: calc.h plan_io.h
plan_io.o test_plan_io.o: plan_io.h
registry_data.o registry_page.o: calc.h registry_data.h

${CORELIB}:
	${MAKE} -C ${COREDIR} libd32core.a

${UILIB}:
	${MAKE} -C ${UIDIR} libd32ui.a

${NETLIB}:
	${MAKE} -C ${NETDIR} libd32netaddr.a

${REGLIB}:
	${MAKE} -C ${REGDIR} libd32regdata.a

d32calc: ${OBJS} ${UILIB} ${CORELIB} ${NETLIB} ${REGLIB}
	${CC} ${LDFLAGS} -o ${.TARGET} ${OBJS} ${UILIB} ${CORELIB} ${NETLIB} ${REGLIB} ${UI_LIBS}

test-base-convert: test_base_convert.o base_convert.o ${NETLIB}
	${CC} ${LDFLAGS} -o ${.TARGET} test_base_convert.o base_convert.o ${NETLIB}

test-plan-io: test_plan_io.o plan_io.o ${CORELIB} ${NETLIB}
	${CC} ${LDFLAGS} -o ${.TARGET} test_plan_io.o plan_io.o ${CORELIB} ${NETLIB}

test: test-base-convert test-plan-io
	./test-base-convert
	@set -e; scratch=$$(mktemp -d "$${TMPDIR:-/tmp}/d32calc-plan.XXXXXXXX"); \
	trap 'rm -f "$$scratch/saved.json" "$$scratch/new-version.json" "$$scratch/bad-row.json" \
	    "$$scratch/oversize.json" "$$scratch/plan-link.json"; rmdir "$$scratch"' EXIT; \
	./test-plan-io "$$scratch"

help: help/D32Calc.sdl

help/D32Calc.sdl: help/D32Calc.htg
	cd help && PATH="${DTBASE}/bin:$$PATH" ${DTBASE}/bin/dthelptag D32Calc.htg

clean:
	rm -f ${OBJS} test_base_convert.o test_plan_io.o test-base-convert test-plan-io d32calc
