#define _POSIX_C_SOURCE 200809L

#include "plan_io.h"

#include "d32_io.h"
#include "d32_json.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PLAN_FILE_LIMIT (1024U * 1024U)

const char *const calc_plan_kind_labels[] = { "Child length", "Hosts", "Reserve", "Pin" };
const char *const calc_plan_policy_labels[] = { "Exact count", "Subnet (network+broadcast reserved)",
	"Point-to-point /31", "Host /32" };
static const char *const kind_keys[] = { "child-len", "hosts", "reserve", "pin" };
static const char *const policy_keys[] = { "exact", "subnet", "point-to-point", "host" };

static bool
digit(unsigned char c)
{
	return c >= '0' && c <= '9';
}

static bool
visible(const char *s, size_t *column)
{
	bool nonspace = false;
	size_t i;

	for (i = 0; s[i]; i++) {
		if ((unsigned char)s[i] < ' ' || (unsigned char)s[i] > '~') {
			*column = i + 1;
			return false;
		}
		if (s[i] != ' ')
			nonspace = true;
	}
	*column = nonspace ? 0 : 1;
	return nonspace;
}

static d32_net_status
read_count(const char *text, d32_count *out, size_t *column)
{
	d32_count value = {0};
	const char *p = text, *end = text + strlen(text);

	while (p < end && *p == ' ')
		p++;
	while (p < end && end[-1] == ' ')
		end--;
	if (end - p == 5 && memcmp(p, "2^128", 5) == 0) {
		*out = d32_count_pow2(128);
		return D32_NET_OK;
	}
	for (; p < end; p++) {
		d32_count times_two, times_four, times_eight, addend;
		*column = (size_t)(p - text) + 1;
		if (!digit((unsigned char)*p))
			return D32_NET_ADDRESS;
		addend = (d32_count){.lo = (uint64_t)(*p - '0')};
		if (!d32_count_add(value, value, &times_two) ||
		    !d32_count_add(times_two, times_two, &times_four) ||
		    !d32_count_add(times_four, times_four, &times_eight) ||
		    !d32_count_add(times_eight, times_two, &value) ||
		    !d32_count_add(value, addend, &value))
			return D32_NET_LIMIT;
	}
	if (d32_count_compare(value, (d32_count){0}) == 0) {
		*column = 1;
		return D32_NET_LENGTH;
	}
	*out = value;
	return D32_NET_OK;
}

static d32_net_status
read_child(const char *text, uint8_t *out, size_t *column)
{
	const char *p = text, *first;
	unsigned value = 0;

	while (*p == ' ')
		p++;
	if (*p == '/')
		p++;
	first = p;
	while (digit((unsigned char)*p)) {
		value = 10 * value + (unsigned)(*p - '0');
		if (value > 128)
			break;
		p++;
	}
	if (p != first && value <= 128) {
		while (*p == ' ')
			p++;
		if (*p == '\0') {
			*out = (uint8_t)value;
			return D32_NET_OK;
		}
	}
	*column = (size_t)(p - text) + 1;
	return D32_NET_LENGTH;
}

static d32_net_status
read_request(const struct calc_plan_row *row, d32_host_policy policy,
    d32_plan_request *out, struct calc_plan_error *error)
{
	d32_net_error diagnostic = {0};
	d32_net_status status;
	const char *zone;

	if (!visible(row->name, &error->column)) {
		error->name = true;
		return D32_NET_ADDRESS;
	}
	if (!visible(row->value, &error->column))
		return D32_NET_ADDRESS;
	*out = (d32_plan_request){.kind = row->kind, .policy = policy};
	switch (row->kind) {
	case D32_PLAN_CHILD:
		return read_child(row->value, &out->length, &error->column);
	case D32_PLAN_HOSTS:
		return read_count(row->value, &out->hosts, &error->column);
	case D32_PLAN_RESERVE:
	case D32_PLAN_PIN:
		zone = strchr(row->value, '%');
		if (zone != NULL) {
			error->column = (size_t)(zone - row->value) + 1;
			return D32_NET_ZONE;
		}
		if (row->kind == D32_PLAN_RESERVE)
			status = d32_range_parse(row->value, &out->reserve, NULL, NULL, &diagnostic);
		else
			status = d32_prefix_parse(row->value, &out->pin, NULL, &diagnostic);
		error->column = diagnostic.column;
		return status;
	}
	return D32_NET_ARG;
}

