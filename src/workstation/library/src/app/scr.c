/* scr.c: parser and validator for "d32scr 1" screen files and "d32keys 1" key files (docs/SCREEN-FORMAT.md). */
#include "scr.h"
#include "registry.h"
#include "keymap.h"
#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXTOK 24
#define TOKLEN 300

typedef struct {
	scr_set *set;
	const char *dir;
	const char *top;      /* the file scr_load was given (screens and menus remember it) */
	char err[300];
	int depth;
	int have_header;
	int located;          /* the error already names its file and line */
	/* parse state */
	scr_screen *screen;   /* current screen or block */
	scr_control *ctl;     /* current control */
	scr_menu *menu;       /* current menu */
} pstate;

static const char *state_names[S_NSTATES] = { "normal", "hot", "pressed", "disabled", "checked", "checked-hot",
                                              "checked-pressed", "checked-disabled" };

const char *scr_state_name(int st) { return st >= 0 && st < S_NSTATES ? state_names[st] : "?"; }

static int tokenize(const char *line, char tok[MAXTOK][TOKLEN], int *ntok)
{
	int n = 0;
	const char *p = line;
	for (;;) {
		size_t k = 0;
		while (*p && isspace((unsigned char)*p)) p++;
		if (!*p || (*p == '#' && (!p[1] || isspace((unsigned char)p[1])))) break; /* '#' + blank starts a comment; #rrggbb is a colour */
		if (n == MAXTOK) return -1;
		if (*p == '"') {
			p++;
			while (*p && *p != '"') {
				if (*p == '\\' && p[1]) p++;
				if (k + 1 >= TOKLEN) return -1;
				tok[n][k++] = *p++;
			}
			if (*p != '"') return -1;
			p++;
		} else {
			while (*p && !isspace((unsigned char)*p)) {
				if (k + 1 >= TOKLEN) return -1;
				tok[n][k++] = *p++;
			}
		}
		tok[n][k] = 0;
		n++;
	}
	*ntok = n;
	return 0;
}

static int num(const char *s, int *v)
{
	char *e;
	long x = strtol(s, &e, 0);
	if (!*s || *e || x < -100000 || x > 100000) return -1;
	*v = (int)x;
	return 0;
}

int scr_parse_colour(const char *s, long *v)
{
	char *e;
	long x;
	if (!s) return -1;
	if (!strncmp(s, "ui:", 3)) {
		if (!isdigit((unsigned char)s[3])) return -1;
		x = strtol(s + 3, &e, 10);
		if (*e || x < 0 || x > 255) return -1;
		*v = SCR_UI(x);
		return 0;
	}
	if (s[0] != '#' || strlen(s) != 7) return -1;
	x = strtol(s + 1, &e, 16);
	if (*e) return -1;
	*v = x;
	return 0;
}

static int colour(const char *s, long *v) { return scr_parse_colour(s, v); }

long scr_colour(long v, const long *pal, int npal)
{
	if (!SCR_IS_UI(v)) return v;
	v -= SCR_UI_BASE;
	return pal && v < npal ? pal[v] : -1;
}

static int resolve_one(long *v, const long *pal, int npal, const char *where, char *err, size_t errlen)
{
	long r;
	if (!SCR_IS_UI(*v)) return 0;
	r = scr_colour(*v, pal, npal);
	if (r < 0) { snprintf(err, errlen, "%s: colour ui:%ld is not in the store's palette", where, *v - SCR_UI_BASE); return -1; }
	*v = r;
	return 0;
}

int scr_resolve_colours(scr_set *set, const long *pal, int npal, char *err, size_t errlen)
{
	int i, k, b;
	char where[100];
	for (b = 0; b < 2; b++) {
		scr_screen *arr = b ? set->blocks : set->s;
		int n = b ? set->nb : set->ns;
		for (i = 0; i < n; i++) {
			scr_screen *s = &arr[i];
			snprintf(where, sizeof where, "screen %s", s->name);
			if (resolve_one(&s->ground, pal, npal, where, err, errlen)) return -1;
			for (k = 0; k < s->nc; k++) {
				snprintf(where, sizeof where, "%s.%s", s->name, s->c[k].id);
				if (resolve_one(&s->c[k].fg, pal, npal, where, err, errlen) || resolve_one(&s->c[k].bg, pal, npal, where, err, errlen) ||
				    resolve_one(&s->c[k].mfg, pal, npal, where, err, errlen) || resolve_one(&s->c[k].ibg, pal, npal, where, err, errlen)) return -1;
			}
		}
	}
	return 0;
}

static int copy(char *dst, size_t n, const char *src)
{
	if (strlen(src) >= n) return -1;
	memcpy(dst, src, strlen(src) + 1);
	return 0;
}

/* grows *arr (of *cap elements of size sz) to hold n + 1; 0, or -1 when out of memory */
static int grow(void **arr, int *cap, int n, size_t sz)
{
	void *na;
	int nc;
	if (n < *cap) return 0;
	nc = *cap ? *cap * 2 : 16;
	na = realloc(*arr, (size_t)nc * sz);
	if (!na) return -1;
	*arr = na;
	*cap = nc;
	return 0;
}

