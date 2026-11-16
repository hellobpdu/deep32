#!/usr/bin/env python3
"""Compare cross-card vector identity and ranking with NumPy.

Usage: crosscard.py A.jsonl B.jsonl [SELF.jsonl] > report.json

A and B cover the same chunk ids. The comparison reports cosine, maximum
component difference, exact equality and top-10 rankings for 256 probes.
SELF is an optional earlier run on A's card for same-card agreement.
The fixed criteria are minimum cosine 0.99999, no inclusion changes and
order swaps only between scores within 0.0002. Inputs are separate build
artifacts; this source release includes no vectors or comparison results.
"""
import json, sys
import numpy as np


def load(path):
    ids, vecs, emb = [], [], {}
    for line in open(path, encoding='utf-8'):
        r = json.loads(line)
        ids.append(r['chunk_id'])
        vecs.append(r['vector'])
        emb[r['chunk_id']] = r.get('embedding_text_sha256')
    return ids, np.asarray(vecs, dtype=np.float32), emb


def main(a_path, b_path, self_path=None):
    ia, A, ea = load(a_path)
    ib, B, eb = load(b_path)
    pos_b = {c: i for i, c in enumerate(ib)}
    assert set(ia) == set(ib) and len(ia) == len(ib), 'the two sets cover different chunk ids'
    assert all(ea[c] == eb[c] for c in ia), 'embedding text hashes differ between the sets'
    B = B[[pos_b[c] for c in ia]]
    A64, B64 = A.astype(np.float64), B.astype(np.float64)
    na, nb = np.linalg.norm(A64, axis=1), np.linalg.norm(B64, axis=1)
    cos = (A64 * B64).sum(1) / (na * nb)
    absd = np.abs(A64 - B64).max(1)
    rep = {'rows': len(ia), 'bit_identical_rows': int((A == B).all(1).sum()),
           'min_cosine': float(cos.min()), 'mean_cosine': float(cos.mean()),
           'max_abs_component_diff': float(absd.max()), 'norm_range_a': [float(na.min()), float(na.max())],
           'norm_range_b': [float(nb.min()), float(nb.max())]}
    order = np.argsort(np.asarray(ia))
    probes = order[np.linspace(0, len(ia) - 1, 256).round().astype(int)]
    incl, swaps, tie_swaps, max_sd, worst = 0, 0, 0, 0.0, []
    for p in probes:
        sa, sb = A64 @ A64[p], B64 @ B64[p]
        ta, tb = np.argsort(-sa)[:10], np.argsort(-sb)[:10]
        union = np.union1d(ta, tb)
        sd = float(np.abs(sa[union] - sb[union]).max())
        max_sd = max(max_sd, sd)
        if set(ta) != set(tb):
            incl += 1
            # the gap at the boundary on each card: how close the exchanged rows were
            gap = float(min(abs(sa[ta[-1]] - np.sort(sa)[-11]), abs(sb[tb[-1]] - np.sort(sb)[-11])))
            worst.append({'probe': ia[p], 'kind': 'inclusion', 'boundary_gap': gap})
        elif list(ta) != list(tb):
            swaps += 1
            gaps = [abs(float(sa[x] - sa[y])) for x, y in zip(ta, tb) if x != y]
            g = max(gaps) if gaps else 0.0
            if g < 2e-4:
                tie_swaps += 1
            worst.append({'probe': ia[p], 'kind': 'order', 'max_gap': g})
    rep['probes'] = {'count': len(probes), 'top_k': 10, 'inclusion_changes': incl, 'order_swaps': swaps,
                     'order_swaps_within_2e-4': tie_swaps, 'max_score_diff': max_sd, 'cases': worst[:20]}
    rep['criteria'] = {'min_cosine_ge_0.99999': rep['min_cosine'] >= 0.99999, 'zero_inclusion_changes': incl == 0,
                       'swaps_only_ties': swaps == tie_swaps}
    rep['verdict'] = 'PASS' if all(rep['criteria'].values()) else 'REVIEW'
    if self_path:
        isf, S, es = load(self_path)
        pos_a = {c: i for i, c in enumerate(ia)}
        Aa = A[[pos_a[c] for c in isf]]
        c2 = (S.astype(np.float64) * Aa.astype(np.float64)).sum(1) / (np.linalg.norm(S, axis=1) * np.linalg.norm(Aa, axis=1))
        rep['same_card'] = {'rows': len(isf), 'bit_identical_rows': int((S == Aa).all(1).sum()),
                            'min_cosine': float(c2.min()), 'max_abs_component_diff': float(np.abs(S - Aa).max())}
    json.dump(rep, sys.stdout, indent=1)


if __name__ == '__main__':
    main(*sys.argv[1:4])
