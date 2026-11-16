/* text8.c: Unicode text on the Library's 8-bit faces (text8.h). */
#include "text8.h"
#include <string.h>

int t8_decode(const char *str, size_t n, uint32_t *cp)
{
	const unsigned char *s = (const unsigned char *)str;
	uint32_t c;
	int len, i;
	if (!n) { *cp = 0xFFFD; return 1; }
	c = s[0];
	if (c < 0x80) { *cp = c; return 1; }
	if (c >= 0xC2 && c <= 0xDF) { len = 2; c &= 0x1F; }
	else if (c >= 0xE0 && c <= 0xEF) { len = 3; c &= 0x0F; }
	else if (c >= 0xF0 && c <= 0xF4) { len = 4; c &= 0x07; }
	else { *cp = 0xFFFD; return 1; }
	if ((size_t)len > n) { *cp = 0xFFFD; return 1; }
	for (i = 1; i < len; i++) {
		if ((s[i] & 0xC0) != 0x80) { *cp = 0xFFFD; return 1; }
		c = (c << 6) | (s[i] & 0x3F);
	}
	if ((len == 3 && c < 0x800) || (len == 4 && (c < 0x10000 || c > 0x10FFFF)) || (c >= 0xD800 && c <= 0xDFFF)) {
		*cp = 0xFFFD;
		return 1;
	}
	*cp = c;
	return len;
}

int t8_encode(uint32_t c, char *o)
{
	if (c < 0x80) { o[0] = (char)c; return 1; }
	if (c < 0x800) { o[0] = (char)(0xC0 | (c >> 6)); o[1] = (char)(0x80 | (c & 0x3F)); return 2; }
	if (c < 0x10000) { o[0] = (char)(0xE0 | (c >> 12)); o[1] = (char)(0x80 | ((c >> 6) & 0x3F)); o[2] = (char)(0x80 | (c & 0x3F)); return 3; }
	o[0] = (char)(0xF0 | (c >> 18)); o[1] = (char)(0x80 | ((c >> 12) & 0x3F)); o[2] = (char)(0x80 | ((c >> 6) & 0x3F));
	o[3] = (char)(0x80 | (c & 0x3F));
	return 4;
}

/* code page 1252 at 0x80-0x9F (0 = undefined there), with the Latin-1 spelling used when the face lacks the glyph */
static const struct { uint16_t cp; const char *latin1; } cp1252_hi[32] = {
	{ 0x20AC, "EUR" }, { 0, NULL }, { 0x201A, "," }, { 0x0192, "f" }, { 0x201E, ",," }, { 0x2026, "..." }, { 0x2020, "+" },
	{ 0x2021, "++" }, { 0x02C6, "^" }, { 0x2030, "%o" }, { 0x0160, "S" }, { 0x2039, "<" }, { 0x0152, "OE" }, { 0, NULL },
	{ 0x017D, "Z" }, { 0, NULL }, { 0, NULL }, { 0x2018, "'" }, { 0x2019, "'" }, { 0x201C, "\"" }, { 0x201D, "\"" },
	{ 0x2022, "\xB7" }, { 0x2013, "-" }, { 0x2014, "--" }, { 0x02DC, "~" }, { 0x2122, "TM" }, { 0x0161, "s" },
	{ 0x203A, ">" }, { 0x0153, "oe" }, { 0, NULL }, { 0x017E, "z" }, { 0x0178, "Y" },
};

int t8_cp1252(uint32_t cp)
{
	int i;
	if ((cp >= 0x20 && cp < 0x7F) || (cp >= 0xA0 && cp <= 0xFF)) return (int)cp;
	for (i = 0; i < 32; i++)
		if (cp1252_hi[i].cp && cp1252_hi[i].cp == cp) return 0x80 + i;
	return -1;
}

/* the Adobe Symbol encoding: Greek in its own order, then the signs (Latin-1 signs such as the degree sign, plus-minus,
 * multiplication and division stay on the text face) */
