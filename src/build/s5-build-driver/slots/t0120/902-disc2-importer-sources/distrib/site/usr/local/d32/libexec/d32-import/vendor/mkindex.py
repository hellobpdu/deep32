"""mkindex.py <libsrc> [--encyc layers.json]: the Library start page (ship lane), from the content's own manifests.

Titles, dates and paths only (public metadata). Links point at /d32/libcontent on the installed system (read-only
for Firefox ESR through /etc/firefox-esr/unveil.*) and at the encyclopedia on kiwix-serve (127.0.0.1:8850).
Writes HTML to stdout. Stdlib only.
"""
import html
import json
import os
import sys

root = sys.argv[1]
raw = os.path.join(root, "raw")
LC = "file:///d32/libcontent"
KS = "http://127.0.0.1:8850"
# update 2 (library lane): "mkindex.py <libsrc> --encyc layers.json" (ship/u2/library/tools/encyc-layers.py) shows
# every encyclopedia layer with its counts taken from the pack at build time; without it the page is the PREVIEW's,
# byte for byte
encyc = None
if len(sys.argv) > 3 and sys.argv[2] == "--encyc":
    with open(sys.argv[3], encoding="utf-8") as f:
        encyc = json.load(f)


def esc(s):
    return html.escape(str(s or ""), quote=True)


cisco = []
with open(os.path.join(raw, "cisco", "manifest.jsonl"), encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        if d.get("role") == "hub" or not d.get("file"):
            continue
        cisco.append((d.get("title") or os.path.basename(d["file"]), d.get("date") or "", d["file"], d.get("role")))
cisco.sort(key=lambda r: (r[0].lower(), r[3] != "book-pdf"))

rfc_n = len(os.listdir(os.path.join(raw, "ietf", "rfc", "txt")))
draft_n = len(os.listdir(os.path.join(raw, "ietf", "drafts", "txt")))

out = []
w = out.append
w("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Deep32/UX Library</title><style>")
w("body{background:#a8a8a8;font-family:Arial,Helvetica,sans-serif;font-size:13px;margin:16px}"
  "h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;border-bottom:1px solid #555;margin-top:20px}"
  ".box{background:#d6d6d6;border:2px outset #e8e8e8;padding:8px 12px;margin:8px 0}"
  "table{border-collapse:collapse}td{padding:1px 8px 1px 0;vertical-align:top}a{color:#00007b}"
  ".note{color:#333;font-size:11px}")
w("</style></head><body>")
if encyc is None:
    w("<h1>Deep32/UX Library</h1><div class='note'>First light PREVIEW (D.32.12): the content baked into this image. "
      "Library class: none of it is read by the RAG tools or Ask (corpus only). Deep32 Library's own readers "
      "replace this page in a later build.</div>")
    w("<div class='box'><b><a href='http://127.0.0.1:8850/'>Encyclopedia</a></b>: Wikipedia, computing and networking "
      "(Kiwix wikipedia_en_computer_maxi, 2026-09; 244,023 entries), offline. Text CC BY-SA 4.0, media under their own "
      "licences; attribution in each article.</div>")
else:
    def num(n):
        return "{:,}".format(int(n))

    layers = encyc["layers"]
    w("<h1>Deep32/UX Library</h1><div class='note'>The Library on this node (encyclopedia: lib-encyclopedia seq %d). "
      "Library class: none of it is read by the RAG tools or Ask (corpus only). Deep32 Library's own readers "
      "replace this page in a later build.</div>" % int(encyc["seq"]))
    w("<div class='box'><b><a href='%s/'>Encyclopedia</a></b>: Wikipedia, computing and networking, offline, in %d "
      "layers (the encyclopedia's own start page lists them all):<table>" % (KS, len(layers)))
    for L in layers:
        link = "<a href='%s/viewer#%s'>%s</a>" % (KS, esc(L["book"]), esc(L["title"]))
        if L["kind"] == "delta":
            c = L["counts"]
            k = c["media_stored_by_kind"]
            w("<tr><td>%s</td><td>Deep32, seq %d, %s: %s articles on networking, security and computer science that "
              "the Kiwix layer lacks, %s more titles that lead to them; %s pictures and %s formulas, each credited in "
              "its article</td></tr>" % (link, L["seq"], esc(L["date"]), num(c["articles"]), num(c["zim_redirects"]),
                                         num(k.get("picture", 0)), num(c["formulas"])))
        else:
            w("<tr><td>%s</td><td>Kiwix %s, %s: %s entries</td></tr>" % (
                link, esc(L["book"].rsplit("_", 1)[0]), esc(L["date"]), num(L["entries"])))
    full = [L for L in layers if L["fulltext"]]
    title_only = [L for L in layers if not L["fulltext"]]
    w("</table>Text CC BY-SA 4.0, media under their own licences; attribution in each article.")
    if title_only:
        w(" Search the Selection and the search bar's &quot;containing&quot; search read the full text of %s; "
          "find an article of %s by its title, in that layer's search bar." % (
              " and ".join("the %s layer" % ("Kiwix" if L["kind"] == "base" else "Deep32") for L in full) or "no layer",
              " and ".join("the %s layer" % ("Kiwix" if L["kind"] == "base" else "Deep32") for L in title_only)))
    if len(layers) > 1:
        w(" A link from a Deep32 article to an article of the Kiwix layer shows &quot;not found&quot; on this server: "
          "type its title in the Kiwix layer's search bar (Deep32 Library's own reader follows these links).")
    w("</div>")
w("<div class='box'><b>RFCs and Internet-Drafts</b> (IETF, 2026-11): <a href='%s/ietf/rfc/txt/'>%d RFCs as text</a>, "
  "<a href='%s/ietf/rfc/html/'>HTML</a>, <a href='%s/ietf/rfc/pdf/'>PDF</a>; <a href='%s/ietf/drafts/txt/'>%d drafts</a> "
  "(work in progress, never a standard); <a href='%s/ietf/index/rfc-index.xml'>RFC index</a>. IETF Trust Legal "
  "Provisions, as stated in each document.</div>" % (LC, rfc_n, LC, LC, LC, draft_n, LC))
w("<h2>Cisco Validated Designs and design guides (%d)</h2>" % len(cisco))
w("<div class='note'>Cisco documentation for viewing only, personal use; not to be redistributed. PDFs open in "
  "Firefox here, or in MuPDF from the Cisco Papers folder (front panel, Deep32 Library subpanel).</div><table>")
for t, d, f, r in cisco:
    kind = "PDF" if f.lower().endswith(".pdf") else "HTML"
    w("<tr><td><a href='%s/cisco/%s'>%s</a></td><td>%s</td><td>%s</td></tr>" % (LC, esc(f), esc(t), esc(d), kind))
w("</table>")
w("</body></html>")
sys.stdout.write("\n".join(out) + "\n")