static void control_defaults(scr_control *c)
{
	int i;
	memset(c, 0, sizeof(*c));
	for (i = 0; i < S_NSTATES; i++) c->frame_of[i] = -1;
	c->fg = c->bg = c->mfg = c->ibg = -1;
	c->frames = 1;
	c->kind = K_BUTTON;
	c->font = F_UI;
	c->align = AL_CENTRE;
	c->text_x = -1;
	c->text_y = -1;
}

static int lookup(const char *s, const char *const *names, int n)
{
	int i;
	for (i = 0; i < n; i++)
		if (!strcmp(s, names[i])) return i;
	return -1;
}

static const char *const font_names[] = { "ui", "ui-bold", "small", "title", "body", "body-bold", "fixed", "ui10", "ui13", "ui-bold13", "serif12",
	"body-italic", "body-bold-italic", "body-small", "fixed-small", "symbol",
	"heading", "heading-italic", "subheading", "subheading-italic", "heading3", "reader-ui", "reader-ui-bold" };
static const char *const align_names[] = { "left", "centre", "right" };

const char *scr_font_name(int font) { return font >= 0 && font < F_ART_SIZE ? font_names[font] : "?"; }

/* an action name: a registered action, or a named one (a dot in the name) declared for its notice fallback */
static int action_id(const char *name)
{
	int v = reg_find(REG_ACTION, name);
	if (v < 0 && strchr(name, '.')) v = reg_declare(REG_ACTION, name);
	return v;
}

/* the argument rule for action v given nargs arguments: built-ins that need one (goto, menu, notice) and named
 * actions (their argument is the notice's title until a module builds them) take exactly one; the others at most
 * one. NULL when nargs is fine, else the reason. */
static const char *action_args(int v, int nargs)
{
	const reg_entry *e = reg_get(REG_ACTION, v);
	int need = e && (e->flags & (REG_A_ARG | REG_A_NAMED));
	if (need && nargs != 1) return "needs one argument";
	if (nargs > 1) return "takes one argument at most";
	return NULL;
}

int scr_action_is_notice(int action)
{
	const reg_entry *e = reg_get(REG_ACTION, action);
	return action == A_NOTICE || (e && (e->flags & REG_A_NAMED));
}

static char *dupstr(const char *s)
{
	size_t n = strlen(s) + 1;
	char *d = (char *)malloc(n);
	if (d) memcpy(d, s, n);
	return d;
}

/* "prop <name> <value>" and "data <value>..." on a control or a screen */
static int add_prop(pstate *ps, scr_prop **props, int *nprops, char t[MAXTOK][TOKLEN], int n)
{
	scr_prop *np;
	int i;
	if (n != 3 || !t[1][0]) { snprintf(ps->err, sizeof ps->err, "prop needs <name> <value>"); return -1; }
	for (i = 0; i < *nprops; i++)
		if (!strcmp((*props)[i].name, t[1])) { snprintf(ps->err, sizeof ps->err, "duplicate prop '%s'", t[1]); return -1; }
	np = (scr_prop *)realloc(*props, (size_t)(*nprops + 1) * sizeof(scr_prop));
	if (!np) { snprintf(ps->err, sizeof ps->err, "out of memory"); return -1; }
	*props = np;
	np[*nprops].name = dupstr(t[1]);
	np[*nprops].value = dupstr(t[2]);
	if (!np[*nprops].name || !np[*nprops].value) { snprintf(ps->err, sizeof ps->err, "out of memory"); return -1; }
	(*nprops)++;
	return 0;
}

static int add_row(pstate *ps, scr_row **rows, int *nrows, char t[MAXTOK][TOKLEN], int n)
{
	scr_row *nr;
	int i;
	if (n < 2) { snprintf(ps->err, sizeof ps->err, "data needs at least one value"); return -1; }
	nr = (scr_row *)realloc(*rows, (size_t)(*nrows + 1) * sizeof(scr_row));
	if (!nr) { snprintf(ps->err, sizeof ps->err, "out of memory"); return -1; }
	*rows = nr;
	nr[*nrows].n = n - 1;
	nr[*nrows].v = (char **)calloc((size_t)(n - 1), sizeof(char *));
	if (!nr[*nrows].v) { snprintf(ps->err, sizeof ps->err, "out of memory"); return -1; }
	for (i = 1; i < n; i++)
		if (!(nr[*nrows].v[i - 1] = dupstr(t[i]))) { snprintf(ps->err, sizeof ps->err, "out of memory"); return -1; }
	(*nrows)++;
	return 0;
}

static int parse_file(pstate *ps, const char *dir, const char *file);

#define ERR(...) do { snprintf(ps->err, sizeof ps->err, __VA_ARGS__); return -1; } while (0)

