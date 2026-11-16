# Linux KB source refresh

The vector join records the upstream model repository and revision separately
from the served embedding name in compat.json. Conversion, verification,
donor state and the final build-report check require those identities to agree.
The new embedding_identity.py is required beside flat_from_jsonl.py.

The generation-c wrappers use d32kb-20261115c and checked vectors from the
earlier build. They require the completed or newly built no-dense root,
the exact original G2 input and split files, the selected 2080 Ti friend
vectors, the home and cross vectors, compat.json and build-host services.
Their source contains no such data. The fresh rerun retains its original
four-block split request; the resume procedure explicitly keeps system
rows on the home host. Do not interchange those procedures without the
operator's original split and equality checks.

Set DEEP32_BUILD_ENV to the operator-supplied build settings file. The
generation-c wrappers also require DEEP32_FRIEND_GATE, the external friend
input gate tools. The public layout keeps kb-bundle beside linux-line.
The r1-after-w2.sh default is generation c; an explicit validated generation
argument remains supported. The generation and fingerprint are finalised
by the release operator after derivation and verification.

tests/test_w2_identity.py is a standalone standard-library suite. It creates
its own SQLite roots and vector rows in TMPDIR and never uses corpus files.
Use an explicit Git Bash path on Windows. crosscard.py is optional source
which requires NumPy; NumPy and vector artifacts are not included here.
