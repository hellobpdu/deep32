#include "policy.h"

#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <ifaddrs.h>
#include <net/if_dl.h>
#include <net/if_types.h>
#include <pcap.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

struct capture {
	const char *reason;
	int result;
	uint64_t deadline, bytes, maxbytes, packets;
	size_t stop_bytes;
};

static volatile sig_atomic_t interrupted;

static void
interrupt(int signo)
{
	(void)signo;
	interrupted = 1;
}

static uint64_t
now_ms(void)
{
	struct timespec ts;
	if (clock_gettime(CLOCK_MONOTONIC, &ts) == -1)
		return UINT64_MAX;
	return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

static bool
finish(struct capture *capture, int result, const char *reason)
{
	capture->result = result;
	capture->reason = reason;
	return false;
}

static bool
control(struct capture *capture)
{
	static const char stop[] = "stop\n";
	char data[sizeof(stop)];
	ssize_t count;

	count = read(STDIN_FILENO, data, sizeof(data));
	if (count < 0 && (errno == EINTR || errno == EAGAIN))
		return true;
	if (count < 0)
		return finish(capture, EX_IOERR, "control-read");
	if (count == 0)
		return finish(capture, EX_OK, "control-eof");
	if ((size_t)count > sizeof(stop) - 1 - capture->stop_bytes ||
	    memcmp(data, stop + capture->stop_bytes, (size_t)count) != 0)
		return finish(capture, EX_DATAERR, "control-frame");
	capture->stop_bytes += (size_t)count;
	if (capture->stop_bytes == sizeof(stop) - 1)
		return finish(capture, EX_OK, "stopped");
	return true;
}

static bool
wait_ready(struct capture *capture, int fd, short events)
{
	struct pollfd fds[] = {{ STDIN_FILENO, POLLIN, 0 }, { fd, events, 0 },
	    { fd == STDOUT_FILENO ? -1 : STDOUT_FILENO, 0, 0 }};
	uint64_t now;
	int rc;

	for (;;) {
		if (interrupted)
			return finish(capture, EX_OK, "signal");
		now = now_ms();
		if (now == UINT64_MAX)
			return finish(capture, EX_IOERR, "clock");
		if (now >= capture->deadline)
			return finish(capture, EX_OK, "duration");
		rc = poll(fds, 3, (int)(capture->deadline - now));
		if (rc == -1 && errno == EINTR)
			continue;
		if (rc == -1)
			return finish(capture, EX_IOERR, "poll");
		if (rc == 0)
			continue;
		if (fds[0].revents && !control(capture))
			return false;
		if (fds[2].revents & (POLLERR | POLLHUP | POLLNVAL))
			return finish(capture, EX_OK, "viewer-closed");
		if (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))
			return finish(capture, fd == STDOUT_FILENO ? EX_OK : EX_IOERR,
			    fd == STDOUT_FILENO ? "viewer-closed" : "capture-closed");
		if (fds[1].revents & events)
			return true;
	}
}