static int control_line(pstate *ps, char t[MAXTOK][TOKLEN], int n)
{
	scr_control *c = ps->ctl;
	int v;
	const char *k = t[0];
	if (!strcmp(k, "kind")) {
		if (n != 2 || (v = reg_find(REG_KIND, t[1])) < 0) ERR("bad kind");
		c->kind = v;
	} else if (!strcmp(k, "rect")) {
		if (n != 5 || num(t[1], &c->x) || num(t[2], &c->y) || num(t[3], &c->w) || num(t[4], &c->h) || c->w <= 0 || c->h <= 0)
			ERR("rect needs x y w h");
	} else if (!strcmp(k, "record")) {
		if (n != 2 || num(t[1], &c->record) || c->record <= 0) ERR("bad record id");
	} else if (!strcmp(k, "strip")) {
		if (n != 3 || copy(c->strip, sizeof c->strip, t[1]) || num(t[2], &c->frames) || c->frames < 1 || c->frames > 16)
			ERR("strip needs <asset> <frames 1-16>");
	} else if (!strcmp(k, "frame")) {
		int st;
		if (n != 3 || (st = lookup(t[1], state_names, S_NSTATES)) < 0 || num(t[2], &v) || v < 0) ERR("frame needs <state> <index>");
		c->frame_of[st] = v;
	} else if (!strcmp(k, "label")) {
		if (n != 2) ERR("label needs one argument");
		if (t[1][0] == '@') { if (num(t[1] + 1, &c->label_sid) || c->label_sid <= 0) ERR("bad string id"); }
		else if (copy(c->label, sizeof c->label, t[1])) ERR("label too long");
	} else if (!strcmp(k, "font")) {
		if (n != 2 || (v = lookup(t[1], font_names, F_ART_SIZE)) < 0) ERR("bad font");
		c->font = v;
	} else if (!strcmp(k, "align")) {
		if (n != 2 || (v = lookup(t[1], align_names, 3)) < 0) ERR("bad align");
		c->align = v;
	} else if (!strcmp(k, "fg") || !strcmp(k, "bg")) {
		if (n != 2 || colour(t[1], k[0] == 'f' ? &c->fg : &c->bg)) ERR("colour must be ui:<n> or #rrggbb");
	} else if (!strcmp(k, "marker-fg")) {
		if (n != 2 || colour(t[1], &c->mfg)) ERR("marker-fg must be ui:<n> or #rrggbb");
	} else if (!strcmp(k, "inactive-bg")) {
		if (n != 2 || colour(t[1], &c->ibg)) ERR("inactive-bg must be ui:<n> or #rrggbb");
	} else if (!strcmp(k, "group")) {
		if (n != 2 || copy(c->group, sizeof c->group, t[1])) ERR("bad group");
	} else if (!strcmp(k, "enabled-by")) {
		if (n != 2 || copy(c->enabled_by, sizeof c->enabled_by, t[1])) ERR("bad enabled-by");
	} else if (!strcmp(k, "checked")) {
		c->checked = 1;
	} else if (!strcmp(k, "disabled")) {
		c->disabled = 1;
	} else if (!strcmp(k, "marker")) {
		c->marker = 1;
	} else if (!strcmp(k, "absolute")) {
		c->absolute = 1;
	} else if (!strcmp(k, "upper")) {
		c->upper = 1;
	} else if (!strcmp(k, "sunken")) {
		c->sunken = 1;
	} else if (!strcmp(k, "text-x")) {
		if (n != 2 || num(t[1], &c->text_x) || c->text_x < 0) ERR("text-x needs a pixel inset");
	} else if (!strcmp(k, "text-y")) {
		if (n != 2 || num(t[1], &c->text_y) || c->text_y < 0) ERR("text-y needs a baseline offset");
	} else if (!strcmp(k, "pitch")) {
		if (n != 2 || num(t[1], &c->pitch) || c->pitch < 1 || c->pitch > 64) ERR("pitch needs a line pitch of 1-64 px");
	} else if (!strcmp(k, "tracking")) {
		char *e;
		double d;
		if (n != 2) ERR("tracking needs pixels");
		d = strtod(t[1], &e);
		if (*e || d < 0 || d > 20) ERR("tracking needs pixels (0-20, decimals allowed)");
		c->tracking = (int)(d * 10 + 0.5);
	} else if (!strcmp(k, "action")) {
		const char *why;
		if (n < 2 || (v = action_id(t[1])) < 0) ERR("bad action");
		c->action = v;
		if ((why = action_args(v, n - 2))) ERR("action %s %s", t[1], why);
		if (n == 3 && copy(c->arg, sizeof c->arg, t[2])) ERR("bad action argument");
	} else if (!strcmp(k, "startup")) {
		if (n != 2 || num(t[1], &c->startup_ms) || c->startup_ms <= 0) ERR("startup needs milliseconds");
	} else if (!strcmp(k, "state")) {
		if (n != 2 || copy(c->state, sizeof c->state, t[1])) ERR("bad state key");
	} else if (!strcmp(k, "category")) {
		if (n != 2 || num(t[1], &c->category) || c->category < 1 || c->category > 64) ERR("category needs 1-64");
	} else if (!strcmp(k, "key")) {
		if (n != 2 || copy(c->key, sizeof c->key, t[1])) ERR("bad key");
	} else if (!strcmp(k, "source")) {
		if (n != 2 || copy(c->source, sizeof c->source, t[1])) ERR("bad source");
	} else if (!strcmp(k, "prop")) {
		return add_prop(ps, &c->props, &c->nprops, t, n);
	} else if (!strcmp(k, "data")) {
		return add_row(ps, &c->rows, &c->nrows, t, n);
	} else if (!strcmp(k, "end")) {
		if (!c->w) ERR("control %s has no rect", c->id);
		ps->ctl = NULL;
	} else ERR("unknown control keyword '%s'", k);
	return 0;
}

