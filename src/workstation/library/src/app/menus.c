/* menus.c: the menus (W1-CHROME owns this file in wave 1). The Category menu with his taxonomy and its subcategory
 * flyouts, and the menus built from the period design's own records (Features 12050, Tools 12051, the Information menu 12053,
 * and the pre-wired Control menu 12052, Views 12070, the article's text menu 12075 and the two window menus 12077 and
 * 12087): loaded from the store, drawn, hit, and their items run through action_run (a named action no room builds
 * yet shows the "coming in a later build" box).
 *
 * W1-CHROME (build 3): menus open when the pointer passes over a menu marker and flyouts when it passes over their
 * item, both gated by FlyoutsOnMousePass (the period design's "Display menus when cursor passes over menu markers", on as
 * shipped); the keyboard walks every menu (arrows, Home, End, Return, space, the items' mnemonics; menus_key, which
 * mod_chrome.c's key handler calls before input.c sees the key); and the Control menu has all three of its entries. */
#include "chrome.h"
#include <X11/keysym.h>
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void pin_menu_items(scr_menu *);
void art_zoom_items(const e26_app *, scr_menu *);
void art_font_items(const e26_app *, scr_menu *);
void art_history_items(e26_app *, scr_menu *);
static int pin_menu(const char *id)
{
	return !strcmp(id, "pin-doc-type") || !strcmp(id, "pin-doc-sort") || !strcmp(id, "pin-doc-scope") ||
	    !strcmp(id, "cisco-subtypes");
}

/* the items' mnemonics ("&Help..." gives 'h'), per menu of the set and item; menus_load fills them */
static char (*mnem)[SCR_MAX_MENU_ITEMS];
static int nmnem;
/* the open menu was opened by passing over its marker; a press on that control keeps it open */
static int pass_opened, pass_ctl = -1;
/* the keyboard is in the Category menu's flyout (Right or Return on a category) */
static int kb_sub;

/* the period design's window commands in a menu the screen file gives no action for: the Control menu (12052) holds
 * Minimize (4868) and Close Library (4870), which the period program's window procedure carried out itself */
static const struct { int cmd; const char *action; } window_cmds[] = { { 4868, "iconify" }, { 4870, "quit" } };

/* ---------------------------------------------------------------- loading */

/* the interface table entry nearest an RGB colour (least squared RGB distance, the first entry on a tie: the rule
 * GDI applies when the period design realises its palette, xassets.c "@ui") */
static int nearest_entry(e26_app *a, long rgb, long *dist)
{
	int k, best = -1;
	long bd = 0;
	for (k = 0; k < REC_PAL_N; k++) {
		long v = rec_pal(&a->pal, k), dr, dg, db, d;
		if (v < 0 || rgb < 0) continue;
		dr = ((v >> 16) & 255) - ((rgb >> 16) & 255); dg = ((v >> 8) & 255) - ((rgb >> 8) & 255); db = (v & 255) - (rgb & 255);
		d = dr * dr + dg * dg + db * db;
		if (best < 0 || d < bd) { best = k; bd = d; }
	}
	if (dist) *dist = bd;
	return best;
}

/* His colours (TAXONOMY.json "colour": face and ink). The art strips keep them exactly (his CAT and Find Wizard
 * strips carry their own palettes, as the Library's bitmaps do). The chrome a 12001-style record per category paints:
 * colour A, the Category field, both bars and the flyout's ground, is his face exactly, so that the flyout and the
 * field are the colour of his strip's selected face beside them, as the Library's are one colour (capture256 07: CAT_HISTORY
 * selected, 12004's ground and the field all entry 31; build 2 snapped A to the table, which split them in 13 of his
 * 20 categories). Colour B, the band under the pointer, which no art of his shows, is his ink snapped to the nearest
 * table entry (AUTHENTICITY.md 17.2 lists every snap and its error). */
void taxonomy_colours(e26_app *a)
{
	int i;
	for (i = 0; i < a->tax.ncat; i++) {
		tax_cat *c = &a->tax.cat[i];
		long df = 0, di = 0;
		c->colour = scr_colour(c->colour, a->pal.rgb, REC_PAL_N);
		c->ink = scr_colour(c->ink, a->pal.rgb, REC_PAL_N);
		c->face_entry = c->colour >= 0 ? nearest_entry(a, c->colour, &df) : -1;
		c->ink_entry = c->ink >= 0 ? nearest_entry(a, c->ink, &di) : -1;
		c->chrome_face = c->colour;
		c->chrome_ink = c->ink_entry >= 0 ? rec_pal(&a->pal, c->ink_entry) : -1;
		if (a->verbose)
			fprintf(stderr, "d32library: chrome %s: face #%06lX (nearest entry %d, rgb distance %.1f, not snapped), ink #%06lX -> entry %d #%06lX (%.1f)\n",
			        c->id, c->colour, c->face_entry, sqrt((double)df), c->ink, c->ink_entry, c->chrome_ink, sqrt((double)di));
	}
}

/* Menus built from the original's records ("items record"): the header's item list read from the store, its labels
 * from the string table ("Help\x85|help_main" reads "Help..."), "|Bitmap_name" items as strips of two frames, items
 * named in "hide" left out, actions from "on <command>" (anything else gets the "coming in a later build" box) */
