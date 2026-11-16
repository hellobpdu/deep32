"""Preserve input citation markers across the name change (release specification section 15).

Only input recognition changes. The current output marker remains CITE_TRAILER;
old programme names here identify previously copied text and are not emitted.
"""
from pathlib import Path

TARGET = 'library/src/app/cite.c'
OLD = (
    b'int cite_has_trailer(const char *text)\r\n'
    b'{\r\n'
    b'\tstatic const char CITE_TRAILER_V1[] = "Deep32 Library, not " "RAG: not verified evidence.";\r\n'
    b'\treturn text && (strstr(text, CITE_TRAILER) != NULL || strstr(text, CITE_TRAILER_V1) != NULL);\r\n'
    b'}\r\n'
)
NEW = (
    b'int cite_has_trailer(const char *text)\r\n'
    b'{\r\n'
    b'\tstatic const char CITE_TRAILER_V1[] = "Deep32 Library, not " "RAG: not verified evidence.";\r\n'
    b'\tstatic const char CITE_TRAILER_PREVIOUS[] = "Deep32 Library: not verified evidence.";\r\n'
    b'\treturn text && (strstr(text, CITE_TRAILER) != NULL\r\n'
    b'\t                || strstr(text, CITE_TRAILER_PREVIOUS) != NULL\r\n'
    b'\t                || strstr(text, CITE_TRAILER_V1) != NULL);\r\n'
    b'}\r\n'
)


def apply(tree_dir):
    root = Path(tree_dir)
    path = root / TARGET
    parts = TARGET.split('/')
    for candidate in [root] + [root.joinpath(*parts[:n]) for n in range(1, len(parts) + 1)]:
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError('1014: linked target path: ' + TARGET)
    if not path.is_file():
        raise FileNotFoundError('1014: target missing: ' + TARGET)
    data = path.read_bytes()
    count = data.count(OLD)
    if count != 1:
        raise ValueError('1014: expected one original citation recogniser, found %d' % count)
    result = data.replace(OLD, NEW, 1)
    path.write_bytes(result)
    return [TARGET]