static const char greek_upper[] = "ABGDEZHQIKLMNXOPR\0STUFCYW";   /* U+0391.. : Alpha Beta Gamma Delta ... Omega */
static const char greek_lower[] = "abgdezhqiklmnxoprVstufcyw";   /* U+03B1.. : final sigma (U+03C2) is 'V' */
static const struct { uint16_t cp; unsigned char b; } symbol_signs[] = {
	{ 0x2200, 0x22 }, { 0x2203, 0x24 }, { 0x220B, 0x27 }, { 0x2217, 0x2A }, { 0x2212, 0x2D }, { 0x2245, 0x40 },
	{ 0x03D1, 0x4A }, { 0x03C2, 0x56 }, { 0x22A5, 0x5E }, { 0x03D5, 0x6A }, { 0x03D6, 0x76 }, { 0x223C, 0x7E },
	{ 0x03D2, 0xA1 }, { 0x2032, 0xA2 }, { 0x2264, 0xA3 }, { 0x2044, 0xA4 }, { 0x221E, 0xA5 }, { 0x2663, 0xA7 },
	{ 0x2666, 0xA8 }, { 0x2665, 0xA9 }, { 0x2660, 0xAA }, { 0x2194, 0xAB }, { 0x2190, 0xAC }, { 0x2191, 0xAD },
	{ 0x2192, 0xAE }, { 0x2193, 0xAF }, { 0x2033, 0xB2 }, { 0x2265, 0xB3 }, { 0x221D, 0xB5 }, { 0x2202, 0xB6 },
	{ 0x2260, 0xB9 }, { 0x2261, 0xBA }, { 0x2248, 0xBB }, { 0x2135, 0xC0 }, { 0x2111, 0xC1 }, { 0x211C, 0xC2 },
	{ 0x2118, 0xC3 }, { 0x2297, 0xC4 }, { 0x2295, 0xC5 }, { 0x2205, 0xC6 }, { 0x2229, 0xC7 }, { 0x222A, 0xC8 },
	{ 0x2283, 0xC9 }, { 0x2287, 0xCA }, { 0x2284, 0xCB }, { 0x2282, 0xCC }, { 0x2286, 0xCD }, { 0x2208, 0xCE },
	{ 0x2209, 0xCF }, { 0x2220, 0xD0 }, { 0x2207, 0xD1 }, { 0x220F, 0xD5 }, { 0x221A, 0xD6 }, { 0x22C5, 0xD7 },
	{ 0x2227, 0xD9 }, { 0x2228, 0xDA }, { 0x21D4, 0xDB }, { 0x21D0, 0xDC }, { 0x21D1, 0xDD }, { 0x21D2, 0xDE },
	{ 0x21D3, 0xDF }, { 0x25CA, 0xE0 }, { 0x2329, 0xE1 }, { 0x27E8, 0xE1 }, { 0x2211, 0xE5 }, { 0x232A, 0xF1 },
	{ 0x27E9, 0xF1 }, { 0x222B, 0xF2 }, { 0x2234, 0x5C }, { 0x2223, 0x7C }, { 0x2309, 0xF9 }, { 0x2308, 0xE9 },
	{ 0x230B, 0xFB }, { 0x230A, 0xEB },
};

int t8_symbol(uint32_t cp)
{
	size_t i;
	if (cp >= 0x0391 && cp <= 0x03A9 && greek_upper[cp - 0x0391]) return (unsigned char)greek_upper[cp - 0x0391];
	if (cp >= 0x03B1 && cp <= 0x03C9) return (unsigned char)greek_lower[cp - 0x03B1];
	for (i = 0; i < sizeof symbol_signs / sizeof symbol_signs[0]; i++)
		if (symbol_signs[i].cp == cp) return symbol_signs[i].b;
	return -1;
}

