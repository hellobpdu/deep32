#!/usr/bin/env python3
"""Checks install/deep32-wizard-errors.tsv, the table the setup wizard reads to explain a failed step.

Run from the repository root:

    python3 -I -B tests/wizard/test_error_table.py

The table has three kinds of row: `exit` (TOOL:STATUS), `code` (a reason code or prerequisite id printed by a
tool) and `text` (a regular expression tried against one output line). The tests check its format, keep the
text patterns inside the regex subset that bash [[ =~ ]] and .NET agree on, and read the install scripts
themselves to make sure every exit status, code and message they can print has a row, and that the table
names nothing the sources never print.

Extraction rules (all read the source text; nothing is executed):

  Exit statuses
    setup      `exit N` in install/deep32-setup.sh and .ps1 (fail/Fail exit 1, usage errors 64, help 0).
    get        `exit N` and trap strings in src/release-tools/deep32-get.sh and .ps1; in the four getter
               helpers, sys.exit(N), X.exit(N, ...) and integer returns from main/run_loader, because the
               bash getter runs them under `set -e` and passes their status through.
    inventory  the `exit codes:` line both inventory scripts print in their usage text.
    forge      in src/gpu-pack/forms/*.py: Refusal(message, N) (a one-argument Refusal uses the class default),
               parser.exit/ap.exit(N, ...), ap.error (argparse exits 2) and Interrupted(N) for signals.
    connect    adapters/connect.py Refusal(message, N) (default from the class), SystemExit(N) and integer
               returns from main; adapters/connect.ps1 `exit N`.
    relay      integer returns (including both arms of `A if c else B`) in deep32_relay/connector.py main.

  Codes
    relay      every StateError('code') in adapters/relay/deep32_relay/*.py (connector.py prints the reason as
               "Relay operation is unavailable (code: ...)"), every string _exception_code returns, and the
               qualification reasons that reach "Relay check code: ...": the REASONS set in selftest.py and the
               hyphenated literals on `reason` lines in selftest.py and engine.py (not 'none').
    getter     DockerImageStoreRefusal('code', ...) in deep32_get_whole.py.
    connect    the pairing reasons connect.py compares exc.reason with, and a 'code: message' prefix of a
               Refusal message (agent-credential-mode-unsupported).
    forge      PreflightError('code', message) reasons in forms/host-requirements.py.
    inventory  every check id in install/prerequisites.json and selection.supported from the inventory scripts.
    wizard     the Workstation VM guide's own checks: the codes of the rule and fix rows in install/deep32-wizard-hv.tsv
               and every code the two wizards set (vm_fail CODE, Set-VmFail 'CODE', a step's code set directly);
               and the send-* codes the wizards name themselves for Send disc 2 (install/deep32-wizard.sh and .ps1):
               both wizards must name the same set, and only wizard rows carry them.

  Messages (interpolations become X: $var, ${...}, $(...), '+ var +', %s/%d, f-string fields, module constants
  stay as their text)
    setup      fail/need/fix "..." and echo "[fail] ..." in the bash form; Fail/Need/Fix in the PowerShell form.
    get        die '...' / die "..." and printf lines to stderr in deep32-get.sh; throw literals, and
               Write-Output literals that carry a Next: or come right before an `exit N`, in deep32-get.ps1.
    helpers    in deep32_get_whole.py, deep32_download.py, deep32_get_manifest.py and deep32_settings.py: the
               message of every raised Refusal/PendingDisc/NeedsAction/DownloadError/LocalError/TransferError/
               ValueError/OSError/FileExistsError/DockerImageStoreRefusal, print(..., file=sys.stderr) literals,
               and every print in run_loader (the loader's own result lines). _MirrorFailure and _IgnoredRange
               are left out: their text reaches the person only inside the TransferError "All mirrors failed"
               line, which has its own row.
    forge      Refusal messages, parser.exit/ap.exit/ap.error messages and PreflightError messages in forms/*.py,
               and the ValueError texts in host-requirements.py and storage_requirements.py that start with
               'Install ' (host-requirements.py prints only those; other text is replaced by a fixed line).
    connect    Refusal messages and print(..., file=sys.stderr) literals in connect.py; Write-Error in connect.ps1.

  A message counts as covered when a text row other than the catch-all matches it, or when it contains, as a
  whole token, the key of a code row (the wizard finds those codes the same way). Literals with no words left
  after the interpolations are replaced (for example "$*") are not messages.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / 'install' / 'deep32-wizard-errors.tsv'

KINDS = ('exit', 'code', 'text')
OSES = ('any', 'linux', 'windows', 'macos')
SOURCES = ('setup', 'get', 'inventory', 'forge', 'connect', 'relay', 'docker', 'tailscale', 'wizard')
TOOLS = ('setup', 'get', 'inventory', 'forge', 'connect', 'relay')
CATCH_ALL = '.'
PH = 'X'
MAX_STEPS = 5

SETUP = ('install/deep32-setup.sh', 'install/deep32-setup.ps1')
GETTER_SH = 'src/release-tools/deep32-get.sh'
GETTER_PS = 'src/release-tools/deep32-get.ps1'
HELPERS = ('src/release-tools/deep32_get_whole.py', 'src/release-tools/deep32_download.py',
           'src/release-tools/deep32_get_manifest.py', 'src/release-tools/deep32_settings.py')
INVENTORY = ('install/deep32-inventory.sh', 'install/deep32-inventory.ps1')
PREREQUISITES = 'install/prerequisites.json'
FORMS = 'src/gpu-pack/forms'
CONNECT_PY = 'adapters/connect.py'
CONNECT_PS = 'adapters/connect.ps1'
CONNECT_SH = 'adapters/connect.sh'
RELAY = 'adapters/relay/deep32_relay'
WIZARD = ('install/deep32-wizard-hv.tsv', 'install/deep32-wizard.sh', 'install/deep32-wizard.ps1')
SEND_CODES = frozenset(('send-no-disc2', 'send-no-tailscale', 'send-signed-out', 'send-needs-root', 'send-serve-refused',
                        'send-port-busy', 'send-self-check'))

HELPER_EXCEPTIONS = frozenset(('Refusal', 'PendingDisc', 'NeedsAction', 'DownloadError', 'LocalError',
                               'TransferError', 'ValueError', 'OSError', 'FileExistsError',
                               'DockerImageStoreRefusal'))
TOKEN_EDGE = 'A-Za-z0-9._-'


def read(relative):
    return (ROOT / relative).read_text(encoding='utf-8', errors='replace')


def forms_files():
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / FORMS).glob('*.py'))


def relay_files():
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / RELAY).glob('*.py'))


def all_source_files():
    return (list(SETUP) + [GETTER_SH, GETTER_PS] + list(HELPERS) + list(INVENTORY) + [PREREQUISITES]
            + forms_files() + [CONNECT_PY, CONNECT_PS, CONNECT_SH] + relay_files() + list(WIZARD))


def is_message(text):
    """A literal is a message when real words remain after the interpolations became X."""
    return text is not None and len(re.findall(r'[A-Za-z]{2,}', text.replace(PH, ' '))) >= 2


def has_token(line, token):
    return re.search('(?<![' + TOKEN_EDGE + '])' + re.escape(token) + '(?![' + TOKEN_EDGE + '])', line) is not None


# ---------------------------------------------------------------- shell and PowerShell string literals

SH_STRING = r'"(?:[^"\\]|\\.)*"|\'[^\']*\''
PS_STRING = r"'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\""


def sh_unquote(token):
    if token.startswith("'"):
        return token[1:-1]
    body = token[1:-1]
    body = re.sub(r'\$\((?:[^()]|\([^()]*\))*\)', PH, body)
    body = re.sub(r'\$\{[^}]*\}', PH, body)
    body = re.sub(r'\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9*@#?$!])', PH, body)
    return re.sub(r'\\(.)', r'\1', body)


def ps_unquote(token):
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    body = token[1:-1]
    body = re.sub(r'\$\((?:[^()]|\([^()]*\))*\)', PH, body)
    body = re.sub(r'\$\{[^}]*\}', PH, body)
    body = re.sub(r'\$[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?(?:\.[A-Za-z_][A-Za-z0-9_]*)*', PH, body)
    return re.sub(r'`(.)', r'\1', body)


def ps_expression(text, start):
    """The message a PowerShell argument builds: one string, or a parenthesised '+' chain of strings and values."""
    match = re.compile(r'\s*').match(text, start)
    position = match.end()
    if position < len(text) and text[position] in '\'"':
        token = re.compile(PS_STRING).match(text, position)
        return ps_unquote(token.group(0)) if token else None
    if position >= len(text) or text[position] != '(':
        return None
    depth, index, parts = 0, position, []
    pattern = re.compile(PS_STRING + r'|[()]|\+|[^\'"()+]+')
    while index < len(text):
        token = pattern.match(text, index)
        if not token:
            return None
        value = token.group(0)
        index = token.end()
        if value == '(':
            depth += 1
            if depth > 1:
                parts.append(PH)
        elif value == ')':
            depth -= 1
            if depth == 0:
                break
        elif value[0] in '\'"' and depth == 1:
            parts.append(ps_unquote(value))
        elif value.strip() and value != '+' and depth == 1:
            parts.append(PH)
    return ''.join(parts) if any(p != PH for p in parts) else None


# ---------------------------------------------------------------- Python literals

def py_constants(tree):
    values = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            values[node.targets[0].id] = node.value.value
    return values


def py_text(node, constants):
    """The text a string expression builds with every non-literal part as X; None when nothing is literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.JoinedStr):
        parts = [v.value if isinstance(v, ast.Constant) else PH for v in node.values]
        return ''.join(parts) if any(p != PH for p in parts) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = py_text(node.left, constants), py_text(node.right, constants)
        if left is None and right is None:
            return None
        return (PH if left is None else left) + (PH if right is None else right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = py_text(node.left, constants)
        return None if left is None else re.sub(r'%[sdr]', PH, left)
    return None


def call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def receiver(node):
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def parse(relative):
    tree = ast.parse(read(relative), filename=relative)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent
    return tree


def enclosing_function(node):
    while node is not None:
        node = getattr(node, 'parent', None)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def ints(node):
    """The integers an expression can evaluate to: a constant, or each arm of `A if c else B` (not the test)."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return [node.value]
    if isinstance(node, ast.IfExp):
        return ints(node.body) + ints(node.orelse)
    if isinstance(node, ast.Call):
        return [v for argument in node.args for v in ints(argument)]
    return []


def is_stderr_print(node):
    return (call_name(node) == 'print' and any(
        k.arg == 'file' and isinstance(k.value, ast.Attribute) and k.value.attr == 'stderr' for k in node.keywords))


def ends_in_failure(statements):
    """True when a statement list leaves by raising, sys.exit/SystemExit, or returning a non-zero integer."""
    for statement in statements:
        if isinstance(statement, ast.Raise):
            return True
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Constant) \
                and type(statement.value.value) is int and statement.value.value != 0:
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) \
                and call_name(statement.value) in ('exit', 'SystemExit'):
            return True
    return False


def failure_print(node):
    """A print a person sees when a step fails: inside an except handler, or in a block that then fails."""
    statement = getattr(node, 'parent', None)
    block = getattr(statement, 'parent', None)
    inside_handler, current = False, node
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.Module)):
        if isinstance(current, ast.ExceptHandler):
            inside_handler = True
        current = getattr(current, 'parent', None)
    if inside_handler and is_stderr_print(node):
        return True
    for field in ('body', 'orelse', 'handlers', 'finalbody'):
        statements = getattr(block, field, None)
        if isinstance(statements, list) and statement in statements:
            return ends_in_failure(statements[statements.index(statement) + 1:])
    return False


def class_default_code(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == '__init__' and item.args.defaults:
                    default = item.args.defaults[-1]
                    if isinstance(default, ast.Constant) and type(default.value) is int:
                        return default.value
    return None


def py_exits(relative, functions=('main',)):
    """sys.exit(N), SystemExit(N), X.exit(N, ...) and integer returns from the named functions."""
    tree = parse(relative)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name in ('exit', 'SystemExit') and node.args and isinstance(node.args[0], ast.Constant) \
                    and type(node.args[0].value) is int:
                found.add(node.args[0].value)
        elif isinstance(node, ast.Return) and node.value is not None and enclosing_function(node) in functions:
            if isinstance(node.value, (ast.Constant, ast.IfExp)):
                found.update(ints(node.value))
    return found


# ---------------------------------------------------------------- extraction: exit statuses

def shell_exits(relative):
    return {int(n) for n in re.findall(r'\bexit\s+(\d+)\b', read(relative))}


def exit_statuses():
    result = {}
    result['setup'] = set().union(*(shell_exits(p) for p in SETUP))

    get = shell_exits(GETTER_SH) | shell_exits(GETTER_PS)
    for path in HELPERS:
        get |= py_exits(path, ('main', 'run_loader'))
    result['get'] = get

    inventory = set()
    for path in INVENTORY:
        for line in re.findall(r'exit codes: ([^\'"\n]*)', read(path)):
            inventory |= {int(n) for n in re.findall(r'\b(\d+) [a-z]', line)}
    result['inventory'] = inventory

    forge = set()
    refusal_default = class_default_code(parse(FORMS + '/docker_role.py'), 'Refusal')  # the forms' common Refusal
    for path in forms_files():
        tree = parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name == 'Refusal':
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and type(node.args[1].value) is int:
                    forge.add(node.args[1].value)
                elif len(node.args) == 1 and is_message(py_text(node.args[0], {})):
                    forge.add(refusal_default)
            elif name == 'exit' and receiver(node) in ('parser', 'ap') and node.args \
                    and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is int:
                forge.add(node.args[0].value)
            elif name == 'error' and receiver(node) in ('parser', 'ap'):
                forge.add(2)
            elif name == 'Interrupted':
                forge.update(ints(node))
    result['forge'] = forge

    connect = shell_exits(CONNECT_PS) | py_exits(CONNECT_PY, ('main',))
    tree = parse(CONNECT_PY)
    default = class_default_code(tree, 'Refusal')
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == 'Refusal':
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and type(node.args[1].value) is int:
                connect.add(node.args[1].value)
            elif len(node.args) == 1:
                connect.add(default)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == 'number':
            connect.update(ints(node.value))  # the enrolment failure number becomes the Refusal code
    result['connect'] = connect

    result['relay'] = py_exits(RELAY + '/connector.py', ('main',))
    return result


# ---------------------------------------------------------------- extraction: codes

CODE_WORD = r'[a-z][a-z0-9]*(?:-[a-z0-9]+)+'


def relay_codes():
    codes = set()
    for path in relay_files():
        codes |= set(re.findall(r'StateError\(\s*[\'"](' + CODE_WORD + r')[\'"]', read(path)))
    tree = parse(RELAY + '/connector.py')
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and enclosing_function(node) == '_exception_code' \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            codes.add(node.value.value)
    selftest = parse(RELAY + '/selftest.py')
    for node in selftest.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'REASONS' for t in node.targets):
            codes |= {n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for path in (RELAY + '/selftest.py', RELAY + '/engine.py'):
        for line in read(path).splitlines():
            if 'reason' in line:
                codes |= set(re.findall(r'[\'"](' + CODE_WORD + r')[\'"]', line))
    codes.discard('none')
    return codes


def getter_codes():
    codes = set()
    tree = parse('src/release-tools/deep32_get_whole.py')
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == 'DockerImageStoreRefusal' and node.args \
                and isinstance(node.args[0], ast.Constant):
            codes.add(node.args[0].value)
    return codes


def connect_codes():
    text = read(CONNECT_PY)
    codes = set()
    for line in text.splitlines():
        if re.search(r'exc\.reason\s*(?:==|in\b)', line):
            codes |= set(re.findall(r'[\'"]([a-z][a-z0-9-]*)[\'"]', line))
    codes |= set(re.findall(r'Refusal\(\s*[\'"](' + CODE_WORD + r'):', text))
    return codes


def forge_codes():
    codes = set()
    tree = parse(FORMS + '/host-requirements.py')
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == 'PreflightError' and node.args \
                and isinstance(node.args[0], ast.Constant):
            codes.add(node.args[0].value)
    return codes


def inventory_codes():
    data = json.loads(read(PREREQUISITES))
    codes = {check['id'] for check in data['checks']}
    for path in INVENTORY:
        codes |= set(re.findall(r'\b(selection\.[a-z]+)\b', read(path)))
    return codes


def vm_codes():
    """The Workstation VM guide's codes: hv.tsv's rule and fix rows and the codes the two wizards set."""
    codes = set()
    for line in read(WIZARD[0]).splitlines():
        fields = line.split('\t')
        if fields[0] == 'rule' and len(fields) == 7:
            codes.add(fields[6])
        if fields[0] == 'fix' and len(fields) == 4:
            codes.add(fields[2])
    for path in WIZARD[1:]:
        text = read(path)
        codes |= set(re.findall(r"\b(?:vm_fail|Set-VmFail|vm_fix_steps|Get-VmFix) '?([A-Z]+_[A-Z0-9_]+)", text))
        codes |= set(re.findall(r"(?:VS_CODE\[VM_I\]=|\.Code = ')([A-Z][A-Z0-9_]+)", text))
    return codes


def send_codes(relative):
    """The send-* codes one wizard names (a code is a word of its own: --send-disc2 is an option, not a code)."""
    return set(re.findall(r'(?<![\w-])(send-[a-z0-9]+(?:-[a-z0-9]+)*)(?![\w-])', read(relative)))


def all_codes():
    return {'relay': relay_codes(), 'get': getter_codes(), 'connect': connect_codes(),
            'forge': forge_codes(), 'inventory': inventory_codes(),
            'wizard': vm_codes() | set().union(*(send_codes(p) for p in WIZARD[1:]))}


# ---------------------------------------------------------------- extraction: messages

def setup_messages():
    found = []
    text = read('install/deep32-setup.sh')
    for match in re.finditer(r'(?<![\w-])(fail|need|fix)\s+(' + SH_STRING + ')', text):
        found.append(sh_unquote(match.group(2)))
    for match in re.finditer(r'\becho\s+(' + SH_STRING + ')', text):
        value = sh_unquote(match.group(1))
        if value.startswith('[fail] '):
            found.append(value[len('[fail] '):])
    text = read('install/deep32-setup.ps1')
    for match in re.finditer(r'(?<![\w-])(Fail|Need|Fix)\s+(?=[\'"(])', text):
        found.append(ps_expression(text, match.end()))
    return [m for m in found if is_message(m)]


def getter_shell_messages():
    """die literals, and every string of a printf statement that writes to stderr (continuation lines joined)."""
    found = []
    text = read(GETTER_SH)
    for match in re.finditer(r'(?<![\w-])die\s+(' + SH_STRING + ')', text):
        found.append(sh_unquote(match.group(1)))
    token_pattern = re.compile(SH_STRING + r'|>&2|[;|]|&&|\s+|[^\s\'";|&]+|.')
    for line in re.sub(r'\\\n\s*', ' ', text).splitlines():
        for start in [m.start() for m in re.finditer(r'\bprintf\b', line)]:
            tokens, position = [], start
            while position < len(line):
                token = token_pattern.match(line, position).group(0)
                if token in (';', '|', '&&'):
                    break
                tokens.append(token)
                position += len(token)
            if '>&2' not in tokens:
                continue
            for token in [t for t in tokens if t[:1] in '\'"']:
                value = sh_unquote(token)
                if value in ('%s\\n', '%s\n'):
                    continue
                found.append(value.replace('%s', PH).replace('\\n', ''))
    return [m for m in found if is_message(m)]


def getter_ps_messages():
    """throw literals, and Write-Output literals within the two lines before an `exit N` with N > 0."""
    found = []
    text = read(GETTER_PS)
    for match in re.finditer(r'(?<![\w-])throw\s+(?=[\'"(])', text):
        found.append(ps_expression(text, match.end()))
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for match in re.finditer(r'\bWrite-Output\s+(?=[\'"(])', line):
            value = ps_expression(line, match.end())
            following = ' '.join(lines[index:index + 3])
            if value and re.search(r'\bexit\s+[1-9]', following):
                found.append(value)
    return [m for m in found if is_message(m)]


def helper_messages():
    found = []
    for path in HELPERS:
        tree = parse(path)
        constants = py_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
                name = call_name(call)
                if name not in HELPER_EXCEPTIONS or not call.args:
                    continue
                if name == 'DockerImageStoreRefusal' and len(call.args) == 2:
                    code, step = py_text(call.args[0], constants), py_text(call.args[1], constants)
                    found.append('%s: %s' % (code, step))
                    continue
                found.append(py_text(call.args[0], constants))
            elif isinstance(node, ast.Call) and call_name(node) == 'DockerImageStoreRefusal' and len(node.args) == 2:
                found.append('%s: %s' % (py_text(node.args[0], constants), py_text(node.args[1], constants)))
            elif isinstance(node, ast.Call) and call_name(node) == 'print' and node.args and failure_print(node):
                found.append(py_text(node.args[0], constants))
            elif isinstance(node, ast.Call) and call_name(node) == 'exit' and receiver(node) in ('ap', 'parser') \
                    and len(node.args) == 2:
                found.append(py_text(node.args[1], constants))
    return [m.strip() for m in found if is_message(m)]


def forge_messages():
    found = []
    for path in forms_files():
        tree = parse(path)
        constants = py_constants(tree)
        install_only = path.endswith(('host-requirements.py', 'storage_requirements.py'))
        for node in ast.walk(tree):
            # The fixed lines a main prints in place of other text: message = '...' and str(exc) == '...'.
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'message'
                                                    for t in node.targets):
                found.append(py_text(node.value, constants) if isinstance(node.value, ast.Constant) else None)
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Call) and call_name(node.left) == 'str':
                found.extend(py_text(c, constants) for c in node.comparators if isinstance(c, ast.Constant))
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name == 'Refusal' and node.args:
                found.append(py_text(node.args[0], constants))
            elif name == 'PreflightError' and len(node.args) == 2:
                found.append(py_text(node.args[1], constants))
            elif name == 'exit' and receiver(node) in ('parser', 'ap') and len(node.args) == 2:
                found.append(py_text(node.args[1], constants))
            elif name == 'error' and receiver(node) in ('parser', 'ap') and node.args:
                found.append(py_text(node.args[0], constants))
            elif install_only and name == 'ValueError' and node.args:
                value = py_text(node.args[0], constants)
                if value and value.startswith('Install '):
                    found.append(value)
    return [m.strip() for m in found if is_message(m)]


def connect_messages():
    found = []
    tree = parse(CONNECT_PY)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and (
                call_name(node) == 'Refusal' or (is_stderr_print(node) and failure_print(node))):
            found.append(py_text(node.args[0], {}))
    text = read(CONNECT_PS)
    for match in re.finditer(r'\bWrite-Error\s+(?=[\'"(])', text):
        found.append(ps_expression(text, match.end()))
    return [m for m in found if is_message(m)]


def all_messages():
    return {'setup': setup_messages(), 'get': getter_shell_messages() + getter_ps_messages(),
            'helpers': helper_messages(), 'forge': forge_messages(), 'connect': connect_messages()}


# ---------------------------------------------------------------- the table

def load_table():
    rows = []
    for number, line in enumerate(TABLE.read_bytes().decode('latin-1').split('\n'), 1):
        if not line.strip() or line.startswith('#'):
            continue
        rows.append((number, line.split('\t')))
    return rows


def text_rows(rows):
    return [(n, f) for n, f in rows if len(f) == 6 and f[0] == 'text']


def is_catch_all(fields):
    return fields[0] == 'text' and fields[1] == CATCH_ALL


ESCAPABLE = set('.*+?()[]{}|^$\\')


def subset_problem(pattern):
    """None when PATTERN uses only the subset bash ERE and .NET read the same way, else the reason."""
    if not pattern:
        return 'empty pattern'
    if any(ord(c) < 32 or ord(c) > 126 for c in pattern):
        return 'control or non-ASCII character'
    i, depth, atom = 0, 0, False   # atom: a quantifier may follow
    previous = 'start'
    while i < len(pattern):
        c = pattern[i]
        if c == '\\':
            if i + 1 >= len(pattern) or pattern[i + 1] not in ESCAPABLE:
                return 'backslash before %r' % (pattern[i + 1:i + 2] or 'end')
            i += 2; atom = True; previous = 'atom'; continue
        if c == '[':
            end = i + 1
            if end < len(pattern) and pattern[end] == '^':
                end += 1
            if end < len(pattern) and pattern[end] == ']':
                return 'bracket expression starting with ]'
            close = pattern.find(']', end)
            if close < 0:
                return 'unclosed bracket expression'
            body = pattern[end:close]
            if not body or '\\' in body or '[' in body:
                return 'bracket expression with a backslash, [ or nothing inside'
            i = close + 1; atom = True; previous = 'atom'; continue
        if c == ']':
            return 'unescaped ]'
        if c == '(':
            if pattern[i + 1:i + 2] == '?':
                return 'group extension (?...)'
            if pattern[i + 1:i + 2] in ('|', ')'):
                return 'empty alternative'
            depth += 1; i += 1; atom = False; previous = 'open'; continue
        if c == ')':
            if depth == 0:
                return 'unbalanced )'
            if previous in ('open', 'bar'):
                return 'empty alternative'
            depth -= 1; i += 1; atom = True; previous = 'atom'; continue
        if c == '|':
            if previous in ('start', 'open', 'bar'):
                return 'empty alternative'
            i += 1; atom = False; previous = 'bar'; continue
        if c in '*+?':
            if not atom:
                return 'quantifier %r without an atom (or a lazy/possessive form)' % c
            i += 1; atom = False; previous = 'quantifier'; continue
        if c == '{':
            match = re.compile(r'\{(\d+)(?:,(\d+))?\}').match(pattern, i)
            if not match:
                return 'brace that is not {n} or {n,m}'
            if not atom:
                return 'brace quantifier without an atom'
            if match.group(2) is not None and int(match.group(2)) < int(match.group(1)):
                return 'brace range out of order'
            i = match.end(); atom = False; previous = 'quantifier'; continue
        if c == '}':
            return 'unescaped }'
        if c in '^$':
            i += 1; atom = False; previous = 'anchor'; continue
        i += 1; atom = True; previous = 'atom'
    if depth:
        return 'unbalanced ('
    if previous == 'bar':
        return 'empty alternative'
    return None


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = TABLE.read_bytes()
        cls.rows = load_table()
        cls.texts = text_rows(cls.rows)
        cls.patterns = [(n, f, re.compile(f[1])) for n, f in cls.texts if not is_catch_all(f)
                        and subset_problem(f[1]) is None]
        cls.code_keys = {f[1] for n, f in cls.rows if len(f) == 6 and f[0] == 'code'}

    def covered(self, message):
        if any(p.search(message) for _, _, p in self.patterns):
            return True
        return any(has_token(message, key) for key in self.code_keys)


class TestFormat(Base):
    def test_ascii_lf(self):
        self.assertTrue(self.raw.isascii(), 'the table must be ASCII only')
        self.assertNotIn(b'\r', self.raw, 'the table must use LF line ends')
        self.assertTrue(self.raw.endswith(b'\n'), 'the table must end with a line end')

    def test_header_comment(self):
        self.assertTrue(self.raw.startswith(b'#'), 'the table starts with its header comment')

    def test_rows(self):
        problems = []
        seen = set()
        for number, fields in self.rows:
            where = 'line %d' % number
            if len(fields) != 6:
                problems.append('%s: %d fields' % (where, len(fields)))
                continue
            kind, key, system, source, meaning, fix = fields
            if kind not in KINDS:
                problems.append('%s: kind %r' % (where, kind))
            if system not in OSES:
                problems.append('%s: os %r' % (where, system))
            if source not in SOURCES:
                problems.append('%s: source %r' % (where, source))
            if not key or key != key.strip():
                problems.append('%s: empty or padded key' % where)
            for name, value in (('meaning', meaning), ('fix', fix)):
                if not value.strip() or value != value.strip():
                    problems.append('%s: empty or padded %s' % (where, name))
                if '!' in value:
                    problems.append('%s: "!" in %s' % (where, name))
            steps = fix.split(' | ')
            if len(steps) > MAX_STEPS or any(not s.strip() for s in steps):
                problems.append('%s: %d fix steps (at most %d, none empty)' % (where, len(steps), MAX_STEPS))
            if kind == 'exit':
                match = re.fullmatch(r'([a-z]+):(\d+)', key)
                if not match or match.group(1) not in TOOLS:
                    problems.append('%s: exit key %r is not TOOL:STATUS' % (where, key))
                elif match.group(1) != source:
                    problems.append('%s: exit key %r has source %r' % (where, key, source))
            if kind == 'code' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', key):
                problems.append('%s: code key %r is not a token' % (where, key))
            identity = (kind, key, system)
            if identity in seen:
                problems.append('%s: duplicate %s row %r for %s' % (where, kind, key, system))
            seen.add(identity)
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_one_catch_all_last(self):
        catch = [n for n, f in self.texts if is_catch_all(f)]
        self.assertEqual(len(catch), 1, 'exactly one catch-all text row with key "."')
        self.assertEqual(catch[0], self.texts[-1][0], 'the catch-all is the last text row')
        fields = dict(self.texts)[catch[0]]
        self.assertEqual(fields[3], 'setup', 'the catch-all row has source setup')


class TestRegexSubset(Base):
    def test_text_keys(self):
        problems = []
        for number, fields in self.texts:
            reason = subset_problem(fields[1])
            if reason:
                problems.append('line %d: %r: %s' % (number, fields[1], reason))
                continue
            try:
                re.compile(fields[1])
            except re.error as error:
                problems.append('line %d: %r: %s' % (number, fields[1], error))
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_validator_examples(self):
        for good in ('Plan consent required', '^Download refused: ', 'docker(-| )store', '[0-9]{1,3} MiB',
                     r'\(code: ', 'a|b', '[^ ]+ is not', 'x{2}'):
            self.assertIsNone(subset_problem(good), good)
        for bad in (r'\d+', r'\bword', '[[:digit:]]', '(?=a)', 'a*?', 'a{2,}', r'[\]]', '(|a)', 'a||b', '{',
                    r'\w', 'a++'):
            self.assertIsNotNone(subset_problem(bad), bad)


class TestCoverage(Base):
    def test_exit_statuses(self):
        keys = {f[1] for n, f in self.rows if len(f) == 6 and f[0] == 'exit'}
        missing = ['%s:%d' % (tool, status) for tool, statuses in sorted(exit_statuses().items())
                   for status in sorted(statuses) if '%s:%d' % (tool, status) not in keys]
        self.assertEqual(missing, [], 'exit statuses with no row: ' + ', '.join(missing))

    def test_codes(self):
        missing = ['%s %s' % (tool, code) for tool, codes in sorted(all_codes().items())
                   for code in sorted(codes) if code not in self.code_keys]
        self.assertEqual(missing, [], 'codes with no row:\n' + '\n'.join(missing))

    def test_messages(self):
        missing = []
        for family, messages in sorted(all_messages().items()):
            for message in messages:
                if not self.covered(message):
                    missing.append('%s: %s' % (family, message))
        self.assertEqual(missing, [], 'messages no text row explains:\n' + '\n'.join(sorted(set(missing))))


class TestWizardCodes(Base):
    """Send disc 2 and the VM guide: the wizards find their own rows by code, so each code needs a row for every OS
    it can run on."""

    def wizard_rows(self):
        return [(n, f) for n, f in self.rows if len(f) == 6 and f[3] == 'wizard']

    def test_both_wizards_name_the_same_codes(self):
        sh, ps = (send_codes(p) for p in WIZARD[1:])
        self.assertEqual(sh, ps)
        self.assertEqual(sh, SEND_CODES)

    def test_each_code_has_a_wizard_row_for_linux_and_windows(self):
        rows = self.wizard_rows()
        self.assertTrue(all(f[0] == 'code' for _, f in rows), 'wizard rows are code rows')
        self.assertEqual({f[1] for _, f in rows}, SEND_CODES | vm_codes(), 'wizard rows name only the codes the wizards use')
        for code in sorted(SEND_CODES | vm_codes()):
            for os_name in ('linux', 'windows'):
                mine = [f for _, f in rows if f[1] == code and f[2] == os_name] or [f for _, f in rows if f[1] == code and f[2] == 'any']
                self.assertEqual(len(mine), 1, (code, os_name))
        for _, f in self.rows:
            if len(f) == 6 and f[1] in SEND_CODES:
                self.assertEqual(f[3], 'wizard', f[1])

    def test_root_and_administrator_rows_say_exactly_what_to_run(self):
        fixes = {f[2]: f[5] for _, f in self.wizard_rows() if f[1] == 'send-needs-root'}
        self.assertIn('`sudo bash install/deep32-wizard.sh --send-disc2`', fixes['linux'])
        self.assertIn('`sudo tailscale set --operator=$USER`', fixes['linux'])
        self.assertIn('`powershell -NoProfile -ExecutionPolicy Bypass -File install\\deep32-wizard.ps1 -SendDisc2`', fixes['windows'])
        self.assertIn('Run as administrator', fixes['windows'])


class TestNothingInvented(Base):
    def test_code_keys_in_sources(self):
        corpus = '\n'.join(read(p) for p in all_source_files())
        missing = [key for key in sorted(self.code_keys) if not has_token(corpus, key)]
        self.assertEqual(missing, [], 'code rows no source prints: ' + ', '.join(missing))

    def test_exit_rows_emitted(self):
        statuses = exit_statuses()
        invented = []
        for number, fields in self.rows:
            if len(fields) == 6 and fields[0] == 'exit':
                match = re.fullmatch(r'([a-z]+):(\d+)', fields[1])
                if match and int(match.group(2)) not in statuses.get(match.group(1), set()):
                    invented.append('line %d: %s' % (number, fields[1]))
        self.assertEqual(invented, [], 'exit rows the tool never emits:\n' + '\n'.join(invented))

    def test_text_rows_match_a_literal(self):
        literals = [m for messages in all_messages().values() for m in messages]
        unused = []
        for number, fields, pattern in self.patterns:
            if fields[3] in ('docker', 'tailscale'):
                continue
            if not any(pattern.search(m) for m in literals):
                unused.append('line %d: %s' % (number, fields[1]))
        self.assertEqual(unused, [], 'text rows that match no source message:\n' + '\n'.join(unused))


class TestSummary(Base):
    def test_zz_summary(self):
        counts = {}
        for _, fields in self.rows:
            if len(fields) == 6:
                counts[(fields[0], fields[3])] = counts.get((fields[0], fields[3]), 0) + 1
        messages = all_messages()
        lines = ['rows: %d' % len(self.rows)]
        for kind in KINDS:
            per = ', '.join('%s %d' % (s, counts[(kind, s)]) for s in SOURCES if (kind, s) in counts)
            lines.append('  %s: %d (%s)' % (kind, sum(v for (k, _), v in counts.items() if k == kind), per))
        lines.append('extracted exit statuses: ' + '; '.join(
            '%s %s' % (t, ','.join(map(str, sorted(s)))) for t, s in sorted(exit_statuses().items())))
        lines.append('extracted codes: ' + ', '.join('%s %d' % (t, len(c)) for t, c in sorted(all_codes().items())))
        lines.append('extracted messages: ' + ', '.join('%s %d' % (f, len(set(m))) for f, m in sorted(messages.items())))
        sys.stderr.write('\n' + '\n'.join(lines) + '\n')
        self.assertTrue(self.rows)


if __name__ == '__main__':
    unittest.main()
