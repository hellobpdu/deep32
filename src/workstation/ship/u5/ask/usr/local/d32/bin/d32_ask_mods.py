"""Deep32 console modifier key and parser, ported from Deep32.ps1.

Keep the console's regex and ordering, including first-occurrence flag
deduplication before resolving group conflicts. Console-generated vectors in
tests/vectors/mods.json are the compatibility reference.
"""

import re
import textwrap


MODIFIERS = (
    ("f", "search", "full", "Full retrieval: 32 hits per search in three tiers (the default)"),
    ("h", "search", "half", "Half retrieval: the top 16 hits per search"),
    ("l", "search", "light", "Light retrieval: the top 8 hits per search"),
    ("d", "search", "deep", "Deep research: the deep rounds (slower, exhaustive)"),
    ("s", "length", "std", "Standard answer: the default layout"),
    ("v", "length", "verbose", "Long form: a full study chapter, every section at depth"),
    ("b", "length", "brief", "Brief: the compact template"),
    ("r", "scope", "RFC", "Search the RFCs only"),
    ("c", "scope", "Cisco", "Search the Cisco documents only"),
    ("w", "scope", "PwSh", "Search the PowerShell docs only (opt-in collection)"),
    ("y", "scope", "system", "Search the RAG system's own docs only (opt-in collection)"),
    ("n", "angle", "config", "Configuration first: complete examples, line by line"),
    ("o", "angle", "verify", "Verification: show and debug commands, healthy and broken output"),
    ("g", "angle", "tshoot", "Troubleshooting: ranked hypotheses and the isolation path"),
    ("m", "angle", "compare", "Compare the options side by side in a table"),
    ("q", "angle", "quotes", "Quote-heavy: the key statements verbatim, every quote verified"),
    ("i", "angle", "RFCvCisco", "RFC versus Cisco up front: the MUST/SHOULD beside the IOS XE default"),
    ("a", "angle", "all-rel", "All releases: 17.18 and 26.x too, not only the 17.9 pin"),
)

INSTRUCTIONS = {
    "d": "Go deep: use deep mode (the deep research rounds).",
    "v": "Long form (verbose): write every section of the expanded layout at full depth, a study chapter of about 2,500 to 4,000 words: the mechanism from first principles, the configuration line by line, a worked example with a table, verification with healthy and broken output, production, and edge cases and common traps, every claim still cited and nothing padded.",
    "b": "Brief: use the compact template.",
    "n": "Lead with configuration: complete examples, line by line with their modes, every command confirmed with find_command at 17.9.",
    "o": "Focus on verification: the show and debug commands that prove it, what healthy and broken output looks like, the counters to watch.",
    "g": "Treat it as troubleshooting: ranked hypotheses, how to confirm or rule out each, and the isolation path.",
    "m": "Compare the options side by side in a table (behaviour, defaults, trade-offs), then explain the table.",
    "q": "Quote the documents more: the key statements verbatim, every quote through verify_quote.",
    "i": "Put RFC versus Cisco first: the RFC's MUST/SHOULD text beside the IOS XE behaviour and default at 17.9.",
    "a": "Do not pin to IOS XE 17.9 alone: cover 17.18 and Catalyst 9300 26.x as well, each statement labelled by its release.",
}
SCOPES = {"r": "rfc", "c": "cisco", "w": "powershell", "y": "system"}
NAMES = {key: name for key, group, name, help_text in MODIFIERS}
RETRIEVAL = {"f": "full", "h": "half", "l": "light"}

# .NET Regex \s and String.Trim use Unicode whitespace, excluding the extra
# U+001C..U+001F separators recognised by Python's \s/str.strip.
WHITESPACE = "\t\n\v\f\r\u0085\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
WS = "[" + WHITESPACE + "]"
SPLIT = re.compile(r"^(.*?)" + WS + r"*\|" + WS + r"*(\$[A-Za-z]*)((?:" + WS + r"+.*)?)$")
TOKEN = re.compile(r'([+\-@#%])("([^"]*)"|[^' + WHITESPACE + r'"]+)')