static int menu_line(pstate *ps, char t[MAXTOK][TOKLEN], int n)
{
	scr_menu *m = ps->menu;
	int v;
	if (!strcmp(t[0], "record")) { if (n != 2 || num(t[1], &m->record)) ERR("bad record"); }
	else if (!strcmp(t[0], "item-height")) { if (n != 2 || num(t[1], &m->item_height) || m->item_height <= 0) ERR("bad item-height"); }
	else if (!strcmp(t[0], "width")) { if (n != 2 || num(t[1], &m->width) || m->width <= 0) ERR("bad width"); }
	else if (!strcmp(t[0], "items")) {
		if (n != 2 || (strcmp(t[1], "taxonomy") && strcmp(t[1], "taxonomy-sub") && strcmp(t[1], "record")) || copy(m->items, sizeof m->items, t[1]))
			ERR("items must be taxonomy, taxonomy-sub or record");
	}
	else if (!strcmp(t[0], "sub")) { if (n != 2 || copy(m->sub, sizeof m->sub, t[1])) ERR("bad sub"); }
	else if (!strcmp(t[0], "style")) { if (n != 2 || copy(m->style, sizeof m->style, t[1])) ERR("bad style"); }
	else if (!strcmp(t[0], "art")) { if (n != 2 || copy(m->art, sizeof m->art, t[1])) ERR("bad art"); }
	else if (!strcmp(t[0], "at")) {
		if (n != 3 || num(t[1], &m->at_x) || num(t[2], &m->at_y) || m->at_x < 0 || m->at_y < 0 || m->at_x >= SCR_FRAME_W || m->at_y >= SCR_FRAME_H)
			ERR("at needs x y inside the frame");
	} else if (!strcmp(t[0], "on")) {
		int cmd;
		if (n < 3 || num(t[1], &cmd) || (v = action_id(t[2])) < 0) ERR("on needs <command> <action> [arg]");
		if (m->non == SCR_MAX_MENU_ITEMS) ERR("too many 'on' lines");
		if (n > 4) ERR("too many arguments");
		if (action_args(v, n - 3)) ERR("on %d %s needs one argument", cmd, t[2]);
		m->on[m->non].cmd = cmd;
		m->on[m->non].action = v;
		if (n == 4 && copy(m->on[m->non].arg, sizeof m->on[m->non].arg, t[3])) ERR("argument too long");
		m->non++;
	} else if (!strcmp(t[0], "hide")) {
		if (n != 2 || m->nhide == SCR_MAX_MENU_ITEMS || num(t[1], &m->hide[m->nhide])) ERR("hide needs a command id");
		m->nhide++;
	} else if (!strcmp(t[0], "icon")) {
		if (n != 4 || m->nicon == SCR_MAX_MENU_ICONS || num(t[1], &m->icon[m->nicon].cmd) ||
		    copy(m->icon[m->nicon].asset, sizeof m->icon[m->nicon].asset, t[2]) ||
		    num(t[3], &m->icon[m->nicon].frames) || m->icon[m->nicon].frames < 1 || m->icon[m->nicon].frames > 16)
			ERR("icon needs <command> <asset> <frames>");
		m->nicon++;
	}
	else if (!strcmp(t[0], "end")) { if (!m->items[0]) ERR("menu %s has no items", m->id); ps->menu = NULL; }
	else ERR("unknown menu keyword '%s'", t[0]);
	return 0;
}

static scr_control *new_control(scr_screen *s)
{
	if (grow((void **)&s->c, &s->capc, s->nc, sizeof(scr_control))) return NULL;
	return &s->c[s->nc++];
}

static int append_block(pstate *ps, scr_screen *dst, const char *name)
{
	int i;
	for (i = 0; i < ps->set->nb; i++)
		if (!strcmp(ps->set->blocks[i].name, name)) {
			const scr_screen *b = &ps->set->blocks[i];
			int k;
			for (k = 0; k < b->nimg; k++) {
				if (grow((void **)&dst->img, &dst->capimg, dst->nimg, sizeof(scr_image))) ERR("out of memory");
				dst->img[dst->nimg++] = b->img[k];
			}
			for (k = 0; k < b->nc; k++) {
				scr_control *c = new_control(dst);
				if (!c) ERR("out of memory");
				*c = b->c[k];
			}
			return 0;
		}
	ERR("unknown block '%s'", name);
}