void menus_load(e26_app *a)
{
	int i, k, n;
	free(mnem);
	nmnem = a->set.nm;
	mnem = nmnem ? (char (*)[SCR_MAX_MENU_ITEMS])calloc((size_t)nmnem, sizeof *mnem) : NULL;
	if (!mnem) nmnem = 0;
	for (i = 0; i < a->set.nm; i++) {
		scr_menu *m = &a->set.m[i];
		rec_menu r;
		rec_menu_entry e[SCR_MAX_MENU_ITEMS];
		if (strcmp(m->items, "record") || pin_menu(m->id)) continue;
		m->nit = 0;
		if (rec_menu_load(a->store, m->record, &r)) { fprintf(stderr, "d32library: menu %s: no record %d in the store\n", m->id, m->record); continue; }
		n = rec_menu_entries(a->store, r.list_id, r.count, e, SCR_MAX_MENU_ITEMS);
		for (k = 0; k < n && m->nit < SCR_MAX_MENU_ITEMS; k++) {
			scr_menu_item *it = &m->it[m->nit];
			const char *str = e[k].sid && a->have_str ? rec_string(&a->str, e[k].sid) : NULL;
			int q, hidden = 0;
			for (q = 0; q < m->nhide; q++) if (m->hide[q] == e[k].cmd && !e[k].rule) hidden = 1;
			if (hidden) continue;
			memset(it, 0, sizeof(*it));
			it->cmd = e[k].cmd;
			if (e[k].rule) it->kind = MI_RULE;
			else if (str && str[0] == '|') {
				size_t z;
				it->kind = MI_BITMAP;
				snprintf(it->asset, sizeof it->asset, "ui/bitmap/%.60s", str + 1);
				for (z = strlen("ui/bitmap/"); it->asset[z]; z++) if (it->asset[z] >= 'a' && it->asset[z] <= 'z') it->asset[z] = (char)(it->asset[z] - 'a' + 'A');
				it->frames = 2;
			} else {
				char raw[SCR_LABEL];
				const char *bar = str ? strchr(str, '|') : NULL;
				it->kind = MI_TEXT;
				if (bar) {
					/* "Help\x85|help_main": the bitmap drawn before the label (HELP_MAIN: normal, on the band) */
					size_t u;
					snprintf(it->asset, sizeof it->asset, "ui/bitmap/%.60s", bar + 1);
					for (u = strlen("ui/bitmap/"); it->asset[u]; u++) if (it->asset[u] >= 'a' && it->asset[u] <= 'z') it->asset[u] = (char)(it->asset[u] - 'a' + 'A');
					it->frames = 2;
				}
				snprintf(raw, sizeof raw, "%.*s", str ? (int)(bar ? (size_t)(bar - str) : strlen(str)) : 0, str ? str : "");
				rec_label(raw, it->label, sizeof it->label, 1); /* the mnemonic '&' goes, the cp1252 ellipsis is "..." */
				if (i < nmnem) {
					/* the letter after a single '&' is the item's mnemonic (W1-CHROME: the keyboard picks it) */
					const char *p;
					for (p = raw; *p; p++) {
						if (p[0] == '&' && p[1] == '&') { p++; continue; }
						if (p[0] == '&' && isalnum((unsigned char)p[1])) { mnem[i][m->nit] = (char)tolower((unsigned char)p[1]); break; }
					}
				}
				for (q = 0; q < m->nicon; q++)
					if (m->icon[q].cmd == it->cmd) { snprintf(it->asset, sizeof it->asset, "%s", m->icon[q].asset); it->frames = m->icon[q].frames; }
			}
			it->action = A_NONE;
			for (q = 0; q < m->non; q++)
				if (m->on[q].cmd == it->cmd) { it->action = m->on[q].action; snprintf(it->arg, sizeof it->arg, "%s", m->on[q].arg); }
			for (q = 0; it->action == A_NONE && it->kind != MI_RULE && q < (int)(sizeof window_cmds / sizeof window_cmds[0]); q++)
				if (window_cmds[q].cmd == it->cmd) it->action = reg_find(REG_ACTION, window_cmds[q].action);
			if (it->action < 0) it->action = A_NONE;
			m->nit++;
		}
		/* a rule left at either end, or doubled, by a hidden item goes: the Library's menus end on an item, never on a rule
		 * (capture256 39, 40: the Features and Tools menus end on ground; Features ended on the rule that stood above
		 * MindMaze until build 2's review) */
		for (k = 0; k < m->nit; k++)
			if (m->it[k].kind == MI_RULE && (k == 0 || k == m->nit - 1 || m->it[k + 1].kind == MI_RULE)) {
				memmove(&m->it[k], &m->it[k + 1], (size_t)(m->nit - k - 1) * sizeof m->it[0]);
				if (i < nmnem) memmove(&mnem[i][k], &mnem[i][k + 1], (size_t)(m->nit - k - 1));
				m->nit--;
				k = -1;
			}
		if (a->verbose) fprintf(stderr, "d32library: menu %s (record %d): %d items\n", m->id, m->record, m->nit);
	}
}

/* ---------------------------------------------------------------- geometry */

static int category_leaf(const e26_app *a, const tax_cat *cat)
{
	return a->have_ww && !cat->nsub && !strcmp(cat->id, "unclassified");
}

static void menu_geometry(e26_app *a, int *ih, int *w, int *sub_ih, int *sub_w)
{
	const scr_menu *m = a->menu, *sm;
	rec_menu r;
	*ih = m->item_height; *w = m->width; *sub_ih = 21; *sub_w = 204;
	if (m->record && !rec_menu_load(a->store, m->record, &r) && r.item_height > 0 && r.width > 0) {
		if (!m->item_height) *ih = r.item_height;
		if (!m->width) *w = r.width;
	}
	if (!*ih) *ih = 41;
	if (!*w) *w = 168;
	sm = m->sub[0] ? scr_menu_find(&a->set, m->sub) : NULL;
	if (sm) {
		*sub_ih = sm->item_height; *sub_w = sm->width;
		if (sm->record && !rec_menu_load(a->store, sm->record, &r) && r.item_height > 0 && r.width > 0) {
			if (!sm->item_height) *sub_ih = r.item_height;
			if (!sm->width) *sub_w = r.width;
		}
		if (!*sub_ih) *sub_ih = 21;
		if (!*sub_w) *sub_w = 204;
	}
}

static void menu_item_rect(e26_app *a, int i, int *x, int *y)
{
	int ih, w, sih, sw;
	menu_geometry(a, &ih, &w, &sih, &sw);
	*x = a->menu_x + a->tax.cat[i].col * w;
	*y = a->menu_y + a->tax.cat[i].row * ih;
}

