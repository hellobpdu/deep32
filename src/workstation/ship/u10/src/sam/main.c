#include "sam.h"
#include "d32_io.h"

#include <sys/wait.h>

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int
usage(void)
{
	fputs("usage: sam [-t | -f settings]\n", stderr);
	return 64;
}

static int
read_only(bool processes)
{
#ifdef __OpenBSD__
	return pledge(processes ? "stdio rpath proc exec" : "stdio rpath", NULL);
#else
	(void)processes;
	return 0;
#endif
}

static int
probe_mode(const char *value)
{
	struct sam_prefs prefs = { .dirfd = -1 };
	struct sam_page_view view;
	enum sam_category page;
	int result;

	if (value[0] < '0' || value[0] >= '0' + SAM_CATEGORY_COUNT || value[1] != '\0')
		return 64;
	page = (enum sam_category)(value[0] - '0');
	if (page == SAM_SUMMARY) {
		/* OpenBSD pledge excludes hw.model. This worker reads only the fixed MIBs. */
		sam_read_page(page, &prefs, &view);
#ifdef __OpenBSD__
		if (pledge("stdio", NULL) == -1)
			return 69;
#endif
	} else {
		if (read_only(page == SAM_SOFTWARE || page == SAM_BOOT) == -1)
			return 69;
		if (page == SAM_DESKTOP)
			(void)sam_prefs_open(&prefs, false);
		sam_read_page(page, &prefs, &view);
	}
	result = fwrite(&view, sizeof(view), 1, stdout) == 1 && fflush(stdout) == 0 ? 0 : 74;
	sam_prefs_close(&prefs);
	return result;
}

static int
check_mode(const char *id)
{
	const struct sam_operation *op = sam_find(id);
	bool rollback = op == sam_action(SAM_ROLLBACK);
	enum sam_revert_release release = SAM_REVERT_UNKNOWN;
	enum sam_state state;

	if (op == NULL || (op->class != SAM_DELEGATE && !rollback))
		return 64;
	if (read_only(rollback) == -1)
		return 69;
	if (rollback)
		release = sam_current_revert_release();
	state = sam_action_eligible(op, release);
	if (release == SAM_REVERT_U9 && sam_current_revert_release() != release)
		state = SAM_PREVIEW;
	sam_audit(op, state);
	return putchar((unsigned char)state) != EOF && fflush(stdout) == 0 ? 0 : 74;
}

static void
text_menu(void)
{
	unsigned i;
	puts("System Administration");
	for (i = 0; i < SAM_CATEGORY_COUNT; i++)
		printf("%u. %s\n", i + 1, sam_category_name(i));
	puts("r. Refresh the current page\nq. Quit");
}

static void
text_preview(enum sam_action_slot action)
{
	const struct sam_operation *op = sam_action(action);
	struct sam_result preview;
	if (sam_command_preview(op, &preview) != SAM_PREVIEW)
		return;
	printf("Command Preview (%s)\n%s%s\n", op->id, preview.value,
	    action == SAM_APPLY ? SAM_APPLY_REASON : sam_revert_reason(sam_current_revert_release()));
	sam_audit(op, SAM_PREVIEW);
}

static int
text_page(const char *self, enum sam_category page)
{
	struct sam_page_view view;
	struct d32_reader reader;
	bool eof, valid;
	char extra;
	int fd, status;
	pid_t child, waited;

	if (sam_spawn_page(self, page, &child, &fd) != SAM_OK)
		return 69;
	d32_reader_init(&reader, fd, sizeof(view) + 1);
	valid = d32_read_record(&reader, &view, sizeof(view), &eof) == D32E_OK && !eof &&
	    d32_read_record(&reader, &extra, 1, &eof) == D32E_OK && eof;
	(void)close(fd);
	do {
		waited = waitpid(child, &status, 0);
	} while (waited == -1 && errno == EINTR);
	if (!valid || waited != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0 ||
	    view.category != page || memchr(view.text, '\0', sizeof(view.text)) == NULL)
		return 69;
	return fputs(view.text, stdout) == EOF ? 74 : 0;
}