static bool
relay(struct capture *capture, const void *buffer, size_t size)
{
	const unsigned char *data = buffer;
	ssize_t count;

	if (size > capture->maxbytes - capture->bytes)
		return finish(capture, EX_OK, "byte-limit");
	while (size != 0 && wait_ready(capture, STDOUT_FILENO, POLLOUT)) {
		count = write(STDOUT_FILENO, data, size);
		if (count < 0 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (count == -1 && errno == EPIPE)
			return finish(capture, EX_OK, "viewer-closed");
		if (count <= 0)
			return finish(capture, EX_IOERR, "output-write");
		data += count;
		size -= (size_t)count;
		capture->bytes += (size_t)count;
	}
	return size == 0;
}

static unsigned
interface_index(const struct cap_interface *policy)
{
	struct ifaddrs *addresses, *entry;
	const struct sockaddr_dl *address;
	unsigned index = 0;

	if (getifaddrs(&addresses) == -1)
		return 0;
	for (entry = addresses; entry != NULL; entry = entry->ifa_next) {
		if (entry->ifa_addr == NULL || entry->ifa_addr->sa_family != AF_LINK ||
		    strcmp(entry->ifa_name, policy->name) != 0 ||
		    (entry->ifa_flags & (IFF_UP | IFF_LOOPBACK)) != IFF_UP)
			continue;
		address = (const struct sockaddr_dl *)entry->ifa_addr;
		if (address->sdl_type == IFT_ETHER && address->sdl_alen == sizeof(policy->lladdr) &&
		    memcmp(LLADDR(address), policy->lladdr, sizeof(policy->lladdr)) == 0) {
			index = address->sdl_index;
			break;
		}
	}
	freeifaddrs(addresses);
	return index;
}

static bool
prepare_streams(void)
{
	struct sigaction action = { .sa_handler = interrupt };
	struct stat input, output;
	int in_flags, out_flags;

	if (fstat(STDIN_FILENO, &input) == -1 || fstat(STDOUT_FILENO, &output) == -1 ||
	    !S_ISFIFO(input.st_mode) || !(S_ISFIFO(output.st_mode) || S_ISREG(output.st_mode)))
		return false;
	in_flags = fcntl(STDIN_FILENO, F_GETFL);
	out_flags = fcntl(STDOUT_FILENO, F_GETFL);
	return in_flags != -1 && out_flags != -1 &&
	    fcntl(STDIN_FILENO, F_SETFL, in_flags | O_NONBLOCK) != -1 &&
	    fcntl(STDOUT_FILENO, F_SETFL, out_flags | O_NONBLOCK) != -1 &&
	    sigemptyset(&action.sa_mask) == 0 &&
	    sigaction(SIGINT, &action, NULL) == 0 && sigaction(SIGTERM, &action, NULL) == 0 &&
	    sigaction(SIGHUP, &action, NULL) == 0 && signal(SIGPIPE, SIG_IGN) != SIG_ERR;
}

static bool
lock_capture(int fd)
{
	struct bpf_insn deny = BPF_STMT(BPF_RET | BPF_K, 0);
	struct bpf_program writes = { .bf_len = 1, .bf_insns = &deny };
	int flags = fcntl(fd, F_GETFL);
	unsigned int immediate = 1;

	return flags != -1 && ((flags & O_ACCMODE) == O_RDONLY ||
	    ioctl(fd, BIOCSETWF, &writes) == 0) && ioctl(fd, BIOCIMMEDIATE, &immediate) == 0 &&
	    ioctl(fd, BIOCLOCK) == 0;
}

static const char *
result_name(int result)
{
	switch (result) {
	case EX_OK: return "ok";
	case EX_USAGE: return "usage";
	case EX_DATAERR: return "bad-data";
	case EX_UNAVAILABLE: return "unavailable";
	case EX_NOPERM: return "no-permission";
	default: return "io";
	}
}

static bool
drop_capture_privileges(void)
{
	const struct passwd *user = getpwnam("_tcpdump");
	gid_t group;
	uid_t uid;

	if (user == NULL || user->pw_uid == 0 || user->pw_gid == 0)
		return false;
	group = user->pw_gid;
	uid = user->pw_uid;
	return setgroups(1, &group) == 0 && setresgid(group, group, group) == 0 &&
	    setresuid(uid, uid, uid) == 0 && pledge("stdio bpf", NULL) == 0;
}

static void
capture_packets(struct capture *capture, pcap_t *handle, int fd, int snaplen)
{
	struct pcap_file_header file = {
		.magic = 0xa1b2c3d4U, .version_major = 2, .version_minor = 4,
		.snaplen = (uint32_t)snaplen, .linktype = DLT_EN10MB
	};
	unsigned char record[16 + 65535];
	struct pcap_pkthdr *header;
	const unsigned char *bytes;
	uint32_t packet[4];
	int rc;

	if (!relay(capture, &file, sizeof(file)))
		return;
	while (capture->reason == NULL) {
		rc = pcap_next_ex(handle, &header, &bytes);
		if (rc == 0) {
			(void)wait_ready(capture, fd, POLLIN);
			continue;
		}
		if (rc != 1 || header->caplen > (uint32_t)snaplen || header->caplen > header->len ||
		    header->ts.tv_sec < 0 || (uint64_t)header->ts.tv_sec > UINT32_MAX ||
		    header->ts.tv_usec < 0 || header->ts.tv_usec >= 1000000) {
			(void)finish(capture, EX_IOERR, "capture-read");
			break;
		}
		packet[0] = (uint32_t)header->ts.tv_sec;
		packet[1] = (uint32_t)header->ts.tv_usec;
		packet[2] = header->caplen;
		packet[3] = header->len;
		memcpy(record, packet, sizeof(packet));
		memcpy(record + sizeof(packet), bytes, header->caplen);
		if (!relay(capture, record, sizeof(packet) + header->caplen))
			break;
		capture->packets++;
	}
}

int
main(int argc, char **argv)
{
	struct cap_policy policy;
	const struct cap_interface *iface = NULL;
	const char *filter = NULL, *why, *target = "none", *doas_user;
	struct pcap_stat stats = { 0 };
	struct bpf_program program;
	struct capture capture = { .result = EX_USAGE, .reason = "arguments" };
	uint64_t start = now_ms(), elapsed;
	char pcap_error[PCAP_ERRBUF_SIZE], drops[24] = "unknown";
	unsigned index, i;
	int seconds, snaplen, fd;
	bool filter_installed;
	pcap_t *handle = NULL;
	uid_t caller = getuid();
	const struct passwd *account;

	closefrom(3);
	openlog("d32-capture", LOG_PID | LOG_NDELAY, LOG_DAEMON);
	if (argc != 7 || strcmp(argv[1], "start") != 0)
		goto done;
	seconds = (int)strtonum(argv[3], 1, 3600, &why);
	if (why != NULL)
		goto done;
	snaplen = (int)strtonum(argv[4], 64, 65535, &why);
	if (why != NULL)
		goto done;
	capture.maxbytes = (uint64_t)strtonum(argv[5], 1048576, 2147483648LL, &why);
	if (why != NULL)
		goto done;
	if (geteuid() != 0 || getuid() != 0) {
		(void)finish(&capture, EX_NOPERM, "authentication");
		goto done;
	}
	doas_user = getenv("DOAS_USER");
	if (doas_user != NULL && (account = getpwnam(doas_user)) != NULL)
		caller = account->pw_uid;
	if (cap_policy_load(&policy) != CAP_POLICY_READY) {
		(void)finish(&capture, EX_UNAVAILABLE, "policy");
		goto done;
	}
	for (i = 0; i < policy.nifaces; i++)
		if (strcmp(argv[2], policy.ifaces[i].name) == 0)
			iface = &policy.ifaces[i];
	if (iface == NULL || (index = interface_index(iface)) == 0) {
		(void)finish(&capture, EX_NOPERM, "interface");
		goto done;
	}
	target = iface->name;
	if (strcmp(argv[6], "none") != 0) {
		for (i = 0; i < policy.nfilters; i++)
			if (strcmp(argv[6], policy.filters[i].id) == 0)
				filter = policy.filters[i].expression;
		if (filter == NULL) {
			(void)finish(&capture, EX_NOPERM, "filter");
			goto done;
		}
	}
	if (start == UINT64_MAX || !prepare_streams()) {
		(void)finish(&capture, EX_IOERR, "streams");
		goto done;
	}
	handle = pcap_open_live(iface->name, snaplen, 0, 100, pcap_error);
	if (handle == NULL || interface_index(iface) != index || pcap_datalink(handle) != DLT_EN10MB ||
	    pcap_setnonblock(handle, 1, pcap_error) == -1 || (fd = pcap_get_selectable_fd(handle)) == -1) {
		(void)finish(&capture, EX_UNAVAILABLE, "capture-open");
		goto done;
	}
	if (filter != NULL) {
		if (pcap_compile(handle, &program, filter, 1, PCAP_NETMASK_UNKNOWN) == -1) {
			(void)finish(&capture, EX_DATAERR, "filter-policy");
			goto done;
		}
		filter_installed = pcap_setfilter(handle, &program) == 0;
		pcap_freecode(&program);
		if (!filter_installed) {
			(void)finish(&capture, EX_IOERR, "filter-install");
			goto done;
		}
	}
	if (!lock_capture(fd) || !drop_capture_privileges()) {
		(void)finish(&capture, EX_NOPERM, "privilege-drop");
		goto done;
	}
	capture.reason = NULL;
	capture.deadline = start + (uint64_t)seconds * 1000;
	capture_packets(&capture, handle, fd, snaplen);
	if (pcap_stats(handle, &stats) == 0)
		(void)snprintf(drops, sizeof(drops), "%u", stats.ps_drop);
done:
	if (handle != NULL)
		pcap_close(handle);
	elapsed = now_ms();
	elapsed = elapsed < start || elapsed == UINT64_MAX ? 0 : elapsed - start;
	syslog(LOG_INFO, "verb=start caller_uid=%u target=%s result=%d bytes=%llu ms=%llu",
	    (unsigned)caller, target, capture.result, (unsigned long long)capture.bytes,
	    (unsigned long long)elapsed);
	fprintf(stderr, "d32: tool=capture verb=start result=%s detail=%s packets=%llu dropped=%s\n",
	    result_name(capture.result), capture.reason,
	    (unsigned long long)capture.packets, drops);
	return capture.result;
}