/* Latin Extended-A, U+0100-U+017F, to base letters (two-letter spellings below) */
static const char latin_ext_a[] =
	"AaAaAaCcCcCcCcDd" "DdEeEeEeEeEeGgGg" "GgGgHhHhIiIiIiIi" "Ii??JjKkkLlLlLlL"
	"lLlNnNnNn?NnOoOo" "Oo??RrRrRrSsSsSs" "SsTtTtTtUuUuUuUu" "UuUuWwYyYZzZzZzs";

static const struct { uint32_t cp; const char *s; } translit[] = {
	/* spaces, joiners and marks that draw nothing */
	{ 0x2000, " " }, { 0x2001, " " }, { 0x2002, " " }, { 0x2003, " " }, { 0x2004, " " }, { 0x2005, " " },
	{ 0x2006, " " }, { 0x2007, " " }, { 0x2008, " " }, { 0x2009, " " }, { 0x200A, " " }, { 0x202F, "\xA0" },
	{ 0x205F, " " }, { 0x3000, " " }, { 0x200B, "" }, { 0x200C, "" }, { 0x200D, "" }, { 0x2060, "" }, { 0xFEFF, "" },
	{ 0x200E, "" }, { 0x200F, "" },
	/* dashes, quotes and punctuation */
	{ 0x2010, "-" }, { 0x2011, "-" }, { 0x2012, "-" }, { 0x2015, "--" }, { 0x2212, "-" }, { 0x2043, "-" },
	{ 0x201B, "'" }, { 0x201F, "\"" }, { 0x2032, "'" }, { 0x2033, "\"" }, { 0x2034, "'''" }, { 0x2035, "`" },
	{ 0x2044, "/" }, { 0x2215, "/" }, { 0x2216, "\\" }, { 0x2027, "\xB7" }, { 0x2219, "\xB7" }, { 0x22C5, "\xB7" },
	{ 0x2023, ">" }, { 0x2024, "." }, { 0x2025, ".." }, { 0x2047, "??" }, { 0x203C, "!!" }, { 0x2048, "?!" },
	{ 0x2049, "!?" }, { 0x2116, "No" }, { 0x2103, "\xB0" "C" }, { 0x2109, "\xB0" "F" }, { 0x2122, "TM" },
	{ 0x2117, "(P)" }, { 0x2120, "SM" }, { 0x02BC, "'" }, { 0x02B9, "'" }, { 0x02BA, "\"" }, { 0x02C8, "'" },
	{ 0x02CC, "," }, { 0x02D0, ":" }, { 0x02DA, "\xB0" }, { 0x02DD, "\"" },
	/* ligatures */
	{ 0xFB00, "ff" }, { 0xFB01, "fi" }, { 0xFB02, "fl" }, { 0xFB03, "ffi" }, { 0xFB04, "ffl" }, { 0xFB05, "st" },
	{ 0xFB06, "st" }, { 0x0132, "IJ" }, { 0x0133, "ij" }, { 0x0149, "'n" }, { 0x0152, "OE" }, { 0x0153, "oe" },
	/* Latin Extended-B and others common in names */
	{ 0x0180, "b" }, { 0x0189, "D" }, { 0x0191, "F" }, { 0x0192, "f" }, { 0x0197, "I" }, { 0x01A0, "O" },
	{ 0x01A1, "o" }, { 0x01AF, "U" }, { 0x01B0, "u" }, { 0x01B5, "Z" }, { 0x01B6, "z" }, { 0x01C4, "DZ" },
	{ 0x01C6, "dz" }, { 0x01C7, "LJ" }, { 0x01C9, "lj" }, { 0x01CA, "NJ" }, { 0x01CC, "nj" }, { 0x01CD, "A" },
	{ 0x01CE, "a" }, { 0x01CF, "I" }, { 0x01D0, "i" }, { 0x01D1, "O" }, { 0x01D2, "o" }, { 0x01D3, "U" },
	{ 0x01D4, "u" }, { 0x01E6, "G" }, { 0x01E7, "g" }, { 0x01EA, "O" }, { 0x01EB, "o" }, { 0x01F0, "j" },
	{ 0x0218, "S" }, { 0x0219, "s" }, { 0x021A, "T" }, { 0x021B, "t" }, { 0x0237, "j" }, { 0x0259, "e" },
	{ 0x1E9E, "SS" }, { 0x1EA0, "A" }, { 0x1EA1, "a" }, { 0x1EB8, "E" }, { 0x1EB9, "e" }, { 0x1ECA, "I" },
	{ 0x1ECB, "i" }, { 0x1ECC, "O" }, { 0x1ECD, "o" }, { 0x1EE4, "U" }, { 0x1EE5, "u" }, { 0x1EF2, "Y" },
	{ 0x1EF3, "y" },
	/* arrows, relations and operators when the Symbol face is absent */
	{ 0x2190, "<-" }, { 0x2192, "->" }, { 0x2194, "<->" }, { 0x2191, "^" }, { 0x2193, "v" }, { 0x21D0, "<=" },
	{ 0x21D2, "=>" }, { 0x21D4, "<=>" }, { 0x2264, "<=" }, { 0x2265, ">=" }, { 0x2260, "!=" }, { 0x2248, "~" },
	{ 0x2261, "==" }, { 0x2245, "~=" }, { 0x223C, "~" }, { 0x221E, "inf" }, { 0x2217, "*" }, { 0x2223, "|" },
	{ 0x2225, "||" }, { 0x2227, "&" }, { 0x2228, "|" }, { 0x2218, "o" }, { 0x2236, ":" }, { 0x226A, "<<" },
	{ 0x226B, ">>" }, { 0x2026, "..." }, { 0x22EF, "..." }, { 0x2295, "(+)" }, { 0x2297, "(x)" },
	{ 0x2329, "<" }, { 0x232A, ">" }, { 0x27E8, "<" }, { 0x27E9, ">" }, { 0x2308, "[" }, { 0x2309, "]" },
	{ 0x230A, "[" }, { 0x230B, "]" },
	/* superscript and subscript digits beyond Latin-1's three */
	{ 0x2070, "0" }, { 0x2074, "4" }, { 0x2075, "5" }, { 0x2076, "6" }, { 0x2077, "7" }, { 0x2078, "8" },
	{ 0x2079, "9" }, { 0x207A, "+" }, { 0x207B, "-" }, { 0x207F, "n" }, { 0x2080, "0" }, { 0x2081, "1" },
	{ 0x2082, "2" }, { 0x2083, "3" }, { 0x2084, "4" }, { 0x2085, "5" }, { 0x2086, "6" }, { 0x2087, "7" },
	{ 0x2088, "8" }, { 0x2089, "9" }, { 0x2153, "1/3" }, { 0x2154, "2/3" }, { 0x215B, "1/8" },
	/* letterlike symbols */
	{ 0x2102, "C" }, { 0x210D, "H" }, { 0x2115, "N" }, { 0x2119, "P" }, { 0x211A, "Q" }, { 0x211D, "R" }, { 0x2124, "Z" },
	{ 0x210E, "h" }, { 0x2113, "l" }, { 0x212F, "e" }, { 0x2134, "o" },
	/* box drawing (RFC figures pasted from HTML editions) */
	{ 0x2500, "-" }, { 0x2502, "|" }, { 0x250C, "+" }, { 0x2510, "+" }, { 0x2514, "+" }, { 0x2518, "+" },
	{ 0x251C, "+" }, { 0x2524, "+" }, { 0x252C, "+" }, { 0x2534, "+" }, { 0x253C, "+" }, { 0x2550, "=" },
	{ 0x2551, "|" }, { 0x25CF, "\xB7" }, { 0x25E6, "o" }, { 0x25AA, "\xB7" }, { 0x2713, "v" }, { 0x2717, "x" },
};

