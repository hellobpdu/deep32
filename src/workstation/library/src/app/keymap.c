/* keymap.c: Deep32 Library's key syntax and the keymap lint (W1-INPUT; keymap.h). X-free. */
#include "keymap.h"
#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

/* the characters a person types in a key name, and their keysym names (X11 keysymdef.h, Latin-1 block) */
static const struct { char c; const char *name; } chars[] = {
	{ ' ', "space" }, { '!', "exclam" }, { '"', "quotedbl" }, { '#', "numbersign" }, { '$', "dollar" },
	{ '%', "percent" }, { '&', "ampersand" }, { '\'', "apostrophe" }, { '(', "parenleft" }, { ')', "parenright" },
	{ '*', "asterisk" }, { '+', "plus" }, { ',', "comma" }, { '-', "minus" }, { '.', "period" }, { '/', "slash" },
	{ ':', "colon" }, { ';', "semicolon" }, { '<', "less" }, { '=', "equal" }, { '>', "greater" }, { '?', "question" },
	{ '@', "at" }, { '[', "bracketleft" }, { '\\', "backslash" }, { ']', "bracketright" }, { '^', "asciicircum" },
	{ '_', "underscore" }, { '`', "grave" }, { '{', "braceleft" }, { '|', "bar" }, { '}', "braceright" },
	{ '~', "asciitilde" },
};

/* the names people write for keys whose keysym name differs (case-insensitive), as the Library's own Help writes them */
static const struct { const char *alias, *name; } aliases[] = {
	{ "esc", "Escape" }, { "escape", "Escape" }, { "enter", "Return" }, { "return", "Return" }, { "ins", "Insert" },
	{ "insert", "Insert" }, { "del", "Delete" }, { "delete", "Delete" }, { "pgup", "Prior" }, { "pageup", "Prior" },
	{ "prior", "Prior" }, { "pgdn", "Next" }, { "pagedown", "Next" }, { "next", "Next" }, { "space", "space" },
	{ "spacebar", "space" }, { "backspace", "BackSpace" }, { "tab", "Tab" }, { "home", "Home" }, { "end", "End" },
	{ "left", "Left" }, { "right", "Right" }, { "up", "Up" }, { "down", "Down" }, { "plus", "plus" },
	{ "minus", "minus" }, { "comma", "comma" }, { "period", "period" },
};

static int prefix(const char *p, const char *mod, size_t *len)
{
	size_t m = strlen(mod);
	if (strncasecmp(p, mod, m) || p[m] != '+' || !p[m + 1]) return 0; /* "Ctrl++": the second '+' is the key */
	*len = m + 1;
	return 1;
}

int keymap_canon(const char *spec, char *out, size_t n)
{
	const char *p = spec, *name = NULL;
	char key[64];
	int mods = 0, i;
	size_t len;
	if (!spec || !out || !n) return -1;
	out[0] = 0;
	for (;;) {
		if (prefix(p, "Control", &len) || prefix(p, "Ctrl", &len)) mods |= KM_CONTROL;
		else if (prefix(p, "Alt", &len)) mods |= KM_ALT;
		else if (prefix(p, "Shift", &len)) mods |= KM_SHIFT;
		else break;
		p += len;
	}
	if (!*p || strlen(p) >= sizeof key) return -1;
	if (!p[1]) {
		/* one character: a letter (its key, lower case), a digit, or punctuation by its keysym name */
		unsigned char c = (unsigned char)p[0];
		if (isalpha(c)) { key[0] = (char)tolower(c); key[1] = 0; name = key; }
		else if (isdigit(c)) { key[0] = (char)c; key[1] = 0; name = key; }
		else {
			for (i = 0; i < (int)(sizeof chars / sizeof chars[0]); i++)
				if (chars[i].c == (char)c) name = chars[i].name;
			if (!name) return -1;
		}
	} else {
		for (i = 0; i < (int)(sizeof aliases / sizeof aliases[0]); i++)
			if (!strcasecmp(p, aliases[i].alias)) name = aliases[i].name;
		if (!name && (p[0] == 'f' || p[0] == 'F') && isdigit((unsigned char)p[1]) && strspn(p + 1, "0123456789") == strlen(p + 1)) {
			/* F1-F35 */
			snprintf(key, sizeof key, "F%s", p + 1);
			name = key;
		}
		if (!name) {
			/* an X keysym name, as written (they are case-sensitive: "BackSpace", "KP_Enter") */
			for (i = 0; p[i]; i++) if (!isalnum((unsigned char)p[i]) && p[i] != '_') return -1;
			name = p;
		}
	}
	if ((size_t)snprintf(out, n, "%s%s%s%s", mods & KM_CONTROL ? "Control+" : "", mods & KM_ALT ? "Alt+" : "",
	                     mods & KM_SHIFT ? "Shift+" : "", name) >= n) { out[0] = 0; return -1; }
	return mods;
}

