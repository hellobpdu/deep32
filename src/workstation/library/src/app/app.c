/* d32library: Deep32 Library, a Motif application over the private asset store imported from the owner's own the period design
 * disc. Title screen -> main screen (top bar, Category menu from his taxonomy) -> Pinpointer and Find Wizard ->
 * article view. The 640x480 frame is the original's logical layout at 100 dpi; original bitmaps are drawn 1:1.
 * Build 3 (W0-F0): rooms, kinds and actions are modules found by registry (registry.h, docs/F0-HANDOFF.md).
 *
 * Options (X resources of class Deep32Library, or the command line):
 *   -assets DIR      *assetStore     the private asset store (default /build/private/e26-assets)
 *   -screens DIR     *screenDir      screen files: every *.scr and *.keys there (default ./screens)
 *   -taxonomy FILE   *taxonomy       taxonomy JSON (default ./data/taxonomy-placeholder.json)
 *   -articles DIR    *articleDir     article files (default ./data/articles)
 *   -start NAME      *startScreen    first screen, a full screen or a window over the Main screen (default title, or
 *                                    main when showTitleScreen is False); the splash shows only before the title
 *   -settings DIR    *settingsDir    where LIBRARY.INI and the reading history live (default ~/.d32/e26)
 *   -notitle         *showTitleScreen: False   (the original's ShowTitleScreen)
 *   -nointrosound    *introPlaySounds: False   (the original's IntroPlaySounds)
 *   -nouisound       *noUISound: True          (the original's NoUISound; silences every sound)
 *   -typesize S|M|L  *typeSize       article type size, the original's TextStyle (e95/win31: Arial 13, 15, 19 px =
 *                                    10, 11, 14 pt at 96 dpi, default S as the period design's; build1: Helvetica 14, 17,
 *                                    20 px, default M as build 1's)
 *   -fonts SET       *fontSet        e95 (default), win31 or build1; see fonts.c and docs/FONTS.md
 *   -wheels DIR      *wheelDir       the catalogue's word wheels for the Pinpointer (default /d32lib/catalogue/wheels)
 *   -nosplash        *splash: False  no splash before the Title screen
 *                    *splashTime     the splash's time in ms (default 450: 0.42 s on the 256-colour capture lane)
 *   -titletheme A    *titleTheme     the Title screen's theme: a store asset ("hilite/aquatic" = STORE/hilite/aquatic.pcm.wav)
 *                                    or an absolute 16-bit PCM .wav; "" (default) or a missing file = the period design's theme
 *   -scaler S        *scaler         how a maximised window enlarges the frame: render (default; RENDER, nearest
 *                                    filter) or cpu (XGetImage and XPutImage); see below
 *   -v               *verbose: True
 * ShowTitleScreen, IntroPlaySounds, NoUISound and TextStyle come from LIBRARY.INI (state.c) when no resource or option
 * names them for the session.
 *
 * Maximise (update 1): a window larger than 640x480 (maximised by the window manager, or by the Library's own caption
 * button, action maximize) shows the frame enlarged k times, k the largest integer that fits (2 on 1920x1080),
 * centred on black, the pointer mapped back to the frame (scale.h, present.c, wm.c). At 640x480 nothing changes.
 */
#include "library_app.h"
#include "appearance.h"
#include "room_openat.h"
#include <Xm/DrawingA.h>
#include <Xm/MwmUtil.h>
#include <Xm/Protocols.h>
#include <Xm/ScrollBar.h>
#include <Xm/Xm.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static e26_app A;
static char start_name[SCR_ID];
static char pending_uri[OPENAT_URI_MAX + 1];

