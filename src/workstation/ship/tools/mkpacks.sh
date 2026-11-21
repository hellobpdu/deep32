#!/bin/ksh
# mkpacks.sh: the packs of the First light PREVIEW medium (ship lane, the build VM, root), from stage.sh's trees and the
# Library content in /build/lanes/ship/libsrc. Output: /build/lanes/ship/media/d32/ (copied onto the medium's FFS
# partition as /d32): packs/ (system packs .tgz), library/ (Library packs), PACKS.tsv, SHA256 + SHA256.sig (the
# release key: system packs and PACKS.tsv) and library/SHA256 + SHA256.sig (the library key). The keys are made
# inside the build VM and named by D32_RELEASE_SEC, D32_RELEASE_PUB, D32_LIBRARY_SEC and D32_LIBRARY_PUB (no
# default). Usage: ksh mkpacks.sh [all|system|library|sign]
set -eu
umask 022
what=${1:-all}
L=/build/lanes/ship
S=$L/src
OUT=$L/media/d32
REL=D.32.12-preview
PY="/usr/local/bin/python3.14 -I $S/ship/tools/mkpack.py --release $REL"
[ "$(id -u)" -eq 0 ] || { echo "mkpacks: run as root" >&2; exit 1; }
log() { print -r -- "mkpacks: $*"; }
row() {	# row name file class rights
	print -r -- "$1	$2	$3	$(stat -f %z $OUT/$2)	$(sha256 -q $OUT/$2)	$4" >>$OUT/PACKS.tsv.new
}

m_system() {
	mkdir -p $OUT/packs
	$PY --name d32-node --class system \
		--rights "Deep32 code (deep32-engine, deep32-gateway, node shims): Apache-2.0, Copyright (C) 2026 Jeffrey A. Cummings (https://github.com/hellobpdu); CPython, SQLite and the PyPI wheels under their own licences (pyrag/locks)" \
		$L/stage/d32-node=. | gzip -6n >$OUT/packs/d32-node.tgz
	$PY --name d32-apps --class system \
		--rights "Deep32 code: Apache-2.0, except the picture and PDF helper e26-render, which uses MuPDF and is AGPL-3.0-or-later; Deep32 documentation: CC-BY-4.0; Deep32 art and sounds: CC BY-NC 4.0; Copyright (C) 2026 Jeffrey A. Cummings (https://github.com/hellobpdu); third-party artwork, data and fonts under their own terms (see NOTICES)" \
		$L/stage/d32-apps=. | gzip -6n >$OUT/packs/d32-apps.tgz
	log "system packs: $(ls -l $OUT/packs/*.tgz | awk '{ printf "%s %d MB; ", $NF, $5 / 1048576 }')"
}

m_library() {
	mkdir -p $OUT/library
	B=$L/libsrc
	$PY --name lib-encyclopedia --class library \
		--rights "Wikipedia text CC BY-SA 4.0 (and GFDL where stated); media under their own licences; via Kiwix" \
		$B/raw/kiwix=d32/libcontent/encyclopedia >$OUT/library/lib-encyclopedia.tar
	$PY --name lib-cisco --class library \
		--rights "Cisco: view only, personal use; do not redistribute (Cisco Web Site Terms of Use)" \
		$B/raw/cisco/files=d32/libcontent/cisco/files $B/raw/cisco/manifest.jsonl=d32/libcontent/cisco/manifest.jsonl \
		$B/raw/cisco/catalogue.jsonl=d32/libcontent/cisco/catalogue.jsonl >$OUT/library/lib-cisco.tar
	$PY --name lib-ietf --class library \
		--rights "IETF Trust Legal Provisions (BCP 78), as stated in each document; drafts are work in progress" \
		$B/raw/ietf=d32/libcontent/ietf | gzip -6n >$OUT/library/lib-ietf.tgz
	$PY --name lib-catalogue --class library \
		--rights "Deep32 catalogue and word wheels (ours); source licence texts as published" \
		$B/catalogue/catalogue.sqlite=d32/libcontent/catalogue/catalogue.sqlite \
		$B/catalogue/facts.sqlite=d32/libcontent/catalogue/facts.sqlite \
		$B/catalogue/wheels=d32/libcontent/catalogue/wheels $S/TAXONOMY.json=d32/libcontent/catalogue/TAXONOMY.json \
		$B/raw/_licences=d32/libcontent/licences | gzip -6n >$OUT/library/lib-catalogue.tgz
	log "library packs: $(ls -l $OUT/library/lib-* | awk '{ printf "%s %d MB; ", $NF, $5 / 1048576 }')"
}

m_sign() {
	: >$OUT/PACKS.tsv.new
	print -r -- "# name	file	class	bytes	sha256	rights (install order within a class = this order)" >>$OUT/PACKS.tsv.new
	row d32-node packs/d32-node.tgz system "Deep32 code (Apache-2.0) + third-party runtime under its own licences"
	row d32-apps packs/d32-apps.tgz system "Deep32 code (Apache-2.0; e26-render uses MuPDF and is AGPL-3.0-or-later), documentation (CC-BY-4.0), Deep32 art (CC BY-NC 4.0), third-party artwork under its own terms"
	for n in lib-encyclopedia lib-cisco lib-ietf lib-catalogue; do
		f=$(cd $OUT && ls library/$n.t*)
		row $n $f library "Library class (see the pack INFO for its rights)"
	done
	mv $OUT/PACKS.tsv.new $OUT/PACKS.tsv
	(cd $OUT && sha256 PACKS.tsv packs/* >SHA256)
	signify -S -e -s "${D32_RELEASE_SEC:?set D32_RELEASE_SEC to the release secret key}" -m $OUT/SHA256 -x $OUT/SHA256.sig
	(cd $OUT && signify -C -p "${D32_RELEASE_PUB:?set D32_RELEASE_PUB to the release public key}" -x SHA256.sig >/dev/null)
	(cd $OUT/library && sha256 lib-* >SHA256)
	signify -S -e -s "${D32_LIBRARY_SEC:?set D32_LIBRARY_SEC to the library secret key}" -m $OUT/library/SHA256 -x $OUT/library/SHA256.sig
	(cd $OUT/library && signify -C -p "${D32_LIBRARY_PUB:?set D32_LIBRARY_PUB to the library public key}" -x SHA256.sig >/dev/null)
	rm -f $OUT/SHA256 $OUT/library/SHA256
	log "signed: $(sed -n '3,$p' $OUT/SHA256.sig | wc -l | tr -d ' ') files (release key), $(sed -n '3,$p' $OUT/library/SHA256.sig | wc -l | tr -d ' ') (library key)"
	cat $OUT/PACKS.tsv
}

case $what in
all) m_system; m_library; m_sign ;;
system|library|sign) m_$what ;;
*) echo "usage: mkpacks.sh [all|system|library|sign]" >&2; exit 64 ;;
esac
