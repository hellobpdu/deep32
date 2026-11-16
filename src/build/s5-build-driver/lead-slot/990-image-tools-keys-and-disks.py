"""Makes the image tools sign with the key the build passes in and install the TEST medium onto a disk the build names, never the old test key or Hyper-V's disk name (release specification section 12, keys; build-guide.md build task).

PROPOSED for the lead's slot 990-999 by the s5-build-driver lane; nothing applies it unless the lead passes
--slot lead=<this folder> to apply-transforms.py.

What it changes
  ship/tools/mkimage-ship.sh and ship/tools/refresh-ramdisk.sh (the disc 1 medium and its TEST twin):
    - the signatures: /build/keys/d32test-release.sec and .pub (the old build VM's test key, build stage 5 traps) become
      D32_RELEASE_SEC and D32_RELEASE_PUB, which the build passes in with no default (mkpacks.sh already works so);
      the copied public keys become D32_RELEASE_PUB and D32_LIBRARY_PUB, written as d32-release.pub and
      d32-library.pub (the names transform 405 gave the site set);
    - the TEST medium's root disk: wd1 (Hyper-V Generation 1, IDE 0:1) becomes D32_TEST_ROOTDISK, with no default
      (sd1 on Proxmox: VirtIO SCSI, lab guide 5);
    - the TEST medium's random root password file moves from /build/private/ship/keys (the old VM's private
      folder) to $L/keys/test-root.pw inside the ship lane's own folder (made 0700 by the build).
  release/mkimage.sh (BASE's test image) and release/mksite.sh: the same key change (D32_RELEASE_SEC,
  D32_RELEASE_PUB; mksite.sh's test set then carries d32-release.pub). The arguments rootdisk and mediadisk keep
  their Hyper-V defaults (callers pass "sd1 sd0" on Proxmox).
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101 to 989. Returns the changed files.
Exit
  Raises when a target file or text is missing or appears a different number of times: never a silent skip.
"""
import os

EDITS = {
    "ship/tools/mkimage-ship.sh": [
        ("\t\tpw=$(cat /build/private/ship/keys/test-root.pw)\n", "\t\tpw=$(cat $L/keys/test-root.pw)\n", 1),
        ('-e "s|@ROOT_DISK@|wd1|"',
         '-e "s|@ROOT_DISK@|${D32_TEST_ROOTDISK:?set D32_TEST_ROOTDISK: sd1 on Proxmox, wd1 on Hyper-V Generation 1}|"',
         1),
        ("[ -f /build/private/ship/keys/test-root.pw ] || (umask 077; openssl rand -hex 12 >/build/private/ship/keys/test-root.pw)\n",
         "[ -f $L/keys/test-root.pw ] || (umask 077; mkdir -p $L/keys; openssl rand -hex 12 >$L/keys/test-root.pw)\n", 1),
        ("signify -S -s /build/keys/d32test-release.sec -m $OUT/SHA256 -x $OUT/SHA256.sig\n"
         "signify -V -p /build/keys/d32test-release.pub -m $OUT/SHA256 -x $OUT/SHA256.sig >/dev/null\n",
         'signify -S -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $OUT/SHA256 -x $OUT/SHA256.sig\n'
         'signify -V -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" -m $OUT/SHA256 -x $OUT/SHA256.sig >/dev/null\n',
         1),
        ("signify -S -s /build/keys/d32test-release.sec -m $OUT/test/SHA256 -x $OUT/test/SHA256.sig\n"
         "cp -p /build/keys/d32test-release.pub /build/keys/d32test-library.pub $OUT/\n",
         'signify -S -s "$D32_RELEASE_SEC" -m $OUT/test/SHA256 -x $OUT/test/SHA256.sig\n'
         'cp -p "$D32_RELEASE_PUB" $OUT/d32-release.pub\n'
         'cp -p "${D32_LIBRARY_PUB:?set D32_LIBRARY_PUB to the library public key}" $OUT/d32-library.pub\n', 1),
    ],
    "ship/tools/refresh-ramdisk.sh": [
        ('hash=$(print -r -- "$(cat /build/private/ship/keys/test-root.pw)" | encrypt -b a)',
         'hash=$(print -r -- "$(cat /build/lanes/ship/keys/test-root.pw)" | encrypt -b a)', 1),
        ('-e "s|@ROOT_DISK@|wd1|"',
         '-e "s|@ROOT_DISK@|${D32_TEST_ROOTDISK:?set D32_TEST_ROOTDISK: sd1 on Proxmox, wd1 on Hyper-V Generation 1}|"',
         1),
        ("signify -S -s /build/keys/d32test-release.sec -m $OUT/SHA256 -x $OUT/SHA256.sig\n",
         'signify -S -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $OUT/SHA256 -x $OUT/SHA256.sig\n',
         1),
        ("signify -S -s /build/keys/d32test-release.sec -m $OUT/test/SHA256 -x $OUT/test/SHA256.sig\n",
         'signify -S -s "$D32_RELEASE_SEC" -m $OUT/test/SHA256 -x $OUT/test/SHA256.sig\n', 1),
    ],
    "release/mkimage.sh": [
        ("signify -S -s /build/keys/d32test-release.sec -m $out/$name.SHA256 -x $out/$name.SHA256.sig\n"
         "signify -V -p /build/keys/d32test-release.pub -m $out/$name.SHA256 -x $out/$name.SHA256.sig >/dev/null\n",
         'signify -S -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $out/$name.SHA256 -x $out/$name.SHA256.sig\n'
         'signify -V -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" -m $out/$name.SHA256 -x $out/$name.SHA256.sig >/dev/null\n',
         1),
    ],
    "release/mksite.sh": [
        ("# /build/keys/d32test-release.pub). Root only", "# the release public key the build passes in as D32_RELEASE_PUB). Root only", 1),
        ("\tcp -p /build/keys/d32test-release.pub $stage/etc/signify/d32test-release.pub\n",
         '\tcp -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" $stage/etc/signify/d32-release.pub\n', 1),
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
        if "/build/keys/d32test" in text:
            raise ValueError("%s: the old test key's path is still named" % rel)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        changed.append(rel)
    return changed
