#include "appearance.h"
#include <string.h>

/* Section bar, thumb/accent, light face, floating caption, dark panel and paper.
 * Classic uses the imported colour table. Other entries are authored presets. */
const e26_theme e26_themes[] = {
	{ "classic", "Classic", "The program's own palette", "original", 0, { 0 } },
	{ "theater", "Theater", "Midnight blue and pale lettering", "original", 0x142044,
	  { 0x2b3c68, 0xaabbd8, 0xdbe4f2, 0x354975, 0x202d4b, 0xeaf0f8 } },
	{ "parchment", "Parchment", "Parchment, gold and red-brown", "original", 0x58342a,
	  { 0x62463a, 0xd7b578, 0xeee0bf, 0x58464a, 0x433631, 0xf5ead2 } },
	{ "explorer", "Explorer", "Illustrated blue-green and cool gray", "original", 0x224b56,
	  { 0x365960, 0x9bc7c6, 0xdbe9e5, 0x395b61, 0x2c4247, 0xf0f5f3 } },
	{ "web", "Web", "Blue masthead and neutral panels", "original", 0x203d83,
	  { 0x355483, 0xb9c9e4, 0xe7eaf0, 0x36537b, 0x303b52, 0xf5f6fa } },
	{ "x11", "X11 Athena", "Neutral X workstation grays", "helvetica", 0x242424,
	  { 0x303030, 0xc0c0c0, 0xe4e4e4, 0x464646, 0x262626, 0xf3f3f3 } },
	{ "hpux", "HP-UX CDE", "Broica copper and slate", "helvetica", 0x4d3729,
	  { 0x3a444f, 0xeda870, 0xe7deda, 0x4e5a65, 0x31353e, 0xf7f3ec } },
	{ "aix", "AIX CDE", "Blue-gray and dark slate", "helvetica", 0x24384f,
	  { 0x3c4451, 0xa3b8ca, 0xd8e2eb, 0x40546b, 0x303740, 0xe8eef4 } },
	{ "hpvue", "HP VUE", "Muted rose and workstation slate", "helvetica", 0x4d4150,
	  { 0x3f465b, 0xd2aea4, 0xe5d5c9, 0x465468, 0x343a49, 0xf1e8da } },
	{ "contrast", "High Contrast", "White paper and high-contrast chrome", "helvetica", 0x111111,
	  { 0x202020, 0xffe680, 0xffffff, 0x333333, 0x171717, 0xfafafa } },
};
const size_t e26_theme_count = sizeof e26_themes / sizeof e26_themes[0];

/* u14 M9: the reading rules' colour (LC_RULE: the H1 and H2 rules, table rules, figure and code-box frames), in
 * e26_themes order, at least 3:1 on White and on Warm paper (readability spec section 6; the accent it replaces drew
 * High Contrast's rules #FFE680 on white, 1.24:1, and HP-UX's #EDA870, 2.0:1). The accent stays for bars and bands. */
static const long theme_rule[] = {
	0x8A8070, /* classic   3.89 on white, 3.63 on warm */
	0x5A6E96, /* theater   5.12 */
	0x8A6A3A, /* parchment 5.00 */
	0x4F7A7A, /* explorer  4.78 */
	0x5A6E9A, /* web       5.08 */
	0x707070, /* x11       4.95 */
	0x7A6250, /* hpux      5.69 */
	0x5E7488, /* aix       4.85 */
	0x8A6A64, /* hpvue     4.85 */
	0x000000, /* contrast 21.0 */
};
typedef char e26_theme_rule_check[sizeof theme_rule / sizeof theme_rule[0] == sizeof e26_themes / sizeof e26_themes[0] ? 1 : -1];

long e26_theme_rule(size_t index)
{
	return theme_rule[index < e26_theme_count ? index : 0];
}

/* u14 S6: the reading paper (ReaderPaper): White #FFFFFF with #000000 text, or Warm #FAF7EE with #1A1A1A text and
 * #1A4FA0 links (7.3:1) where JumpColor is the default navy; quiet text #4A4A4A (8.9:1) on either */
long e26_paper_rgb(int paper, int role)
{
	int warm = paper == E26_PAPER_WARM;
	switch (role) {
	case E26_PAPER_GROUND: return warm ? 0xFAF7EE : 0xFFFFFF;
	case E26_PAPER_TEXT: return warm ? 0x1A1A1A : 0x000000;
	case E26_PAPER_LINK: return warm ? 0x1A4FA0 : 0x000080;
	default: return 0x4A4A4A;
	}
}

size_t e26_theme_index(const char *id)
{
	size_t i;
	for (i = 0; id && i < e26_theme_count; i++)
		if (!strcmp(id, e26_themes[i].id)) return i;
	return 0;
}

const char *e26_theme_reader_font(size_t index)
{
	return e26_themes[index < e26_theme_count ? index : 0].reader_font;
}

void e26_theme_preview_palette(const e26_app *a, size_t index, rec_palette *out)
{
	static const int slots[] = { UI_CAPTION, UI_TAUPE, UI_STONE, UI_PERIWINKLE, UI_CHARCOAL, UI_PAPER };
	size_t i;
	*out = a->classic_pal;
	if (!index || index >= e26_theme_count) return;
	for (i = 0; i < sizeof slots / sizeof slots[0]; i++)
		out->rgb[slots[i]] = e26_themes[index].colours[i];
}

void e26_theme_apply_bands(e26_app *a)
{
	static const char *const names[] = { "main", "article" };
	size_t i;
	if (!a->appearance_theme || a->appearance_theme >= e26_theme_count) return;
	for (i = 0; i < sizeof names / sizeof names[0]; i++) {
		scr_screen *screen = scr_find(&a->set, names[i]);
		scr_control *band = screen ? scr_control_find(screen, "band") : NULL;
		if (band) band->bg = e26_themes[a->appearance_theme].title;
	}
}