static int screen_line(pstate *ps, char t[MAXTOK][TOKLEN], int n)
{
	scr_screen *s = ps->screen;
	if (!strcmp(t[0], "size")) {
		if (n != 3 || num(t[1], &s->w) || num(t[2], &s->h) || s->w <= 0 || s->h <= 0) ERR("size needs w h");
	} else if (!strcmp(t[0], "origin")) {
		if (n != 3 || num(t[1], &s->ox) || num(t[2], &s->oy)) ERR("origin needs x y");
	} else if (!strcmp(t[0], "over")) {
		if (n != 2 || copy(s->over, sizeof s->over, t[1])) ERR("bad over");
	} else if (!strcmp(t[0], "ground")) {
		if (n != 2 || colour(t[1], &s->ground)) ERR("ground must be ui:<n> or #rrggbb");
	} else if (!strcmp(t[0], "image")) {
		scr_image *im;
		if (grow((void **)&s->img, &s->capimg, s->nimg, sizeof(scr_image))) ERR("out of memory");
		im = &s->img[s->nimg];
		memset(im, 0, sizeof(*im));
		if ((n != 2 && n != 4) || copy(im->asset, sizeof im->asset, t[1])) ERR("image needs <asset> [x y]");
		if (n == 4 && (num(t[2], &im->x) || num(t[3], &im->y))) ERR("bad image position");
		s->nimg++;
	} else if (!strcmp(t[0], "sound")) {
		if (n != 3 || copy(s->sound, sizeof s->sound, t[1]) || (strcmp(t[2], "once") && strcmp(t[2], "loop"))) ERR("sound needs <asset> once|loop");
		s->sound_loop = !strcmp(t[2], "loop");
	} else if (!strcmp(t[0], "uses")) {
		if (n != 2) ERR("uses needs a block name");
		return append_block(ps, s, t[1]);
	} else if (!strcmp(t[0], "prop")) {
		return add_prop(ps, &s->props, &s->nprops, t, n);
	} else if (!strcmp(t[0], "data")) {
		return add_row(ps, &s->rows, &s->nrows, t, n);
	} else if (!strcmp(t[0], "control")) {
		int i;
		if (n != 2 || strlen(t[1]) >= SCR_ID) ERR("control needs an id");
		for (i = 0; i < s->nc; i++)
			if (!strcmp(s->c[i].id, t[1])) ERR("duplicate control '%s'", t[1]);
		if (!(ps->ctl = new_control(s))) ERR("out of memory");
		control_defaults(ps->ctl);
		snprintf(ps->ctl->id, sizeof ps->ctl->id, "%s", t[1]);
	} else if (!strcmp(t[0], "end")) {
		ps->screen = NULL;
	} else ERR("unknown screen keyword '%s'", t[0]);
	return 0;
}

/* "include store:<path>": a fragment the importer wrote into the asset store (a relative path, no "..") */
static int include_store(pstate *ps, const char *path)
{
	const char *p;
	if (!ps->set->store[0]) ERR("include store: needs the asset store (none given)");
	if (!path[0] || path[0] == '/' || strstr(path, "..")) ERR("include store: takes a relative path inside the store");
	for (p = path; *p; p++)
		if (!isalnum((unsigned char)*p) && !strchr("/._-", *p)) ERR("include store: path has a character outside [A-Za-z0-9/._-]");
	return parse_file(ps, ps->set->store, path);
}

static int top_line(pstate *ps, char t[MAXTOK][TOKLEN], int n)
{
	scr_set *set = ps->set;
	if (!strcmp(t[0], "d32scr")) {
		if (n != 2 || strcmp(t[1], "1")) ERR("unsupported format version");
		ps->have_header = 1;
		return 0;
	}
	if (!ps->have_header) ERR("file must start with 'd32scr 1'");
	if (!strcmp(t[0], "include")) {
		if (n != 2) ERR("include takes a plain file name in the same directory");
		if (ps->depth >= 4) ERR("includes nested too deeply");
		if (!strncmp(t[1], "store:", 6)) return include_store(ps, t[1] + 6);
		if (strchr(t[1], '/') || t[1][0] == '.') ERR("include takes a plain file name in the same directory");
		return parse_file(ps, ps->dir, t[1]);
	}
	if (!strcmp(t[0], "screen") || !strcmp(t[0], "block")) {
		scr_screen *s;
		int blk = t[0][0] == 'b';
		if (n != 2 || strlen(t[1]) >= SCR_ID) ERR("%s needs a name", t[0]);
		if (!blk && set->ns == SCR_MAX_SCREENS) ERR("too many screens (%d at most)", SCR_MAX_SCREENS);
		if (!blk && scr_find(set, t[1])) ERR("duplicate screen '%s'", t[1]);
		if (blk ? grow((void **)&set->blocks, &set->capb, set->nb, sizeof(scr_screen)) : grow((void **)&set->s, &set->caps, set->ns, sizeof(scr_screen)))
			ERR("out of memory");
		s = blk ? &set->blocks[set->nb++] : &set->s[set->ns++];
		memset(s, 0, sizeof(*s));
		snprintf(s->name, sizeof s->name, "%s", t[1]);
		snprintf(s->file, sizeof s->file, "%s", ps->top ? ps->top : "");
		s->ground = -1;
		ps->screen = s;
		return 0;
	}
	if (!strcmp(t[0], "menu")) {
		scr_menu *m;
		if (n != 2 || strlen(t[1]) >= SCR_ID) ERR("menu needs an id");
		if (grow((void **)&set->m, &set->capm, set->nm, sizeof(scr_menu))) ERR("out of memory");
		m = &set->m[set->nm++];
		memset(m, 0, sizeof(*m));
		m->colour[0] = m->colour[1] = m->colour[2] = -1;
		m->at_x = m->at_y = -1;
		snprintf(m->id, sizeof m->id, "%s", t[1]);
		snprintf(m->file, sizeof m->file, "%s", ps->top ? ps->top : "");
		ps->menu = m;
		return 0;
	}
	ERR("unknown keyword '%s'", t[0]);
}

