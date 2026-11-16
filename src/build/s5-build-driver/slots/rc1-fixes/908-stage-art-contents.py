"""Copies generated art contents into the pack's existing folders (release specification section 15).

Copying a source folder into an existing destination nests an unwanted out/
folder. Copy its contents before the existing theme overlay, retaining that
overlay's precedence and the installed names.
"""
import os


TARGET = "ship/tools/stage.sh"
BEFORE = (
    b"\t# art (the art lane's originals and the Library category art)\n"
    b"\tcp -R $S/art/out $A/usr/local/share/d32/art/deep32\n"
    b"\tcp -R $S/art/library-categories/out $A/usr/local/share/d32/art/library-categories\n"
)
AFTER = (
    b"\t# Generated art first; the chosen theme below keeps precedence.\n"
    b"\tmkdir -p \"$A/usr/local/share/d32/art/deep32\" \"$A/usr/local/share/d32/art/library-categories\"\n"
    b"\tcp -R \"$S/art/out/.\" \"$A/usr/local/share/d32/art/deep32/\"\n"
    b"\tcp -R \"$S/art/library-categories/out/.\" \"$A/usr/local/share/d32/art/library-categories/\"\n"
)
THEME_ANCHOR = b"\t# the desktop: the public theme generator's output "


def apply(tree_dir):
    path = os.path.join(tree_dir, *TARGET.split("/"))
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("stage art target is not a plain file")
    with open(path, "rb") as stream:
        data = stream.read()
    if data.count(BEFORE) != 1 or data.count(THEME_ANCHOR) != 1 or AFTER in data:
        raise ValueError("stage art copy block differs or was already corrected")
    data = data.replace(BEFORE, b"")
    data = data.replace(THEME_ANCHOR, AFTER + THEME_ANCHOR)
    with open(path, "wb") as stream:
        stream.write(data)
    return [TARGET]
