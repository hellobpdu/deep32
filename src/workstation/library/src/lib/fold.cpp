// fold.cpp: NFKC then full case folding through ICU's C API (W1-DATA; zimshim.h), the fold the pack builder applies
// to sort keys (ARTICLE-FORMAT-2: "Unicode NFKC followed by full casefold", Python's normalize("NFKC").casefold()).
// ICU is libzim's own dependency, so it is on the node already.
#include "zimshim.h"
#include <unicode/unorm2.h>
#include <unicode/ustring.h>
#include <unicode/utypes.h>
#include <cstdlib>
#include <cstring>

extern "C" char *lib_icu_fold(const char *s)
{
	UErrorCode e = U_ZERO_ERROR;
	const UNormalizer2 *nfkc = unorm2_getNFKCInstance(&e);
	int32_t n = 0, m, f, o;
	UChar *u = nullptr, *v = nullptr, *w = nullptr;
	char *out = nullptr;
	if (U_FAILURE(e)) return nullptr;
	u_strFromUTF8(nullptr, 0, &n, s, -1, &e);
	if (e != U_BUFFER_OVERFLOW_ERROR && U_FAILURE(e)) return nullptr;
	e = U_ZERO_ERROR;
	if (!(u = static_cast<UChar *>(std::malloc(sizeof(UChar) * (n + 1))))) return nullptr;
	u_strFromUTF8(u, n + 1, nullptr, s, -1, &e);
	if (U_FAILURE(e)) goto out;
	m = unorm2_normalize(nfkc, u, n, nullptr, 0, &e);
	if (e != U_BUFFER_OVERFLOW_ERROR && U_FAILURE(e)) goto out;
	e = U_ZERO_ERROR;
	if (!(v = static_cast<UChar *>(std::malloc(sizeof(UChar) * (m + 1))))) goto out;
	unorm2_normalize(nfkc, u, n, v, m + 1, &e);
	if (U_FAILURE(e)) goto out;
	f = u_strFoldCase(nullptr, 0, v, m, U_FOLD_CASE_DEFAULT, &e);
	if (e != U_BUFFER_OVERFLOW_ERROR && U_FAILURE(e)) goto out;
	e = U_ZERO_ERROR;
	if (!(w = static_cast<UChar *>(std::malloc(sizeof(UChar) * (f + 1))))) goto out;
	u_strFoldCase(w, f + 1, v, m, U_FOLD_CASE_DEFAULT, &e);
	if (U_FAILURE(e)) goto out;
	o = 0;
	u_strToUTF8(nullptr, 0, &o, w, f, &e);
	if (e != U_BUFFER_OVERFLOW_ERROR && U_FAILURE(e)) goto out;
	e = U_ZERO_ERROR;
	if (!(out = static_cast<char *>(std::malloc(o + 1)))) goto out;
	u_strToUTF8(out, o + 1, nullptr, w, f, &e);
	if (U_FAILURE(e)) { std::free(out); out = nullptr; }
out:
	std::free(u);
	std::free(v);
	std::free(w);
	return out;
}
