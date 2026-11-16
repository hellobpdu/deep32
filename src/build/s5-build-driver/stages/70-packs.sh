# STAGE 70-packs: the system packs, PACKS.tsv and their signature (ship/tools/mkpacks.sh system, then the signature: the throwaway key for v0 and v1, the train key for RC1, whose passphrase he types in 910's console)
# AS root
# IN /build/lanes/ship/stage/d32-node, d32-apps; D32_RELEASE_SEC and D32_RELEASE_PUB (the library key only when Library packs exist: disc 1 has none)
# OUT /build/lanes/ship/media/d32/{packs/d32-node.tgz,packs/d32-apps.tgz,PACKS.tsv,SHA256.sig}
run() {
	M=/build/lanes/ship/media/d32
	rm -rf "$M"
	ksh "$S/ship/tools/mkpacks.sh" system || die "mkpacks.sh system failed"
	if ls "$M"/library/lib-* >/dev/null 2>&1; then
		ksh "$S/ship/tools/mkpacks.sh" sign || die "mkpacks.sh sign failed"
		return 0
	fi
	# Disc 1 carries no Library pack (S6 moves them to disc 2) and mkpacks.sh's m_sign lists four of them, so this is
	# the system half of m_sign, step for step: PACKS.tsv rows, SHA256 of PACKS.tsv and packs/*, signify -S -e, -C.
	# The rights text of each row is read from m_sign's own row line, so a transform that changes the wording (the
	# HP and Microsoft layers now ship in d32-apps under their own terms) changes it here too.
	mk=$S/ship/tools/mkpacks.sh
	rights() {	# rights NAME: the text m_sign's "row NAME packs/NAME.tgz system ..." line gives
		sed -n "s|^[[:space:]]*row $1 packs/$1\.tgz system \"\(.*\)\"[[:space:]]*\$|\1|p" "$mk"
	}
	rn=$(rights d32-node); ra=$(rights d32-apps)
	[ -n "$rn" ] && [ -n "$ra" ] || die "mkpacks.sh m_sign has no row line for d32-node or d32-apps (its form moved)"
	{
		printf '# name\tfile\tclass\tbytes\tsha256\trights (install order within a class = this order)\n'
		printf 'd32-node\tpacks/d32-node.tgz\tsystem\t%s\t%s\t%s\n' "$(stat -f %z "$M/packs/d32-node.tgz")" \
			"$(sha256 -q "$M/packs/d32-node.tgz")" "$rn"
		printf 'd32-apps\tpacks/d32-apps.tgz\tsystem\t%s\t%s\t%s\n' "$(stat -f %z "$M/packs/d32-apps.tgz")" \
			"$(sha256 -q "$M/packs/d32-apps.tgz")" "$ra"
	} >"$M/PACKS.tsv"
	(cd "$M" && sha256 PACKS.tsv packs/* >SHA256)
	signify -S -e -s "$D32_RELEASE_SEC" -m "$M/SHA256" -x "$M/SHA256.sig" || die "signify -S failed"
	(cd "$M" && signify -C -p "$D32_RELEASE_PUB" -x SHA256.sig >/dev/null) || die "signify -C failed"
	rm -f "$M/SHA256"
	log "signed with the $D32_KEYSET key: $(sed -n '3,$p' "$M/SHA256.sig" | wc -l | tr -d ' ') files"
	cat "$M/PACKS.tsv"
}
