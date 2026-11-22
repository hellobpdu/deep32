"""Saving answers to an Obsidian vault (DEEP32_RELAY_SAVE_DIR): one Markdown note per finished answer, filed under
the exam blueprint topic the answer names.

With saving on, the agent is asked on every turn (INSTRUCTION, added after its instruction file) to end the answer
with one line `Blueprint: <exam> <id>`. After the answer the relay reads the last Blueprint line. When it names an id
of the blueprint map (DEEP32_RELAY_SAVE_MAP) whose folder exists in the vault, the note goes into that folder;
every other note goes into _inbox at the top of the vault, which is made when it is missing.

A note is YAML front matter (title, type, lens, exam, ids, date, model, effort, modifiers, quote_checks, docs,
tags), a heading, the question as an Obsidian question callout, and the answer as the Workstation received it: the
streamed text, or the final text when nothing streamed, without the Checks footer. Its name is
"<id> Ask - <short question>.md", or "Ask - <short question>.md" in _inbox. A note never replaces a file: a name in
use gets " (2)", " (3)" and so on. The note is written under a hidden temporary name in its folder and then renamed,
readable and writable by the user the relay runs as. Nothing is written outside the vault or through a linked
folder, and no folder's permissions are changed.

After a save the optional program DEEP32_RELAY_SAVE_HOOK runs with the vault folder as its only argument, without a
shell and for at most HOOK_SECONDS; answers saved while it runs are followed by one more run. Notes are written and
the program runs on the vault's own thread, never inside a turn. A note that cannot be written and a program that
fails are lines in the service's log (logs/events.jsonl: answer-save, save-hook), never an answer's failure.

The map file (TSV): one line per topic, "<exam> <id><TAB><folder relative to the vault>", for example
"ENARSI 1.10.d<TAB>Exams/ENARSI/1.10.d OSPF path preference". The exams are ENARSI and CCIE; an id is like 1.4 or
1.10.d. Lines starting with # are comments and blank lines are ignored.
"""
import contextlib
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import tempfile
import threading
import time

INSTRUCTION = ("End the answer with one line `Blueprint: <exam> <id>` naming the closest topic of the blueprint map "
               "(for example `Blueprint: ENARSI 1.10.d`), or `Blueprint: none`.")
EXAMS = {"ENARSI": ("ENARSI 300-410", "enarsi"), "CCIE": ("CCIE Enterprise Infrastructure", "ccie")}
INBOX = "_inbox"
HOOK_SECONDS = 60
QUEUE = 32                  # answers waiting for the vault's thread; one more is not saved and is logged
SHORT = 80                  # characters of the question kept in a note's name and title
MAP_BYTES = 1024 * 1024
MAP_LINES = 10000
MAX_DOCS = 64
ID = r"\d{1,2}\.\d{1,2}(?:\.[a-z])?"
MAP_KEY = re.compile(r"(ENARSI|CCIE) (" + ID + ")")
# A Blueprint line, with the Markdown an agent may put around it: "Blueprint: ENARSI 1.10.d",
# "**Blueprint:** CCIE 3.2.a", "### Blueprint: ENARSI 1.4", "`Blueprint: none`". Group 1 is what follows the colon.
BLUEPRINT = re.compile(r"^[ \t>#*_`-]*blueprint[ \t*_`]*:[ \t*_`]*(.*?)[ \t*_`.]*$", re.I | re.M)
TOPIC = re.compile(r"(ENARSI|CCIE)[ \t]+(\d{1,2}\.\d{1,2}(?:\.[A-Za-z])?)(?!\w|\.\w)", re.I)
# The Workstation resolves typed modifiers ("question | $hv +word") into sentences after the question
# (src/workstation/ask-client/bridge/d32_ask_mods.py, resolve_research_turn); another client may send them as typed.
CONSOLE_NOTE = "\n\n(Deep32 console modifiers) "
TYPED = re.compile(r"^(.*?)\s*\|\s*(\$[A-Za-z]*(?:\s.*)?)$", re.S)
NAME_DROP = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f\x7f-\x9f]')
YAML_ESCAPE = re.compile("[\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]")
RFC_CITE = re.compile(r"\bRFC\s?(\d{3,5})\b")