static int parse_file(pstate *ps, const char *dir, const char *file)
{
	char path[1024], line[1024], t[MAXTOK][TOKLEN];
	int lineno = 0, n;
	FILE *f;
	snprintf(path, sizeof path, "%s/%s", dir, file);
	f = fopen(path, "r");
	if (!f) { snprintf(ps->err, sizeof ps->err, "%s: cannot open", file); ps->located = 1; return -1; }
	ps->depth++;
	while (fgets(line, sizeof line, f)) {
		int rc;
		lineno++;
		if (!strchr(line, '\n') && !feof(f)) { snprintf(ps->err, sizeof ps->err, "%s:%d: line too long", file, lineno); ps->located = 1; goto fail; }
		if (tokenize(line, t, &n)) { snprintf(ps->err, sizeof ps->err, "%s:%d: bad quoting or too many tokens", file, lineno); ps->located = 1; goto fail; }
		if (!n) continue;
		if (ps->ctl) rc = control_line(ps, t, n);
		else if (ps->menu) rc = menu_line(ps, t, n);
		else if (ps->screen) rc = screen_line(ps, t, n);
		else rc = top_line(ps, t, n);
		if (rc) {
			char inner[300];
			if (ps->located) goto fail; /* an include already located it */
			snprintf(inner, sizeof inner, "%s", ps->err);
			snprintf(ps->err, sizeof ps->err, "%s:%d: %.200s", file, lineno, inner);
			ps->located = 1;
			goto fail;
		}
	}
	fclose(f);
	ps->depth--;
	if (ps->depth == 0 && (ps->ctl || ps->menu || ps->screen)) {
		snprintf(ps->err, sizeof ps->err, "%s: missing 'end' at end of file", file);
		return -1;
	}
	return 0;
fail:
	fclose(f);
	ps->depth--;
	return -1;
}

int scr_load(scr_set *set, const char *dir, const char *file, char *err, size_t errlen)
{
	pstate ps;
	memset(&ps, 0, sizeof ps);
	ps.set = set;
	ps.dir = dir;
	ps.top = file;
	if (parse_file(&ps, dir, file)) {
		snprintf(err, errlen, "%s", ps.err);
		return -1;
	}
	return 0;
}

void scr_set_store(scr_set *set, const char *store)
{
	snprintf(set->store, sizeof set->store, "%s", store ? store : "");
}

/* the names in dir ending in ext, sorted, NULL-terminated and malloc'd (free each, then the array); NULL when the
 * directory cannot be read */
static char **list_dir(const char *dir, const char *ext, int *count)
{
	DIR *d = opendir(dir);
	struct dirent *de;
	char **v = NULL;
	int n = 0, cap = 0, i, k;
	size_t el = strlen(ext);
	*count = 0;
	if (!d) return NULL;
	if (grow((void **)&v, &cap, 0, sizeof(char *))) { closedir(d); return NULL; }
	while ((de = readdir(d))) {
		size_t len = strlen(de->d_name);
		if (de->d_name[0] == '.' || len <= el || strcmp(de->d_name + len - el, ext)) continue;
		if (grow((void **)&v, &cap, n + 1, sizeof(char *))) break;
		if (!(v[n] = dupstr(de->d_name))) break;
		n++;
	}
	closedir(d);
	for (i = 1; i < n; i++)
		for (k = i; k > 0 && strcmp(v[k - 1], v[k]) > 0; k--) { char *t = v[k]; v[k] = v[k - 1]; v[k - 1] = t; }
	v[n] = NULL;
	*count = n;
	return v;
}

int scr_load_dir(scr_set *set, const char *dir, char *err, size_t errlen)
{
	static const char *const first[] = { "main.scr", "dialogs.scr" };
	int n = 0, i, rc = 0;
	size_t f;
	char **v = list_dir(dir, ".scr", &n);
	if (!v || !n) { snprintf(err, errlen, "%s: no screen files", dir); free(v); return -1; }
	for (f = 0; f < sizeof first / sizeof first[0] && !rc; f++)
		for (i = 0; i < n; i++)
			if (!strcmp(v[i], first[f])) { rc = scr_load(set, dir, v[i], err, errlen); break; }
	for (i = 0; i < n && !rc; i++) {
		if (!strcmp(v[i], first[0]) || !strcmp(v[i], first[1])) continue;
		rc = scr_load(set, dir, v[i], err, errlen);
	}
	for (i = 0; i < n; i++) free(v[i]);
	free(v);
	return rc;
}