#define OFF(f) XtOffsetOf(e26_app, f)
static XtResource resources[] = {
	{ "assetStore", "AssetStore", XmRString, sizeof(String), OFF(store), XmRString, (XtPointer)"/build/private/e26-assets" },
	{ "screenDir", "ScreenDir", XmRString, sizeof(String), OFF(screens), XmRString, (XtPointer)"screens" },
	{ "taxonomy", "Taxonomy", XmRString, sizeof(String), OFF(taxonomy_path), XmRString, (XtPointer)"data/taxonomy-placeholder.json" },
	{ "articleDir", "ArticleDir", XmRString, sizeof(String), OFF(article_dir), XmRString, (XtPointer)"data/articles" },
	{ "startScreen", "StartScreen", XmRString, sizeof(String), OFF(start), XmRString, (XtPointer)"" },
	{ "typeSize", "TypeSize", XmRString, sizeof(String), OFF(type_size), XmRString, (XtPointer)"" }, /* "": the set's default */
	{ "noUISound", "NoUISound", XmRBoolean, sizeof(Boolean), OFF(no_ui_sound), XmRImmediate, (XtPointer)False },
	{ "introPlaySounds", "IntroPlaySounds", XmRBoolean, sizeof(Boolean), OFF(intro_sounds), XmRImmediate, (XtPointer)True },
	{ "showTitleScreen", "ShowTitleScreen", XmRBoolean, sizeof(Boolean), OFF(show_title), XmRImmediate, (XtPointer)True },
	{ "verbose", "Verbose", XmRBoolean, sizeof(Boolean), OFF(verbose), XmRImmediate, (XtPointer)False },
	{ "wheelDir", "WheelDir", XmRString, sizeof(String), OFF(wheel_dir), XmRString, (XtPointer)"/d32lib/catalogue/wheels" },
	{ "splash", "Splash", XmRBoolean, sizeof(Boolean), OFF(splash), XmRImmediate, (XtPointer)True },
	{ "splashTime", "SplashTime", XmRInt, sizeof(int), OFF(splash_ms), XmRImmediate, (XtPointer)450 },
	{ "titleTheme", "TitleTheme", XmRString, sizeof(String), OFF(title_theme), XmRString, (XtPointer)"" },
	{ "scaler", "Scaler", XmRString, sizeof(String), OFF(scaler), XmRString, (XtPointer)"render" },
	{ "settingsDir", "SettingsDir", XmRString, sizeof(String), OFF(settings_dir), XmRString, (XtPointer)"" },
};

static XrmOptionDescRec options[] = {
	{ "-assets", "*assetStore", XrmoptionSepArg, NULL },
	{ "-screens", "*screenDir", XrmoptionSepArg, NULL },
	{ "-taxonomy", "*taxonomy", XrmoptionSepArg, NULL },
	{ "-articles", "*articleDir", XrmoptionSepArg, NULL },
	{ "-start", "*startScreen", XrmoptionSepArg, NULL },
	{ "-typesize", "*typeSize", XrmoptionSepArg, NULL },
	{ "-notitle", "*showTitleScreen", XrmoptionNoArg, (XtPointer)"False" },
	{ "-nointrosound", "*introPlaySounds", XrmoptionNoArg, (XtPointer)"False" },
	{ "-nouisound", "*noUISound", XrmoptionNoArg, (XtPointer)"True" },
	{ "-v", "*verbose", XrmoptionNoArg, (XtPointer)"True" },
	{ "-fonts", "*fontSet", XrmoptionSepArg, NULL },
	{ "-wheels", "*wheelDir", XrmoptionSepArg, NULL },
	{ "-nosplash", "*splash", XrmoptionNoArg, (XtPointer)"False" },
	{ "-titletheme", "*titleTheme", XrmoptionSepArg, NULL },
	{ "-scaler", "*scaler", XrmoptionSepArg, NULL },
	{ "-settings", "*settingsDir", XrmoptionSepArg, NULL },
};

/* No colour resources: widget colours are set from the store's interface colour table (widget_colours). */
static String fallbacks[] = {
	NULL
};

/* ---------------------------------------------------------------- settings */

/* a resource the session names (command line, app-defaults, the server's resources) wins over LIBRARY.INI */
static int resource_given(const char *res, const char *cls)
{
	String app_name = NULL, app_class = NULL;
	char n[200], c[200], *type = NULL;
	XrmValue v;
	XtGetApplicationNameAndClass(A.dpy, &app_name, &app_class);
	snprintf(n, sizeof n, "%s.%s", app_name ? app_name : "d32library", res);
	snprintf(c, sizeof c, "%s.%s", app_class ? app_class : "Deep32Library", cls);
	return XrmGetResource(XtDatabase(A.dpy), n, c, &type, &v) ? 1 : 0;
}