def read_map(path):
    """The blueprint map at `path` as a tuple of ("<exam> <id>", "folder/under/the/vault") pairs.
    Raises ValueError (or OSError) for a file that is not a map as the module docstring describes it."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("not-a-map")
    raw = path.read_bytes()
    if len(raw) > MAP_BYTES:
        raise ValueError("map-too-large")
    lines = raw.decode("utf-8-sig").splitlines()
    if len(lines) > MAP_LINES:
        raise ValueError("map-too-large")
    found = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, tab, folder = line.partition("\t")
        key, folder = key.strip(), folder.strip()
        if not tab or not MAP_KEY.fullmatch(key) or key in found:
            raise ValueError("invalid-map-line")
        found[key] = "/".join(_folder_parts(folder))
    return tuple(found.items())


def _folder_parts(folder):
    """The parts of a folder written relative to the vault. Raises ValueError for any folder that could leave the
    vault or that Windows cannot name: absolute, a drive, an empty, . or .. part, a reserved character."""
    parts = folder.replace("\\", "/").split("/")
    for part in parts:
        if (not part or part in (".", "..") or part != part.strip() or part.endswith(".")
                or re.search(r'[:*?"<>|\x00-\x1f\x7f]', part)):
            raise ValueError("invalid-map-folder")
    return parts


def runnable(path):
    """True when `path` is an absolute path of a program the relay can start without a shell."""
    path = Path(path)
    return path.is_absolute() and path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def blueprint(answer):
    """(exam, id) named by the answer's last Blueprint line, such as ("ENARSI", "1.10.d"), or None for an answer
    without one, a "Blueprint: none" line or a line naming no ENARSI or CCIE id."""
    lines = BLUEPRINT.findall(answer or "")
    match = TOPIC.match(lines[-1]) if lines else None
    return (match.group(1).upper(), match.group(2).lower()) if match else None


def split_question(text):
    """(question as shown, question without modifiers, modifiers) of the text the relay received."""
    text = "".join(c for c in str(text or "") if c in "\n\t" or ord(c) >= 32 and not 127 <= ord(c) <= 159)
    head, note, sentences = text.partition(CONSOLE_NOTE)
    if note:
        # The Workstation sends its resolved sentences, never the letters typed: the sentences are the modifiers.
        return head.strip(), head.strip(), " ".join(sentences.split())
    typed = TYPED.match(text)
    if typed:
        return text.strip(), typed.group(1).strip(), " ".join(typed.group(2).split())
    return text.strip(), text.strip(), ""


def short_question(question):
    """The question as a name: no character Windows or Obsidian refuses in a file name or link, white space
    collapsed, no leading or trailing dot or space, at most SHORT characters."""
    short = " ".join(NAME_DROP.sub("", " ".join(question.split())).split()).strip(". ")
    short = short[:SHORT].rstrip(". ")
    return short or "question"


def _yaml(value):
    """A YAML value: a JSON string or list of strings (YAML reads JSON flow values). Characters YAML does not take
    as they are (or reads as line breaks) are written as escapes."""
    text = json.dumps(value, ensure_ascii=False)
    return YAML_ESCAPE.sub(lambda match: "\\u%04x" % ord(match.group()), text)


def _docs(answer, calls):
    """The distinct document keys the answer's citations name (section ids such as rfc2328#10.1, the file of a
    verification marker such as (verified - rfc2328.txt:118), RFC numbers in bracket citations), in the order they
    first appear, keeping only documents the turn's tools returned."""
    from . import answer_check
    known = set()
    for call in calls:
        for ident in call.get("ids") or ():
            known.add(re.split(r"[#:]", str(ident), maxsplit=1)[0].lower())
    found = [(m.start(), m.group(0).split("#", 1)[0]) for m in answer_check.SECTION_ID.finditer(answer)]
    for marker in answer_check.MARKER.finditer(answer):
        where = answer_check.LOCATOR.search(marker.group(2) or "")
        if where:
            found.append((marker.start(), where.group(1).rsplit(".", 1)[0]))
    for cite in answer_check.BRACKET_CITE.finditer(answer):
        found.extend((cite.start(), "rfc" + number) for number in RFC_CITE.findall(cite.group(0)))
    keys = (key.lower() for _, key in sorted(found, key=lambda item: item[0]))
    return list(dict.fromkeys(key for key in keys if key in known))[:MAX_DOCS]