static int keys_file(scr_set *set, const char *dir, const char *file, char *err, size_t errlen)
{
	char path[1024], line[1024], t[MAXTOK][TOKLEN], canon[SCR_ID];
	int lineno = 0, n, header = 0, v, i;
	FILE *f;
	snprintf(path, sizeof path, "%s/%s", dir, file);
	if (!(f = fopen(path, "r"))) { snprintf(err, errlen, "%s: cannot open", file); return -1; }
	while (fgets(line, sizeof line, f)) {
		scr_key *k;
		const char *why;
		lineno++;
		if (!strchr(line, '\n') && !feof(f)) { snprintf(err, errlen, "%s:%d: line too long", file, lineno); goto fail; }
		if (tokenize(line, t, &n)) { snprintf(err, errlen, "%s:%d: bad quoting or too many tokens", file, lineno); goto fail; }
		if (!n) continue;
		if (!header) {
			if (n != 2 || strcmp(t[0], "d32keys") || strcmp(t[1], "1")) { snprintf(err, errlen, "%s:%d: file must start with 'd32keys 1'", file, lineno); goto fail; }
			header = 1;
			continue;
		}
		if (n < 3 || n > 4 || keymap_canon(t[0], canon, sizeof canon) < 0 || strlen(t[1]) >= SCR_ID) {
			snprintf(err, errlen, "%s:%d: a binding is <key> <scope> <action> [arg] (key as Ctrl+B, Alt+, or Control+b, scope a screen or *)", file, lineno);
			goto fail;
		}
		if ((v = action_id(t[2])) < 0) { snprintf(err, errlen, "%s:%d: bad action '%s'", file, lineno, t[2]); goto fail; }
		if ((why = action_args(v, n - 3))) { snprintf(err, errlen, "%s:%d: action %s %s", file, lineno, t[2], why); goto fail; }
		for (i = 0; i < set->nkeys; i++)
			if (!strcmp(set->keys[i].key, canon) && !strcmp(set->keys[i].scope, t[1])) {
				snprintf(err, errlen, "%s:%d: %s on %s is already bound in %s:%d", file, lineno, t[0], t[1], set->keys[i].file, set->keys[i].line);
				goto fail;
			}
		if (grow((void **)&set->keys, &set->capk, set->nkeys, sizeof(scr_key))) { snprintf(err, errlen, "out of memory"); goto fail; }
		k = &set->keys[set->nkeys++];
		memset(k, 0, sizeof(*k));
		snprintf(k->key, sizeof k->key, "%s", canon);
		snprintf(k->scope, sizeof k->scope, "%s", t[1]);
		k->action = v;
		if (n == 4) snprintf(k->arg, sizeof k->arg, "%s", t[3]);
		snprintf(k->file, sizeof k->file, "%s", file);
		k->line = lineno;
	}
	fclose(f);
	if (!header) { snprintf(err, errlen, "%s: file must start with 'd32keys 1'", file); return -1; }
	return 0;
fail:
	fclose(f);
	return -1;
}

int scr_keys_load_dir(scr_set *set, const char *dir, char *err, size_t errlen)
{
	int n = 0, i, rc = 0;
	char **v = list_dir(dir, ".keys", &n);
	for (i = 0; i < n && !rc; i++) rc = keys_file(set, dir, v[i], err, errlen);
	for (i = 0; v && i < n; i++) free(v[i]);
	free(v);
	return rc;
}

scr_screen *scr_find(scr_set *set, const char *name)
{
	int i;
	for (i = 0; i < set->ns; i++)
		if (!strcmp(set->s[i].name, name)) return &set->s[i];
	return NULL;
}

const scr_menu *scr_menu_find(const scr_set *set, const char *id)
{
	int i;
	for (i = 0; i < set->nm; i++)
		if (!strcmp(set->m[i].id, id)) return &set->m[i];
	return NULL;
}

scr_control *scr_control_find(scr_screen *s, const char *id)
{
	int i;
	for (i = 0; i < s->nc; i++)
		if (!strcmp(s->c[i].id, id)) return &s->c[i];
	return NULL;
}

const char *scr_prop_get(const scr_control *c, const char *name)
{
	int i;
	for (i = 0; c && i < c->nprops; i++)
		if (!strcmp(c->props[i].name, name)) return c->props[i].value;
	return NULL;
}

const char *scr_screen_prop(const scr_screen *s, const char *name)
{
	int i;
	for (i = 0; s && i < s->nprops; i++)
		if (!strcmp(s->props[i].name, name)) return s->props[i].value;
	return NULL;
}