d32_net_status
calc_plan_compute(struct calc_plan_model *model, struct calc_plan_error *error)
{
	*error = (struct calc_plan_error){.row = SIZE_MAX};
	if (model->count > D32_PLAN_MAX_REQUESTS)
		return error->status = D32_NET_LIMIT;
	for (size_t i = 0; i < model->count; i++) {
		error->row = i;
		error->status = read_request(&model->rows[i], model->policy,
		    &model->requests[i], error);
		if (error->status != D32_NET_OK)
			return error->status;
	}
	error->column = 0;
	return error->status = d32_plan(&model->parent, model->requests, model->count,
	    D32_PLAN_MAX_OPERATIONS, &model->result, &error->row);
}

static d32_json_id
member(const d32_json *json, d32_json_id id, const char *name)
{
	return d32_json_get(json, id, name, strlen(name));
}

static bool
string_member(const d32_json *json, d32_json_id id, const char *name, char *out, size_t cap)
{
	size_t n;
	const char *str = d32_json_string(json, member(json, id, name), &n);
	if (str == NULL || n >= cap || memchr(str, '\0', n) != NULL)
		return false;
	memcpy(out, str, n + 1);
	return true;
}

static int
key_index(const char *key, const char *const keys[4])
{
	for (int i = 0; i < 4; i++)
		if (strcmp(key, keys[i]) == 0)
			return i;
	return -1;
}

static enum d32_error
decode_plan(const d32_json *json, struct calc_plan_model *model, struct calc_plan_error *error)
{
	d32_json_id root = d32_json_root(json), requests, id;
	char context[D32_ADDR_TEXT_CAP + 4], key[32], zone[D32_ZONE_CAP];
	d32_net_error parse_error;
	size_t n;
	const char *version = d32_json_number(json, member(json, root, "d32plan"), &n);
	int index;

	if (version == NULL)
		return D32E_FORMAT;
	if (n != 1 || *version != '1')
		return D32E_VERSION;
	if (d32_json_typeof(json, root) != D32_JSON_OBJECT || d32_json_count(json, root) != 4 ||
	    !string_member(json, root, "context", context, sizeof(context)) ||
	    !string_member(json, root, "host_policy", key, sizeof(key)))
		return D32E_FORMAT;
	if (d32_prefix_parse(context, &model->parent, zone, &parse_error) != D32_NET_OK ||
	    strchr(context, '/') == NULL || zone[0] || d32_prefix_has_host_bits(&model->parent))
		return D32E_FORMAT;
	index = key_index(key, policy_keys);
	if (index < 0 || (model->parent.addr.fam == D32_IPV6) != (index == D32_HOST_RAW))
		return D32E_FORMAT;
	model->policy = (d32_host_policy)index;
	requests = member(json, root, "requests");
	if (d32_json_typeof(json, requests) != D32_JSON_ARRAY)
		return D32E_FORMAT;
	model->count = d32_json_count(json, requests);
	if (model->count > D32_PLAN_MAX_REQUESTS)
		return D32E_LIMIT;
	id = d32_json_first(json, requests);
	for (size_t i = 0; i < model->count; i++, id = d32_json_next(json, id)) {
		struct calc_plan_row *row = &model->rows[i];
		if (d32_json_typeof(json, id) != D32_JSON_OBJECT || d32_json_count(json, id) != 3 ||
		    !string_member(json, id, "kind", key, sizeof(key)) ||
		    !string_member(json, id, "name", row->name, sizeof(row->name)) ||
		    !string_member(json, id, "value", row->value, sizeof(row->value)) ||
		    (index = key_index(key, kind_keys)) < 0)
			return D32E_FORMAT;
		row->kind = (d32_plan_kind)index;
	}
	return calc_plan_compute(model, error) == D32_NET_OK ? D32E_OK : D32E_FORMAT;
}

enum d32_error
calc_plan_read(const char *path, struct calc_plan_model *model, struct calc_plan_error *error)
{
	struct stat st;
	int fd;
	enum d32_error rc = d32_open_input(path, D32_INPUT_FILE, PLAN_FILE_LIMIT, &fd, &st);
	char *data;
	size_t used = 0, cap;
	d32_json *json = NULL;
	struct calc_plan_model *next;

	*error = (struct calc_plan_error){.row = SIZE_MAX};
	if (rc != D32E_OK)
		return rc;
	cap = (size_t)st.st_size + 1;
	data = malloc(cap);
	if (data == NULL) {
		close(fd);
		return D32E_NOMEM;
	}
	while (used < cap) {
		ssize_t n = read(fd, data + used, cap - used);
		if (n < 0 && errno == EINTR)
			continue;
		if (n < 0) {
			rc = D32E_IO;
			break;
		}
		if (n == 0)
			break;
		used += (size_t)n;
	}
	close(fd);
	if (rc == D32E_OK)
		rc = used != cap - 1 ? D32E_FORMAT : d32_json_parse(data, used, &json, NULL);
	if (rc == D32E_OK) {
		next = calloc(1, sizeof(*next));
		if (next == NULL)
			rc = D32E_NOMEM;
		else {
			rc = decode_plan(json, next, error);
			if (rc == D32E_OK)
				*model = *next;
			free(next);
		}
	}
	d32_json_free(json);
	free(data);
	return rc;
}

