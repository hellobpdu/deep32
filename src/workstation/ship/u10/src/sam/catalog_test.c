/* T23 catalogue and bounded-source tests. No commands are launched. */
#include "catalog.h"
#include "d32core.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static unsigned int checks;

static void
check(bool ok, const char *expression, unsigned int line)
{
	checks++;
	if (!ok) {
		fprintf(stderr, "catalog_test:%u: %s\n", line, expression);
		exit(1);
	}
}

#define CHECK(expression) check((expression), #expression, __LINE__)
#define LENGTH(array) (sizeof(array) / sizeof((array)[0]))

/* Resolve libsamcatalog's launcher reference before libd32core is searched. */
enum d32_error
d32_exec_fixed(const char *path, char *const argv[], int input, int output,
    int errors, pid_t *child)
{
	(void)path;
	(void)argv;
	(void)input;
	(void)output;
	(void)errors;
	(void)child;
	fputs("catalog_test: attempted to launch a command\n", stderr);
	exit(1);
}

static const char *const look_args[] = {
	"/usr/local/dt/bin/dtaction", "D32LookProfile", NULL
};
static const char *const sound_args[] = {
	"/usr/local/dt/bin/dtaction", "D32Sound", NULL
};
static const char *const audio_args[] = {
	"/usr/local/dt/bin/dtaction", "AudioControl", NULL
};
static const char *const services_args[] = {
	"/usr/sbin/rcctl", "ls", "started", NULL
};
static const char *const ready_args[] = {
	"/usr/local/d32/sbin/d32ready", "-q", NULL
};
static const char *const apply_args[] = {
	"/usr/local/dt/bin/dtterm", "-title", "Deep32 Admin: upd.apply", "-e",
	"/usr/bin/doas", "/usr/local/d32/sbin/d32upd", "apply-staged", NULL
};
static const char *const rollback_args[] = {
	"/usr/local/dt/bin/dtterm", "-title", "Deep32 Admin: upd.rollback", "-e",
	"/usr/bin/doas", "/usr/local/d32/sbin/d32upd", "revert", NULL
};

static const struct expected_op {
	const char *id;
	enum sam_class class;
	const char *const *argv;
} expected_ops[] = {
	{ "sys.summary", SAM_READ, NULL },
	{ "d32.release", SAM_READ, NULL },
	{ "upd.state", SAM_READ, NULL },
	{ "disk.free", SAM_READ, NULL },
	{ "pkg.list", SAM_READ, NULL },
	{ "svc.status", SAM_READ, services_args },
	{ "boot.ready", SAM_READ, ready_args },
	{ "look", SAM_DELEGATE, look_args },
	{ "sound", SAM_DELEGATE, sound_args },
	{ "audio", SAM_DELEGATE, audio_args },
	{ "upd.apply", SAM_TERMINAL, apply_args },
	{ "upd.rollback", SAM_TERMINAL, rollback_args },
	{ "prefs.sam", SAM_PREF, NULL }
};

static void
same_argv(const char *const *actual, const char *const *expected)
{
	size_t i;

	CHECK(actual != NULL);
	for (i = 0; expected[i] != NULL; i++) {
		CHECK(actual[i] != NULL);
		CHECK(strcmp(actual[i], expected[i]) == 0);
	}
	CHECK(actual[i] == NULL);
}

static void
catalogue(void)
{
	const struct sam_operation *ops, *op;
	size_t count, i, j;

	ops = sam_catalog(&count);
	CHECK(ops != NULL);
	CHECK(count == LENGTH(expected_ops));
	for (i = 0; i < count; i++) {
		CHECK(ops[i].id != NULL && ops[i].id[0] != '\0');
		CHECK(ops[i].title != NULL && ops[i].title[0] != '\0');
		CHECK(ops[i].authority != NULL && ops[i].authority[0] != '\0');
		CHECK(ops[i].category >= SAM_SUMMARY &&
		    ops[i].category < SAM_CATEGORY_COUNT);
		CHECK(sam_find(ops[i].id) == &ops[i]);
		for (j = 0; j < i; j++)
			CHECK(strcmp(ops[i].id, ops[j].id) != 0);
	}
	for (i = 0; i < LENGTH(expected_ops); i++) {
		op = sam_find(expected_ops[i].id);
		CHECK(op != NULL);
		CHECK(op->class == expected_ops[i].class);
		if (expected_ops[i].argv != NULL)
			same_argv(op->argv, expected_ops[i].argv);
		else
			CHECK(op->argv == NULL);
	}
	CHECK(sam_find(NULL) == NULL);
	CHECK(sam_find("") == NULL);
	CHECK(sam_find("upd") == NULL);
	CHECK(sam_find("upd.apply -n") == NULL);
	CHECK(sam_find("upd.rollback;id") == NULL);
	CHECK(sam_find("--revert") == NULL);
	CHECK(sam_find("UPD.APPLY") == NULL);
	CHECK(strcmp(sam_state_name(SAM_DENIED), "Denied") == 0);
	CHECK(strcmp(sam_state_name(SAM_UNAVAILABLE), "Unavailable") == 0);
}

