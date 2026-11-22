"""The optional answer record (DEEP32_RELAY_ANSWER_RECORD=on): one small JSON file per finished answer, kept in
the relay's own folder and readable only by the user the relay runs as.

A record says what answered and on what: the time, the agent and its version, the model that answered and the
effort, the hash of the instruction the agent was given, the index generation and fingerprint rag_status printed
in that turn, the tool calls (the tool, the ids it returned, its outcome) and the answer check's counts.
It never holds the question, the answer, a passage, a search query or a quotation.

Files: logs/answers/<UTC time>-<n>.json. The newest KEEP are kept; older ones are deleted when a new one is
written. A record that cannot be written is skipped: writing one never fails an answer.
"""
import re
import time

from . import __version__

SCHEMA = "deep32.relay-answer.v1"
FOLDER = "logs/answers"
KEEP = 200
NAME = re.compile(r"\d{8}T\d{6}Z-\d{4}\.json")


class AnswerRecords:
    def __init__(self, owner, install, agent, *, wall_clock=time.time, keep=KEEP):
        self.owner, self.wall_clock, self.keep = owner, wall_clock, keep
        self.agent = str(install.get("agent") or "")
        self.release = str(install.get("release_id") or "")
        self.instruction = str(getattr(agent, "instruction_sha256", "") or "")
        self.count = 0

    def write(self, turn):
        """Write the record of one finished answer from what the supervisor hands over: {"agent", "usage",
        "index", "calls", "check"}. Returns the file's path inside the relay folder, or None when it was skipped."""
        try:
            now = time.gmtime(self.wall_clock())
            self.count = self.count % 9999 + 1
            name = "%s-%04d.json" % (time.strftime("%Y%m%dT%H%M%SZ", now), self.count)
            about = turn.get("agent") or {}
            value = {
                "schema": SCHEMA,
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", now),
                "relay": {"version": __version__, "release_id": self.release},
                "agent": {"name": self.agent, "version": str(about.get("version", "")),
                          "model": str(about.get("model", "")), "effort": str(about.get("effort", "")),
                          "model_choice": str(about.get("model_choice", "")),
                          "effort_choice": str(about.get("effort_choice", ""))},
                "instruction_sha256": self.instruction,
                "index": turn.get("index"),
                "usage": turn.get("usage"),
                "calls": turn.get("calls") or [],
                "check": turn.get("check"),
            }
            self.owner.mkdir(FOLDER)
            self.owner.write_json(FOLDER + "/" + name, value)
            self._trim()
            return FOLDER + "/" + name
        except Exception:
            return None

    def _trim(self):
        """Keep the newest self.keep records: the names sort by time."""
        folder = self.owner.path(FOLDER + "/x").parent
        names = sorted(item.name for item in folder.iterdir() if NAME.fullmatch(item.name))
        for name in names[:max(0, len(names) - self.keep)]:
            path = self.owner.path(FOLDER + "/" + name)
            if path.is_file() and not path.is_symlink():
                path.unlink()
