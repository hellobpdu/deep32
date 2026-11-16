/* records.c: original control/menu records and strings from the private asset store. */
#include "records.h"
#include "../common/le.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int load_bin(const char *store, int id, unsigned char *buf, size_t max, size_t *n)
{
	char path[1024];
	FILE *f;
	snprintf(path, sizeof path, "%s/ui/rcdata/%d.bin", store, id);
	f = fopen(path, "rb");
	if (!f) return -1;
	*n = fread(buf, 1, max, f);
	fclose(f);
	return 0;
}

int rec_control_load(const char *store, int id, rec_control *out)
{
	unsigned char b[64];
	size_t n = 0, i;
	memset(out, 0, sizeof(*out));
	if (load_bin(store, id, b, sizeof b, &n) || n < 35) return -1;
	for (i = 0; i < REC_NSLOTS; i++) out->colour[i] = (int)le32s(b + 4 * i);
	out->flags = le32(b + 28);
	out->id = le16s(b + 32);
	for (i = 34; i < n && b[i] && i - 34 < sizeof out->label - 1; i++) out->label[i - 34] = (char)b[i];
	out->bitmap_button = out->label[0] == '|' && out->label[1] == '^';
	return 0;
}

int rec_menu_load(const char *store, int id, rec_menu *out)
{
	unsigned char b[64];
	size_t n = 0;
	memset(out, 0, sizeof(*out));
	if (load_bin(store, id, b, sizeof b, &n) || n < 32) return -1;
	out->colour_a = (int)le32s(b);
	out->colour_b = (int)le32s(b + 4);
	out->colour_c = (int)le32s(b + 8);
	out->list_id = (int)le32s(b + 12);
	out->flags = le16(b + 18);
	out->item_height = le16(b + 20);
	out->width = le16(b + 22);
	out->count = le16(b + 28);
	out->margin = le16(b + 30);
	return 0;
}

const int rec_slot_default[REC_NSLOTS] = { UI_STONE, UI_BLACK, UI_STONE, UI_WHITE, UI_BLACK, UI_CHARCOAL, UI_BLACK };

int rec_palette_load(const char *store, rec_palette *p)
{
	char path[1024], line[256];
	FILE *f;
	int i;
	for (i = 0; i < REC_PAL_N; i++) p->rgb[i] = -1;
	p->n = 0;
	snprintf(path, sizeof path, "%s/ui/palette.tsv", store);
	f = fopen(path, "rb");
	if (!f) return -1;
	while (fgets(line, sizeof line, f)) {
		char *e, *hash;
		long idx, v;
		if (line[0] == '#' || line[0] == '\n') continue;
		idx = strtol(line, &e, 10);
		if (e == line || *e != '\t' || idx < 0 || idx >= REC_PAL_N) continue;
		hash = e + 1;
		if (hash[0] != '#') continue;
		v = strtol(hash + 1, &e, 16);
		if (e != hash + 7 || v < 0 || p->rgb[idx] >= 0) continue;
		p->rgb[idx] = v;
		p->n++;
	}
	fclose(f);
	return p->n == REC_PAL_N ? 0 : -1;
}

long rec_pal(const rec_palette *p, int index)
{
	if (!p || index < 0 || index >= REC_PAL_N) return -1;
	return p->rgb[index];
}

int rec_menu_items(const char *store, int list_id, rec_menu_item *items, int max)
{
	unsigned char b[2048];
	size_t n = 0, i;
	int k = 0;
	if (load_bin(store, list_id, b, sizeof b, &n)) return -1;
	for (i = 0; i + 14 <= n && k < max; i += 14) {
		if (!le16(b + i)) break;
		items[k].target_sid = le16(b + i);
		items[k].label_sid = le16(b + i + 12);
		k++;
	}
	return k;
}

int rec_menu_entries(const char *store, int list_id, int count, rec_menu_entry *e, int max)
{
	unsigned char b[2048];
	size_t n = 0, i;
	int k = 0;
	if (load_bin(store, list_id, b, sizeof b, &n)) return -1;
	for (i = 0; i + 14 <= n && k < max && k < count; i += 14) {
		e[k].sid = le16(b + i);
		e[k].cmd = le16(b + i + 12);
		/* a rule: no string, and no colours (0xFFFF, 0xFFFF: 12063, 12076, 12078, 12088), or no command but a colour
		 * (the Control menu's list 12062 gives its rule the menu's colour C, 31, and command 0; W1-CHROME). An entry
		 * of zeros is padding and ends the list. */
		e[k].rule = !e[k].sid && ((le16(b + i + 6) == 0xFFFF && le16(b + i + 8) == 0xFFFF) ||
		                          (!e[k].cmd && (le16(b + i + 6) || le16(b + i + 8))));
		if (!e[k].sid && !e[k].rule) break; /* padding: the list ends */
		k++;
	}
	return k;
}

static char *unescape(const char *s)
{
	char *o = (char *)malloc(strlen(s) + 1), *d = o;
	if (!o) return NULL;
	for (; *s; s++) {
		if (*s == '\\' && s[1]) {
			s++;
			*d++ = *s == 't' ? '\t' : *s == 'n' ? '\n' : *s == 'r' ? '\r' : *s;
		} else *d++ = *s;
	}
	*d = 0;
	return o;
}

int rec_strings_load(const char *store, rec_strings *s)
{
	char path[1024], line[4096];
	FILE *f;
	size_t cap = 0;
	memset(s, 0, sizeof(*s));
	snprintf(path, sizeof path, "%s/ui/strings.tsv", store);
	f = fopen(path, "rb");
	if (!f) return -1;
	while (fgets(line, sizeof line, f)) {
		char *tab = strchr(line, '\t'), *nl;
		if (!tab) continue;
		*tab = 0;
		if ((nl = strchr(tab + 1, '\n'))) *nl = 0;
		if (s->n == cap) {
			cap = cap ? cap * 2 : 1024;
			s->id = (int *)realloc(s->id, cap * sizeof(int));
			s->text = (char **)realloc(s->text, cap * sizeof(char *));
			if (!s->id || !s->text) { fclose(f); return -1; }
		}
		s->id[s->n] = atoi(line);
		s->text[s->n] = unescape(tab + 1);
		if (s->text[s->n]) s->n++;
	}
	fclose(f);
	return 0;
}

const char *rec_string(const rec_strings *s, int id)
{
	size_t i;
	for (i = 0; i < s->n; i++)
		if (s->id[i] == id) return s->text[i];
	return NULL;
}

void rec_strings_free(rec_strings *s)
{
	size_t i;
	for (i = 0; i < s->n; i++) free(s->text[i]);
	free(s->text);
	free(s->id);
	memset(s, 0, sizeof(*s));
}

static int alnum(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'); }

void rec_label(const char *in, char *out, size_t n, int ellipsis)
{
	size_t k = 0;
	if (!n) return;
	for (; *in && k + 1 < n; in++) {
		if (*in == '&' && in[1] == '&') { out[k++] = '&'; in++; continue; }
		if (*in == '&' && alnum(in[1])) continue; /* a mnemonic marker */
		if (*in == '|' && in[1] == '|') break;    /* "BC||f" style suffixes */
		if (ellipsis && (unsigned char)*in == 0x85) {
			if (k + 4 > n) break;
			memcpy(out + k, "...", 3);
			k += 3;
			continue;
		}
		out[k++] = *in;
	}
	out[k] = 0;
}
