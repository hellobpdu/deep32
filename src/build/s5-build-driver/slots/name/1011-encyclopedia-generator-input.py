"""Use the lead's Ask tile label (release specification section 15 item 43; ruling4 U3)."""
from pathlib import Path


EDITS = {
    'art/src/library/index.js': [
        ("const text = 'Ask Library', w = labelWidth(text);",
         "const text = 'Ask Deep32', w = labelWidth(text);", 1),
    ],
}


def apply(tree_dir):
    root = Path(tree_dir)
    prepared = []
    for rel, edits in sorted(EDITS.items()):
        target = root / rel
        for part in [target, *target.parents]:
            if part == root:
                break
            if part.is_symlink():
                raise ValueError('linked generator path: ' + rel)
        before = target.read_bytes()
        after = before
        for old, new, expected in edits:
            old_bytes, new_bytes = old.encode('ascii'), new.encode('ascii')
            actual = after.count(old_bytes)
            if actual != expected:
                raise ValueError('%s: expected %d generator inputs, found %d' %
                                 (rel, expected, actual))
            after = after.replace(old_bytes, new_bytes)
        if before == after:
            raise ValueError('generator input did not change: ' + rel)
        prepared.append((rel, target, after))
    for _, target, after in prepared:
        target.write_bytes(after)
    return [rel for rel, _, _ in prepared]
