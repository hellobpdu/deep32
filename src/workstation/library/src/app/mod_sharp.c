/* mod_sharp.c: SHARP mode's registrations (W1-SHARP, item 63). The drawing is gfx.c, fonts.c and present.c; this
 * module gives the scaler its LIBRARY.INI key and an action the Settings page (wave 2) can bind:
 *   Scaler=sharp|render|cpu      ([Library], Deep32 Library's own key): how a window larger than 640x480 shows the
 *                                frame; sharp (the default) draws it at full resolution, render and cpu are update
 *                                1's nearest enlargement (the fallback). *scaler / -scaler for the session wins.
 *   settings.scaler <name>       switches the scaler now, stores it and writes LIBRARY.INI; the frame is redrawn. */
#include "library_app.h"
#include <stdio.h>

static int set_scaler(e26_app *a, const e26_actx *x)
{
	const char *v = x->arg && x->arg[0] ? x->arg : "sharp";
	if (present_set_scaler(a, v)) {
		fprintf(stderr, "d32library: settings.scaler: no scaler %s (sharp, render, cpu)\n", v);
		return 0;
	}
	set_put("Scaler", present_scaler());
	set_save();
	return 1;
}

void e26_mod_mod_sharp(void);
void e26_mod_mod_sharp(void)
{
	static const e26_setting key = { "Scaler", "scaler", SET_STRING, "sharp" };
	static const e26_action act = { set_scaler };
	reg_add(REG_SETTING, key.key, &key, "mod_sharp", 0);
	reg_add(REG_ACTION, "settings.scaler", &act, "mod_sharp", 0);
}
