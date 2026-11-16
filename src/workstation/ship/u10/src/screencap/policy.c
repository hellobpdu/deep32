#include "screencap.h"

#include <X11/Xatom.h>
#include <X11/Xutil.h>
#include <X11/extensions/XRes.h>

#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <unistd.h>

#define REGISTRY_LIMIT 8192U
#define CLASS_LIMIT 63U
#define INSTANCE_LIMIT 127U
#define WINDOW_LIMIT 4096U
#define DEPTH_LIMIT 64U
#define CLIENT_LIMIT 256
#define STOCK_WM "/usr/local/dt/bin/dtwm"

struct registry {
	char entries[REGISTRY_LIMIT + 1];
	size_t bytes;
};

struct owner {
	bool checked, local, stock;
};

struct scan {
	Display *display;
	Atom handling, utf8, wm_state;
	const struct registry *registry;
	const struct sc_policy *policy;
	XResClient *clients;
	int client_count;
	struct owner owners[CLIENT_LIMIT];
	char *restricted;
	size_t restricted_size, visited;
};

static bool
local_process(pid_t pid, struct kinfo_proc *proc)
{
	size_t length = sizeof(*proc);
	int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid, sizeof(*proc), 1};
	uid_t uid = getuid();

	return pid > 0 && sysctl(mib, 6, proc, &length, NULL, 0) == 0 &&
	    length == sizeof(*proc) && (pid_t)proc->p_pid == pid &&
	    proc->p_uid == uid && proc->p_ruid == uid && proc->p_ustart_sec != 0;
}

static bool
client_list(Display *display, XResClient **clients, int *count)
{
	int major, minor;

	return XResQueryVersion(display, &major, &minor) &&
	    (major > 1 || (major == 1 && minor >= 2)) &&
	    XResQueryClients(display, count, clients) &&
	    *count > 0 && *count <= CLIENT_LIMIT;
}

static pid_t
client_pid(Display *display, XID base)
{
	XResClientIdSpec spec = {base, XRES_CLIENT_ID_PID_MASK};
	XResClientIdValue *ids = NULL;
	long count = 0;
	pid_t pid = -1;

	if (base == 0)
		return -1;
	if (XResQueryClientIds(display, 1, &spec, &count, &ids) == Success &&
	    count == 1 && ids != NULL && ids[0].spec.client == base &&
	    ids[0].spec.mask == XRES_CLIENT_ID_PID_MASK && ids[0].length == 4 &&
	    ids[0].value != NULL)
		pid = XResGetClientPid(&ids[0]);
	if (ids != NULL)
		XResClientIdsDestroy(count, ids);
	return pid;
}

/* KERN_FILE_TEXT is available before pledge. Match the kernel's executable
 * vnode to the fixed root-owned binary, never the client-supplied properties.
 */
static bool
stock_executable(pid_t pid, const struct stat *wm)
{
	struct kinfo_file files[128];
	size_t length = sizeof(files), i;
	int mib[] = {CTL_KERN, KERN_FILE, KERN_FILE_BYPID, pid,
	    sizeof(files[0]), 128};

	if (sysctl(mib, 6, files, &length, NULL, 0) == -1 ||
	    length % sizeof(files[0]) != 0)
		return false;
	for (i = 0; i < length / sizeof(files[0]); i++)
		if (files[i].fd_fd == KERN_FILE_TEXT && files[i].p_pid == (uint32_t)pid &&
		    files[i].va_fsid == (uint32_t)wm->st_dev && files[i].va_fileid == wm->st_ino &&
		    S_ISREG(files[i].va_mode))
			return true;
	return false;
}

void
sc_policy_init(Display *display, struct sc_policy *policy)
{
	XResClient *clients = NULL;
	struct kinfo_proc proc, after;
	struct stat wm;
	int i, count = 0;
	pid_t pid;

	memset(policy, 0, sizeof(*policy));
	if (lstat(STOCK_WM, &wm) == -1 || !S_ISREG(wm.st_mode) ||
	    wm.st_uid != 0 || (wm.st_mode & 0022) != 0 ||
	    !client_list(display, &clients, &count))
		goto out;
	for (i = 0; i < count; i++) {
		pid = client_pid(display, clients[i].resource_base);
		if (pid == policy->wm_pid || !local_process(pid, &proc) ||
		    strcmp(proc.p_comm, "dtwm") != 0 || !stock_executable(pid, &wm) ||
		    !local_process(pid, &after) || after.p_ustart_sec != proc.p_ustart_sec ||
		    after.p_ustart_usec != proc.p_ustart_usec)
			continue;
		if (policy->wm_pid != 0) {
			memset(policy, 0, sizeof(*policy));
			break;
		}
		policy->wm_pid = pid;
		policy->wm_started_sec = proc.p_ustart_sec;
		policy->wm_started_usec = proc.p_ustart_usec;
	}
out:
	if (clients != NULL)
		XFree(clients);
}