/* the subcategory flyout's columns: up to 17 rows of 21 px in a column (the most rows a 12001-style menu holds
 * under the Category menu's top), more columns when a category has more subcategories */
#define FLY_MAX_ROWS 17
static void flyout_size(int n, int *cols, int *rows)
{
	if (n < 1) n = 1;
	*cols = (n + FLY_MAX_ROWS - 1) / FLY_MAX_ROWS;
	*rows = (n + *cols - 1) / *cols;
}

/* -------- menus built from the original's menu headers and item lists (12050 Features, 12051 Tools, 12053 the
 * Information menu; 256-colour captures 39, 40, 62, 132, 140-144)
 *
 * A text menu (12053: colours A 14, B 13, C 15, item height 16, width 100, margin 10) is 5 px of ground, then every
 * entry 16 px: a label in C with its capitals on the entry's rows 4-11 (the X baseline 12 below the top), from the
 * margin (an item with an icon, Help: HELP_MAIN's 17x16 frame at the margin, its label 22 px further), or a rule in
 * C on the entry's 9th row from the margin
 * to the margin at the right; then 5 px of ground (capture 62: x 540-639, rows 36-157, rules on rows 65 and 113,
 * baselines 52, 84, 100, 132, 148). The entry under the pointer lies on a band of B inset 5 px at either end
 * (capture 132: x 545-634, rows 137-152), its label unchanged. A bitmap menu (12050, 12051) stacks its items'
 * strips, frame 0 at rest and frame 1 under the pointer, with no ground between them (capture 39: 63x61 strips from
 * (499,36); capture 40: 54x51 strips from (564,36)). */
#define SMENU_PAD 5
static int smenu_ih(e26_app *a, const scr_menu *m)
{
	rec_menu r;
	if (m->item_height) return m->item_height;
	if (m->record && !rec_menu_load(a->store, m->record, &r) && r.item_height > 0 && r.item_height < 200) return r.item_height;
	return 16;
}

static int smenu_item_h(e26_app *a, const scr_menu *m, int k)
{
	if (m->it[k].kind == MI_BITMAP) {
		xasset *x = xa_get(a, m->it[k].asset);
		if (x) return x->h;
	}
	return smenu_ih(a, m);
}

static int smenu_pad(const scr_menu *m)
{
	int k;
	for (k = 0; k < m->nit; k++)
		if (m->it[k].kind != MI_BITMAP) return SMENU_PAD;
	return 0;
}

static void smenu_size(e26_app *a, const scr_menu *m, int *w, int *h)
{
	rec_menu r;
	int k;
	*w = m->width;
	if (!*w && m->record && !rec_menu_load(a->store, m->record, &r)) *w = r.width;
	if (!*w) *w = 100;
	*h = 2 * smenu_pad(m);
	for (k = 0; k < m->nit; k++) *h += smenu_item_h(a, m, k);
}

/* item index, -1 on the menu but no item, -2 outside */
static int smenu_hit(e26_app *a, int x, int y)
{
	const scr_menu *m = a->menu;
	int w, h, k, top;
	if (!m) return -2;
	smenu_size(a, m, &w, &h);
	if (x < a->menu_x || y < a->menu_y || x >= a->menu_x + w || y >= a->menu_y + h) return -2;
	top = a->menu_y + smenu_pad(m);
	for (k = 0; k < m->nit; k++) {
		int ih = smenu_item_h(a, m, k);
		if (y >= top && y < top + ih) return m->it[k].kind == MI_RULE ? -1 : k;
		top += ih;
	}
	return -1;
}

int menus_record_open(const e26_app *a) { return a->menu && !strcmp(a->menu->items, "record"); }

/* ---------------------------------------------------------------- drawing */

static void draw_smenu(e26_app *a, gfx_ctx *g)
{
	const scr_menu *m = a->menu;
	int w, h, k, x = a->menu_x, y, margin = m->margin > 0 ? m->margin : 10;
	long ground = m->colour[0] >= 0 ? m->colour[0] : ui_rgb(a, UI_STONE), band = m->colour[1] >= 0 ? m->colour[1] : ui_rgb(a, UI_TAUPE);
	long ink = m->colour[2] >= 0 ? m->colour[2] : C_BLACK;
	smenu_size(a, m, &w, &h);
	gfx_fill(g, x, a->menu_y, w, h, ground);
	y = a->menu_y + smenu_pad(m);
	for (k = 0; k < m->nit; k++) {
		const scr_menu_item *it = &m->it[k];
		int ih = smenu_item_h(a, m, k), hot = k == a->menu_hot;
		if (it->kind == MI_BITMAP) {
			xasset *b = xa_get(a, it->asset);
			if (b) {
				int fw = b->w / (it->frames > 0 ? it->frames : 1), fr = hot && it->frames > 1 ? 1 : 0;
				gfx_blit(g, b, fr * fw, 0, fw < w ? fw : w, b->h, x, y);
				/* the strips carry the separator under them in their last row (TIMEL_ICON's row 60: an entry-13 rule
				 * from x 3 to 59, the one above MindMaze); the menu's last item has none, its last row is its own ground
				 * (capture256 39 ends on MindMaze's, 40 on WORDP_ICON's), so with MindMaze hidden Timeline's goes */
				if (k == m->nit - 1) {
					long gr = xa_sample(b, fr * fw, b->h - 1);
					if (gr >= 0) gfx_fill(g, x, y + b->h - 1, fw < w ? fw : w, 1, gr);
				}
			}
		} else if (it->kind == MI_RULE) {
			gfx_line(g, x + margin, y + 8, x + w - 1 - margin, y + 8, ink);
		} else {
			int lx = x + margin;
			if (hot) gfx_fill(g, x + 5, y, w - 10, ih, band);
			if (it->asset[0]) {
				/* the item's icon (Help's HELP_MAIN, named by the string's "|help_main"): frame 0 on the ground, frame 1
				 * on the band (capture256 62: frame 0 exact at (550,41)) */
				xasset *ic = xa_get(a, it->asset);
				if (ic) {
					int fw = ic->w / (it->frames > 0 ? it->frames : 1);
					gfx_blit(g, ic, hot && it->frames > 1 ? fw : 0, 0, fw, ic->h, lx, y);
				}
				lx += 22;
			}
			gfx_text(g, lx, y + 12, F_UI, ink, it->label, -1);
		}
		y += ih;
	}
}

