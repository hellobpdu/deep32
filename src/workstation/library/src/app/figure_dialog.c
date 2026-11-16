/* A native transient for article figures. It never enters the Reader's screen stack.
 * u14 S4: sized for the screen (0.88 of it, centred), lettered in the reader's interface face (F_RUI); Fit (never
 * enlarged: a picture smaller than the canvas shows 1:1, "Fit (enlarge)" offered), 100%, and zoom steps 25, 50,
 * 100, 200, 400 from +/- or the wheel (centred on the pointer); a button-1 drag pans, a double click toggles Fit and
 * 100%; N/P or Page Down/Up go to the next or previous figure ("Figure 3 of 12"). */
#include "library_app.h"
#include "figure_dialog.h"
#include "picture.h"
#include "room_article.h"
#include "select.h"
#include <X11/Shell.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <Xm/DrawingA.h>
#include <Xm/Form.h>
#include <Xm/Frame.h>
#include <Xm/Label.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/ScrollBar.h>
#include <Xm/Text.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct {
	e26_app *app;
	Widget shell, canvas, status, enlarge;
	Window reader_focus;
	char spec[512], status_text[160];
	XFontStruct *font;          /* the dialog's own copy of the reader's interface face */
	XmFontList fonts;
	int drag, last_x, last_y;   /* a button-1 drag pans */
	Time last_press;
} dialog;

static int within(Display *dpy, Window window, Widget parent)
{
	Widget w;
	for (w = XtWindowToWidget(dpy, window); w; w = XtParent(w))
		if (w == parent) return 1;
	return 0;
}

static int is_picture(const a2_media *m) { return m && m->kind && !strcmp(m->kind, "image"); }

/* "Figure 3 of 12": the open figure among the article's pictures */
static void figure_place(char *buf, size_t n)
{
	const art_state *st = art_st();
	int i, k = 0, count = 0;
	buf[0] = '\0';
	if (!st->is_v2) return;
	for (i = 0; i < st->d2.nmedia; i++) if (is_picture(&st->d2.media[i])) {
		count++;
		if (i <= st->media_index) k = count;
	}
	if (count > 1 && k) snprintf(buf, n, "Figure %d of %d  |  ", k, count);
}

void figure_dialog_redraw(e26_app *a)
{
	Dimension w, h;
	gfx_ctx g;
	char buf[128], text[160], place[40];
	if (!dialog.shell || dialog.app != a || !XtIsRealized(dialog.canvas)) return;
	XtVaGetValues(dialog.canvas, XmNwidth, &w, XmNheight, &h, NULL);
	gfx_init(&g, a, XtWindow(dialog.canvas), 1);
	gfx_unclip(&g);
	picture_view_draw(&g, dialog.spec, w, h);
	figure_place(place, sizeof place);
	snprintf(text, sizeof text, "%s%s", place, picture_view_info(buf, sizeof buf));
	if (strcmp(text, dialog.status_text)) {
		XmString label = XmStringCreateLocalized(text);
		XtVaSetValues(dialog.status, XmNlabelString, label, NULL);
		XmStringFree(label);
		snprintf(dialog.status_text, sizeof dialog.status_text, "%s", text);
	}
	if (dialog.enlarge) XtSetSensitive(dialog.enlarge, picture_view_small());
}

void figure_dialog_close(e26_app *a)
{
	Widget shell;
	Window current, restore = None;
	XWindowAttributes attr;
	XFontStruct *font;
	XmFontList fonts;
	int revert;
	if (!dialog.shell || dialog.app != a) return;
	shell = dialog.shell;
	font = dialog.font;
	fonts = dialog.fonts;
	XGetInputFocus(a->dpy, &current, &revert);
	if (within(a->dpy, current, shell)) restore = dialog.reader_focus;
	memset(&dialog, 0, sizeof dialog);
	picture_view_reset();
	XtPopdown(shell);
	XtDestroyWidget(shell);
	if (fonts) XmFontListFree(fonts);
	if (font) XFreeFont(a->dpy, font);
	if (restore && within(a->dpy, restore, a->top) &&
	    XGetWindowAttributes(a->dpy, restore, &attr) && attr.map_state == IsViewable)
		XSetInputFocus(a->dpy, restore, RevertToParent, CurrentTime);
}