int scr_validate(const scr_set *cset, char *err, size_t errlen)
{
	scr_set *set = (scr_set *)cset;
	int i, k, st;
	for (i = 0; i < set->ns; i++) {
		scr_screen *s = &set->s[i];
		if (!s->w || !s->h) { snprintf(err, errlen, "screen %s: no size", s->name); return -1; }
		if (!s->over[0] && (s->ox < 0 || s->oy < 0 || s->ox >= SCR_FRAME_W || s->oy >= SCR_FRAME_H)) {
			snprintf(err, errlen, "screen %s: origin outside the frame", s->name);
			return -1;
		}
		if (!strcmp(s->over, "*")) {
			/* drawn over whichever full screen is in front: it must fit the work area of a captioned screen */
			if (s->ox < 0 || s->oy < 0 || s->ox + s->w > SCR_FRAME_W || s->oy + s->h > SCR_FRAME_H - 16) {
				snprintf(err, errlen, "screen %s: overlay does not fit inside the work area", s->name);
				return -1;
			}
		} else if (s->over[0]) {
			scr_screen *base = scr_find(set, s->over);
			if (!base || base->over[0]) { snprintf(err, errlen, "screen %s: 'over' must name a full screen", s->name); return -1; }
			if (s->ox < 0 || s->oy < 0 || s->ox + s->w > base->w || s->oy + s->h > base->h) {
				snprintf(err, errlen, "screen %s: overlay does not fit inside %s", s->name, base->name);
				return -1;
			}
		}
		for (k = 0; k < s->nc; k++) {
			scr_control *c = &s->c[k];
			int fw = c->absolute ? SCR_FRAME_W : s->w, fh = c->absolute ? SCR_FRAME_H : s->h;
			if (c->x < 0 || c->y < 0 || c->x + c->w > fw || c->y + c->h > fh) {
				snprintf(err, errlen, "%s.%s: rect outside the screen", s->name, c->id);
				return -1;
			}
			for (st = 0; st < S_NSTATES; st++)
				if (c->frame_of[st] >= c->frames) {
					snprintf(err, errlen, "%s.%s: frame %d for %s but the strip has %d", s->name, c->id, c->frame_of[st], scr_state_name(st), c->frames);
					return -1;
				}
			if (c->action == A_GOTO && !scr_find(set, c->arg)) { snprintf(err, errlen, "%s.%s: goto unknown screen %s", s->name, c->id, c->arg); return -1; }
			if (c->action == A_MENU && !scr_menu_find(set, c->arg)) { snprintf(err, errlen, "%s.%s: unknown menu %s", s->name, c->id, c->arg); return -1; }
			if (c->enabled_by[0] && strncmp(c->enabled_by, "group:", 6) && !scr_control_find(s, c->enabled_by)) {
				snprintf(err, errlen, "%s.%s: enabled-by unknown control %s", s->name, c->id, c->enabled_by);
				return -1;
			}
			if ((c->kind == K_RADIO) && !c->group[0]) { snprintf(err, errlen, "%s.%s: a radio needs a group", s->name, c->id); return -1; }
			if (scr_action_is_notice(c->action) && !scr_find(set, "notice")) { snprintf(err, errlen, "%s.%s: notice needs a screen 'notice'", s->name, c->id); return -1; }
		}
	}
	for (i = 0; i < set->nm; i++) {
		const scr_menu *m = &set->m[i];
		if (m->sub[0] && !scr_menu_find(set, m->sub)) { snprintf(err, errlen, "menu %s: unknown sub menu %s", m->id, m->sub); return -1; }
		if (!strcmp(m->items, "record") && !m->record) { snprintf(err, errlen, "menu %s: items record needs a record", m->id); return -1; }
		for (k = 0; k < m->non; k++) {
			if (m->on[k].action == A_GOTO && !scr_find(set, m->on[k].arg)) { snprintf(err, errlen, "menu %s: goto unknown screen %s", m->id, m->on[k].arg); return -1; }
			if (m->on[k].action == A_MENU && !scr_menu_find(set, m->on[k].arg)) { snprintf(err, errlen, "menu %s: unknown menu %s", m->id, m->on[k].arg); return -1; }
			if (scr_action_is_notice(m->on[k].action) && !scr_find(set, "notice")) { snprintf(err, errlen, "menu %s: notice needs a screen 'notice'", m->id); return -1; }
		}
	}
	for (i = 0; i < set->nkeys; i++) {
		const scr_key *k2 = &set->keys[i];
		if (strcmp(k2->scope, "*") && !scr_find(set, k2->scope)) { snprintf(err, errlen, "%s:%d: key %s: unknown screen %s", k2->file, k2->line, k2->key, k2->scope); return -1; }
		if (k2->action == A_GOTO && !scr_find(set, k2->arg)) { snprintf(err, errlen, "%s:%d: key %s: goto unknown screen %s", k2->file, k2->line, k2->key, k2->arg); return -1; }
		if (k2->action == A_MENU && !scr_menu_find(set, k2->arg)) { snprintf(err, errlen, "%s:%d: key %s: unknown menu %s", k2->file, k2->line, k2->key, k2->arg); return -1; }
		if (scr_action_is_notice(k2->action) && !scr_find(set, "notice")) { snprintf(err, errlen, "%s:%d: key %s: notice needs a screen 'notice'", k2->file, k2->line, k2->key); return -1; }
	}
	return 0;
}