void menus_draw(e26_app *a, gfx_ctx *g)
{
	int ih, w, sih, sw, i;
	xasset *style;
	const scr_menu *sm;
	long ground = C_CAPTION, label = C_WHITE, mlabel;
	if (menus_record_open(a)) { draw_smenu(a, g); return; }
	if (!a->menu || !a->have_tax) return;
	sm = a->menu->sub[0] ? scr_menu_find(&a->set, a->menu->sub) : NULL;
	/* record 12000's colour C: the label on the selected face (black) */
	mlabel = a->menu->colour[2] >= 0 ? a->menu->colour[2] : C_BLACK;
	menu_geometry(a, &ih, &w, &sih, &sw);
	style = a->menu->style[0] ? xa_get(a, a->menu->style) : NULL;
	if (style) {
		/* the original item's ground, and its label colour (the brightest pixel of the lettering) */
		int sx, sy, best = -1;
		long gr = xa_sample(style, 4, style->h / 2);
		if (gr >= 0) ground = gr;
		for (sy = 4; sy < style->h - 5 && sy < 36; sy++)
			for (sx = 20; sx < style->w / 2 - 28; sx++) {
				long v = xa_sample(style, sx, sy);
				int lum = (int)(((v >> 16) & 255) + ((v >> 8) & 255) + (v & 255));
				if (v >= 0 && lum > best) { best = lum; label = v; }
			}
	}
	for (i = 0; i < a->tax.ncat; i++) {
		const tax_cat *tc = &a->tax.cat[i];
		int x, y, hot = i == a->menu_hot || (a->sub_open && a->sub_cat == i), lx, nl = 0, k;
		long bg, fg;
		char lines[2][TAX_TEXT], tmp[TAX_TEXT], *br;
		menu_item_rect(a, i, &x, &y);
		if (a->menu->art[0]) {
			/* his own CAT strip (the category-art lane: 336x41, frame 0 at rest, frame 1 selected, his label baked in
			 * as the Library's are), drawn 1:1 like the CAT_* strips; the last item of each column has no rule */
			char an[SCR_ASSET];
			xasset *st;
			snprintf(an, sizeof an, "%s/%s", a->menu->art, tc->id);
			st = xa_get(a, an);
			if (st && st->w == 2 * w && st->h == ih) {
				int k2, last = 1;
				gfx_blit(g, st, hot ? w : 0, 0, w, ih, x, y);
				for (k2 = 0; k2 < a->tax.ncat; k2++)
					if (a->tax.cat[k2].col == tc->col && a->tax.cat[k2].row > tc->row) last = 0;
				if (last && !hot) { long gr = xa_sample(st, 4, ih - 1); gfx_fill(g, x, y + ih - 1, w, 1, gr >= 0 ? gr : ground); }
				continue;
			}
		}
		bg = hot ? (tc->colour >= 0 ? tc->colour : ui_rgb(a, UI_PERIWINKLE)) : ground;
		fg = hot ? mlabel : label; /* TAXONOMY colour_rules: black label on the face */
		lx = x + (style ? 30 : 12); /* the original lettering starts 30 px in */
		/* the original item's frame (normal frame of the style strip) gives the edges, the rule and the arrow
		 * column; the label field is repainted and lettered with his category */
		if (style && style->w / 2 == w && style->h == ih) {
			int k2, last = 1;
			gfx_blit(g, style, 0, 0, w, ih, x, y);
			/* the menu ends without a rule: the Library's last item, CAT_SPORTS, has none in its last row, where the
			 * other eight CAT_* strips have the entry-13 rule (strips [d]; capture256 06: rules on rows 76-363,
			 * none on 404) */
			for (k2 = 0; k2 < a->tax.ncat; k2++)
				if (a->tax.cat[k2].col == tc->col && a->tax.cat[k2].row > tc->row) last = 0;
			if (last) gfx_fill(g, x, y + ih - 1, w, 1, ground);
		} else gfx_fill(g, x, y, w, ih, ground);
		gfx_fill(g, x + 2, y + 2, w - (style && !category_leaf(a, tc) ? 22 : 4), ih - 4, bg);
		if (hot) {
			gfx_fill(g, x + 2, y + 2, w - 4, ih - 4, bg);
			if (tc->ink >= 0) gfx_line(g, x + 14, y + ih - 3, x + w - 20, y + ih - 3, tc->ink);
		}
		if ((hot || !style) && !category_leaf(a, tc)) gfx_arrow_right(g, x + w - 12, y + ih / 2, fg);
		/* two lines at most: the taxonomy's own line break, else the last space that makes it fit */
		snprintf(tmp, sizeof tmp, "%s", tc->label);
		if ((br = strchr(tmp, '\n'))) { *br = 0; snprintf(lines[0], TAX_TEXT, "%s", tmp); snprintf(lines[1], TAX_TEXT, "%s", br + 1); nl = 2; }
		else if (gfx_text_width(g, F_UI_BOLD, tmp, -1) > w - (lx - x) - 22 && (br = strrchr(tmp, ' '))) {
			*br = 0; snprintf(lines[0], TAX_TEXT, "%s", tmp); snprintf(lines[1], TAX_TEXT, "%s", br + 1); nl = 2;
		} else { snprintf(lines[0], TAX_TEXT, "%s", tmp); nl = 1; }
		for (k = 0; k < nl; k++) {
			int by = nl == 1 ? y + (ih + gfx_ascent(g, F_UI_BOLD)) / 2 - 1 : y + ih / 2 - 2 + k * gfx_font_height(g, F_UI_BOLD);
			gfx_text(g, lx, by, F_UI_BOLD, fg, lines[k], -1);
		}
	}
	if (a->sub_open) {
		/* a subcategory menu as the original paints one from its header (12001-12009; seg26:01d6-07d9): ground A
		 * (his face), the item under the pointer on B (his ink), every label in C, and with flag 0x0800 a rule in C
		 * under every item but the last; the left margin from the header. [m] capture256 08: no border, the ground
		 * runs to all four edges (9 rows of 21 px, 204 wide, at the menu's right edge); the rules and the pointer's
		 * band are inset 5 px at both ends, and the band stops above the rule (build 1 framed the flyout in B and
		 * ran rules and band across the full width) */
		/* a 12001-style record per category (taxonomy_colours): colour A his face exactly, the face of his CAT strip's
		 * selected frame beside it (capture256 07: CAT_HISTORY's selected face and 12004's ground are one colour,
		 * entry 31), B his ink snapped to the interface table; C, the item height, width, margin and rules from 12001.
		 * No arrows: the Library's flyouts have none, and every subcategory opens the Pinpointer (capture 09). */
		const tax_cat *tc = &a->tax.cat[a->sub_cat];
		long face = tc->chrome_face >= 0 ? tc->chrome_face : ui_rgb(a, UI_PERIWINKLE);
		long ink = tc->chrome_ink >= 0 ? tc->chrome_ink : sm && sm->colour[1] >= 0 ? sm->colour[1] : C_CAPTION;
		long lab = sm && sm->colour[2] >= 0 ? sm->colour[2] : C_BLACK;
		int n = tc->nsub ? tc->nsub : 1, rules = sm && (sm->rec_flags & REC_MENU_RULES), mx = sm && sm->margin > 0 ? sm->margin : 8, cols, rows;
		flyout_size(n, &cols, &rows);
		gfx_fill(g, a->sub_x, a->sub_y, cols * sw, rows * sih, face);
		for (i = 0; i < tc->nsub; i++) {
			int col = i / rows, row = i % rows, x = a->sub_x + col * sw, y = a->sub_y + row * sih, hot = i == a->sub_hot;
			if (hot) gfx_fill(g, x + 5, y, sw - 10, sih - 1, ink);
			if (rules && row < rows - 1 && i < tc->nsub - 1) gfx_line(g, x + 5, y + sih - 1, x + sw - 6, y + sih - 1, lab);
			gfx_text(g, x + mx, y + (sih + gfx_ascent(g, F_UI)) / 2 - 2, F_UI, lab, tc->sub[i].label, -1);
		}
		if (!tc->nsub) gfx_text(g, a->sub_x + mx, a->sub_y + 13, F_UI, lab, "(no subcategories yet)", -1);
	}
}