def split_mods(line):
    result = {"Has": False, "Query": line, "Flags": "", "Inc": [], "Exc": [],
              "Docs": [], "Rel": "", "Plat": "", "Error": ""}
    match = SPLIT.search(line)
    if match is None:
        return result
    result["Has"] = True
    result["Query"] = match[1].strip(WHITESPACE)
    for key in match[2][1:].lower():
        if key not in NAMES:
            result["Error"] = "Unknown modifier $" + key + ": type /mods for the key."
            return result
        if key not in result["Flags"]:
            result["Flags"] += key
    rest = match[3]
    for token in TOKEN.finditer(rest):
        value = token[3] or token[2]
        key = {"+": "Inc", "-": "Exc", "@": "Docs", "#": "Rel", "%": "Plat"}[token[1]]
        if key in ("Inc", "Exc", "Docs"):
            result[key].append(value)
        else:
            result[key] = value
    left = TOKEN.sub("", rest).strip(WHITESPACE)
    if left:
        result["Error"] = ("Not a modifier: " + left +
                           ". After the $letters use +include -exclude @doc #release %platform.")
        return result
    if not result["Query"]:
        result["Error"] = "Nothing before the |: type the question first."
    return result


def get_last(flags, group):
    return next((key for key in reversed(flags) if key in group), "")


def get_mod_summary(result):
    parts = []
    retrieval = get_last(result["Flags"], "fhl")
    if retrieval:
        parts.append(NAMES[retrieval])
    if "d" in result["Flags"]:
        parts.append("deep")
    length = get_last(result["Flags"], "svb")
    if length:
        parts.append(NAMES[length])
    parts.extend(NAMES[key] for key in result["Flags"] if key not in "fhldsvb")
    for prefix, key in (("+", "Inc"), ("-", "Exc"), ("@", "Docs")):
        parts.extend(prefix + value for value in result[key])
    for prefix, key in (("#", "Rel"), ("%", "Plat")):
        if result[key]:
            parts.append(prefix + result[key])
    return " ".join(parts)


def get_mod_text(result):
    lines = []
    retrieval = RETRIEVAL.get(get_last(result["Flags"], "fhl"), "")
    if retrieval:
        lines.append(f"Use {retrieval} mode for the searches of this answer.")
    length = get_last(result["Flags"], "svb")
    for key in result["Flags"]:
        if key in "vb" and key != length:
            continue
        if key in INSTRUCTIONS:
            lines.append(INSTRUCTIONS[key])
    scopes = [SCOPES[key] for key in result["Flags"] if key in SCOPES]
    if scopes:
        lines.append("Search only these collections (doc_type): " + ", ".join(scopes) + ".")
    if result["Inc"]:
        lines.append("Make sure the answer covers: " + "; ".join(result["Inc"]) + ".")
    if result["Exc"]:
        lines.append("Leave out: " + "; ".join(result["Exc"]) + " (do not search for or write about it).")
    if result["Docs"]:
        lines.append("Restrict the searches to these documents (docs=): " + ", ".join(result["Docs"]) + ".")
    if result["Rel"]:
        lines.append("Pin to release " + result["Rel"] + " instead of 17.9 where a release applies.")
    if result["Plat"]:
        lines.append("Platform: " + result["Plat"] + " (filter find_command rows and searches to it).")
    return {"Retrieval": retrieval, "Lines": lines}


def resolve_research_turn(text, parsed=None):
    parsed = split_mods(text) if parsed is None else parsed
    if parsed["Error"]:
        return {"Error": parsed["Error"], "Text": None}
    if not parsed["Has"]:
        return {"Error": None, "Text": text}
    lines = get_mod_text(parsed)["Lines"]
    sent = parsed["Query"]
    if lines:
        sent += "\n\n(Deep32 console modifiers) " + " ".join(lines)
    return {"Error": None, "Text": sent}


def mods_key(width=80):
    """The console's grouped /mods key, word-wrapped for a plain terminal."""
    lines = []

    def add(text, indent=""):
        lines.extend(textwrap.wrap(text, width=width, subsequent_indent=indent,
                                   break_long_words=False, break_on_hyphens=False) or [""])

    add("Modifiers: put them after a | at the end of the line; the letters combine ($hv = half + verbose).")
    previous = ""
    for key, group, name, help_text in MODIFIERS:
        if group != previous:
            lines.append("  " + group)
            previous = group
        add(f"    ${key}  {name:<10} {help_text}", " " * 19)
    lines.append("  values")
    for line in ('    +word or +"a phrase"   the answer must cover it',
                 '    -word                  leave it out',
                 '    @doc_key               search only that document (repeat for more)',
                 '    #release               pin to it instead of 17.9 (#17.18, #26.1)',
                 '    %platform              e.g. %c9300 or %router'):
        add(line, " " * 27)
    add('Example: ospf nssa translation | $hv +"forwarding address" -ipv6 @rfc3101')
    add('A Cisco pipe without a $ after it (show run | include ospf) stays part of the question.')
    return "\n".join(lines) + "\n"
