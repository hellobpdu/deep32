"""Synthetic answers and tool events for the answer check. Invented text: no corpus passage, no real answer.

The "documents" are a made-up RFC 9999 and a made-up guide "exguide-1"; the tool results have the shapes the
alpha.2 gateway prints (src/gateway/rag_edge.py: render_status, render_rfc, verify_quote, render_hit, get_outline).
"""

STATUS_OK = ("ok: True; generation: g-synthetic-1; corpus_fingerprint: abc123def456; n_docs: 3; n_chunks: 30\n"
             "bm25: ok\ndense: ok; breaker: closed\nreranker: ok\nkb: ok\nstructure: ok\n"
             "degraded: none\nedge: 1.0.0; core round trip 12 ms")
STATUS_RERANKER_OFF = STATUS_OK.replace("reranker: ok", "reranker: off")

SECTION_31 = ('<section section="rfc9999#3.1" doc="rfc9999" title="Timers" path="3 \u203a 3.1" '
              'local="rfc9999.txt:118-131" chars="0-420/420">\n'
              "The hold timer defaults to 180 seconds. A speaker MUST restart the hold timer when it\n"
              "receives a KEEPALIVE message. The keepalive interval is one third of the hold time.\n"
              "</section>")
SECTION_GUIDE = ('<section section="exguide-1#timers" doc="exguide-1" title="Configuring Timers" '
                 'path="Example Protocol \u203a Configuring Timers" local="exguide-1.txt:400-430" '
                 'chars="0-300/300">\n'
                 "Use the example timers keepalive holdtime command to change the timers. The default\n"
                 "keepalive interval is 60 seconds on IOS XE 17.9 and the range is 0 to 65535.\n"
                 "</section>")
SEARCH = ('<query n=1 mode="bm25">example timers</query>\n'
          '<hit n=1 cite="RFC 9999 \u00a73.1" id="rfc9999:c0007" section="rfc9999#3.1" status="Proposed Standard" '
          'local="rfc9999.txt:118-131">\nThe hold timer defaults to 180 seconds.\n</hit>\n'
          '<hit n=2 cite="Example Guide IOS XE 17.9 \u203a Configuring Timers" id="exguide-1:c0031" '
          'section="exguide-1#timers" release="17.9" local="exguide-1.txt:400-430">\n'
          "Use the example timers keepalive holdtime command to change the timers.\n</hit>")
RFC_9999 = ("<rfc>\nRFC 9999: Example Protocol\nstatus: Proposed Standard\n"
            "obsoletes: RFC 8888 | updates: - | updated by: -\ncorpus: rfc9999\nerrata: 0\n</rfc>")
RFC_8888 = ("<rfc>\nRFC 8888: Example Protocol, first version\n"
            "status: Proposed Standard; OBSOLETED by RFC 9999: cite the newer RFC for current behaviour\n"
            "obsoletes: - | updates: - | updated by: -\ncorpus: not included (metadata only)\nerrata: 0\n</rfc>")
OUTLINE = ('<outline doc="exguide-1" title="Example Guide">\nExample Protocol [exguide-1#example-protocol] 9000 chars\n'
           "  Configuring Timers [exguide-1#timers] 300 chars\n</outline>")

Q_HOLD = "The hold timer defaults to 180 seconds."
Q_RESTART = "A speaker MUST restart the hold timer when it receives a KEEPALIVE message."
Q_COMMAND = "Use the example timers keepalive holdtime command to change the timers."


def call(tool, result, is_error=False, **arguments):
    """One tool event in the normalized form: the tool, its arguments, its result text, and whether it failed."""
    return {"tool": tool, "arguments": arguments, "result": result, "is_error": is_error}


def verify(quote, result, section="rfc9999#3.1", local="rfc9999.txt:118"):
    if result == "not_found":
        return call("verify_quote", '<verify result="not_found" section="%s"/>' % section, ref=section, quote=quote)
    return call("verify_quote", '<verify result="%s" section="%s" local="%s">\n%s\n</verify>'
                % (result, section, local, quote), ref=section, quote=quote)


def clean_events():
    return [call("rag_status", STATUS_OK),
            call("search_many", SEARCH, queries=[{"text": "example timers", "mode": "bm25"}]),
            call("get_section", SECTION_31, ref="rfc9999#3.1"),
            call("get_section", SECTION_GUIDE, ref="exguide-1#timers"),
            call("lookup_rfc", RFC_9999, rfc=9999),
            verify(Q_HOLD, "exact"),
            verify(Q_RESTART, "normalized", local="rfc9999.txt:118-119"),
            verify(Q_COMMAND, "exact", section="exguide-1#timers", local="exguide-1.txt:401")]


QUESTION = "What is the default hold timer of the example protocol, and how do I change it on IOS XE 17.9?"

CLEAN_ANSWER = """**Answer.** The hold timer defaults to 180 seconds [RFC 9999 \u00a73.1], and the `example timers keepalive holdtime` command changes it [Example Guide IOS XE 17.9 \u203a Configuring Timers].

**How it works.** A speaker restarts the hold timer each time a KEEPALIVE arrives, so a silent peer is noticed when the timer runs out. (unsourced)

**Evidence**
- Default: [RFC 9999 \u00a73.1] | "The hold timer defaults to 180 seconds." (verified \u00b7 rfc9999.txt:118)
- Restart rule: [RFC 9999 \u00a73.1] | "A speaker MUST restart the hold timer when it receives a KEEPALIVE message." (normalized \u00b7 rfc9999.txt:118-119)
- Command: [Example Guide IOS XE 17.9 \u203a Configuring Timers] | "Use the example timers keepalive holdtime command to change the timers." (verified \u00b7 exguide-1.txt:401)

**RFC vs Cisco.** No difference found.

**Confidence and gaps.** High. Nothing was left open.

**Read.** rfc9999#3.1, exguide-1#timers; Full, default.
"""

FLAGGED_ANSWER = """**Answer.** The hold timer defaults to 180 seconds [RFC 9999 \u00a73.1]. The keepalive is sent every 750 seconds on IOS XE 17.12.

**Evidence**
- Default: [RFC 9999 \u00a73.1] | "The hold timer defaults to 180 seconds." (verified \u00b7 rfc9999.txt:118)
- Restart rule: "A speaker MUST restart the hold timer when it receives a KEEPALIVE message." (verified \u00b7 rfc9999.txt:118-119)
- Older text: RFC 8888 said the same, and "The timer is never restarted by a peer." (verified \u00b7 rfc9999.txt:140)
- A neighbour at `192.0.2.77` keeps the session up.

**Confidence and gaps.** Medium. The dampening default is not in corpus.

RAG: 12 deep32 calls; docs: rfc9999
"""


def flagged_events():
    return [call("rag_status", STATUS_OK),
            call("search_many", "degraded: reranker\n" + SEARCH, queries=[{"text": "example timers"}]),
            call("get_section", SECTION_31, ref="rfc9999#3.1"),
            call("lookup_rfc", RFC_9999, rfc=9999),
            call("lookup_rfc", RFC_8888, rfc="RFC 8888"),
            verify(Q_HOLD, "exact"),
            verify(Q_RESTART, "normalized", local="rfc9999.txt:118-119"),
            verify("The timer is never restarted by a peer.", "not_found")]
