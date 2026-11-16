#include "d32ui.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

#define JOB_POLL_MS 25

struct d32ui_job {
	XtAppContext app;
	XtInputId input;
	XtIntervalId timer;
	pid_t pid;
	int fd;
	int status;
	d32ui_job_data_fn data;
	d32ui_job_done_fn done;
	void *arg;
	bool input_active;
	bool timer_active;
	bool dispatching;
	bool cancelled;
	bool failed;
	bool finishing;
};

static void input_ready(XtPointer, int *, XtInputId *);
static void timer_ready(XtPointer, XtIntervalId *);

static void
arm_job(d32ui_job *job)
{
	if (job->fd >= 0 && !job->input_active) {
		job->input = XtAppAddInput(job->app, job->fd,
		    (XtPointer)XtInputReadMask, input_ready, job);
		job->input_active = true;
	}
	if (!job->timer_active) {
		job->timer = XtAppAddTimeOut(job->app, JOB_POLL_MS, timer_ready,
		    job);
		job->timer_active = true;
	}
}

static void
disarm_input(d32ui_job *job)
{
	if (job->input_active) {
		XtRemoveInput(job->input);
		job->input_active = false;
	}
}

static void
close_pipe(d32ui_job *job)
{
	disarm_input(job);
	if (job->fd >= 0) {
		(void)close(job->fd);
		job->fd = -1;
	}
}

static void
reap_child(d32ui_job *job)
{
	pid_t result;

	if (job->pid <= 0)
		return;
	result = waitpid(job->pid, &job->status, WNOHANG);
	if (result == 0 || (result == -1 && errno == EINTR))
		return;
	if (result == -1)
		job->failed = true;
	job->pid = 0;
}

static void
read_pipe(d32ui_job *job)
{
	unsigned char buffer[4096];
	ssize_t size;

	/* Keep nested Xt dispatch in the user's data callback from reentering
	 * this input source. The main dispatch rearms it after the callback.
	 */
	disarm_input(job);
	size = read(job->fd, buffer, sizeof(buffer));
	if (size > 0) {
		if (job->data != NULL)
			job->data(buffer, (size_t)size, job->arg);
		return;
	}
	if (size == -1) {
		if (errno == EINTR)
			return;
		if (errno == EAGAIN || errno == EWOULDBLOCK) {
			if (job->pid > 0)
				return;
		} else {
			job->failed = true;
		}
	}
	close_pipe(job);
}

static void
dispatch_job(d32ui_job *job, bool readable)
{
	if (job->dispatching)
		return;
	job->dispatching = true;
	reap_child(job);
	if (job->fd >= 0 && (readable || job->pid == 0))
		read_pipe(job);
	job->dispatching = false;
	if (job->pid > 0 || job->fd >= 0) {
		arm_job(job);
		return;
	}
	if (job->timer_active)
		XtRemoveTimeOut(job->timer);
	job->finishing = true;
	if (job->done != NULL)
		job->done(job->failed ? -1 : job->status, job->arg);
	free(job);
}

static void
input_ready(XtPointer arg, int *source, XtInputId *id)
{
	(void)source;
	(void)id;
	dispatch_job(arg, true);
}

static void
timer_ready(XtPointer arg, XtIntervalId *id)
{
	d32ui_job *job = arg;

	(void)id;
	job->timer_active = false;
	dispatch_job(job, false);
}

d32ui_job *
d32ui_job_watch(d32ui *ui, pid_t pid, int fd, d32ui_job_data_fn data,
    d32ui_job_done_fn done, void *arg)
{
	XtAppContext app;
	d32ui_job *job;
	pid_t reaped;
	int flags, fd_flags, error;

	if (ui == NULL || pid <= 0 || fd < 0 || (app = d32ui_context(ui)) == NULL) {
		errno = EINVAL;
		return NULL;
	}
	flags = fcntl(fd, F_GETFL);
	if (flags == -1)
		return NULL;
	if ((flags & O_ACCMODE) == O_WRONLY) {
		errno = EINVAL;
		return NULL;
	}
	fd_flags = fcntl(fd, F_GETFD);
	if (fd_flags == -1 || (job = calloc(1, sizeof(*job))) == NULL)
		return NULL;
	if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) == -1)
		goto fail;
	if (fcntl(fd, F_SETFD, fd_flags | FD_CLOEXEC) == -1)
		goto restore_flags;
	do {
		reaped = waitpid(pid, &job->status, WNOHANG);
	} while (reaped == -1 && errno == EINTR);
	if (reaped == -1) {
		error = errno;
		(void)fcntl(fd, F_SETFD, fd_flags);
		errno = error;
		goto restore_flags;
	}
	job->app = app;
	job->pid = reaped == 0 ? pid : 0;
	job->fd = fd;
	job->data = data;
	job->done = done;
	job->arg = arg;
	arm_job(job);
	return job;

restore_flags:
	error = errno;
	(void)fcntl(fd, F_SETFL, flags);
	errno = error;
fail:
	error = errno;
	free(job);
	errno = error;
	return NULL;
}

void
d32ui_job_cancel(d32ui_job *job)
{
	if (job == NULL || job->cancelled || job->finishing)
		return;
	job->cancelled = true;
	reap_child(job);
	if (job->pid > 0)
		(void)kill(job->pid, SIGTERM);
	close_pipe(job);
}
