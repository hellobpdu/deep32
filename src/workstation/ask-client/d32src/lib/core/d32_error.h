#ifndef D32_ERROR_H
#define D32_ERROR_H

enum d32_error {
	D32E_OK = 0,
	D32E_NOENT,
	D32E_LINK,
	D32E_NOTREG,
	D32E_TOOBIG,
	D32E_EMPTY,
	D32E_PERM,
	D32E_INVAL,
	D32E_IO,
	D32E_NOMEM,
	D32E_FORMAT,
	D32E_LIMIT,
	D32E_EXISTS,
	D32E_NEWER,
	D32E_VERSION,
	D32E_UNAVAIL,
	D32E_TEMP,
	D32E_CREATE
};

/* Maps a library result to the u10 sysexits contract. */
int d32_sysexit(enum d32_error);

#endif