/* the next or previous picture of the article in this window */
static void step_figure(e26_app *a, int dir)
{
	art_state *st = art_st();
	int i, action = reg_find(REG_ACTION, "article.enlarge");
	e26_actx x;
	if (!st->is_v2 || action < 0) return;
	for (i = st->media_index + dir; i >= 0 && i < st->d2.nmedia; i += dir)
		if (is_picture(&st->d2.media[i])) break;
	if (i < 0 || i >= st->d2.nmedia) return;
	st->media_index = i;
	memset(&x, 0, sizeof x);
	x.ctl = -1;
	(void)action_run(a, action, &x);
}

static void paint(Widget w, XtPointer app, XtPointer call)
{
	(void)w; (void)call;
	figure_dialog_redraw(app);
}

static void command(Widget w, XtPointer action, XtPointer call)
{
	(void)w; (void)call;
	if (!strcmp(action, "close")) figure_dialog_close(dialog.app);
	else if (!strcmp(action, "next")) step_figure(dialog.app, 1);
	else if (!strcmp(action, "previous")) step_figure(dialog.app, -1);
	else if (picture_view_change(action)) figure_dialog_redraw(dialog.app);
}

static void key(Widget w, XtPointer unused, XEvent *ev, Boolean *dispatch)
{
	const char *action;
	KeySym sym;
	(void)unused;
	if (ev->type != KeyPress || (ev->xkey.state & (ControlMask | Mod1Mask))) return;
	sym = XLookupKeysym(&ev->xkey, 0);
	if ((XmIsText(w) || XmIsScrollBar(w)) && sym != XK_Escape && sym != XK_f && sym != XK_1) return;
	switch (sym) {
	case XK_Escape: action = "close"; break;
	case XK_f: case XK_0: case XK_KP_0: case XK_KP_Insert: action = "fit"; break;
	case XK_1: case XK_KP_1: case XK_KP_End: action = "actual"; break;
	case XK_e: action = "enlarge"; break;
	case XK_plus: case XK_equal: case XK_KP_Add: action = "in"; break;
	case XK_minus: case XK_KP_Subtract: action = "out"; break;
	case XK_n: case XK_Next: action = "next"; break;
	case XK_p: case XK_Prior: action = "previous"; break;
	case XK_Left: action = "left"; break;
	case XK_Right: action = "right"; break;
	case XK_Up: action = "up"; break;
	case XK_Down: action = "down"; break;
	case XK_Home: action = "home"; break;
	case XK_End: action = "end"; break;
	default: return;
	}
	*dispatch = False;
	command(w, (XtPointer)action, NULL);
}

/* the wheel zooms at the pointer; button 1 drags the picture, a double click toggles Fit and 100% */
static void mouse(Widget w, XtPointer unused, XEvent *ev, Boolean *dispatch)
{
	char cmd[48];
	(void)w; (void)unused; (void)dispatch;
	if (ev->type == ButtonPress && (ev->xbutton.button == 4 || ev->xbutton.button == 5)) {
		snprintf(cmd, sizeof cmd, "%s@%d,%d", ev->xbutton.button == 4 ? "in" : "out", ev->xbutton.x, ev->xbutton.y);
		if (picture_view_change(cmd)) figure_dialog_redraw(dialog.app);
	} else if (ev->type == ButtonPress && ev->xbutton.button == 1) {
		if (dialog.last_press && ev->xbutton.time - dialog.last_press < 400 &&
		    abs(ev->xbutton.x - dialog.last_x) < 5 && abs(ev->xbutton.y - dialog.last_y) < 5) {
			dialog.last_press = 0;
			dialog.drag = 0;
			if (picture_view_change("toggle")) figure_dialog_redraw(dialog.app);
			return;
		}
		dialog.last_press = ev->xbutton.time;
		dialog.drag = 1;
		dialog.last_x = ev->xbutton.x;
		dialog.last_y = ev->xbutton.y;
	} else if (ev->type == MotionNotify && dialog.drag) {
		snprintf(cmd, sizeof cmd, "pan:%d,%d", ev->xmotion.x - dialog.last_x, ev->xmotion.y - dialog.last_y);
		dialog.last_x = ev->xmotion.x;
		dialog.last_y = ev->xmotion.y;
		if (picture_view_change(cmd)) figure_dialog_redraw(dialog.app);
	} else if (ev->type == ButtonRelease && ev->xbutton.button == 1) dialog.drag = 0;
}

