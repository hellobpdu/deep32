# Reference-check refusal messages and coverage descriptions below.
"""Check source references in a virtual tree without running a compiler or make.

The Make reader evaluates pure list/path functions against the supplied names.
It checks all conditional branches, recognizes declared rules, the usual C object
inference, BSD library/program declarations, and literal shell output targets.
Shell commands, recipes, included system makefiles, and external files are never
executed or read. An opaque expansion used as a prerequisite is a coverage refusal,
not evidence that the prerequisite exists. Unused build flags may remain opaque.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import posixpath
import re


_DYNAMIC = "__D32_UNRESOLVED_MAKE_EXPANSION__"
_C_SUFFIXES = {".c", ".h", ".inc", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm"}
_MAKE_NAMES = {"Makefile", "makefile", "GNUmakefile"}
_ASSIGN = re.compile(r"^(?:(?:override|export|private)\s+)*([\w.]+)\s*(::=|:=|\?=|\+=|!=|=)\s*(.*)$")


def is_reference_file(name: str) -> bool:
    """Select bytes needed by the checker; all tree names are still required."""
    return posixpath.basename(name) in _MAKE_NAMES or posixpath.splitext(name)[1].lower() in _C_SUFFIXES | {".mk"}


def _is_make(name: str) -> bool:
    return posixpath.basename(name) in _MAKE_NAMES or name.endswith(".mk")


def _join(base: str, name: str) -> str:
    return posixpath.normpath(posixpath.join(base, name))


def _match(pattern: str, value: str) -> bool:
    """Make wildcard matching, with a star restricted to one path component."""
    pparts, vparts = pattern.split("/"), value.split("/")
    return len(pparts) == len(vparts) and all(fnmatch.fnmatchcase(v, p) for p, v in zip(pparts, vparts))


def _percent(pattern: str, word: str) -> str | None:
    if "%" not in pattern:
        return "" if word == pattern else None
    before, after = pattern.split("%", 1)
    if word.startswith(before) and word.endswith(after) and len(word) >= len(before) + len(after):
        return word[len(before):len(word) - len(after) if after else None]
    return None


def _split_args(text: str) -> list[str]:
    args, start, stack = [], 0, []
    for index, char in enumerate(text):
        if char in "({" and index and text[index - 1] == "$":
            stack.append(")" if char == "(" else "}")
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "," and not stack:
            args.append(text[start:index])
            start = index + 1
    return args + [text[start:]]


def _logical_lines(text: str) -> list[tuple[int, str]]:
    lines, pending, first = [], "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            first = number
        # Continuations in recipes retain their leading tab and remain recipes.
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append((first, pending + line))
        pending = ""
    if pending:
        lines.append((first, pending))
    return lines


def _uncomment(line: str) -> str:
    return re.split(r"(?<!\\)#", line, maxsplit=1)[0].replace("\\#", "#")


@dataclass
class _Rule:
    targets: list[str]
    prerequisites: list[str]
    line: int


@dataclass
class _Make:
    path: str
    names: set[str]
    variables: dict[str, list[str]] = field(default_factory=dict)
    rules: list[_Rule] = field(default_factory=list)
    raw_rules: list[tuple[int, str, str]] = field(default_factory=list)
    generated: set[str] = field(default_factory=set)
    include_dirs: set[str] = field(default_factory=set)
    shell_outputs: list[str] = field(default_factory=list)
    source_search: list[str] = field(default_factory=list)
    bsd_kind: str = ""

    @property
    def base(self) -> str:
        return posixpath.dirname(self.path)

    def expand(self, text: str, active: tuple[str, ...] = ()) -> str:
        if len(active) > 40 or len(text) > 2_000_000:
            return _DYNAMIC
        result, index = [], 0
        while index < len(text):
            if text[index] != "$":
                result.append(text[index])
                index += 1
                continue
            if index + 1 == len(text):
                result.append(_DYNAMIC)
                break
            next_char = text[index + 1]
            if next_char == "$":
                result.append(_DYNAMIC)
                index += 2
            elif next_char in "({":
                stack = [")" if next_char == "(" else "}"]
                end = index + 2
                while end < len(text) and stack:
                    if text[end] in "({" and text[end - 1] == "$":
                        stack.append(")" if text[end] == "(" else "}")
                    elif text[end] == stack[-1]:
                        stack.pop()
                    end += 1
                if stack:
                    result.append(_DYNAMIC)
                    break
                result.append(self.expression(text[index + 2:end - 1], active))
                index = end
            else:
                result.append(self.variable(next_char, active))
                index += 2
        value = "".join(result)
        return value if len(value) <= 2_000_000 else _DYNAMIC

    def variable(self, name: str, active: tuple[str, ...]) -> str:
        name = self.expand(name, active) if "$" in name else name
        if name in (".CURDIR", ".OBJDIR", "CURDIR"):
            return "."
        if name in active:
            return _DYNAMIC
        if name not in self.variables:
            return _DYNAMIC
        return " ".join(self.expand(value, (*active, name)) for value in self.variables[name])

    def expression(self, expr: str, active: tuple[str, ...]) -> str:
        match = re.match(r"^([A-Za-z][\w-]*)\s+(.*)$", expr, re.S)
        if not match:
            # GNU substitution references and the equivalent BSD suffix form.
            subst = re.fullmatch(r"([^:]+):([^=]*)=(.*)", expr, re.S)
            if subst:
                name, old, new = subst.groups()
                words = self.variable(name, active).split()
                if "%" in old:
                    return " ".join(new.replace("%", stem) if (stem := _percent(old, word)) is not None else word for word in words)
                return " ".join(word[:-len(old)] + new if old and word.endswith(old) else word + new if not old else word for word in words)
            return self.variable(expr, active)
        function, body = match.groups()
        raw = _split_args(body)
        if function == "shell" or function in {"eval", "file", "guile", "load"}:
            return _DYNAMIC
        if function == "foreach" and len(raw) == 3:
            name = self.expand(raw[0], active).strip()
            old = self.variables.get(name)
            values = []
            for word in self.expand(raw[1], active).split()[:100_000]:
                self.variables[name] = [word]
                values.append(self.expand(raw[2], active))
            if old is None:
                self.variables.pop(name, None)
            else:
                self.variables[name] = old
            return " ".join(values)
        args = [self.expand(value, active) for value in raw]
        a = args + [""] * 3
        words = a[0].split()
        if function == "wildcard":
            found = []
            for pattern in a[0].split():
                full = _join(self.base, pattern)
                found.extend(posixpath.relpath(name, self.base or ".") for name in sorted(self.names) if _match(full, name))
            return " ".join(found)
        if function == "if":
            if _DYNAMIC in a[0]:
                return a[1] + " " + a[2]
            return a[1] if a[0].strip() else a[2]
        if function in {"and", "or"}:
            if any(_DYNAMIC in value for value in args):
                return _DYNAMIC
            if function == "and":
                return args[-1] if all(value.strip() for value in args) else ""
            return next((value for value in args if value.strip()), "")
        if function == "patsubst":
            return " ".join(a[1].replace("%", stem) if (stem := _percent(a[0], word)) is not None else word for word in a[2].split())
        if function == "subst":
            return a[2].replace(a[0], a[1])
        if function in {"filter", "filter-out"}:
            return " ".join(word for word in a[1].split() if any(_percent(pattern, word) is not None for pattern in words) == (function == "filter"))
        if function == "sort":
            return " ".join(sorted(set(words)))
        if function == "strip":
            return " ".join(words)
        if function in {"addprefix", "addsuffix"}:
            return " ".join(a[0] + word if function == "addprefix" else word + a[0] for word in a[1].split())
        if function == "notdir":
            return " ".join(posixpath.basename(word) for word in words)
        if function == "dir":
            return " ".join((posixpath.dirname(word) + "/") if "/" in word else "./" for word in words)
        if function == "basename":
            return " ".join(posixpath.splitext(word)[0] for word in words)
        if function == "suffix":
            return " ".join(posixpath.splitext(word)[1] for word in words)
        if function in {"abspath", "realpath"}:
            # A virtual root is sufficient; never consult the host filesystem.
            return " ".join(posixpath.relpath(_join(self.base, word), self.base or ".") for word in words)
        if function == "join":
            left, right = a[0].split(), a[1].split()
            return " ".join((left[i] if i < len(left) else "") + (right[i] if i < len(right) else "") for i in range(max(len(left), len(right))))
        if function == "words":
            return str(len(words))
        if function in {"firstword", "lastword"}:
            return (words[0] if function == "firstword" else words[-1]) if words else ""
        if function in {"word", "wordlist"}:
            try:
                start = int(a[0]) - 1
                end = int(a[1]) if function == "wordlist" else start + 1
                return " ".join((a[2] if function == "wordlist" else a[1]).split()[max(start, 0):max(end, 0)])
            except ValueError:
                return _DYNAMIC
        if function == "value":
            return " ".join(self.variables.get(a[0].strip(), [_DYNAMIC]))
        if function in {"origin", "flavor"}:
            return "file" if a[0].strip() in self.variables else "undefined"
        # Preserve literal arguments as well as the coverage marker: an unknown
        # function must not hide a concrete missing/deleted source reference.
        return _DYNAMIC + " " + " ".join(args)

    def parse(self, text: str) -> None:
        lines = _logical_lines(text)
        self._parse_lines(lines)
        for line, lhs, rhs in self.raw_rules:
            targets = self.expand(lhs).split()
            prerequisites = self.expand(rhs).replace("|", " ").split()
            self.rules.append(_Rule(targets, prerequisites, line))
        for raw in self.shell_outputs:
            name = self.expand(raw).strip("\"'")
            if name and _DYNAMIC not in name and not name.startswith("/") and "$" not in name:
                self.generated.add(_join(self.base, name))
        flag_text = " ".join(self.variable(name, ()) for name in sorted(self.variables) if "FLAGS" in name)
        flag_text += " " + " ".join(line for _, line in lines if line.startswith("\t"))
        flag_text = self.expand(flag_text)
        for match in re.finditer(r"(?:^|\s)(?:-I\s*|-iquote\s+|-isystem\s+)([^\s]+)", flag_text):
            directory = match.group(1).strip("\"'")
            if directory and not directory.startswith("/") and _DYNAMIC not in directory:
                self.include_dirs.add(_join(self.base, directory))
        self.include_dirs.add(self.base or ".")
        if self.bsd_kind:
            sources = self.variable("SRCS", ()).split() if "SRCS" in self.variables else []
            targets = []
            if self.bsd_kind == "lib" and "LIB" in self.variables:
                targets = ["lib" + value + ".a" for value in self.variable("LIB", ()).split()]
            elif "PROG" in self.variables:
                targets = self.variable("PROG", ()).split()
            self.rules.append(_Rule(targets, sources, 0))

    def _parse_lines(self, lines: list[tuple[int, str]], conditional: int = 0) -> None:
        index, defining = 0, False
        while index < len(lines):
            number, raw = lines[index]
            index += 1
            if raw.startswith("\t"):
                continue
            line = _uncomment(raw).strip()
            if not line:
                continue
            if line.startswith("define "):
                defining = True
                continue
            if line == "endef":
                defining = False
                continue
            if defining:
                continue
            loop = re.fullmatch(r"\.for\s+(\w+)\s+in\s+(.*)", line)
            if loop:
                body, nesting = [], 1
                while index < len(lines) and nesting:
                    item = lines[index]
                    index += 1
                    if item[1].strip().startswith(".for "):
                        nesting += 1
                    elif item[1].strip() == ".endfor":
                        nesting -= 1
                    if nesting:
                        body.append(item)
                name, values = loop.groups()
                for value in self.expand(values).split()[:100_000]:
                    self._parse_lines([(n, s.replace("${" + name + "}", value).replace("$(" + name + ")", value)) for n, s in body], conditional)
                continue
            if re.match(r"^(?:\.if\b|\.ifdef\b|\.ifndef\b|ifeq\b|ifneq\b|ifdef\b|ifndef\b)", line):
                conditional += 1
                continue
            if line in {"endif", ".endif"}:
                conditional = max(0, conditional - 1)
                continue
            if re.match(r"^(?:else\b|\.else\b|\.elif\b)", line):
                continue
            assignment = _ASSIGN.match(line)
            if assignment:
                name, operator, value = assignment.groups()
                if operator == "?=" and name in self.variables:
                    continue
                if operator == "!=":
                    value = _DYNAMIC
                elif operator in {":=", "::="}:
                    value = self.expand(value)
                if operator == "+=":
                    self.variables.setdefault(name, []).append(value)
                elif conditional and name in self.variables:
                    self.variables[name].append(value)
                else:
                    self.variables[name] = [value]
                continue
            if line.startswith(".include"):
                if "<bsd.lib.mk>" in line:
                    self.bsd_kind = "lib"
                elif "<bsd.prog.mk>" in line:
                    self.bsd_kind = "prog"
                else:
                    match = re.fullmatch(r'\.include\s+"([^"]+)"', line)
                    if match:
                        self.raw_rules.append((number, ".make-includes", match.group(1)))
                continue
            if re.match(r"^-?include\s", line):
                self.raw_rules.append((number, ".make-includes", line.split(None, 1)[1]))
                continue
            if line.startswith("$(shell") or line.startswith("${shell"):
                # Literal redirection outputs are declarations, not executed
                # shell. Ignore descriptor redirects and absolute host files.
                for output in re.finditer(r"(?<![\w>])>(?![>&])\s*((?:\$[({][^)}]*[)}]|[^\s;|&()])+)", line):
                    self.shell_outputs.append(output.group(1))
                continue
            if ":" not in line:
                continue
            # Rules are the only remaining source of prerequisites; assignment
            # strings, comments, macro bodies, and recipes never reach this.
            lhs, rhs = line.split(":", 1)
            rhs = rhs.lstrip(":").split(";", 1)[0]
            if lhs == ".PATH" or lhs.startswith(".PATH."):
                self.source_search.extend(_join(self.base, directory) for directory in self.expand(rhs).split())
            elif lhs not in {".PHONY", ".SUFFIXES", ".MAIN", ".ORDER", ".NOTPARALLEL", ".PRECIOUS", ".SECONDARY", ".DELETE_ON_ERROR"}:
                self.raw_rules.append((number, lhs, rhs))


def check_references(contents: dict[str, bytes], final_names: set[str],
                     merged_names: set[str], deleted: set[str], strict: set[str] | None = None) -> list[dict]:
    """Return deterministic conflicts for quoted C includes and prerequisites.

    ``contents`` contains surviving reference-file bytes only; ``final_names``
    and ``merged_names`` contain the complete mapped virtual-tree namespace.
    PIN audits can supply committed content and PIN names without claiming that
    unprovided alpha.1 content was scanned.

    ``strict`` (lead, 2026-11-17): when given, only those paths (the merged
    commit's published files) get the full missing-reference check. Surviving
    alpha.1-only files are checked only for references that resolve to an
    actual deleted path; their pre-existing unresolved includes built in
    alpha.1 and are not new breakage.
    """
    names = set(final_names) | set(merged_names)
    deleted_bases = {posixpath.basename(name) for name in deleted}
    models = []
    conflicts = []
    for name in sorted(contents):
        if _is_make(name):
            model = _Make(name, names)
            model.parse(contents[name].decode("utf-8", "replace"))
            models.append(model)

    def candidates(reference: str, base: str, directories=()) -> list[str]:
        # Mapped Workstation paths lose src/workstation/. Keep that source
        # alias for references that deliberately reach another committed root.
        return list(dict.fromkeys([_join(base, reference), _join("", reference),
                                   _join("src/workstation/" + base, reference),
                                   _join("src/" + base, reference),
                                   *[_join(directory, reference) for directory in directories]]))

    def removed(reference: str, base: str, *, basename: bool = False) -> bool:
        return (basename and posixpath.basename(reference) in deleted_bases) or any(name in deleted for name in candidates(reference, base))

    def is_strict(path: str) -> bool:
        return strict is None or path in strict

    def removed_exact(reference: str, base: str, directories=()) -> bool:
        # The first candidate that exists decides; a same-named local file is not the deleted one.
        for name in candidates(reference, base, directories):
            if name in deleted:
                return True
            if name in names:
                return False
        return False

    def add(path: str, line: int, reference: str, reason: str, kind: str) -> None:
        conflicts.append({"path": path, "line": line, "reference": reference,
                          "reason": reason + ": " + reference, "kind": kind})

    # Generated names are local to their Makefile; pattern prerequisites are
    # instantiated only when the generated pattern is actually referenced.
    generated = set().union(*(model.generated for model in models)) if models else set()
    exact = set(generated)
    patterns = []
    for model in models:
        for rule in model.rules:
            for target in rule.targets:
                if _DYNAMIC in target:
                    continue
                if "%" in target:
                    patterns.append((model, target, rule.prerequisites))
                elif re.fullmatch(r"\.[\w]+\.[\w]+", target):
                    before, after = target[1:].split(".", 1)
                    patterns.append((model, "%." + after, ["%." + before]))
                else:
                    exact.add(_join(model.base, target))

    def buildable(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in names or name in exact:
            return True
        if name in seen or len(seen) > 40 or _DYNAMIC in name:
            return False
        active = seen | {name}
        # Common make built-ins remain available even without an explicit .c.o
        # rule. They require a real or explicitly generated source file.
        if name.endswith(".o") and any(buildable(name[:-2] + suffix, active) for suffix in (".c", ".cc", ".cpp", ".cxx", ".m", ".mm")):
            return True
        for model, pattern, prerequisites in patterns:
            stem = _percent(_join(model.base, pattern), name)
            if stem is None:
                continue
            if all(any(buildable(candidate, active) for candidate in candidates(ref.replace("%", stem), model.base, model.source_search)) for ref in prerequisites if ref != "|"):
                return True
        return False

    for model in models:
        for rule in model.rules:
            for reference in rule.prerequisites:
                if reference in {"|", ".WAIT"}:
                    continue
                if not is_strict(model.path):
                    if removed_exact(reference, model.base, model.source_search):
                        add(model.path, rule.line, reference, "Makefile prerequisite names a deleted path", "make-prerequisite")
                    continue
                if removed(reference, model.base):
                    add(model.path, rule.line, reference, "Makefile prerequisite names a deleted path", "make-prerequisite")
                elif _DYNAMIC in reference or "$" in reference:
                    add(model.path, rule.line, reference, "Makefile prerequisite has an unsupported dynamic expansion; offline coverage is incomplete", "make-coverage")
                elif "%" in reference:
                    # Its concrete instances are checked through buildable().
                    continue
                elif not any(buildable(candidate) for candidate in candidates(reference, model.base, model.source_search)):
                    add(model.path, rule.line, reference, "Makefile prerequisite is missing from the final tree and merged commit", "make-prerequisite")

    for path in sorted(contents):
        if _is_make(path):
            continue
        # Remove comments while retaining line numbers and string literals.
        text = contents[path].decode("utf-8", "replace")
        text = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                      lambda match: "\n" * match.group().count("\n") if match.group().startswith(("/*", "//")) else match.group(), text, flags=re.S)
        base = posixpath.dirname(path)
        applicable = [model for model in models if not model.base or path.startswith(model.base + "/")
                      or any(path.startswith(directory.rstrip("/") + "/") for directory in model.include_dirs)
                      or any(path in candidates(reference, model.base, model.source_search)
                             for rule in model.rules for reference in rule.prerequisites
                             if "%" not in reference and _DYNAMIC not in reference)]
        directories = sorted(set().union(*(model.include_dirs for model in applicable))) if applicable else []
        for match in re.finditer(r'^\s*#\s*include\s*"([^"\r\n]+)"', text, re.M):
            reference = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            if not is_strict(path):
                if removed_exact(reference, base, directories):
                    add(path, line, reference, "quoted include names a deleted path", "quoted-include")
                continue
            if removed(reference, base, basename=True):
                add(path, line, reference, "quoted include names a deleted path", "quoted-include")
            elif not any(name in names for name in candidates(reference, base, directories)):
                add(path, line, reference, "quoted include is missing from the final tree and merged commit", "quoted-include")

    unique = {(row["path"], row["line"], row["reference"], row["kind"]): row for row in conflicts}
    return [unique[key] for key in sorted(unique)]