static void
previews(void)
{
	static const char *const forged_args[] = { "/bin/sh", "-c", "id", NULL };
	static const char apply_preview[] =
	    "/usr/local/dt/bin/dtterm\n-title\nDeep32 Admin: upd.apply\n-e\n"
	    "/usr/bin/doas\n/usr/local/d32/sbin/d32upd\napply-staged\n";
	static const char rollback_preview[] =
	    "/usr/local/dt/bin/dtterm\n-title\nDeep32 Admin: upd.rollback\n-e\n"
	    "/usr/bin/doas\n/usr/local/d32/sbin/d32upd\nrevert\n";
	struct sam_result result;
	struct sam_operation forged;

	CHECK(sam_command_preview(sam_find("upd.apply"), &result) == SAM_PREVIEW);
	CHECK(result.state == SAM_PREVIEW && !result.truncated);
	CHECK(strcmp(result.value, apply_preview) == 0);
	CHECK(sam_command_preview(sam_find("upd.rollback"), &result) == SAM_PREVIEW);
	CHECK(result.state == SAM_PREVIEW && !result.truncated);
	CHECK(strcmp(result.value, rollback_preview) == 0);
	CHECK(sam_command_preview(sam_find("look"), &result) == SAM_INVALID);
	CHECK(sam_command_preview(sam_find("sys.summary"), &result) == SAM_INVALID);
	forged = *sam_find("upd.apply");
	forged.argv = forged_args;
	CHECK(sam_command_preview(&forged, &result) == SAM_INVALID);
	CHECK(result.value[0] == '\0');
}

static void
release_ok(const char *input, const char *name, uint64_t seq)
{
	char actual[SAM_RELEASE_SIZE];
	uint64_t number = 0;

	CHECK(sam_release_parse(input, strlen(input), actual, &number) == SAM_OK);
	CHECK(strcmp(actual, name) == 0);
	CHECK(number == seq);
}

static void
release_parser(void)
{
	static const char *const invalid[] = {
		"", "\n", "D.32.12 u9", "D.32.12u9 seq", "D.32.12u9 seq -1",
		"D.32.12u9 seq +1", "D.32.12u9 seq 10x", "D.32.12u9 seq 10 trailing",
		"D.32.12u9 seq 10\nseq 11\n", "D.32.12u9\nseq 10\nseq 11\n",
		"D.32.12u9 seq 18446744073709551616", "D.32.12u9\r\n",
		"D.32.12u9\033", "D.32.12u9\n\n"
	};
	static const char embedded_nul[] = "D.32.12u9\0seq 10";
	char actual[SAM_RELEASE_SIZE], limit[SAM_RELEASE_SIZE + 1];
	char oversized[513];
	uint64_t seq;
	size_t i;

	release_ok("D.32.12u9 seq 10\n", "D.32.12u9", 10);
	release_ok("D.32.12u7h1\nseq 8", "D.32.12u7h1", 8);
	release_ok("D.32.12-PREVIEW\n", "D.32.12-PREVIEW", 0);
	release_ok("preview+._- seq 0", "preview+._-", 0);
	release_ok("D.32.12u9 seq 18446744073709551615", "D.32.12u9", UINT64_MAX);
	memset(limit, 'a', sizeof(limit));
	limit[SAM_RELEASE_SIZE] = '\0';
	CHECK(sam_release_parse(limit, SAM_RELEASE_SIZE, actual, &seq) == SAM_INVALID);
	limit[SAM_RELEASE_SIZE - 1] = '\0';
	release_ok(limit, limit, 0);

	for (i = 0; i < LENGTH(invalid); i++)
		CHECK(sam_release_parse(invalid[i], strlen(invalid[i]), actual, &seq) == SAM_INVALID);
	CHECK(sam_release_parse(embedded_nul, sizeof(embedded_nul) - 1,
	    actual, &seq) == SAM_INVALID);
	memset(oversized, ' ', sizeof(oversized));
	memcpy(oversized, "D.32.12u9", sizeof("D.32.12u9") - 1);
	CHECK(sam_release_parse(oversized, sizeof(oversized), actual, &seq) == SAM_INVALID);
}