/* ---------------------------------------------------------------- input */

/* the Category menu under (x,y): 1 when the menu took it, with the category or the flyout's subcategory */
static int menu_hit(e26_app *a, int x, int y, int *cat, int *sub)
{
	int ih, w, sih, sw;
	*cat = *sub = -1;
	if (!a->menu || !a->have_tax || menus_record_open(a)) return 0;
	menu_geometry(a, &ih, &w, &sih, &sw);
	if (a->sub_open) {
		int cols, rows, n = a->tax.cat[a->sub_cat].nsub;
		flyout_size(n, &cols, &rows);
		if (x >= a->sub_x && x < a->sub_x + cols * sw && y >= a->sub_y && y < a->sub_y + rows * sih) {
			*sub = (x - a->sub_x) / sw * rows + (y - a->sub_y) / sih;
			if (*sub >= n) { *sub = -1; return 1; }
			return 1;
		}
	}
	{
		int i, ix, iy;
		for (i = 0; i < a->tax.ncat; i++) {
			menu_item_rect(a, i, &ix, &iy);
			if (x >= ix && x < ix + w && y >= iy && y < iy + ih) { *cat = i; return 1; }
		}
	}
	return 0;
}

static void open_flyout(e26_app *a, int cat)
{
	int ih, w, sih, sw, cols, rows, ix, iy, n, width, height;
	if (category_leaf(a, &a->tax.cat[cat])) {
		a->sub_open = 0;
		a->sub_hot = -1;
		return;
	}
	present_frame_bounds(a, &width, &height);
	menu_geometry(a, &ih, &w, &sih, &sw);
	n = a->tax.cat[cat].nsub ? a->tax.cat[cat].nsub : 1;
	flyout_size(n, &cols, &rows);
	menu_item_rect(a, cat, &ix, &iy);
	a->sub_open = a->menu->sub[0] != 0;
	a->sub_cat = cat;
	a->sub_x = ix + w;
	if (a->sub_x + cols * sw > width) a->sub_x = ix - cols * sw; /* no room on the right: open to the left */
	a->sub_y = iy;
	if (a->sub_y + rows * sih > height) a->sub_y = height - rows * sih; /* no border (capture256 08) */
	if (width != E26_W || height != E26_H) {
		if (a->sub_x < 0) a->sub_x = 0;
		if (a->sub_y < 0) a->sub_y = 0;
	}
	a->sub_hot = -1;
}

void menus_close(e26_app *a)
{
	a->menu = NULL;
	a->sub_open = 0;
	pass_opened = 0;
	pass_ctl = -1;
	kb_sub = 0;
}