def render(turn, topic, when):
    """(base name, note bytes) of one answer filed under `topic` ((exam, id) or None for _inbox)."""
    shown, plain, modifiers = split_question(turn.get("question"))
    answer = str(turn.get("answer") or "")
    short = short_question(plain)
    if topic:
        exam, ident = topic
        title, base = "%s %s Ask: %s" % (exam, ident, short), "%s Ask - %s" % (ident, short)
        tag = EXAMS[exam][1]
        tags = [tag, "%s/sec-%s" % (tag, ident.split(".")[0]), "%s/%s" % (tag, ident.replace(".", "-"))]
    else:
        title, base, tags = "Ask: " + short, "Ask - " + short, ["deep32/ask"]
    lines = ["---", "title: " + _yaml(title), "type: " + _yaml("ask"), "lens: " + _yaml("ask")]
    if topic:
        lines.append("exam: " + _yaml(EXAMS[topic[0]][0]))
    lines += ["ids: " + _yaml([topic[1]] if topic else []),
              "date: " + time.strftime("%Y-%m-%d", time.gmtime(when)),
              "model: " + _yaml(str(turn.get("model") or "")),
              "effort: " + _yaml(str(turn.get("effort") or "")),
              "modifiers: " + _yaml(modifiers)]
    events = turn.get("events")
    if isinstance(events, (list, tuple)):
        # The turn's tool calls were kept: how many verify_quote calls it made is known, even when it is none.
        from . import answer_check
        calls = answer_check.summarize(events)
        lines.append("quote_checks: %d" % sum(1 for call in calls if call["tool"] == "verify_quote"))
        docs = _docs(answer, calls)
        if docs:
            lines.append("docs: " + _yaml(docs))
    lines += ["tags: " + _yaml(tags), "---", ""]
    question = shown.split("\n") if shown else [""]
    callout = "> [!question] " + question[0] + "".join("\n> " + line for line in question[1:])
    text = "\n".join(lines) + "\n# " + title + "\n\n" + callout + "\n\n" + answer
    return base, text.encode("utf-8", "replace")