const char *t8_translit(uint32_t cp)
{
	size_t i;
	static char one[2];
	if (cp >= 0x0300 && cp <= 0x036F) return "";  /* combining marks: the base letter shows */
	if (cp >= 0xFE00 && cp <= 0xFE0F) return "";  /* variation selectors */
	/* mathematical alphanumerics (bold, italic, script ... letters in runs of 52, digits in runs of 10) and the
	 * double-struck capitals of the letterlike block: their letter */
	if (cp >= 0x1D400 && cp <= 0x1D6A3) {
		int k = (int)((cp - 0x1D400) % 52);
		one[0] = (char)(k < 26 ? 'A' + k : 'a' + k - 26);
		one[1] = 0;
		return one;
	}
	if (cp >= 0x1D7CE && cp <= 0x1D7FF) {
		one[0] = (char)('0' + (cp - 0x1D7CE) % 10);
		one[1] = 0;
		return one;
	}
	if (cp >= 0x0100 && cp <= 0x017F && latin_ext_a[cp - 0x0100] != '?') {
		one[0] = latin_ext_a[cp - 0x0100];
		one[1] = 0;
		return one;
	}
	for (i = 0; i < sizeof translit / sizeof translit[0]; i++)
		if (translit[i].cp == cp) return translit[i].s;
	return NULL;
}