void menus_toggle(e26_app *a, const char *id, int x, int y)
{
	e26_event ev;
	if (a->menu && !strcmp(a->menu->id, id)) { menus_close(a); return; }
	a->menu = scr_menu_find(&a->set, id);
	if (a->menu && pin_menu(id)) pin_menu_items(&a->set.m[a->menu - a->set.m]);
	if (a->menu && !strcmp(id, "article-zoom"))
		art_zoom_items(a, &a->set.m[a->menu - a->set.m]);
	if (a->menu && !strcmp(id, "article-font"))
		art_font_items(a, &a->set.m[a->menu - a->set.m]);
	if (a->menu && !strcmp(id, "article-history"))
		art_history_items(a, &a->set.m[a->menu - a->set.m]);
	a->menu_x = x;
	a->menu_y = y;
	if (a->menu && a->menu->at_x >= 0) { a->menu_x = a->menu->at_x; a->menu_y = a->menu->at_y; }
	if (a->menu && present_client_viewport(a)) {
		int w, h, width, height;
		present_frame_bounds(a, &width, &height);
		if (menus_record_open(a)) smenu_size(a, a->menu, &w, &h);
		else {
			int ih, iw, sih, sw, i;
			menu_geometry(a, &ih, &iw, &sih, &sw);
			w = h = 0;
			for (i = 0; i < a->tax.ncat; i++) {
				int right = (a->tax.cat[i].col + 1) * iw;
				int bottom = (a->tax.cat[i].row + 1) * ih;
				if (right > w) w = right;
				if (bottom > h) h = bottom;
			}
		}
		if (a->menu_x + w > width) a->menu_x = width - w;
		if (a->menu_y + h > height) a->menu_y = height - h;
		if (a->menu_x < 0) a->menu_x = 0;
		if (a->menu_y < 0) a->menu_y = 0;
	}
	a->menu_hot = -1;
	a->sub_open = 0;
	pass_opened = 0;
	pass_ctl = -1;
	kb_sub = 0;
	if (a->menu) {
		memset(&ev, 0, sizeof ev);
		ev.type = EV_MENU_OPEN;
		ev.name = a->menu->id;
		ev.ctl = -1;
		ev_publish(a, &ev);
	}
}

static void pick(e26_app *a, const scr_menu_item *it)
{
	e26_actx x;
	e26_event ev;
	const char *id = a->menu ? a->menu->id : "";
	memset(&ev, 0, sizeof ev);
	ev.type = EV_MENU_PICK;
	ev.name = id;
	ev.arg = it->label;
	ev.ctl = -1;
	menus_close(a);
	ev_publish(a, &ev);
	memset(&x, 0, sizeof x);
	x.ctl = -1;
	x.arg = it->arg;
	x.label = it->label;
	if (action_run(a, it->action, &x)) nav_redraw(a);
}

/* a subcategory picked, by the pointer or the keyboard: every subcategory opens the Pinpointer filtered to it
 * (capture 09; build 2 opened only the seven with a corpus article link and left the other 170 as dead clicks) */
static void pick_category(e26_app *a, int cat, int sub)
{
	e26_event ev;
	a->cur_cat = cat;
	a->cur_sub = sub;
	memset(&ev, 0, sizeof ev);
	ev.type = EV_MENU_PICK;
	ev.name = a->menu->id;
	ev.arg = sub < 0 ? a->tax.cat[cat].label : a->tax.cat[cat].sub[sub].label;
	ev.ctl = -1;
	menus_close(a);
	ev_publish(a, &ev);
	if (a->have_ww) { pin_open_filtered(a, a->cur_cat, sub); return; }
	if (sub >= 0 && a->tax.cat[a->cur_cat].sub[sub].article[0]) {
		art_open(a, a->tax.cat[a->cur_cat].sub[sub].article); return;
	}
	nav_redraw(a);
}

/* ---------------------------------------------------------------- passing over a menu marker */

static int pass_on(e26_app *a) { return chrome_flag(a, "FlyoutsOnMousePass", "flyoutsOnMousePass", "FlyoutsOnMousePass", 1); }

/* the control of the front screen whose menu marker lies under (x,y), -1 none. A bar with a marker opens its menu
 * when the pointer is over the marker, from the bar's left edge to its lettering (capture256 100 and 102: the pointer
 * on the lettering of Features and Tools opens nothing; 101 and 103: pressed, they open); a menu button that is all
 * bitmap, the (i) button, is a marker as a whole (capture256 104: passing over (i) opens the Information menu). The
 * Control menu's ENCSMALL is Windows' control-menu box, which a pass never opens [i]. */
static int marker_at(e26_app *a, int x, int y)
{
	scr_screen *s = a->cur;
	int i;
	if (!s) return -1;
	for (i = s->nc - 1; i >= 0; i--) {
		const scr_control *c = &s->c[i];
		int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy), zone;
		if (c->hidden || x < cx || y < cy || x >= cx + c->w || y >= cy + c->h) continue;
		if (c->action != A_MENU || !strcmp(c->arg, "control")) return -1;
		if (c->marker) zone = c->text_x > 0 ? c->text_x : 15;
		else if (c->strip[0] && !c->label[0] && !c->label_sid) zone = c->w;
		else return -1;
		return x < cx + zone ? i : -1;
	}
	return -1;
}

/* opens the menu control ci of s names, under it (as action menu does) */
static void open_under(e26_app *a, const scr_screen *s, int ci)
{
	const scr_control *c = &s->c[ci];
	if (a->menu) menus_close(a);
	menus_toggle(a, c->arg, c->x + (c->absolute ? 0 : s->ox), c->y + (c->absolute ? 0 : s->oy) + c->h + 1);
}

int menus_pass_opened(void) { return pass_opened; }

