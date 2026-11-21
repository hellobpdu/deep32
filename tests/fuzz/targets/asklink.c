/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target asklink: the parts of the Ask link daemon (src/workstation/node/asklink/src) that read bytes from
 * the relay: the D32U/1 frame codec (frame.c), the strict JSON reader and writer (json.c), the message schemas
 * (msg.c), the identifier and hex helpers (util.c) and the pairing code check (sas.c).
 *
 * Byte 0 of the input picks the path; the rest is the frame, message or typed text:
 *   0  one frame: decoded, re-encoded, and the bytes must match
 *   1  HELLO payload            2  HELLO_ACK payload        3  OPEN payload, both forms
 *   4  a relay event, status or MCP request: parsed, checked against each schema, written back and parsed again
 *   5  text: sanitized for display, then quoted with json_put_str and parsed back to the same string
 *   6  typed pairing confirmation against a fixed code
 *   7  two pins and the nonces: the code they give must verify when typed, in either case
 */
#include "fuzz.h"
#include "msg.h"
#include <string.h>

static void roundtrip_json(const struct jval *root)
{
	struct dbuf a = { NULL, 0, 0 }, b = { NULL, 0, 0 };
	struct jval *again = NULL;
	FUZZ_ASSERT(json_write(&a, root) == 0);
	FUZZ_ASSERT(json_parse(a.b, a.len, &again) == 0);
	FUZZ_ASSERT(json_write(&b, again) == 0);
	FUZZ_ASSERT(a.len == b.len && memcmp(a.b, b.b, a.len) == 0);
	json_free(again);
	dbuf_free(&a);
	dbuf_free(&b);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	const uint8_t *p;
	size_t n;

	if (size < 1) return 0;
	p = data + 1;
	n = size - 1;
	switch (data[0] % 8) {
	case 0: {
		struct d32u_hdr h;
		const uint8_t *payload = NULL;
		if (d32u_frame_decode(p, n, &h, &payload) == 0) {
			uint8_t *out = (uint8_t *)malloc(n);
			size_t written = 0;
			if (!out) abort();
			FUZZ_ASSERT(payload == p + D32U_HDR_LEN && h.len == n - D32U_HDR_LEN);
			FUZZ_ASSERT(d32u_frame_encode(out, n, &h, payload, &written) == 0);
			FUZZ_ASSERT(written == n && memcmp(out, p, n) == 0);
			FUZZ_ASSERT(d32u_frame_encode(out, n - 1, &h, payload, &written) == E_LIMIT);
			free(out);
			(void)d32u_type_name(h.type);
			(void)d32u_is_control(h.type);
		}
		if (n >= D32U_HDR_LEN) {
			struct d32u_hdr h2;
			(void)d32u_hdr_parse(p, &h2);
		}
		break;
	}
	case 1: {
		struct peer_hello h;
		if (msg_hello(p, n, &h) == 0) {
			int i;
			FUZZ_ASSERT(d32u_id_ok(h.id, strlen(h.id)));
			FUZZ_ASSERT(h.role == PEER_ROLE_RELAY || h.role == PEER_ROLE_HUB);
			FUZZ_ASSERT(h.nmodels >= 0 && h.nmodels <= HELLO_LIST_MAX && h.nefforts >= 0 && h.nefforts <= HELLO_LIST_MAX);
			for (i = 0; i < h.nmodels; i++) FUZZ_ASSERT(strlen(h.models[i]) <= HELLO_STR_MAX);
			for (i = 0; i < h.nefforts; i++) FUZZ_ASSERT(strlen(h.efforts[i]) <= HELLO_STR_MAX);
		}
		break;
	}
	case 2: {
		int paired = -1;
		if (msg_hello_ack(p, n, &paired) == 0) FUZZ_ASSERT(paired == 0 || paired == 1);
		break;
	}
	case 3: {
		char sess[D32U_ID_MAX + 1];
		(void)msg_open(p, n, 0, NULL, 0);
		memset(sess, 'x', sizeof sess);
		if (msg_open(p, n, 1, sess, sizeof sess) == 0) FUZZ_ASSERT(d32u_id_ok(sess, strlen(sess)));
		break;
	}
	case 4: {
		struct jval *root = NULL;
		if (json_parse(p, n, &root) == 0) {
			struct ctl_event ev;
			struct mcp_request r;
			int code;
			FUZZ_ASSERT(root != NULL);
			if (msg_event(root, &ev) == 0) {
				FUZZ_ASSERT(ev.op >= EV_OPENED && ev.op <= EV_STATUS);
				FUZZ_ASSERT(d32u_id_ok(ev.session, ev.sessionlen));
			}
			if (msg_status(root, &code) == 0) FUZZ_ASSERT(code >= 0 && status_codes[code] != NULL);
			if (msg_mcp_request(root, &r) == 0 && r.sid) FUZZ_ASSERT(ascii_token_ok(r.sid, r.sidlen, 128));
			(void)status_index(root);
			roundtrip_json(root);
			json_free(root);
		} else
			FUZZ_ASSERT(root == NULL);
		break;
	}
	case 5: {
		char *s = (char *)malloc(n ? n : 1);
		size_t m;
		if (!s) abort();
		if (n) memcpy(s, p, n);
		m = text_sanitise(s, n);
		FUZZ_ASSERT(m <= n);
		if (utf8_valid((const uint8_t *)s, m)) {
			struct dbuf d = { NULL, 0, 0 };
			struct jval *v = NULL;
			FUZZ_ASSERT(json_put_str(&d, s, m) == 0);
			FUZZ_ASSERT(json_parse(d.b, d.len, &v) == 0);
			FUZZ_ASSERT(v->type == J_STR && v->n == m && memcmp(v->s, s, m) == 0);
			json_free(v);
			dbuf_free(&d);
		}
		(void)d32u_id_ok((const char *)p, n);
		(void)ascii_token_ok((const char *)p, n, 128);
		(void)mcp_tool_index((const char *)p, n);
		if (n % 2 == 0 && n <= 128) {
			uint8_t out[64];
			char hex[129];
			if (hex_decode((const char *)p, n, out, n / 2) == 0) {
				hex_encode(out, n / 2, hex);
				FUZZ_ASSERT(memcmp(hex, p, n) == 0);
			}
		}
		free(s);
		break;
	}
	case 6: {
		static const uint8_t pin_a[D32U_PIN_LEN] = { 1 }, pin_b[D32U_PIN_LEN] = { 2 };
		static const uint8_t nonce[2 * D32U_NONCE_LEN] = { 3 };
		char code[SAS_CODE_MAX];
		FUZZ_ASSERT(sas_code(pin_a, pin_b, nonce, code, sizeof code) == 0);
		(void)sas_verify_typed((const char *)p, n, code);
		break;
	}
	case 7: {
		char code[SAS_CODE_MAX], lower[SAS_CODE_MAX];
		size_t i;
		if (n < 2 * D32U_PIN_LEN + 2 * D32U_NONCE_LEN) break;
		if (sas_code(p, p + D32U_PIN_LEN, p + 2 * D32U_PIN_LEN, code, sizeof code) != 0) {
			FUZZ_ASSERT(sas_pin_cmp(p, p + D32U_PIN_LEN) == 0);
			break;
		}
		FUZZ_ASSERT(strlen(code) < SAS_CODE_MAX);
		FUZZ_ASSERT(sas_verify_typed(code, strlen(code), code) == 1);
		for (i = 0; code[i]; i++) lower[i] = (char)(code[i] >= 'A' && code[i] <= 'Z' ? code[i] - 'A' + 'a' : code[i]);
		lower[i] = 0;
		FUZZ_ASSERT(sas_verify_typed(lower, strlen(lower), code) == 1);
		FUZZ_ASSERT(sas_code(p + D32U_PIN_LEN, p, p + 2 * D32U_PIN_LEN, lower, sizeof lower) == 0);
		FUZZ_ASSERT(strcmp(lower, code) == 0);
		break;
	}
	}
	return 0;
}