const char *keymap_keyname(const char *canon)
{
	const char *p = canon;
	size_t len;
	for (;;) {
		if (prefix(p, "Control", &len) || prefix(p, "Alt", &len) || prefix(p, "Shift", &len)) p += len;
		else return p;
	}
}

void keymap_display(const char *canon, char *out, size_t n)
{
	char c[64];
	const char *k;
	int mods = keymap_canon(canon, c, sizeof c), i;
	char key[32];
	if (mods < 0) { snprintf(out, n, "%s", canon); return; }
	k = keymap_keyname(c);
	snprintf(key, sizeof key, "%s", k);
	if (!k[1] && isalpha((unsigned char)k[0])) key[0] = (char)toupper((unsigned char)k[0]);
	for (i = 0; i < (int)(sizeof chars / sizeof chars[0]); i++)
		if (!strcmp(k, chars[i].name) && chars[i].c != ' ') { key[0] = chars[i].c; key[1] = 0; }
	if (!strcmp(k, "Prior")) snprintf(key, sizeof key, "PgUp");
	else if (!strcmp(k, "Next")) snprintf(key, sizeof key, "PgDn");
	else if (!strcmp(k, "Return")) snprintf(key, sizeof key, "Enter");
	else if (!strcmp(k, "Escape")) snprintf(key, sizeof key, "Esc");
	else if (!strcmp(k, "Insert")) snprintf(key, sizeof key, "Ins");
	else if (!strcmp(k, "Delete")) snprintf(key, sizeof key, "Del");
	else if (!strcmp(k, "space")) snprintf(key, sizeof key, "Space");
	snprintf(out, n, "%s%s%s%s", mods & KM_CONTROL ? "Ctrl+" : "", mods & KM_ALT ? "Alt+" : "", mods & KM_SHIFT ? "Shift+" : "", key);
}

static void say(char *report, size_t n, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void say(char *report, size_t n, const char *fmt, ...)
{
	size_t k;
	va_list ap;
	if (!report || !n) return;
	k = strlen(report);
	if (k + 1 >= n) return;
	va_start(ap, fmt);
	vsnprintf(report + k, n - k, fmt, ap);
	va_end(ap);
}

int keymap_lint(const scr_set *set, char *report, size_t n, int *notes)
{
	int i, j, errors = 0, nn = 0;
	char ci[64], cj[64];
	if (report && n) report[0] = 0;
	for (i = 0; i < set->nkeys; i++) {
		const scr_key *a = &set->keys[i];
		if (keymap_canon(a->key, ci, sizeof ci) < 0) {
			errors++;
			say(report, n, "error: %s:%d: '%s' is not a key\n", a->file, a->line, a->key);
			continue;
		}
		for (j = i + 1; j < set->nkeys; j++) {
			const scr_key *b = &set->keys[j];
			if (keymap_canon(b->key, cj, sizeof cj) < 0 || strcmp(ci, cj)) continue;
			if (!strcmp(a->scope, b->scope)) {
				errors++;
				say(report, n, "error: %s:%d: %s bound twice on %s: here and %s:%d\n", b->file, b->line, b->key, a->scope, a->file, a->line);
			} else if (!strcmp(a->scope, "*") || !strcmp(b->scope, "*")) {
				nn++;
				say(report, n, "note: %s:%d and %s:%d: %s on one screen overrides the every-screen binding\n", a->file, a->line, b->file, b->line, b->key);
			}
		}
	}
	if (notes) *notes = nn;
	return errors;
}
