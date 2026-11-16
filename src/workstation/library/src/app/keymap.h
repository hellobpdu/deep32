/* keymap.h: Deep32 Library's key syntax (W1-INPUT, plan 3.2). X-free, so the screen parser, the tests and the helpers
 * can link it without Xlib.
 *
 * A key as a person writes it: "Ctrl+B", "Control+b", "Alt+,", "Ctrl+Alt+R", "Ctrl+Shift+C", "Ctrl++", "Shift+Tab",
 * "Ctrl+Ins", "PgDn", "F1". Modifiers (any order, any case): Ctrl or Control, Alt, Shift. The key: a single
 * character ("]", ",", "+", "a"), an alias (Esc, Enter, Ins, Del, PgUp, PgDn, Space, Backspace, case-insensitive),
 * or an X keysym name ("bracketright", "KP_Enter", "F12"). A letter means its key, whatever its case: "Ctrl+B" is
 * the B key with Control (write Shift+ for Shift).
 *
 * The canonical form is what F0's matcher (input.c binding_matches) and its lint already read: the modifiers in the
 * order Control+, Alt+, Shift+, then the keysym name ("Control+Alt+r", "Alt+comma", "Control+plus"). Two spellings of
 * one key have one canonical form, so duplicate detection compares canonical forms. */
#ifndef E26_KEYMAP_H
#define E26_KEYMAP_H
#include <stddef.h>
#include "scr.h"

#define KM_CONTROL 0x1
#define KM_ALT     0x2
#define KM_SHIFT   0x4

/* spec -> canonical form in out; returns the modifier bits (KM_*), or -1 when spec is not a key (an empty key, an
 * unknown modifier, a character with no keysym, a name with characters a keysym name cannot hold) */
int keymap_canon(const char *spec, char *out, size_t n);
/* the keysym name of a canonical form (the part after the modifiers) */
const char *keymap_keyname(const char *canon);
/* how a canonical key reads in Help and in the lint: "Ctrl+Alt+R", "Alt+,", "Ctrl++", "Shift+Tab" */
void keymap_display(const char *canon, char *out, size_t n);

/* The keymap lint over a loaded set (every screens/ *.keys): every key canonicalises; no key is bound twice in one
 * scope, however it is spelt (an error each); a key bound on one screen and on every screen ("*") is an override, which
 * is allowed and listed as a note. Writes one line per finding into report (may be NULL); returns the number of
 * errors. */
int keymap_lint(const scr_set *set, char *report, size_t n, int *notes);

#endif