int menus_motion(e26_app *a, int x, int y, int *changed)
{
	int cat, sub, m;
	if (menus_record_open(a)) {
		int k = smenu_hit(a, x, y);
		if (k >= -1) {
			if (k != a->menu_hot) { a->menu_hot = k; nav_redraw(a); }
			return 1;
		}
		if (a->menu_hot != -1) { a->menu_hot = -1; *changed = 1; }
	}
	if (menu_hit(a, x, y, &cat, &sub)) {
		if (cat >= 0) {
			int pass = !category_leaf(a, &a->tax.cat[cat]) && pass_on(a);
			if (cat != a->menu_hot || (pass && (!a->sub_open || a->sub_cat != cat))) {
				a->menu_hot = cat;
				kb_sub = 0;
				if (pass) open_flyout(a, cat); /* FlyoutsOnMousePass (on as Library shipped it) */
				else if (a->sub_open && a->sub_cat != cat) a->sub_open = 0; /* off: a click opens it */
				*changed = 1;
			}
		}
		if (sub != a->sub_hot && cat < 0) { a->sub_hot = sub; *changed = 1; }
		if (*changed) nav_redraw(a);
		return 1;
	}
	/* a menu marker under the pointer opens its menu (or takes over from the one open) */
	if ((m = marker_at(a, x, y)) >= 0 && !(a->menu && !strcmp(a->menu->id, a->cur->c[m].arg)) && pass_on(a)) {
		open_under(a, a->cur, m);
		pass_opened = 1;
		pass_ctl = m;
		*changed = 1; /* input.c works out the hover as usual and draws the frame */
	}
	return 0;
}

int menus_press(e26_app *a, int x, int y)
{
	int cat, sub, h;
	if (menus_record_open(a)) {
		int k = smenu_hit(a, x, y);
		if (k >= 0) { pick(a, &a->menu->it[k]); return 1; }
		if (k == -1) return 1;
	}
	if (menu_hit(a, x, y, &cat, &sub)) {
		if (cat >= 0) {
			if (category_leaf(a, &a->tax.cat[cat])) { pick_category(a, cat, -1); return 1; }
			a->menu_hot = cat; open_flyout(a, cat); a->cur_cat = cat; kb_sub = 0;
		} else if (sub >= 0) { pick_category(a, a->sub_cat, sub); return 1; }
		nav_redraw(a);
		return 1;
	}
	if (a->menu) {
		h = hit_control(a, x, y);
		/* a press on the control whose marker has just opened the menu by passing over it keeps the menu open */
		if (pass_opened && h >= 0 && h == pass_ctl) return 1;
		/* a press outside the open menu closes it; on the control that opened a menu it only closes it */
		menus_close(a);
		if (h >= 0 && a->cur->c[h].action == A_MENU) { nav_redraw(a); return 1; }
	}
	return 0;
}

/* ---------------------------------------------------------------- the keyboard */

char menus_mnemonic(const e26_app *a, const scr_menu *m, int item)
{
	int idx = (int)(m - a->set.m);
	if (!mnem || idx < 0 || idx >= nmnem || item < 0 || item >= SCR_MAX_MENU_ITEMS) return 0;
	return mnem[idx][item];
}

/* the next item that is not a rule, from item `from` (-1: before the first, or after the last going up), wrapping */
static int step_item(const scr_menu *m, int from, int dir)
{
	int k, n = m->nit, i = from;
	if (n <= 0) return -1;
	if (i < 0 || i >= n) i = dir > 0 ? -1 : n;
	for (k = 0; k < n; k++) {
		i += dir;
		if (i < 0) i = n - 1;
		if (i >= n) i = 0;
		if (m->it[i].kind != MI_RULE) return i;
	}
	return -1;
}

/* the menus the front screen's controls open, left to right (the top bar: Control, Category, Features, Tools, the
 * Information menu), each once */
static int bar_menus(e26_app *a, int *ctl, int max)
{
	scr_screen *s = a->cur;
	int i, k, n = 0;
	for (i = 0; s && i < s->nc && n < max; i++) {
		const scr_control *c = &s->c[i];
		if (c->hidden || c->action != A_MENU) continue;
		for (k = 0; k < n; k++)
			if (!strcmp(s->c[ctl[k]].arg, c->arg)) break;
		if (k < n) continue;
		ctl[n++] = i;
	}
	for (i = 1; i < n; i++) /* by their left edge */
		for (k = i; k > 0; k--) {
			const scr_control *p = &s->c[ctl[k - 1]], *q = &s->c[ctl[k]];
			int px = p->x + (p->absolute ? 0 : s->ox), qx = q->x + (q->absolute ? 0 : s->ox), t;
			if (px <= qx) break;
			t = ctl[k]; ctl[k] = ctl[k - 1]; ctl[k - 1] = t;
		}
	return n;
}

static int first_cat(const e26_app *a)
{
	int i, best = -1;
	for (i = 0; i < a->tax.ncat; i++)
		if (best < 0 || a->tax.cat[i].col < a->tax.cat[best].col ||
		    (a->tax.cat[i].col == a->tax.cat[best].col && a->tax.cat[i].row < a->tax.cat[best].row)) best = i;
	return best;
}

/* the keyboard's first item in a menu just opened: the first item that is not a rule (capture256 140: Alt+E, Atlas
 * on the band; 143: Alt+I, Help), or the first category */
static void key_first(e26_app *a)
{
	if (!a->menu) return;
	if (menus_record_open(a)) a->menu_hot = step_item(a->menu, -1, 1);
	else if (a->have_tax) a->menu_hot = first_cat(a);
}

/* Left and Right in a menu: the menu of the bar control beside the one that opened it, its first item hot */
static void neighbour(e26_app *a, int dir)
{
	int ctl[32], n = bar_menus(a, ctl, 32), k, cur = -1;
	for (k = 0; k < n; k++)
		if (a->menu && !strcmp(a->cur->c[ctl[k]].arg, a->menu->id)) cur = k;
	if (cur < 0 || n < 2) return;
	open_under(a, a->cur, ctl[(cur + dir + n) % n]);
	key_first(a);
}

void menus_open_by_key(e26_app *a, const char *id)
{
	scr_screen *s = a->cur;
	int i;
	if (a->menu && !strcmp(a->menu->id, id)) { menus_close(a); return; }
	for (i = 0; s && i < s->nc; i++)
		if (!s->c[i].hidden && s->c[i].action == A_MENU && !strcmp(s->c[i].arg, id)) break;
	if (s && i < s->nc) open_under(a, s, i);
	else {
		if (a->menu) menus_close(a);
		menus_toggle(a, id, 0, 0);
	}
	key_first(a);
}