static int
text_mode(const char *self)
{
	enum sam_category page = SAM_SUMMARY;
	char line[64];
	size_t len;
	int ch;
	if (read_only(true) == -1)
		return 69;
	text_menu();
	for (;;) {
		fputs("Selection> ", stdout);
		if (fflush(stdout) == EOF || fgets(line, sizeof(line), stdin) == NULL)
			break;
		len = strlen(line);
		if (len != 0 && line[len - 1] == '\n')
			line[--len] = '\0';
		else if (!feof(stdin)) {
			while ((ch = getchar()) != '\n' && ch != EOF)
				;
			len = 0;
		}
		if (len != 1) {
			puts("Invalid selection.");
			continue;
		}
		ch = line[0];
		if (ch == 'q')
			break;
		if (page == SAM_SOFTWARE && (ch == 'a' || ch == 'b')) {
			text_preview(ch == 'a' ? SAM_APPLY : SAM_ROLLBACK);
			continue;
		}
		if (ch >= '1' && ch < '1' + SAM_CATEGORY_COUNT)
			page = (enum sam_category)(ch - '1');
		else if (ch != 'r') {
			puts("Invalid selection.");
			continue;
		}
		if (text_page(self, page) != 0)
			puts("Page unavailable.");
		if (page == SAM_SOFTWARE)
			puts("a. Apply Staged Update preview\nb. Roll Back Update preview");
	}
	return ferror(stdin) || ferror(stdout) ? 74 : 0;
}

static bool
local_display(const char *value)
{
	size_t n;
	if (value == NULL)
		return false;
	if (strncmp(value, "unix/", 5) == 0)
		value += 5;
	else if (strncmp(value, "unix:", 5) == 0)
		value += 4;
	if (*value++ != ':')
		return false;
	n = strspn(value, "0123456789");
	if (n == 0)
		return false;
	value += n;
	if (*value == '.') {
		n = strspn(++value, "0123456789");
		if (n == 0)
			return false;
		value += n;
	}
	return *value == '\0';
}

static bool
self_path(const char *arg, char resolved[PATH_MAX])
{
	static const char *const installed[] = {
		"/usr/local/d32/bin/sam", "/d32/active/bin/sam", "/usr/local/d32/ship/bin/sam"
	};
	size_t i;
	const char *base;
	if (strchr(arg, '/') != NULL)
		return realpath(arg, resolved) != NULL;
	for (i = 0; i < sizeof(installed) / sizeof(installed[0]); i++) {
		if (realpath(installed[i], resolved) == NULL)
			continue;
		base = strrchr(resolved, '/');
		if (base != NULL && strcmp(base + 1, "sam") == 0)
			return true;
	}
	return false;
}

int
main(int argc, char **argv)
{
	char self[PATH_MAX];
	bool settings = argc == 3 && strcmp(argv[1], "-f") == 0 && strcmp(argv[2], "settings") == 0;
	bool text = argc == 2 && strcmp(argv[1], "-t") == 0;
	bool probe = argc == 3 && strcmp(argv[1], "--probe") == 0;
	bool check = argc == 3 && strcmp(argv[1], "--check") == 0;

	if (argc != 1 && !settings && !text && !probe && !check)
		return usage();
	if (getuid() == 0 || getuid() != geteuid() || getgid() != getegid()) {
		fputs("sam: a regular user account is required\n", stderr);
		return 77;
	}
	if (probe)
		return probe_mode(argv[2]);
	if (check)
		return check_mode(argv[2]);
	if (text)
		return self_path(argv[0], self) ? text_mode(self) : 69;
	if (!local_display(getenv("DISPLAY")) || !self_path(argv[0], self)) {
		fputs("sam: a local CDE display and executable path are required\n", stderr);
		return 69;
	}
	argc = 1;
	argv[1] = NULL;
	return sam_gui(argc, argv, settings, self);
}
