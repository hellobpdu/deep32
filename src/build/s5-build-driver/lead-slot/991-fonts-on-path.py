"""Restores the laptop's reachable HP VUE and Library font paths (release specification section 15, items 29, 31 and 33; source review).

What it changes
  Reverses only transform 510's font edits in 75-ship-desktop.sh and
  90-d32-ship-fontpath.conf, and restores transform 204's d32private read
  permissions in 68-ship-apps.sh. The original HP VUE BDF compile/mkfontdir
  loop and hpvue/d32private pcf/ttf paths return, with transform 603's names.
  No xft, xft-bdf or fontconfig copy returns (ruling D8).
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101-806 and 990.
  Returns the changed files.
Exit
  Raises when a target or expected text is missing or its count differs.
  A second run on its output raises, as does an empty folder.
"""
import os

EDITS = {
    'ship/site/etc/d32/site.d/75-ship-desktop.sh': [
        ("# dtlogin: X on UNIX sockets only, no XDMCP (ISO 9.1); the desktop's fonts on the server's path.",
         "# dtlogin: X on UNIX sockets only, no XDMCP (ISO 9.1); the private fonts on the server's path.", 1),
        ("# X fonts (boot test, 2026-11-06). The theme ships its faces as BDF sources (fonts/d32hp/src, from CDE 2.5.3's\n"
         '# interface BDFs) and Xorg drops a font directory that has no fonts.dir; and Xorg keeps only the LAST "Files"\n'
         "# section it reads from xorg.conf.d. So: compile the faces here (bdftopcf + mkfontdir, as the theme's\n"
         '# install-user.sh does for a home), then ONE Files section, sorted last, with the whole path in order: the Deep32\n'
         "# faces first (CDE's -dt-interface names), then X11's own.\n",
         '# X fonts (boot test, 2026-11-06). The look ships its faces as BDF sources (fonts/d32hp/src, fonts/hpvue/src) and\n'
         '# Xorg drops a font directory that has no fonts.dir; and Xorg keeps only the LAST "Files" section it reads from\n'
         "# xorg.conf.d, so the look's 11-d32-hpvue-fonts.conf hid its own 10-d32-fonts.conf. Result on the first test system:\n"
         "# no Deep32 faces at the greeter (CDE's -dt-interface names unresolved) and no Arial for Deep32 Library. So: compile the\n"
         "# faces here (bdftopcf + mkfontdir, as the theme's install-user.sh does for a home), then ONE Files section, sorted\n"
         "# last, with the whole path in order: Deep32 faces first (CDE's -dt-interface names), the VUE faces, his private\n"
         "# Windows 3.1 and Library faces (Deep32 Library asks for -monotype-arial-*), then X11's own (the look's order).\n", 1),
        ('for d in /usr/local/share/fonts/d32hp; do',
         'for d in /usr/local/share/fonts/d32hp /usr/local/share/fonts/hpvue; do', 1),
        ('for d in /usr/local/share/fonts/d32hp/; do',
         'for d in /usr/local/share/fonts/d32hp/ /usr/local/share/fonts/hpvue/ /usr/local/share/fonts/d32private/pcf/ \\\n'
         '\t/usr/local/share/fonts/d32private/ttf/; do', 1),
        ("\tprint -r -- '# xorg.conf.d, so this file, sorted last, holds the whole path: the Deep32 faces, then the X11'\n"
         "\tprint -r -- '# directories.'\n",
         "\tprint -r -- '# xorg.conf.d, so this file, sorted last, holds the whole path: Deep32 faces, VUE faces, the private'\n"
         "\tprint -r -- '# Windows 3.1 and Library faces, then the X11 directories.'\n", 1),
    ],
    'ship/u1/etc/X11/xorg.conf.d/90-d32-ship-fontpath.conf': [
        ('# holds the whole path: the Deep32 faces, then the X11 directories.\n',
         '# holds the whole path: Deep32 faces, VUE faces, the private Windows 3.1 and Library faces, then the X11 directories.\n', 1),
        ('    FontPath "/usr/local/share/fonts/d32hp/"\n',
         '    FontPath "/usr/local/share/fonts/d32hp/"\n'
         '    FontPath "/usr/local/share/fonts/hpvue/"\n'
         '    FontPath "/usr/local/share/fonts/d32private/pcf/"\n'
         '    FontPath "/usr/local/share/fonts/d32private/ttf/"\n', 1),
    ],
    'ship/site/etc/d32/site.d/68-ship-apps.sh': [
        ('# the generation: root:_d32core, files 0440, directories 0550 (ISO 4.1, security 21)\n',
         '# private fonts: the X server (privsep user _x11) must read them; this is his own disk, encrypted\n'
         'if [ -d /usr/local/share/fonts/d32private ]; then\n'
         '\tfind /usr/local/share/fonts/d32private -type d -exec chmod 0555 {} +\n'
         '\tfind /usr/local/share/fonts/d32private -type f -exec chmod 0444 {} +\n'
         'fi\n'
         '# the generation: root:_d32core, files 0440, directories 0550 (ISO 4.1, security 21)\n', 1),
    ],
}


def apply(tree_dir):
    changed = []
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
        for old, new, count in EDITS[rel]:
            found = text.count(old)
            if found != count:
                raise ValueError("%s: %r found %d times, expected %d" % (rel, old[:60], found, count))
            text = text.replace(old, new)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        changed.append(rel)
    return changed