static int
window_owner(struct scan *scan, Window window)
{
	struct kinfo_proc proc;
	pid_t pid;
	int i, found = -1;

	for (i = 0; i < scan->client_count; i++)
		if ((window & ~scan->clients[i].resource_mask) == scan->clients[i].resource_base) {
			if (found != -1)
				return -1;
			found = i;
		}
	if (found == -1)
		return -1;
	if (!scan->owners[found].checked) {
		struct owner *owner = &scan->owners[found];

		owner->checked = true;
		pid = client_pid(scan->display, scan->clients[found].resource_base);
		owner->local = local_process(pid, &proc);
		owner->stock = owner->local && pid == scan->policy->wm_pid &&
		    proc.p_ustart_sec == scan->policy->wm_started_sec &&
		    proc.p_ustart_usec == scan->policy->wm_started_usec &&
		    strcmp(proc.p_comm, "dtwm") == 0;
	}
	return scan->owners[found].local ? found : -1;
}

static bool
class_token(const char *name, size_t len)
{
	size_t i;

	if (len == 0 || len > CLASS_LIMIT)
		return false;
	for (i = 0; i < len; i++) {
		unsigned char c = (unsigned char)name[i];

		if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		    (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-'))
			return false;
	}
	return true;
}

static bool
contains_class(const struct registry *registry, const char *name)
{
	size_t at;

	for (at = 0; at < registry->bytes; at += strlen(registry->entries + at) + 1)
		if (strcmp(registry->entries + at, name) == 0)
			return true;
	return false;
}

static bool
read_registry(const char *path, struct registry *registry)
{
	struct stat before, after;
	char *start, *end, *next;
	ssize_t n;
	size_t used = 0, len;
	int fd;
	bool valid = false;

	fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return false;
	if (fstat(fd, &before) == -1 || !S_ISREG(before.st_mode) ||
	    before.st_uid != 0 || (before.st_mode & 07777) != 0444 ||
	    before.st_size <= 0 || before.st_size > (off_t)REGISTRY_LIMIT)
		goto out;
	while (used < sizeof(registry->entries)) {
		n = read(fd, registry->entries + used, sizeof(registry->entries) - used);
		if (n == -1 && errno == EINTR)
			continue;
		if (n == -1)
			goto out;
		if (n == 0)
			break;
		used += (size_t)n;
	}
	if (used != (size_t)before.st_size || fstat(fd, &after) == -1 ||
	    before.st_size != after.st_size || before.st_mode != after.st_mode ||
	    before.st_uid != after.st_uid || before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
	    before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
	    before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
	    before.st_ctim.tv_nsec != after.st_ctim.tv_nsec)
		goto out;
	start = registry->entries;
	end = start + used;
	for (; start < end; start = next) {
		next = memchr(start, '\n', (size_t)(end - start));
		len = (size_t)((next == NULL ? end : next) - start);
		if (!class_token(start, len))
			goto out;
		start[len] = '\0';
		registry->bytes = (size_t)(start - registry->entries);
		if (contains_class(registry, start))
			goto out;
		next = start + len + 1;
	}
	registry->bytes = (size_t)(start - registry->entries);
	valid = true;
out:
	(void)close(fd);
	return valid;
}

static enum sc_result
public_handling(const struct scan *scan, Window window)
{
	Atom type;
	int format, status;
	unsigned long count, extra;
	unsigned char *bytes = NULL;
	bool public;

	if (scan->handling == None)
		return SC_RESTRICTED;
	status = XGetWindowProperty(scan->display, window, scan->handling, 0, 2,
	    False, AnyPropertyType, &type, &format, &count, &extra, &bytes);
	public = status == Success && (type == XA_STRING || type == scan->utf8) &&
	    type != None && format == 8 && count == 6 && extra == 0 && bytes != NULL &&
	    memcmp(bytes, "public", 6) == 0;
	if (bytes != NULL)
		XFree(bytes);
	return status != Success ? SC_WINDOW_CLOSED : public ? SC_OK : SC_RESTRICTED;
}

static enum sc_result
client_class(const struct scan *scan, Window window, char name[CLASS_LIMIT + 1])
{
	Atom type;
	int format, status;
	unsigned long count, extra;
	unsigned char *bytes = NULL, *separator;
	size_t len, i;
	enum sc_result result = SC_RESTRICTED;

	status = XGetWindowProperty(scan->display, window, XA_WM_CLASS, 0,
	    (INSTANCE_LIMIT + CLASS_LIMIT + 5) / 4, False, AnyPropertyType,
	    &type, &format, &count, &extra, &bytes);
	if (status != Success) {
		result = SC_WINDOW_CLOSED;
		goto out;
	}
	if (type != XA_STRING || format != 8 || count < 4 || extra != 0 ||
	    bytes == NULL || bytes[count - 1] != '\0')
		goto out;
	separator = memchr(bytes, '\0', count);
	if (separator == NULL || separator == bytes || separator >= bytes + count - 1 ||
	    (size_t)(separator - bytes) > INSTANCE_LIMIT)
		goto out;
	for (i = 0; i < (size_t)(separator - bytes); i++)
		if (bytes[i] < 32 || bytes[i] > 126)
			goto out;
	len = (size_t)((bytes + count - 1) - (separator + 1));
	if (!class_token((char *)separator + 1, len))
		goto out;
	memcpy(name, separator + 1, len + 1);
	result = SC_OK;
out:
	if (bytes != NULL)
		XFree(bytes);
	return result;
}

static enum sc_result
managed_client(const struct scan *scan, Window window)
{
	Atom type;
	int format, status;
	unsigned long count, extra;
	unsigned char *bytes = NULL;
	bool normal;

	status = XGetWindowProperty(scan->display, window, scan->wm_state, 0, 2,
	    False, AnyPropertyType, &type, &format, &count, &extra, &bytes);
	normal = status == Success && type == scan->wm_state && format == 32 &&
	    count == 2 && extra == 0 && bytes != NULL && ((unsigned long *)bytes)[0] == NormalState;
	if (bytes != NULL)
		XFree(bytes);
	if (status != Success)
		return SC_WINDOW_CLOSED;
	return normal ? SC_OK : SC_RESTRICTED;
}

static enum sc_result scan_children(struct scan *, Window, unsigned, int);

static enum sc_result
scan_window(struct scan *scan, Window window, unsigned depth, int parent_owner)
{
	XWindowAttributes attributes;
	Atom *properties;
	int count = 0, i, owner;
	bool stock, has_class = false, has_handling = false, has_wm_state = false;
	char name[CLASS_LIMIT + 1];
	size_t len;
	enum sc_result result;

	if (depth > DEPTH_LIMIT || ++scan->visited > WINDOW_LIMIT)
		return SC_LIMIT;
	if (!XGetWindowAttributes(scan->display, window, &attributes))
		return SC_WINDOW_CLOSED;
	if (attributes.class == InputOnly || attributes.map_state != IsViewable)
		return SC_OK;
	owner = window_owner(scan, window);
	if (owner < 0)
		return SC_RESTRICTED;
	stock = scan->owners[owner].stock;
	properties = XListProperties(scan->display, window, &count);
	for (i = 0; properties != NULL && i < count; i++) {
		if (properties[i] == scan->wm_state)
			has_wm_state = true;
		if (properties[i] == scan->handling)
			has_handling = true;
		if (properties[i] == XA_WM_CLASS)
			has_class = true;
	}
	if (properties != NULL)
		XFree(properties);
	if (has_class) {
		result = client_class(scan, window, name);
		if (result != SC_OK)
			return result;
		if (contains_class(scan->registry, name)) {
			len = strlen(name) + 1;
			if (scan->restricted_size >= len)
				memcpy(scan->restricted, name, len);
			return SC_RESTRICTED;
		}
	}
	if (has_handling || (!stock && (has_class || has_wm_state ||
	    attributes.override_redirect || owner != parent_owner))) {
		if (!has_class)
			return SC_RESTRICTED;
		result = public_handling(scan, window);
		if (result != SC_OK)
			return result;
	}
	if (has_wm_state && (result = managed_client(scan, window)) != SC_OK)
		return result;
	return scan_children(scan, window, depth, owner);
}

static enum sc_result
scan_children(struct scan *scan, Window window, unsigned depth, int parent_owner)
{
	Window root, parent, *children = NULL;
	unsigned count, i;
	enum sc_result result = SC_OK;

	if (!XQueryTree(scan->display, window, &root, &parent, &children, &count))
		return SC_WINDOW_CLOSED;
	if (count > WINDOW_LIMIT - scan->visited)
		result = SC_LIMIT;
	for (i = 0; i < count && result == SC_OK; i++)
		result = scan_window(scan, children[i], depth + 1, parent_owner);
	if (children != NULL)
		XFree(children);
	return result;
}

enum sc_result
sc_policy_check(Display *display, const struct sc_policy *policy, const char *path,
    char *restricted_class, size_t class_size)
{
	struct registry registry;
	struct scan scan = {0};
	int screen;
	enum sc_result result;

	if (class_size != 0) {
		if (restricted_class == NULL)
			return SC_ERROR;
		restricted_class[0] = '\0';
	}
	if (display == NULL || policy == NULL || path == NULL)
		return SC_ERROR;
	if (!read_registry(path, &registry))
		return SC_POLICY_UNAVAILABLE;
	if (ScreenCount(display) > (int)SC_MONITOR_LIMIT)
		return SC_LIMIT;
	scan.display = display;
	scan.handling = XInternAtom(display, "_D32_HANDLING", True);
	scan.utf8 = XInternAtom(display, "UTF8_STRING", True);
	scan.wm_state = XInternAtom(display, "WM_STATE", True);
	scan.registry = &registry;
	scan.policy = policy;
	scan.restricted = restricted_class;
	scan.restricted_size = class_size;
	if (!client_list(display, &scan.clients, &scan.client_count)) {
		result = SC_POLICY_UNAVAILABLE;
		goto out;
	}
	for (screen = 0; screen < ScreenCount(display); screen++) {
		result = scan_children(&scan, RootWindow(display, screen), 0, -1);
		if (result != SC_OK)
			goto out;
	}
	result = SC_OK;
out:
	if (scan.clients != NULL)
		XFree(scan.clients);
	return result;
}