void e26_settings_session(e26_app *a, int menu_override, int title_override);

static void apply_settings(void)
{
	static char ts[2];
	int menu_override = resource_given("noUISound", "NoUISound");
	int title_override = resource_given("introPlaySounds", "IntroPlaySounds");
	if (state_open(A.settings_dir) && A.verbose) fprintf(stderr, "d32library: settings: %s/LIBRARY.INI unreadable; defaults\n", state_dir());
	if (A.verbose) fprintf(stderr, "d32library: settings in %s\n", state_dir());
	if (!resource_given("showTitleScreen", "ShowTitleScreen") && set_stored("ShowTitleScreen")) A.show_title = set_get_bool("ShowTitleScreen", 1) ? True : False;
	if (!title_override && set_stored("IntroPlaySounds")) A.intro_sounds = set_get_bool("IntroPlaySounds", 1) ? True : False;
	if (!menu_override && set_stored("NoUISound")) A.no_ui_sound = set_get_bool("NoUISound", 0) ? True : False;
	e26_settings_session(&A, menu_override, title_override);
	if (!resource_given("typeSize", "TypeSize") && set_stored("TextStyle")) {
		/* the period program seg100:0bbc: "m..." is Medium, "l..." Large, anything else Small */
		const char *v = set_get("TextStyle");
		ts[0] = v && (v[0] == 'm' || v[0] == 'M') ? 'M' : v && (v[0] == 'l' || v[0] == 'L') ? 'L' : 'S';
		A.type_size = ts;
	}
}

/* controls bound to a setting ("prop setting <Key>", a check box) show its stored state (plan O3) */
static void setting_controls(void)
{
	int i, k;
	for (i = 0; i < A.set.ns; i++)
		for (k = 0; k < A.set.s[i].nc; k++) {
			scr_control *c = &A.set.s[i].c[k];
			const char *key = scr_prop_get(c, "setting");
			if (key && set_stored(key)) c->checked = set_get_bool(key, c->checked);
		}
}

/* ---------------------------------------------------------------- start */

static int load_screens(void)
{
	char err[400];
	int i;
	scr_set_store(&A.set, A.store);
	if (scr_load_dir(&A.set, A.screens, err, sizeof err) || scr_keys_load_dir(&A.set, A.screens, err, sizeof err)) {
		fprintf(stderr, "d32library: %s\n", err);
		return -1;
	}
	if (scr_validate(&A.set, err, sizeof err)) { fprintf(stderr, "d32library: %s\n", err); return -1; }
	/* an overlay's origin is given inside its base screen's work area; nav.c makes it absolute for the base it lies
	 * over (its named screen, or with "over *" the full screen in front) */
	for (i = 0; i < A.set.ns; i++) {
		scr_screen *s = &A.set.s[i], *base = s->over[0] && strcmp(s->over, "*") ? scr_find(&A.set, s->over) : NULL;
		s->rox = s->ox;
		s->roy = s->oy;
		if (base) { s->ox += base->ox; s->oy += base->oy; }
	}
	if (A.verbose) fprintf(stderr, "d32library: %d screens, %d menus, %d key bindings from %s\n", A.set.ns, A.set.nm, A.set.nkeys, A.screens);
	return 0;
}

/* A control's seven colour slots: its record's, with -1 leaving the class default (records.h) in place and -2
 * kept as transparent; a control without a record takes the class defaults. */
static void control_slots(scr_control *c)
{
	rec_control r;
	int k, have = c->record > 0 && !rec_control_load(A.store, c->record, &r);
	for (k = 0; k < REC_NSLOTS; k++) {
		int idx = have ? r.colour[k] : REC_UNSET;
		if (idx == REC_TRANSPARENT) c->slot[k] = -2;
		else c->slot[k] = rec_pal(&A.pal, idx >= 0 && idx < REC_PAL_N ? idx : rec_slot_default[k]);
	}
	c->has_slots = 1;
}