int t8_map(uint32_t cp, int flags, char *out, int *len)
{
	int b = t8_cp1252(cp), s;
	const char *t;
	*len = 0;
	if (b >= 0 && (b < 0x80 || b >= 0xA0)) { out[0] = (char)b; *len = 1; return T8_LATIN1; }
	if (b >= 0x80) {
		if (flags & T8_HAVE_CP1252) { out[0] = (char)b; *len = 1; return T8_LATIN1; }
		t = cp1252_hi[b - 0x80].latin1;
		*len = (int)strlen(t);
		memcpy(out, t, (size_t)*len);
		return T8_LATIN1;
	}
	if ((flags & T8_HAVE_SYMBOL) && (s = t8_symbol(cp)) >= 0) { out[0] = (char)s; *len = 1; return T8_SYMBOL; }
	if ((t = t8_translit(cp)) != NULL) {
		size_t n = strlen(t);
		if (n > T8_MAXOUT) n = T8_MAXOUT;
		memcpy(out, t, n);
		*len = (int)n;
		return T8_LATIN1;
	}
	return T8_NONE;
}

size_t t8_to_8bit(const char *in, size_t n, int flags, char *out, size_t outn)
{
	size_t i = 0, o = 0;
	if (!outn) return 0;
	while (i < n) {
		uint32_t cp;
		char buf[T8_MAXOUT];
		int len, k = t8_decode(in + i, n - i, &cp), kind;
		i += (size_t)k;
		if (cp < 0x20 || cp == 0x7F) continue;
		kind = t8_map(cp, flags & ~T8_HAVE_SYMBOL, buf, &len);
		if (kind == T8_NONE) { buf[0] = '?'; len = 1; }
		if (o + (size_t)len >= outn) break;
		memcpy(out + o, buf, (size_t)len);
		o += (size_t)len;
	}
	out[o] = 0;
	return o;
}

int t8_is_word(uint32_t c)
{
	if (c < 0x80) return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
	if (c == 0xAA || c == 0xB5 || c == 0xBA || c == 0xB2 || c == 0xB3 || c == 0xB9 || (c >= 0xBC && c <= 0xBE)) return 1;
	if (c >= 0xC0 && c <= 0x24F) return c != 0xD7 && c != 0xF7;
	if (c >= 0x0370 && c <= 0x03FF) return c != 0x037E && c != 0x0387;
	if (c >= 0x0400 && c <= 0x052F) return 1;
	if (c >= 0x1E00 && c <= 0x1EFF) return 1;
	if (c >= 0x2000 && c <= 0x2BFF) return 0;   /* punctuation, signs, arrows, box drawing */
	if (c >= 0x3000 && c <= 0x303F) return 0;
	return c >= 0x0250 && !(c >= 0xFE00 && c <= 0xFE0F) && c != 0xFEFF && !(c >= 0xFF00 && c <= 0xFF0F);
}