static int cat_at(const e26_app *a, int col, int row)
{
	int i;
	for (i = 0; i < a->tax.ncat; i++)
		if (a->tax.cat[i].col == col && a->tax.cat[i].row == row) return i;
	return -1;
}

/* the top (last 0) or bottom (last 1) category of a column */
static int col_end(const e26_app *a, int col, int last)
{
	int i, best = -1;
	for (i = 0; i < a->tax.ncat; i++)
		if (a->tax.cat[i].col == col &&
		    (best < 0 || (last ? a->tax.cat[i].row > a->tax.cat[best].row : a->tax.cat[i].row < a->tax.cat[best].row))) best = i;
	return best;
}

/* the keyboard enters the flyout of category hot: its first subcategory under the keyboard */
static void enter_flyout(e26_app *a, int hot)
{
	open_flyout(a, hot);
	a->cur_cat = hot;
	kb_sub = a->sub_open;
	a->sub_hot = kb_sub && a->tax.cat[hot].nsub ? 0 : -1;
}

/* the Category menu: arrows walk his grid of categories; Right past the last column, or Return, opens the flyout
 * and moves into it; Left in the flyout goes back to the category; Return on a subcategory picks it.
 * 0 not taken, 1 taken (redraw), 2 picked (the pick drew) */
static int cat_key(e26_app *a, KeySym ks)
{
	int hot = a->menu_hot, col, row, next = -1;
	if (!a->have_tax || !a->tax.ncat) return 0;
	if (kb_sub && a->sub_open) {
		int ns = a->tax.cat[a->sub_cat].nsub;
		switch (ks) {
		case XK_Down: case XK_KP_Down: a->sub_hot = ns ? (a->sub_hot + 1) % ns : -1; return 1;
		case XK_Up: case XK_KP_Up: a->sub_hot = ns ? (a->sub_hot <= 0 ? ns - 1 : a->sub_hot - 1) : -1; return 1;
		case XK_Home: case XK_KP_Home: a->sub_hot = ns ? 0 : -1; return 1;
		case XK_End: case XK_KP_End: a->sub_hot = ns - 1; return 1;
		case XK_Left: case XK_KP_Left: kb_sub = 0; a->sub_hot = -1; return 1;
		case XK_Return: case XK_KP_Enter: case XK_space:
			if (a->sub_hot >= 0 && a->sub_hot < ns) { pick_category(a, a->sub_cat, a->sub_hot); return 2; }
			return 1;
		default: return 0;
		}
	}
	if (hot < 0 || hot >= a->tax.ncat) {
		switch (ks) {
		case XK_Down: case XK_Up: case XK_Left: case XK_Right: case XK_Home: case XK_End:
		case XK_KP_Down: case XK_KP_Up: case XK_KP_Left: case XK_KP_Right: case XK_KP_Home: case XK_KP_End:
			a->menu_hot = first_cat(a);
			return 1;
		default: return 0;
		}
	}
	col = a->tax.cat[hot].col;
	row = a->tax.cat[hot].row;
	switch (ks) {
	case XK_Down: case XK_KP_Down: next = cat_at(a, col, row + 1); if (next < 0) next = col_end(a, col, 0); break;
	case XK_Up: case XK_KP_Up: next = row > 0 ? cat_at(a, col, row - 1) : -1; if (next < 0) next = col_end(a, col, 1); break;
	case XK_Home: case XK_KP_Home: next = col_end(a, col, 0); break;
	case XK_End: case XK_KP_End: next = col_end(a, col, 1); break;
	case XK_Right: case XK_KP_Right:
		next = cat_at(a, col + 1, row);
		if (next < 0) { enter_flyout(a, hot); return 1; }
		break;
	case XK_Left: case XK_KP_Left:
		next = col > 0 ? cat_at(a, col - 1, row) : -1;
		if (next < 0 && col == 0) { neighbour(a, -1); return 1; }
		break;
	case XK_Return: case XK_KP_Enter: case XK_space:
		if (category_leaf(a, &a->tax.cat[hot])) { pick_category(a, hot, -1); return 2; }
		enter_flyout(a, hot); return 1;
	default: return 0;
	}
	if (next >= 0 && next != hot) {
		a->menu_hot = next;
		if (a->sub_open && a->sub_cat != next) a->sub_open = 0;
	}
	return 1;
}

int menus_key(e26_app *a, KeySym ks, unsigned state)
{
	const scr_menu *m = a->menu;
	if (!m) return 0;
	pass_opened = 0; /* the keyboard has it now */
	if (!menus_record_open(a)) {
		int r = cat_key(a, ks);
		if (r == 1) nav_redraw(a);
		return r != 0;
	}
	switch (ks) {
	case XK_Down: case XK_KP_Down: a->menu_hot = step_item(m, a->menu_hot, 1); break;
	case XK_Up: case XK_KP_Up: a->menu_hot = step_item(m, a->menu_hot, -1); break;
	case XK_Home: case XK_KP_Home: a->menu_hot = step_item(m, -1, 1); break;
	case XK_End: case XK_KP_End: a->menu_hot = step_item(m, -1, -1); break;
	case XK_Right: case XK_KP_Right: neighbour(a, 1); break;
	case XK_Left: case XK_KP_Left: neighbour(a, -1); break;
	case XK_Return: case XK_KP_Enter: case XK_space:
		if (a->menu_hot >= 0 && a->menu_hot < m->nit && m->it[a->menu_hot].kind != MI_RULE) pick(a, &m->it[a->menu_hot]);
		return 1;
	default:
		{
			int k;
			if (state & (ControlMask | Mod1Mask)) return 0;
			if (ks < 0x20 || ks > 0x7e) return 0;
			for (k = 0; k < m->nit; k++)
				if (m->it[k].kind != MI_RULE && menus_mnemonic(a, m, k) == (char)tolower((int)ks)) { pick(a, &m->it[k]); return 1; }
			return 0;
		}
	}
	nav_redraw(a);
	return 1;
}
