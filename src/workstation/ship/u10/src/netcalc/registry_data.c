#include "registry_data.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define REG_ROWS 16384
#define REG_TEXT (2U * 1024 * 1024)

struct reg_table {
	bool ready, previous;
	char generation[64], date[16];
	size_t count, used;
	struct calc_reg_row rows[REG_ROWS];
	char text[REG_TEXT];
};

struct geo_row {
	uint8_t family;
	char country[3];
	uint8_t first[16], last[16];
};

struct geo_table {
	bool ready, previous;
	char generation[64], date[16];
	size_t count;
	struct geo_row rows[D32_REGDATA_MAX_ROWS];
};

struct registry_data {
	struct reg_table *tables;
	struct geo_table *geo;
	d32ui_job *job;
	bool complete;
};

static const char *
copy_text(struct reg_table *table, const char *value)
{
	size_t size = strlen(value) + 1;
	if (size > sizeof(table->text) - table->used)
		return NULL;
	char *dest = table->text + table->used;
	memcpy(dest, value, size);
	table->used += size;
	return dest;
}

static bool
copy_row(struct reg_table *table, size_t index, struct d32_regdata_row row)
{
	struct calc_reg_row *dest = &table->rows[index];
	const char **fields[] = {&row.name, &row.registry, &row.status, &row.country,
	    &row.date, &row.reference, &row.detail};

	for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
		*fields[i] = copy_text(table, *fields[i]);
		if (*fields[i] == NULL)
			return false;
	}
	memcpy(dest->first, row.first, sizeof(dest->first));
	memcpy(dest->last, row.last, sizeof(dest->last));
	row.first = dest->first;
	row.last = dest->last;
	dest->value = row;
	return true;
}

static void
load_table(int root, const char *name, enum d32_regdata_dataset dataset,
    struct reg_table *table)
{
	struct d32_regdata *data;
	int fd = openat(root, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_DIRECTORY);
	if (fd == -1)
		return;
	int status = d32_regdata_load_at(fd, dataset, &data);
	close(fd);
	if (status == -1)
		return;
	const char *gen = d32_regdata_gen(data), *date = d32_regdata_source_date(data);
	size_t count = d32_regdata_count(data);
	if (count > REG_ROWS || strlen(gen) >= sizeof(table->generation) ||
	    strlen(date) >= sizeof(table->date))
		goto done;
	for (size_t i = 0; i < count; ++i) {
		struct d32_regdata_row row;
		if (!d32_regdata_get(data, i, &row) || !copy_row(table, i, row))
			goto done;
	}
	memcpy(table->generation, gen, strlen(gen) + 1);
	memcpy(table->date, date, strlen(date) + 1);
	table->previous = d32_regdata_is_previous(data);
	table->count = count;
	table->ready = true;
done:
	d32_regdata_free(data);
}

static void
load_geo(int root, struct geo_table *table)
{
	struct d32_regdata *data;
	int fd = openat(root, "geo", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_DIRECTORY);
	if (fd == -1)
		return;
	int status = d32_regdata_load_at(fd, D32_REGDATA_GEO, &data);
	close(fd);
	if (status == -1)
		return;
	size_t count = d32_regdata_count(data);
	for (size_t i = 0; i < count; ++i) {
		struct d32_regdata_row source;
		struct geo_row *row = &table->rows[i];
		if (!d32_regdata_get(data, i, &source))
			goto done;
		row->family = source.family;
		memcpy(row->country, source.country, sizeof(row->country));
		memcpy(row->first, source.first, sizeof(row->first));
		memcpy(row->last, source.last, sizeof(row->last));
	}
	snprintf(table->generation, sizeof(table->generation), "%s", d32_regdata_gen(data));
	snprintf(table->date, sizeof(table->date), "%s", d32_regdata_source_date(data));
	table->previous = d32_regdata_is_previous(data);
	table->count = count;
	table->ready = true;
done:
	d32_regdata_free(data);
}

static void
finished(int status, void *arg)
{
	struct calc *calc = arg;
	struct registry_data *data = calc->datasets;

	data->job = NULL;
	data->complete = status >= 0 && WIFEXITED(status) && WEXITSTATUS(status) == 0 &&
	    mprotect(data->tables, 2 * sizeof(*data->tables), PROT_READ) == 0;
	if (data->geo != NULL && mprotect(data->geo, sizeof(*data->geo), PROT_READ) == -1) {
		munmap(data->geo, sizeof(*data->geo));
		data->geo = NULL;
	}
	/* The child has exited. Both views can now borrow immutable rows. */
	calc_registries_refresh(calc);
	calc_multicast_refresh(calc);
}