/* Every colour Deep32 Library draws from here on comes from the store's interface colour table (PALETTE.md): the
 * screen files' ui:<n> colours, the records' slots and menu colours, and ui:<n> colours in the taxonomy. */
static int load_palette(void)
{
	char err[300];
	int i, k;
	if (rec_palette_load(A.store, &A.classic_pal)) {
		fprintf(stderr, "d32library: %s/ui/palette.tsv is missing or incomplete (%d of 32 colours): re-run d32-library-import\n", A.store, A.classic_pal.n);
		return -1;
	}
	A.appearance_theme = e26_theme_index(set_get("AppearanceTheme"));
	e26_theme_preview_palette(&A, A.appearance_theme, &A.pal);
	if (scr_resolve_colours(&A.set, A.pal.rgb, REC_PAL_N, err, sizeof err)) { fprintf(stderr, "d32library: %s\n", err); return -1; }
	e26_theme_apply_bands(&A);
	if (A.verbose) fprintf(stderr, "d32library: appearance %s\n", e26_themes[A.appearance_theme].id);
	for (i = 0; i < A.set.ns; i++)
		for (k = 0; k < A.set.s[i].nc; k++) control_slots(&A.set.s[i].c[k]);
	for (i = 0; i < A.set.nb; i++)
		for (k = 0; k < A.set.blocks[i].nc; k++) control_slots(&A.set.blocks[i].c[k]);
	for (i = 0; i < A.set.nm; i++) {
		scr_menu *m = &A.set.m[i];
		rec_menu r;
		if (m->record > 0 && !rec_menu_load(A.store, m->record, &r)) {
			m->colour[0] = rec_pal(&A.pal, r.colour_a);
			m->colour[1] = rec_pal(&A.pal, r.colour_b);
			m->colour[2] = rec_pal(&A.pal, r.colour_c);
			m->rec_flags = r.flags;
			m->margin = r.margin;
		}
	}
	return 0;
}

/* Motif's own widgets take table colours too. The article scroll bar after the original's MAINUP/MAINDN and
 * MTHUMB strips (PALETTE.md 6, [i]): arrow face 31 with a white and a taupe edge, thumb 13, track 28. */
static void widget_colours(void)
{
	e26_app *a = &A;
	Pixel black = xa_pixel(a, ui_rgb(a, UI_BLACK));
	XtVaSetValues(a->top, XmNbackground, black, NULL);
	XtVaSetValues(a->da, XmNbackground, black, NULL);
	XtVaSetValues(a->sb, XmNbackground, xa_pixel(a, ui_rgb(a, UI_PAPER)),
	              XmNforeground, xa_pixel(a, ui_rgb(a, UI_TAUPE)),
	              XmNtroughColor, xa_pixel(a, ui_rgb(a, UI_CHARCOAL)),
	              XmNtopShadowColor, xa_pixel(a, ui_rgb(a, UI_WHITE)),
	              XmNbottomShadowColor, xa_pixel(a, ui_rgb(a, UI_TAUPE)),
#ifdef XmNsliderVisual
	              XmNsliderVisual, XmFOREGROUND_COLOR,
#endif
	              NULL);
}

static void expose_cb(Widget w, XtPointer cd, XtPointer call)
{
	(void)w; (void)cd; (void)call;
	draw_flush(&A);
}

/* The drawing area follows the shell's size (maximised, restored, or a -geometry larger than the frame): the frame's
 * factor and place are worked out again, the rooms follow (the Motif scroll bar), and the window is drawn again. */
static void configure_handler(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_event e;
	(void)w; (void)cd; (void)cont;
	if (ev->type != ConfigureNotify || !A.cur) return;
	if (present_resize(&A, ev->xconfigure.width, ev->xconfigure.height)) {
		memset(&e, 0, sizeof e);
		e.type = EV_RESIZE;
		e.ctl = -1;
		ev_publish(&A, &e);
		draw_flush(&A);
	}
}

