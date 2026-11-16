#define _POSIX_C_SOURCE 200809L
#define _BSD_SOURCE
#include "child.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef D32_ASK_CHILD
#define D32_ASK_CHILD "/usr/local/d32/bin/d32-ask"
#endif
#ifndef D32_ASK_DTTERM
#define D32_ASK_DTTERM "/usr/local/dt/bin/dtterm"
#endif

struct AskChild {
	XtAppContext app;
	XtInputId input, output, signal_input;
	XtIntervalId timeout;
	void *owner;
	void (*receive)(void *, const char *, size_t);
	void (*ended)(void *, int);
	int to_child, from_child, wake_read, wake_write;
	pid_t pid;
	int closing;
	size_t used;
	char queue[2 * 65536];
};

static volatile sig_atomic_t wake_fd = -1;

static void
notify(int signo)
{
	int saved = errno;
	char byte = 1;
	(void)signo;
	if (wake_fd != -1)
		(void)write((int)wake_fd, &byte, 1);
	errno = saved;
}

static void
close_input(AskChild *child)
{
	if (child->input) XtRemoveInput(child->input);
	child->input = 0;
	if (child->from_child != -1) close(child->from_child);
	child->from_child = -1;
}

static void
close_output(AskChild *child)
{
	if (child->output) XtRemoveInput(child->output);
	child->output = 0;
	if (child->to_child != -1) close(child->to_child);
	child->to_child = -1;
	explicit_bzero(child->queue, child->used);
	child->used = 0;
}

static void
read_child(AskChild *child, unsigned limit)
{
	char buffer[8192];
	ssize_t size;
	unsigned i;

	for (i = 0; i < limit && child->from_child != -1; i++) {
		size = read(child->from_child, buffer, sizeof buffer);
		if (size > 0) child->receive(child->owner, buffer, (size_t)size);
		else if (size == 0) close_input(child);
		else if (errno == EINTR) continue;
		else if (errno == EAGAIN || errno == EWOULDBLOCK) break;
		else close_input(child);
	}
}

static void
read_ready(XtPointer data, int *fd, XtInputId *id)
{
	(void)fd;
	(void)id;
	read_child(data, 16);
}

static void
write_ready(XtPointer data, int *fd, XtInputId *id)
{
	AskChild *child = data;
	ssize_t size;
	(void)id;
	size = write(*fd, child->queue, child->used);
	if (size > 0) {
		child->used -= (size_t)size;
		memmove(child->queue, child->queue + size, child->used);
		explicit_bzero(child->queue + child->used, (size_t)size);
	} else if (size < 0 && errno != EINTR && errno != EAGAIN &&
	    errno != EWOULDBLOCK) {
		close_output(child);
	}
	if (child->used == 0 && child->output) {
		XtRemoveInput(child->output);
		child->output = 0;
	}
}

static void
reap_ready(XtPointer data, int *fd, XtInputId *id)
{
	AskChild *child = data;
	char bytes[64];
	int status;
	pid_t pid;
	(void)id;
	while (read(*fd, bytes, sizeof bytes) > 0) {}
	while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
		if (pid != child->pid) continue;
		child->pid = 0;
		if (child->timeout) XtRemoveTimeOut(child->timeout);
		child->timeout = 0;
		read_child(child, 128);
		close_input(child);
		close_output(child);
		status = WIFEXITED(status) ? WEXITSTATUS(status) :
		    WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 1;
		child->ended(child->owner, status);
	}
}

static int
nonblocking(int fd)
{
	int flags = fcntl(fd, F_GETFL);
	return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

AskChild *
child_start(XtAppContext app, void *owner,
    void (*receive)(void *, const char *, size_t), void (*ended)(void *, int))
{
	AskChild *child = calloc(1, sizeof *child);
	int in[2] = {-1, -1}, out[2] = {-1, -1}, wake[2] = {-1, -1};
	struct sigaction action;
	pid_t pid;
	size_t i;

	if (child == NULL) return NULL;
	if (pipe2(in, O_CLOEXEC) || pipe2(out, O_CLOEXEC) ||
	    pipe2(wake, O_NONBLOCK | O_CLOEXEC) ||
	    nonblocking(in[1]) || nonblocking(out[0])) goto fail;
	memset(&action, 0, sizeof action);
	action.sa_handler = notify;
	action.sa_flags = SA_NOCLDSTOP | SA_RESTART;
	sigemptyset(&action.sa_mask);
	wake_fd = wake[1];
	if (sigaction(SIGCHLD, &action, NULL) == -1) goto fail;
	signal(SIGPIPE, SIG_IGN);
	if ((pid = fork()) < 0) goto fail;
	if (pid == 0) {
		static const char error[] = "V\t1\nX\tAsk could not start the local client.\n";
		char *const args[] = { "d32-ask", "--events", "--window-auth", NULL };
		signal(SIGPIPE, SIG_DFL);
		if (dup2(in[0], STDIN_FILENO) == -1 ||
		    dup2(out[1], STDOUT_FILENO) == -1) _exit(126);
		execv(D32_ASK_CHILD, args);
		(void)write(STDOUT_FILENO, error, sizeof error - 1);
		_exit(127);
	}
	close(in[0]);
	close(out[1]);
	child->app = app;
	child->owner = owner;
	child->receive = receive;
	child->ended = ended;
	child->pid = pid;
	child->to_child = in[1];
	child->from_child = out[0];
	child->wake_read = wake[0];
	child->wake_write = wake[1];
	child->input = XtAppAddInput(app, out[0],
	    (XtPointer)XtInputReadMask, read_ready, child);
	child->signal_input = XtAppAddInput(app, wake[0],
	    (XtPointer)XtInputReadMask, reap_ready, child);
	return child;

fail:
	wake_fd = -1;
	for (i = 0; i < 2; i++) {
		if (in[i] != -1) close(in[i]);
		if (out[i] != -1) close(out[i]);
		if (wake[i] != -1) close(wake[i]);
	}
	free(child);
	return NULL;
}

int
child_send(AskChild *child, const char *record, size_t length)
{
	if (child == NULL || child->to_child == -1 ||
	    length > sizeof child->queue - child->used) return 0;
	memcpy(child->queue + child->used, record, length);
	child->used += length;
	if (!child->output)
		child->output = XtAppAddInput(child->app, child->to_child,
		    (XtPointer)XtInputWriteMask, write_ready, child);
	return 1;
}

static void
close_timeout(XtPointer data, XtIntervalId *id)
{
	AskChild *child = data;
	(void)id;
	child->timeout = 0;
	if (child->pid <= 0) return;
	if (child->closing == 1) {
		child->closing = 2;
		(void)kill(child->pid, SIGTERM);
		child->timeout = XtAppAddTimeOut(child->app, 500, close_timeout, child);
	} else {
		(void)kill(child->pid, SIGKILL);
	}
}

void
child_close(AskChild *child)
{
	if (child == NULL || child->pid <= 0 || child->closing) return;
	child->closing = 1;
	(void)child_send(child, "Q\n", 2);
	child->timeout = XtAppAddTimeOut(child->app, 2000, close_timeout, child);
}

void
child_terminal(int detached)
{
	pid_t pid;
	char *const args[] = { "dtterm", "-title", "Ask Deep32", "-e", D32_ASK_CHILD, NULL };

	if (detached) {
		pid = fork();
		if (pid != 0) return;
		(void)setsid();
	}
	signal(SIGPIPE, SIG_DFL);
	execv(D32_ASK_DTTERM, args);
	if (detached) _exit(127);
}