def _linked(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _publish(temporary, target):
    """Give the written file its name, never replacing a file. Raises FileExistsError when the name is taken."""
    if os.name == "nt":
        os.rename(temporary, target)        # on Windows a rename never replaces an existing name
        return
    try:
        os.link(temporary, target)          # fails when the name is taken, whatever is there
    except FileExistsError:
        raise
    except OSError:
        # A file system without hard links: the name is checked, then the file renamed onto it.
        if os.path.lexists(target):
            raise FileExistsError(str(target)) from None
        os.rename(temporary, target)
        return
    os.unlink(temporary)


class AnswerVault:
    """Saves answers into the vault at `root` (DEEP32_RELAY_SAVE_DIR), filing them by `folders` (settings'
    save_folders, from DEEP32_RELAY_SAVE_MAP), and runs `hook` (DEEP32_RELAY_SAVE_HOOK) after saving.

    `log(event, reason, **counts)` is the service's event log (runtime.EventLedger.append). `runner` starts the hook
    the way subprocess.run does; the default is subprocess.run.
    """
    def __init__(self, root, folders=(), hook="", *, log=None, wall_clock=time.time, runner=None,
                 hook_seconds=HOOK_SECONDS):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("not-a-folder")
        self.folders = dict(folders)
        self.hook = str(hook or "")
        self.log = log
        self.wall_clock, self.runner, self.hook_seconds = wall_clock, runner, hook_seconds
        self.jobs = queue.Queue(maxsize=QUEUE)
        self.lock = threading.Lock()
        self.worker = None
        self.closed = False

    def _log(self, event, reason, **counts):
        with contextlib.suppress(Exception):    # a log that cannot be written never stops a save
            if self.log is not None:
                self.log(event, reason, **counts)

    # -- the supervisor's side: hand the answer over, never wait ---------------------------------------------
    def submit(self, turn):
        """The supervisor's on_save: queue one finished answer ({"question", "answer", "model", "effort",
        "events"}) for the vault's thread. Never writes, never waits. Returns False when the answer was not queued."""
        try:
            job = dict(turn, time=self.wall_clock())
        except Exception:
            self._log("answer-save", "unavailable")
            return False
        with self.lock:
            if self.closed:
                return False
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name="deep32-vault", daemon=True)
                self.worker.start()
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                self._log("answer-save", "limit")
                return False
        return True

    def close(self, timeout=5.0):
        """Save what is queued and run the hook once more if it is owed, waiting at most `timeout` seconds.
        Returns True when the vault's thread finished."""
        with self.lock:
            self.closed = True
            worker = self.worker
        if worker is None:
            return True
        try:
            self.jobs.put(None, timeout=timeout)
        except queue.Full:
            return False
        worker.join(timeout)
        return not worker.is_alive()

    def _work(self):
        owed = False
        while True:
            job = self.jobs.get()
            if job is None:
                break
            owed = self.save(job) is not None or owed
            if owed and self.hook and self.jobs.empty():
                self.run_hook()
                owed = False
        if owed and self.hook:
            self.run_hook()

    # -- the vault's thread: write the note, run the hook ------------------------------------------------------
    def save(self, turn):
        """Write one answer's note. Returns its path, or None when it was not written (logged). Never raises."""
        try:
            topic = blueprint(str(turn.get("answer") or ""))
            folder = self._mapped(topic)
            if folder is None:
                topic, folder = None, self._inside([INBOX], create=True)
            if folder is None:
                raise ValueError("unsafe-folder")
            when = turn.get("time")
            base, data = render(turn, topic, when if isinstance(when, (int, float)) else self.wall_clock())
            path = self._write(folder, base, data)
        except Exception:
            self._log("answer-save", "unavailable")
            return None
        self._log("answer-save", "none", bytes=len(data))
        return path

    def _mapped(self, topic):
        """The vault folder the map gives this topic, when the map names one and it is a real folder in the vault."""
        relative = self.folders.get("%s %s" % topic) if topic else None
        if not relative:
            return None
        try:
            return self._inside(_folder_parts(relative), create=False)
        except ValueError:
            return None

    def _inside(self, parts, *, create):
        """The folder root/parts when every part is a real folder (no link, no junction) and it resolves inside the
        vault; None otherwise. With create, a missing last part is made (with the default permissions)."""
        path = self.root
        for index, part in enumerate(parts):
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                return None
            path = path / part
            if create and index == len(parts) - 1 and not os.path.lexists(path):
                try:
                    path.mkdir()
                except FileExistsError:
                    pass
            try:
                info = path.lstat()
            except OSError:
                return None
            if _linked(path) or not stat.S_ISDIR(info.st_mode):
                return None
        real = path.resolve(strict=True)
        return path if real == path and real.is_relative_to(self.root) else None

    @staticmethod
    def _write(folder, base, data):
        """Write `data` under a hidden temporary name in `folder`, then give it the first free name of base.md,
        base (2).md, base (3).md ... Returns the path."""
        handle, temporary = tempfile.mkstemp(prefix=".deep32-ask-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            for number in range(1, 1000):
                target = folder / (base + (".md" if number == 1 else " (%d).md" % number))
                try:
                    _publish(temporary, target)
                except FileExistsError:
                    continue
                return str(target)
            raise OSError("no-free-name")
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def run_hook(self):
        """Run the hook once with the vault folder as its only argument. Returns True when it exited with 0.
        Never raises: a hook that cannot start, fails or runs too long is logged."""
        if not self.hook:
            return False
        started = time.monotonic()
        try:
            options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           cwd=str(self.root), timeout=self.hook_seconds, check=False, shell=False)
            if os.name == "nt":
                options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = (self.runner or subprocess.run)([self.hook, str(self.root)], **options)
            reason = "none" if getattr(result, "returncode", 1) == 0 else "unavailable"
        except subprocess.TimeoutExpired:
            reason = "timeout"
        except Exception:
            reason = "unavailable"
        self._log("save-hook", reason, duration_ms=int(max(0.0, time.monotonic() - started) * 1000))
        return reason == "none"