static void quit_cb(Widget w, XtPointer cd, XtPointer call)
{
	(void)w; (void)cd; (void)call;
	e26_quit(&A);
}

static void start_main(void);

int main(int argc, char **argv)
{
	XGCValues gv;
	Atom wm_delete;
	char err[300];
	const char *start, *display, *library;
	int open_status;
	if (openat_args(&argc, argv, pending_uri) < 0) {
		fprintf(stderr, "d32library: usage: -open library:<pack>/<article>[#<anchor>]\n");
		return 2;
	}
	A.hot = A.pressed = A.menu_hot = A.sub_hot = A.outline_hot = -1;
	A.cur_sub = -1;
	A.snd_pid = -1;
	A.pan_hot = A.flt_cat = A.flt_sub = -1;
	scale_fit(&A.sc, E26_W, E26_H);
	/* the registries: the built-in actions and settings keys, then every module (modules.gen.c) */
	actions_register();
	state_builtin_keys();
	e26_modules_register();
	/* the window manager may maximise the window (update 1: the frame is enlarged k times in it); it is never
	 * smaller than the frame, and there is no free resizing: maximised or 640x480. The base size is 0 so that a
	 * -geometry size is in pixels (without one, XWMGeometry takes the minimum as the base: 1920x1080 gave 2560x1560) */
	A.top = XtVaOpenApplication(&A.ctx, "Deep32Library", options, XtNumber(options), &argc, argv, fallbacks,
	                            applicationShellWidgetClass, XmNmwmDecorations, MWM_DECOR_BORDER, /* the caption is the Library's own (main.scr block caption) */
	                            XmNmwmFunctions, MWM_FUNC_MOVE | MWM_FUNC_MINIMIZE | MWM_FUNC_MAXIMIZE | MWM_FUNC_CLOSE,
	                            XmNminWidth, E26_W, XmNminHeight, E26_H, XmNbaseWidth, 0, XmNbaseHeight, 0,
	                            XmNiconName, "Deep32 Encyclopedia", NULL);
	XtGetApplicationResources(A.top, &A, resources, XtNumber(resources), NULL, 0);
	if (argc > 1) { fprintf(stderr, "d32library: unknown option %s\n", argv[1]); return 2; }
	A.dpy = XtDisplay(A.top);
	display = DisplayString(A.dpy);
	library = getenv("E26_LIB_PACK");
	/* Preserve launches that select their own Library or start screen. */
	if (!pending_uri[0] && (A.start[0] || (library && strcmp(library, "/d32/libcontent/e26"))))
		display = NULL;
	open_status = openat_begin(&A, display, pending_uri);
	if (open_status != 0) return open_status > 0 ? 0 : 1;
	apply_settings();
	if (load_screens()) return 1;
	if (load_palette()) return 1;
	if (tax_load(A.taxonomy_path, &A.tax, err, sizeof err)) fprintf(stderr, "d32library: taxonomy: %s (Category menu empty)\n", err);
	else {
		A.have_tax = 1;
		taxonomy_colours(&A);
		if (A.verbose) fprintf(stderr, "d32library: taxonomy %s: %d categories%s\n", A.taxonomy_path, A.tax.ncat, A.tax.placeholder ? " (PLACEHOLDER)" : "");
	}
	if (rec_strings_load(A.store, &A.str) == 0) A.have_str = 1;
	else fprintf(stderr, "d32library: no strings in %s (is the asset store imported?)\n", A.store);
	menus_load(&A);
	findwiz_category_buttons(&A);
	setting_controls();
	pin_open(&A);

	A.da = XtVaCreateManagedWidget("library", xmDrawingAreaWidgetClass, A.top, XmNwidth, E26_W, XmNheight, E26_H,
	                               XmNmarginWidth, 0, XmNmarginHeight, 0, XmNresizePolicy, XmRESIZE_NONE, NULL);
	A.sb = XtVaCreateWidget("articleScroll", xmScrollBarWidgetClass, A.da, XmNorientation, XmVERTICAL,
	                        XmNx, 622, XmNy, 78, XmNwidth, 16, XmNheight, 400, XmNshadowThickness, 1,
	                        XmNtraversalOn, False, NULL);
	XtAddCallback(A.sb, XmNvalueChangedCallback, art_scroll_cb, (XtPointer)&A);
	XtAddCallback(A.sb, XmNdragCallback, art_scroll_cb, (XtPointer)&A);
	XtAddCallback(A.da, XmNexposeCallback, expose_cb, NULL);
	XtAddEventHandler(A.da, PointerMotionMask | ButtonPressMask | ButtonReleaseMask | LeaveWindowMask | KeyPressMask, False, input_handler, (XtPointer)&A);
	XtAddEventHandler(A.da, StructureNotifyMask, False, configure_handler, NULL);
	XtAddEventHandler(A.top, KeyPressMask, False, input_handler, (XtPointer)&A);
	wm_delete = XmInternAtom(A.dpy, "WM_DELETE_WINDOW", False);
	XmAddWMProtocolCallback(A.top, wm_delete, quit_cb, NULL);

	/* the screen's default visual and the root window serve the splash; the main window takes over once realised */
	A.win = RootWindow(A.dpy, DefaultScreen(A.dpy));
	A.vis = DefaultVisual(A.dpy, DefaultScreen(A.dpy));
	A.depth = DefaultDepth(A.dpy, DefaultScreen(A.dpy));
	if (A.vis->class != TrueColor) { fprintf(stderr, "d32library: Deep32 Encyclopedia needs a TrueColor visual (depth %d)\n", A.depth); return 1; }
	A.rmask = A.vis->red_mask; A.gmask = A.vis->green_mask; A.bmask = A.vis->blue_mask;
	A.gc = XCreateGC(A.dpy, A.win, 0, &gv);
	XSetGraphicsExposures(A.dpy, A.gc, False);
	start = pending_uri[0] ? "main" : A.start[0] ? A.start : A.show_title ? "title" : "main";
	if (!scr_find(&A.set, start)) { fprintf(stderr, "d32library: no screen %s\n", start); return 1; }
	snprintf(start_name, sizeof start_name, "%s", start);
	if (!A.splash || strcmp(start, "title") || title_splash(&A, start_main)) start_main();
	XtAppMainLoop(A.ctx);
	return 0;
}