static void mapped(Widget shell, XtPointer app, XEvent *ev, Boolean *dispatch)
{
	e26_app *a = app;
	Window current;
	XWindowAttributes attr;
	int revert;
	(void)dispatch;
	if (shell != dialog.shell || ev->type != MapNotify || ev->xmap.window != XtWindow(shell)) return;
	if (!XGetWindowAttributes(a->dpy, XtWindow(shell), &attr) || attr.map_state != IsViewable) return;
	XGetInputFocus(a->dpy, &current, &revert);
	if (current == None || current == PointerRoot || within(a->dpy, current, a->top))
		XSetInputFocus(a->dpy, XtWindow(shell), RevertToParent, CurrentTime);
	(void)XmProcessTraversal(dialog.canvas, XmTRAVERSE_CURRENT);
}

/* a copy of the reader's interface face (F_RUI) for the window's lettering: its own, so a Text size change that
 * reloads the reader's faces cannot pull it from under Motif */
static void reader_face(e26_app *a)
{
	unsigned long atom;
	char *name;
	XmFontListEntry entry;
	if (!a->font[F_RUI] || !XGetFontProperty(a->font[F_RUI], XA_FONT, &atom)) return;
	if (!(name = XGetAtomName(a->dpy, (Atom)atom))) return;
	dialog.font = XLoadQueryFont(a->dpy, name);
	XFree(name);
	if (!dialog.font) return;
	entry = XmFontListEntryCreate(XmFONTLIST_DEFAULT_TAG, XmFONT_IS_FONT, (XtPointer)dialog.font);
	dialog.fonts = XmFontListAppendEntry(NULL, entry);
	XmFontListEntryFree(&entry);
}

