/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * replay.c: a plain driver for the harnesses in tests/fuzz/targets. It runs LLVMFuzzerTestOneInput once on every
 * file named on the command line (a directory means every regular file in it, in name order) and needs no libFuzzer,
 * so it builds with any C compiler that has AddressSanitizer and UndefinedBehaviorSanitizer (clang or gcc). It is
 * how a seed corpus or a saved crash input is replayed:
 *
 *   replay_dib tests/fuzz/corpus/dib
 *   replay_dib crash-1a2b3c...
 *
 * Exit status 0 when every input ran; a sanitizer report or a failed FUZZ_ASSERT aborts with the input's name on
 * standard error first.
 */
#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE 1
#endif
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);
int LLVMFuzzerInitialize(int *argc, char ***argv) __attribute__((weak));

static unsigned long ran;

static int run_file(const char *path)
{
	FILE *f = fopen(path, "rb");
	unsigned char *buf = NULL;
	size_t n = 0, cap = 0;
	if (!f) { fprintf(stderr, "replay: cannot open %s\n", path); return -1; }
	for (;;) {
		size_t r;
		if (n == cap) {
			unsigned char *nb;
			cap = cap ? cap * 2 : 65536;
			nb = (unsigned char *)realloc(buf, cap);
			if (!nb) { free(buf); fclose(f); fprintf(stderr, "replay: out of memory\n"); return -1; }
			buf = nb;
		}
		r = fread(buf + n, 1, cap - n, f);
		n += r;
		if (r == 0) break;
	}
	if (ferror(f)) { free(buf); fclose(f); fprintf(stderr, "replay: cannot read %s\n", path); return -1; }
	fclose(f);
	fprintf(stderr, "replay: %s (%lu bytes)\n", path, (unsigned long)n);
	/* the input in an allocation of exactly its size, so a read past its end is reported */
	{
		unsigned char *exact = (unsigned char *)malloc(n ? n : 1);
		if (!exact) { free(buf); return -1; }
		if (n) memcpy(exact, buf, n);
		free(buf);
		LLVMFuzzerTestOneInput(exact, n);
		free(exact);
	}
	ran++;
	return 0;
}

static int cmp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

static int run_dir(const char *dir)
{
	DIR *d = opendir(dir);
	struct dirent *e;
	char **names = NULL;
	size_t n = 0, cap = 0, i;
	int rc = 0;
	if (!d) { fprintf(stderr, "replay: cannot open directory %s\n", dir); return -1; }
	while ((e = readdir(d)) != NULL) {
		size_t len = strlen(dir) + strlen(e->d_name) + 2;
		struct stat st;
		char *p;
		if (e->d_name[0] == '.') continue;
		p = (char *)malloc(len);
		if (!p) { rc = -1; break; }
		snprintf(p, len, "%s/%s", dir, e->d_name);
		if (stat(p, &st) != 0 || !S_ISREG(st.st_mode)) { free(p); continue; }
		if (n == cap) {
			char **nn;
			cap = cap ? cap * 2 : 64;
			nn = (char **)realloc(names, cap * sizeof *names);
			if (!nn) { free(p); rc = -1; break; }
			names = nn;
		}
		names[n++] = p;
	}
	closedir(d);
	if (n) qsort(names, n, sizeof *names, cmp);
	for (i = 0; i < n; i++) {
		if (rc == 0 && run_file(names[i])) rc = -1;
		free(names[i]);
	}
	free(names);
	return rc;
}

int main(int argc, char **argv)
{
	int i, rc = 0;
	if (argc < 2) {
		fprintf(stderr, "usage: %s FILE_OR_DIRECTORY...\n", argv[0]);
		return 2;
	}
	if (LLVMFuzzerInitialize) LLVMFuzzerInitialize(&argc, &argv);
	for (i = 1; i < argc; i++) {
		struct stat st;
		if (stat(argv[i], &st) != 0) { fprintf(stderr, "replay: no such file %s\n", argv[i]); rc = 1; continue; }
		if ((S_ISDIR(st.st_mode) ? run_dir(argv[i]) : run_file(argv[i])) != 0) rc = 1;
	}
	fprintf(stderr, "replay: %lu inputs ran\n", ran);
	return rc;
}