static void start_main(void)
{
	XtRealizeWidget(A.top);
	A.win = XtWindow(A.da);
	{
		XWindowAttributes wa;
		XGetWindowAttributes(A.dpy, A.win, &wa);
		if (wa.visual->visualid != A.vis->visualid) fprintf(stderr, "d32library: the window's visual differs from the screen's default\n");
	}
	widget_colours();
	A.back = XCreatePixmap(A.dpy, A.win, E26_W, E26_H, (unsigned)A.depth);
	gfx_init(&A.g, &A, A.back, 1);
	{
		/* the window's first size (640x480, or a larger -geometry) sets the frame's factor */
		Dimension w = E26_W, h = E26_H;
		XtVaGetValues(A.da, XmNwidth, &w, XmNheight, &h, NULL);
		present_resize(&A, w, h);
		present_init(&A);
	}
	fonts_load(&A);
	nav_go(&A, start_name, 0);
	openat_ready(&A);
	if (!pending_uri[0] && !A.start[0] && set_get_bool("SetBookmarkOnExit", 1)) {
		const char *library = getenv("E26_LIB_PACK"), *position;
		if ((!library || !strcmp(library, "/d32/libcontent/e26")) &&
		    (position = set_get("LastPosition")) && *position) {
			char uri[OPENAT_URI_MAX + 1];
			openat_target target;
			e26_reader_pos saved;
			/* A plain startup may use the precise saved place. An explicit open keeps its anchor. */
			if (!openat_parse(position, &target) &&
			    state_reader_load(target.pack, target.article, &saved)) {
				snprintf(uri, sizeof uri, "library:%s/%s", target.pack, target.article);
				position = uri;
			}
			(void)openat_submit(&A, position);
		}
	}
	title_startup_timer(&A);
}