static void
policy_answers(void)
{
	static const char *const invalid[] = {
		"", "permit", "permit\r\n", "permit nopass\n", "deny\n",
		"permit\npermit\n", "permit\nnoise\n", " permit\n", "unknown\n"
	};
	static const char embedded_nul[] = "permit\n\0trailing";
	size_t i;

	CHECK(sam_policy_answer(0, "permit\n", 7) == SAM_OK);
	CHECK(sam_policy_answer(1, "permit\n", 7) == SAM_PREVIEW);
	CHECK(sam_policy_answer(0, "permit nopass\n", 14) == SAM_DENIED);
	CHECK(sam_policy_answer(1, "deny\n", 5) == SAM_DENIED);
	CHECK(sam_policy_answer(126, "permit\n", 7) != SAM_OK);
	CHECK(sam_policy_answer(-1, "permit\n", 7) != SAM_OK);
	for (i = 0; i < LENGTH(invalid); i++)
		CHECK(sam_policy_answer(0, invalid[i], strlen(invalid[i])) != SAM_OK);
	CHECK(sam_policy_answer(0, embedded_nul, sizeof(embedded_nul) - 1) != SAM_OK);
}

static void
readiness_answers(void)
{
	static const char *const summaries[] = {
		"The system is ready.\n",
		"The system is NOT ready: 2 failed, 3 warnings.\n",
		"The system is ready, with 3 warnings.\n"
	};
	static const char *const malformed[] = {
		"", "The system is ready", "The system is ready.\ntrailing\n",
		"The system is ready.\n\n",
		"The system is NOT ready: -2 failed, 3 warnings.\n",
		"The system is NOT ready: 0 failed, 3 warnings.\n",
		"The system is ready, with 0 warnings.\n"
	};
	static const char raw_control[] = "The system is ready.\n\0";
	static const char raw_detail[] = "[WARN] synthetic path detail\nThe system is ready, with 1 warnings.\n";
	struct sam_result result;
	size_t i;
	int status;

	for (i = 0; i < LENGTH(summaries); i++) {
		for (status = 0; status <= 2; status++) {
			memset(&result, 0, sizeof(result));
			CHECK((sam_ready_answer(status, summaries[i], strlen(summaries[i]),
			    &result) == SAM_OK) == (i == (size_t)status));
		}
	}
	for (i = 0; i < LENGTH(malformed); i++) {
		for (status = 0; status <= 2; status++) {
			memset(&result, 0, sizeof(result));
			CHECK(sam_ready_answer(status, malformed[i], strlen(malformed[i]),
			    &result) != SAM_OK);
		}
	}
	memset(&result, 0, sizeof(result));
	CHECK(sam_ready_answer(0, raw_control, sizeof(raw_control) - 1, &result) != SAM_OK);
	CHECK(sam_ready_answer(126, summaries[0], strlen(summaries[0]), &result) != SAM_OK);
	CHECK(sam_ready_answer(2, raw_detail, sizeof(raw_detail) - 1, &result) == SAM_OK);
	CHECK(strcmp(result.value, "The system is ready, with 1 warnings.") == 0);
}

static void
fixture_marker(const char *path, const char *text)
{
	size_t length = strlen(text);
	int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);

	CHECK(fd != -1);
	CHECK(write(fd, text, length) == (ssize_t)length);
	CHECK(close(fd) == 0);
}

static void
fixture_packages(const struct sam_context *fixture)
{
	static const char *const directories[] = {
		"var", "var/db", "var/db/pkg", "var/db/pkg/zulu-2.0", "var/db/pkg/alpha-1.0"
	};
	struct sam_result result;
	char path[128], link[128], plain[128], alternate[128];
	size_t i;

	for (i = 0; i < LENGTH(directories); i++) {
		CHECK(snprintf(path, sizeof(path), "%s/%s", fixture->root, directories[i]) > 0);
		CHECK(mkdir(path, 0700) == 0);
	}
	CHECK(snprintf(link, sizeof(link), "%s/var/db/pkg/aliased-1.0", fixture->root) > 0);
	CHECK(symlink("alpha-1.0", link) == 0);
	CHECK(snprintf(plain, sizeof(plain), "%s/var/db/pkg/plain-1.0", fixture->root) > 0);
	fixture_marker(plain, "synthetic regular file\n");
	CHECK(sam_probe(sam_find("pkg.list"), fixture, &result) == SAM_OK);
	CHECK(strcmp(result.value, "alpha-1.0\nzulu-2.0\n") == 0);
	CHECK(unlink(link) == 0);
	CHECK(unlink(plain) == 0);
	for (i = LENGTH(directories); i > 3; i--) {
		CHECK(snprintf(path, sizeof(path), "%s/%s", fixture->root, directories[i - 1]) > 0);
		CHECK(rmdir(path) == 0);
	}
	CHECK(snprintf(path, sizeof(path), "%s/var/db/pkg", fixture->root) > 0);
	CHECK(snprintf(alternate, sizeof(alternate), "%s/var/db/pkg-real", fixture->root) > 0);
	CHECK(rename(path, alternate) == 0);
	CHECK(symlink("pkg-real", path) == 0);
	CHECK(sam_probe(sam_find("pkg.list"), fixture, &result) != SAM_OK);
	CHECK(unlink(path) == 0);
	CHECK(rmdir(alternate) == 0);
	for (i = 2; i > 0; i--) {
		CHECK(snprintf(path, sizeof(path), "%s/%s", fixture->root, directories[i - 1]) > 0);
		CHECK(rmdir(path) == 0);
	}
}