int figure_dialog_open(e26_app *a, const char *spec, const char *caption)
{
	static const char *const buttons[][2] = {
		{ "Fit", "fit" }, { "Fit (enlarge)", "enlarge" }, { "100%", "actual" }, { "Zoom out", "out" }, { "Zoom in", "in" },
		{ "Previous", "previous" }, { "Next", "next" }, { "Close", "close" }
	};
	Widget form, row, text, scroll, bar, frame;
	Arg text_args[10];
	Cardinal n = 0;
	Window current;
	int revert, w, h, x, y, sw, sh;
	size_t i, len = caption ? strlen(caption) : 0;
	char *display_text;
	if (!a->top || !a->da || !a->win || !spec || !*spec) return -1;
	sw = WidthOfScreen(XtScreen(a->top)); sh = HeightOfScreen(XtScreen(a->top));
	if (sw < 490 || sh < 335) return -1;
	if (dialog.shell) {
		if (!strcmp(dialog.spec, spec)) {
			XRaiseWindow(a->dpy, XtWindow(dialog.shell));
			return 0;
		}
		figure_dialog_close(a);
	}
	display_text = malloc(len + 1);
	if (!display_text) return -1;
	sel_utf8_to_cp1252(caption ? caption : "", len, display_text, len + 1);
	XGetInputFocus(a->dpy, &current, &revert);
	dialog.reader_focus = within(a->dpy, current, a->top) ? current : XtWindow(a->da);
	dialog.app = a;
	snprintf(dialog.spec, sizeof dialog.spec, "%s", spec);
	picture_view_reset();
	/* u14 S4: 0.88 of the screen, centred on it (1690 x 950 at 1920 x 1080) */
	w = sw * 88 / 100;
	h = sh * 88 / 100;
	if (w < 470) w = sw > 490 ? 470 : sw - 20;
	if (h < 300) h = sh > 335 ? 300 : sh - 35;
	x = (sw - w) / 2; y = (sh - h) / 2;
	reader_face(a);
	dialog.shell = XtVaCreatePopupShell("figure", transientShellWidgetClass, a->top,
	    XtNtitle, "Enlarge", XtNtransientFor, a->top, XtNinput, True, XtNallowShellResize, False,
	    XtNx, x, XtNy, y, XtNwidth, w, XtNheight, h, XtNminWidth, 470, XtNminHeight, 300,
	    XmNdeleteResponse, XmDO_NOTHING, NULL);
	form = XtVaCreateManagedWidget("figureForm", xmFormWidgetClass, dialog.shell, NULL);
	row = XtVaCreateManagedWidget("controls", xmRowColumnWidgetClass, form,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	for (i = 0; i < sizeof buttons / sizeof buttons[0]; i++) {
		XmString label = XmStringCreateLocalized((char *)buttons[i][0]);
		Widget button = XtVaCreateManagedWidget(buttons[i][1], xmPushButtonWidgetClass, row,
		    XmNlabelString, label, NULL);
		XmStringFree(label);
		if (dialog.fonts) XtVaSetValues(button, XmNfontList, dialog.fonts, NULL);
		if (!strcmp(buttons[i][1], "enlarge")) { dialog.enlarge = button; XtSetSensitive(button, False); }
		XtAddCallback(button, XmNactivateCallback, command, (XtPointer)buttons[i][1]);
		XtAddEventHandler(button, KeyPressMask, False, key, NULL);
	}
	dialog.status = XtVaCreateManagedWidget("mode", xmLabelWidgetClass, row,
	    XmNrecomputeSize, True, XmNalignment, XmALIGNMENT_BEGINNING, NULL);
	if (dialog.fonts) XtVaSetValues(dialog.status, XmNfontList, dialog.fonts, NULL);
	XtSetArg(text_args[n], XmNeditMode, XmMULTI_LINE_EDIT); n++;
	XtSetArg(text_args[n], XmNeditable, False); n++;
	XtSetArg(text_args[n], XmNcursorPositionVisible, False); n++;
	XtSetArg(text_args[n], XmNwordWrap, True); n++;
	XtSetArg(text_args[n], XmNscrollHorizontal, False); n++;
	XtSetArg(text_args[n], XmNrows, 3); n++;
	XtSetArg(text_args[n], XmNresizeHeight, False); n++;
	if (dialog.fonts) { XtSetArg(text_args[n], XmNfontList, dialog.fonts); n++; }
	text = XmCreateScrolledText(form, "caption", text_args, n);
	scroll = XtParent(text);
	XtVaSetValues(scroll, XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	XmTextSetString(text, display_text);
	free(display_text);
	XtManageChild(text);
	XtAddEventHandler(text, KeyPressMask, False, key, NULL);
	XtVaGetValues(scroll, XmNverticalScrollBar, &bar, NULL);
	if (bar) XtAddEventHandler(bar, KeyPressMask, False, key, NULL);
	frame = XtVaCreateManagedWidget("figureFrame", xmFrameWidgetClass, form,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, row,
	    XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, scroll,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, XmNshadowType, XmSHADOW_IN, NULL);
	dialog.canvas = XtVaCreateManagedWidget("figurePixels", xmDrawingAreaWidgetClass, frame, XmNtraversalOn, True, 
	    XmNwidth, (Dimension)(w - 8), XmNheight, (Dimension)(h > 200 ? h - 160 : 40), XmNresizePolicy, XmRESIZE_NONE, NULL);
	XtAddCallback(dialog.canvas, XmNexposeCallback, paint, a);
	XtAddCallback(dialog.canvas, XmNresizeCallback, paint, a);
	XtAddEventHandler(dialog.canvas, KeyPressMask, False, key, NULL);
	XtAddEventHandler(dialog.canvas, ButtonPressMask | ButtonReleaseMask | Button1MotionMask, False, mouse, NULL);
	XtAddEventHandler(dialog.shell, KeyPressMask, False, key, NULL);
	XtAddEventHandler(dialog.shell, StructureNotifyMask, False, mapped, a);
	XtRealizeWidget(dialog.shell);
	XSetTransientForHint(a->dpy, XtWindow(dialog.shell), XtWindow(a->top));
	XmAddWMProtocolCallback(dialog.shell, XmInternAtom(a->dpy, "WM_DELETE_WINDOW", False), command, "close");
	XtPopup(dialog.shell, XtGrabNone);
	return 0;
}
