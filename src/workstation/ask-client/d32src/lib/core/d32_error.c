#include "d32_error.h"

#include <sysexits.h>

int
d32_sysexit(enum d32_error error)
{
	switch (error) {
	case D32E_OK:
		return EX_OK;
	case D32E_INVAL:
		return EX_USAGE;
	case D32E_LINK:
	case D32E_NOTREG:
	case D32E_TOOBIG:
	case D32E_EMPTY:
	case D32E_FORMAT:
	case D32E_LIMIT:
	case D32E_VERSION:
		return EX_DATAERR;
	case D32E_NOENT:
		return EX_NOINPUT;
	case D32E_NEWER:
	case D32E_UNAVAIL:
		return EX_UNAVAILABLE;
	case D32E_EXISTS:
	case D32E_CREATE:
		return EX_CANTCREAT;
	case D32E_IO:
		return EX_IOERR;
	case D32E_NOMEM:
	case D32E_TEMP:
		return EX_TEMPFAIL;
	case D32E_PERM:
		return EX_NOPERM;
	}
	return EX_SOFTWARE;
}
