# STAGE 20-py312: CPython 3.12.14 and static SQLite 3.53.1 with the d32_log swap (pyrag/build-py312.sh)
# AS d32build
# IN /build/src/pyrag (Python-3.12.14.tar.xz, sqlite-amalgamation-3530100.zip), /build/src/ports-7.8 patches, /build/lanes/identity/src/d32log
# OUT /usr/local/d32/py312, /usr/local/d32/sqlite
run() {
	install -d -o d32build -g d32build /usr/local/d32 /build/pobj
	rm -rf /usr/local/d32/py312 /usr/local/d32/sqlite
	as_build "sh /build/lanes/pyrag/build-py312.sh" || die "build-py312.sh failed"
	/usr/local/d32/py312/bin/python3.12 -I -c 'import sqlite3, ssl; print(sqlite3.sqlite_version)' || die "py312 does not start"
}