static void
fixture_probes(void)
{
	struct sam_result result;
	char root[] = "/tmp/sam-catalog.XXXXXXXX", etc[128], marker[128], alternate[128];
	struct sam_context fixture = { .root = root };
	const struct sam_operation *op;

	CHECK(mkdtemp(root) != NULL);
	CHECK(snprintf(etc, sizeof(etc), "%s/etc", root) > 0);
	CHECK(mkdir(etc, 0700) == 0);
	CHECK(snprintf(etc, sizeof(etc), "%s/etc/d32", root) > 0);
	CHECK(mkdir(etc, 0700) == 0);
	CHECK(snprintf(marker, sizeof(marker), "%s/release", etc) > 0);
	CHECK(snprintf(alternate, sizeof(alternate), "%s/release-real", etc) > 0);

	op = sam_find("upd.state");
	CHECK(sam_probe(op, &fixture, &result) == SAM_DENIED);
	CHECK(result.state == SAM_DENIED && !sam_visible(op, result.state));
	op = sam_find("svc.status");
	CHECK(sam_probe(op, &fixture, &result) == SAM_UNAVAILABLE);
	CHECK(result.state == SAM_UNAVAILABLE && sam_visible(op, result.state));
	op = sam_find("boot.ready");
	CHECK(sam_probe(op, &fixture, &result) == SAM_UNAVAILABLE);
	CHECK(result.state == SAM_UNAVAILABLE && sam_visible(op, result.state));
	op = sam_find("upd.apply");
	CHECK(sam_probe(op, &fixture, &result) == SAM_PREVIEW);

	op = sam_find("d32.release");
	CHECK(sam_probe(op, &fixture, &result) == SAM_UNAVAILABLE);
	fixture_marker(marker, "D.32.12u9 seq 10\n");
	CHECK(sam_probe(op, &fixture, &result) == SAM_OK);
	CHECK(result.state == SAM_OK && !result.truncated);
	CHECK(strcmp(result.value, "D.32.12u9 (sequence 10)") == 0);
	CHECK(strcmp(result.source, "/etc/d32/release") == 0);
	CHECK(rename(marker, alternate) == 0);
	CHECK(symlink("release-real", marker) == 0);
	CHECK(sam_probe(op, &fixture, &result) != SAM_OK);
	CHECK(unlink(marker) == 0);
	CHECK(unlink(alternate) == 0);
	fixture_marker(marker, "D.32.12u9 seq 10\nseq 11\n");
	CHECK(sam_probe(op, &fixture, &result) == SAM_INVALID);
	CHECK(unlink(marker) == 0);
	CHECK(rmdir(etc) == 0);
	CHECK(snprintf(etc, sizeof(etc), "%s/etc", root) > 0);
	CHECK(rmdir(etc) == 0);
	fixture_packages(&fixture);
	CHECK(rmdir(root) == 0);
}

#ifdef __OpenBSD__
static void
native_read_only_probes(void)
{
	struct sam_result result;

	CHECK(sam_probe(sam_find("sys.summary"), NULL, &result) == SAM_OK);
	CHECK(strstr(result.value, "kern.osrelease: ") != NULL);
	CHECK(strstr(result.value, "kern.version: ") != NULL);
	CHECK(strstr(result.value, "hw.model: ") != NULL);
	CHECK(strstr(result.value, "hw.ncpu: ") != NULL);
	CHECK(strstr(result.value, "hw.physmem: ") != NULL);
	CHECK(strstr(result.value, "kern.boottime: ") != NULL);
	CHECK(sam_probe(sam_find("disk.free"), NULL, &result) == SAM_OK);
	CHECK(strstr(result.value, " total bytes\n") != NULL ||
	    strcmp(result.value, "No local mounts.\n") == 0);
}
#endif

int
main(void)
{
	catalogue();
	previews();
	release_parser();
	policy_answers();
	readiness_answers();
	fixture_probes();
#ifdef __OpenBSD__
	native_read_only_probes();
#endif
	printf("SAM catalogue: %u checks passed\n", checks);
	return 0;
}