void
calc_registry_start(struct calc *calc, const char *root)
{
	struct registry_data *data = calloc(1, sizeof(*data));
	int pipefd[2];
	pid_t pid;

	if (data == NULL)
		goto unavailable;
	/* Fork inherits the same mapping addresses. Row pointers remain valid in
	 * the parent after the worker copies them into this shared mapping. */
	data->tables = mmap(NULL, 2 * sizeof(*data->tables), PROT_READ | PROT_WRITE,
	    MAP_SHARED | MAP_ANON, -1, 0);
	if (data->tables == MAP_FAILED)
		goto free_data;
	data->geo = mmap(NULL, sizeof(*data->geo), PROT_READ | PROT_WRITE,
	    MAP_SHARED | MAP_ANON, -1, 0);
	if (data->geo == MAP_FAILED)
		data->geo = NULL;
	if (pipe(pipefd) == -1)
		goto unmap;
	pid = fork();
	if (pid == -1) {
		close(pipefd[0]);
		close(pipefd[1]);
		goto unmap;
	}
	if (pid == 0) {
		close(pipefd[0]);
		close(ConnectionNumber(XtDisplay(d32ui_shell(calc->ui))));
#ifdef __OpenBSD__
		if ((unveil(root, "r") == -1 && errno != ENOENT) ||
		    unveil(NULL, NULL) == -1 || pledge("stdio rpath", NULL) == -1)
			_exit(1);
#endif
		int fd = open(root, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_DIRECTORY);
		if (fd != -1) {
			load_table(fd, "iana", D32_REGDATA_IANA, &data->tables[0]);
			load_table(fd, "mcast", D32_REGDATA_MCAST, &data->tables[1]);
			if (data->tables[0].ready && data->geo != NULL)
				load_geo(fd, data->geo);
			close(fd);
		}
		close(pipefd[1]);
		_exit(0);
	}
	close(pipefd[1]);
	calc->datasets = data;
	data->job = d32ui_job_watch(calc->ui, pid, pipefd[0], NULL, finished, calc);
	if (data->job != NULL)
		return;
	close(pipefd[0]);
	(void)kill(pid, SIGTERM);
	while (waitpid(pid, NULL, 0) == -1 && errno == EINTR)
		continue;
	calc->datasets = NULL;
unmap:
	if (data->geo != NULL)
		munmap(data->geo, sizeof(*data->geo));
	munmap(data->tables, 2 * sizeof(*data->tables));
free_data:
	free(data);
unavailable:
	calc_registries_refresh(calc);
	calc_multicast_refresh(calc);
}

bool
calc_registry_loading(const struct calc *calc)
{
	return calc->datasets != NULL && calc->datasets->job != NULL;
}

bool
calc_registry_view(const struct calc *calc, enum d32_regdata_dataset dataset,
    struct calc_reg_view *out)
{
	const struct registry_data *data = calc->datasets;
	if (data == NULL || !data->complete ||
	    (dataset != D32_REGDATA_IANA && dataset != D32_REGDATA_MCAST))
		return false;
	const struct reg_table *table = &data->tables[dataset == D32_REGDATA_MCAST];
	if (!table->ready)
		return false;
	*out = (struct calc_reg_view){table->rows, table->count, table->generation,
	    table->date, table->previous};
	return true;
}

bool
calc_geo_snapshot(const struct calc *calc, struct calc_reg_view *out)
{
	const struct registry_data *data = calc->datasets;
	if (data == NULL || !data->complete || data->geo == NULL || !data->geo->ready)
		return false;
	const struct geo_table *table = data->geo;
	*out = (struct calc_reg_view){NULL, table->count, table->generation,
	    table->date, table->previous};
	return true;
}

enum calc_geo_result
calc_geo_country(const struct calc *calc, const d32_range *range, char country[3])
{
	const struct registry_data *data = calc->datasets;
	struct calc_reg_view iana, geo;
	uint8_t first[16] = {0}, last[16] = {0}, family = range->lo.fam;
	size_t width = family == 4 ? 4 : 16;

	country[0] = '\0';
	if (!calc_geo_snapshot(calc, &geo) || !calc_registry_view(calc, D32_REGDATA_IANA, &iana))
		return CALC_GEO_UNKNOWN;
	if ((family == 4 && range->hi.b[0] >= 0xe0) ||
	    (family == 6 && ((range->lo.b[0] & 0xe0) != 0x20 ||
	    (range->hi.b[0] & 0xe0) != 0x20)))
		return CALC_GEO_SPECIAL;
	memcpy(first + 16 - width, range->lo.b, width);
	memcpy(last + 16 - width, range->hi.b, width);
	for (size_t i = 0; i < iana.count; ++i) {
		const struct d32_regdata_row *row = &iana.rows[i].value;
		if (row->family == family && row->kind == D32_REGDATA_INTERVAL &&
		    memcmp(row->last, first, 16) >= 0 && memcmp(row->first, last, 16) <= 0)
			return CALC_GEO_SPECIAL;
	}

	/* Disjoint, sorted intervals make the first row ending at or after the
	 * query start the only possible single-country cover. */
	size_t low = 0, high = geo.count;
	while (low < high) {
		size_t mid = low + (high - low) / 2;
		const struct geo_row *row = &data->geo->rows[mid];
		if (row->family < family || (row->family == family && memcmp(row->last, first, 16) < 0))
			low = mid + 1;
		else
			high = mid;
	}
	if (low == geo.count)
		return CALC_GEO_UNKNOWN;
	const struct geo_row *row = &data->geo->rows[low];
	if (row->family != family || memcmp(row->first, last, 16) > 0)
		return CALC_GEO_UNKNOWN;
	if (memcmp(row->first, first, 16) > 0 || memcmp(row->last, last, 16) < 0)
		return CALC_GEO_MIXED;
	if (row->country[0] == '\0')
		return CALC_GEO_UNKNOWN;
	memcpy(country, row->country, sizeof(row->country));
	return CALC_GEO_COUNTRY;
}

void
calc_registry_stop(struct calc *calc)
{
	struct registry_data *data = calc->datasets;
	if (data == NULL)
		return;
	d32ui_job_cancel(data->job);
	while (data->job != NULL)
		XtAppProcessEvent(d32ui_context(calc->ui), XtIMAlternateInput | XtIMTimer);
	if (data->geo != NULL)
		munmap(data->geo, sizeof(*data->geo));
	munmap(data->tables, 2 * sizeof(*data->tables));
	free(data);
	calc->datasets = NULL;
}