struct output {
	char *data;
	size_t used;
};

static enum d32_error
append(void *arg, const char *bytes, size_t n)
{
	struct output *out = arg;
	if (n > PLAN_FILE_LIMIT - out->used)
		return D32E_TOOBIG;
	memcpy(out->data + out->used, bytes, n);
	out->used += n;
	return D32E_OK;
}

static enum d32_error
quoted_member(struct output *out, const char *leader, const char *value)
{
	enum d32_error rc = append(out, leader, strlen(leader));
	return rc == D32E_OK ? d32_json_quote(value, strlen(value), append, out) : rc;
}

static enum d32_error
encode_plan(const struct calc_plan_model *model, struct output *out)
{
	char address[D32_ADDR_TEXT_CAP], context[D32_ADDR_TEXT_CAP + 4];
	enum d32_error rc;

	if (d32_addr_format(&model->parent.addr, address, sizeof(address)) != D32_NET_OK)
		return D32E_FORMAT;
	(void)snprintf(context, sizeof(context), "%s/%u", address, (unsigned)model->parent.len);
	rc = quoted_member(out, "{\"context\":", context);
	if (rc == D32E_OK)
		rc = quoted_member(out, ",\"d32plan\":1,\"host_policy\":", policy_keys[model->policy]);
	if (rc == D32E_OK)
		rc = append(out, ",\"requests\":[", sizeof(",\"requests\":[") - 1);
	for (size_t i = 0; rc == D32E_OK && i < model->count; i++) {
		const struct calc_plan_row *row = &model->rows[i];
		rc = quoted_member(out, i ? ",{\"kind\":" : "{\"kind\":", kind_keys[row->kind]);
		if (rc == D32E_OK)
			rc = quoted_member(out, ",\"name\":", row->name);
		if (rc == D32E_OK)
			rc = quoted_member(out, ",\"value\":", row->value);
		if (rc == D32E_OK)
			rc = append(out, "}", 1);
	}
	return rc == D32E_OK ? append(out, "]}\n", 3) : rc;
}

static enum d32_error
open_parent(char *path, int *parent, const char **name)
{
	int fd, next;
	char *component = path + 1, *slash;
	struct stat st;

	if (path[0] != '/')
		return D32E_INVAL;
	fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (fd == -1)
		return D32E_IO;
	while ((slash = strchr(component, '/')) != NULL) {
		*slash = '\0';
		if (*component != '\0' && strcmp(component, ".") != 0) {
			if (strcmp(component, "..") == 0) {
				close(fd);
				return D32E_INVAL;
			}
			next = openat(fd, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
			close(fd);
			if (next == -1)
				return D32E_PERM;
			fd = next;
		}
		component = slash + 1;
	}
	if (fstat(fd, &st) == -1 || st.st_uid != getuid() || (st.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
		close(fd);
		return D32E_PERM;
	}
	*name = component;
	*parent = fd;
	return D32E_OK;
}

enum d32_error
calc_plan_write_at(int parent, const char *name, const struct calc_plan_model *model)
{
	struct output out = {.data = malloc(PLAN_FILE_LIMIT)};
	if (out.data == NULL)
		return D32E_NOMEM;
	enum d32_error rc = encode_plan(model, &out);
	if (rc == D32E_OK)
		rc = d32_atomic_write(parent, name, out.data, out.used, 0600, true);
	free(out.data);
	return rc;
}

enum d32_error
calc_plan_write(const char *path, const struct calc_plan_model *model)
{
	char *copy = strdup(path);
	if (copy == NULL)
		return D32E_NOMEM;
	const char *name;
	int parent;
	enum d32_error rc = open_parent(copy, &parent, &name);
	if (rc == D32E_OK) {
		rc = calc_plan_write_at(parent, name, model);
		close(parent);
	}
	free(copy);
	return rc;
}
