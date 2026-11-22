"""Saving answers to a vault (DEEP32_RELAY_SAVE_DIR, vault.py): the settings, where a note goes and what it holds, the
hook program, the Blueprint sentence in the instructions, and nothing at all while the setting is unset.

Synthetic questions, answers, maps and folder names in temporary folders; no agent, no network, no program is run."""
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import unittest
from unittest import mock

from support import Agent, Scratch, request
from test_footer import Play, USAGE
import answer_fixtures as fx
from deep32_relay import answer_check, engine, native, runtime, settings, vault
from deep32_relay.sessions import Supervisor
from deep32_relay.state import StateError

NOW = 1795000000                                    # 2026-11-18 in UTC
DATE = "2026-11-18"
OSPF = "Exams/Enarsi/1.10.d Synthetic path topic"   # made-up folder names
QOS = "Exams/Ccie/3.2.a Synthetic queue topic"
SECTION = "Exams/Enarsi/1.4 Synthetic section topic"
MAP = ("# exam id<TAB>folder under the vault (synthetic)\n"
       "ENARSI 1.10.d\t%s\n\n"
       "ENARSI 1.4\t%s\n"
       "CCIE 3.2.a\t%s\n"
       "ENARSI 2.1\tExams/Enarsi/2.1 Folder that is not there\n" % (OSPF, SECTION, QOS))
QUESTION = "Which path does OSPF prefer between areas?"
ANSWER = "**Answer.** Intra-area first, then inter-area.\n\nBlueprint: ENARSI 1.10.d\n"


def front(data):
    """The front matter of a note as a dict: every value must be JSON (which YAML reads) or the bare date."""
    text = data.decode("utf-8")
    assert text.startswith("---\n"), text[:40]
    head, _, body = text[4:].partition("\n---\n")
    values = {}
    for line in head.split("\n"):
        key, _, value = line.partition(": ")
        values[key] = value if key == "date" else json.loads(value)
    return values, body


class Vault(Play):
    def setUp(self):
        super().setUp()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        for folder in (OSPF, QOS, SECTION):
            (self.vault / folder).mkdir(parents=True)
        self.map = self.root / "map.tsv"
        self.map.write_text(MAP, encoding="utf-8", newline="\n")
        self.logged = []

    def store(self, **options):
        options.setdefault("log", lambda event, reason, **counts: self.logged.append((event, reason, counts)))
        return vault.AnswerVault(self.vault, vault.read_map(self.map), wall_clock=lambda: NOW, **options)

    def save(self, store=None, answer=ANSWER, question=QUESTION, **turn):
        store = store or self.store()
        path = store.save(dict(dict(question=question, answer=answer, model="synthetic-model-9",
                                    effort="synthetic-effort", time=NOW), **turn))
        self.assertIsNotNone(path, self.logged)
        return Path(path)

    def everything(self, base=None):
        base = base or self.root
        return sorted(str(path.relative_to(base)).replace("\\", "/") for path in base.rglob("*") if path.is_file())


class Settings(Vault):
    def load(self, **names):
        return settings.load({"DEEP32_RELAY_" + key: value for key, value in names.items()})

    def hook_file(self):
        hook = self.root / "synthetic-hook"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        hook.chmod(0o700)
        return hook

    def test_unset_means_nothing_is_saved(self):
        value = settings.load({})
        self.assertEqual((value.save_dir, value.save_folders, value.save_hook), ("", (), ""))
        self.assertEqual(self.load(SAVE_DIR="", SAVE_MAP=" ", SAVE_HOOK=""), settings.Settings())

    def test_a_folder_a_map_and_a_hook(self):
        hook = self.hook_file()
        value = self.load(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.map), SAVE_HOOK=str(hook))
        self.assertEqual((value.save_dir, value.save_hook), (str(self.vault), str(hook)))
        self.assertEqual(dict(value.save_folders), {"ENARSI 1.10.d": OSPF, "ENARSI 1.4": SECTION, "CCIE 3.2.a": QOS,
                                                    "ENARSI 2.1": "Exams/Enarsi/2.1 Folder that is not there"})
        self.assertEqual(self.load(SAVE_DIR=str(self.vault)).save_folders, ())

    def test_a_map_written_on_windows_reads_the_same(self):
        self.map.write_bytes(b"\xef\xbb\xbf" + MAP.replace("\n", "\r\n").replace("/", "\\").encode("utf-8"))
        self.assertEqual(dict(self.load(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.map)).save_folders)["CCIE 3.2.a"],
                         QOS)

    def test_a_value_that_is_not_valid_stops_the_start(self):
        hook = self.hook_file()
        a_file = self.root / "a-file.txt"
        a_file.write_text("synthetic\n", encoding="ascii")
        bad = [dict(SAVE_DIR="vault"), dict(SAVE_DIR=str(self.root / "missing")), dict(SAVE_DIR=str(a_file)),
               dict(SAVE_MAP=str(self.map)), dict(SAVE_HOOK=str(hook)),
               dict(SAVE_DIR=str(self.vault), SAVE_MAP="map.tsv"),
               dict(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.root / "missing.tsv")),
               dict(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.vault)),
               dict(SAVE_DIR=str(self.vault), SAVE_HOOK="synthetic-hook"),
               dict(SAVE_DIR=str(self.vault), SAVE_HOOK=str(self.root / "missing-hook")),
               dict(SAVE_DIR=str(self.vault), SAVE_HOOK=str(self.vault))]
        for names in bad:
            with self.subTest(names=names), self.assertRaisesRegex(StateError, "^invalid-relay-setting$"):
                self.load(**names)

    @unittest.skipIf(os.name == "nt", "Windows has no execute permission bit")
    def test_a_hook_that_cannot_be_run_stops_the_start(self):
        hook = self.hook_file()
        hook.chmod(0o600)
        with self.assertRaisesRegex(StateError, "^invalid-relay-setting$"):
            self.load(SAVE_DIR=str(self.vault), SAVE_HOOK=str(hook))

    def test_a_map_line_that_is_not_valid_stops_the_start(self):
        for line in ("ENARSI 1.10.d Exams/no tab", "ENARSI\t" + OSPF, "ENCOR 1.1\tExams/x", "ENARSI 1.10.D\tExams/x",
                     "enarsi 1.4\tExams/x", "ENARSI 1.10.d.x\tExams/x", "ENARSI 1.4\t", "ENARSI 1.4\t../outside",
                     "ENARSI 1.4\tExams/../../outside", "ENARSI 1.4\t/absolute", "ENARSI 1.4\tC:/drive",
                     "ENARSI 1.4\tExams//double", "ENARSI 1.4\tExams/./here", "ENARSI 1.4\tExams/trailing.",
                     "ENARSI 1.4\tExams/a?b", "ENARSI 1.10.d\t" + OSPF + "\nENARSI 1.10.d\t" + QOS):
            with self.subTest(line=line):
                self.map.write_text(line + "\n", encoding="utf-8")
                with self.assertRaisesRegex(StateError, "^invalid-relay-setting$"):
                    self.load(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.map))
        self.map.write_bytes(b"ENARSI 1.4\t\xff\xfe\n")
        with self.assertRaisesRegex(StateError, "^invalid-relay-setting$"):
            self.load(SAVE_DIR=str(self.vault), SAVE_MAP=str(self.map))


class Placement(Vault):
    def test_a_mapped_answer_goes_into_its_topic_folder(self):
        path = self.save()
        self.assertEqual(path.parent, (self.vault / OSPF).resolve())
        self.assertEqual(path.name, "1.10.d Ask - Which path does OSPF prefer between areas.md")
        self.assertEqual([event for event, reason, counts in self.logged], ["answer-save"])
        self.assertEqual(self.logged[0][:2], ("answer-save", "none"))
        self.assertEqual(self.logged[0][2]["bytes"], len(path.read_bytes()))

    def test_ccie_ids_and_section_ids_are_mapped_too(self):
        self.assertEqual(self.save(answer="Text.\n**Blueprint:** CCIE 3.2.a\n").parent, (self.vault / QOS).resolve())
        self.assertEqual(self.save(answer="Text.\n`Blueprint: ENARSI 1.4`").parent, (self.vault / SECTION).resolve())

    def test_anything_else_goes_into_the_inbox(self):
        self.assertFalse((self.vault / "_inbox").exists())
        for answer in ("No Blueprint line at all.\n", "Text.\nBlueprint: none\n", "Text.\nBlueprint: ENARSI 9.9.z\n",
                       "Text.\nBlueprint: ENARSI 2.1\n",          # mapped, but the folder is not in the vault
                       "Text.\nBlueprint: ENCOR 1.1\n", "Blueprint: ENARSI 1.10.d\nThen it changed.\nBlueprint: none"):
            with self.subTest(answer=answer):
                path = self.save(answer=answer, question="Synthetic inbox question " + str(len(answer)))
                self.assertEqual(path.parent, (self.vault / "_inbox").resolve())
                self.assertTrue(path.name.startswith("Ask - Synthetic inbox question"), path.name)

    def test_without_a_map_every_note_goes_into_the_inbox(self):
        store = vault.AnswerVault(self.vault, wall_clock=lambda: NOW)
        self.assertEqual(self.save(store).parent, (self.vault / "_inbox").resolve())

    def test_the_last_blueprint_line_counts(self):
        self.assertEqual(vault.blueprint("Blueprint: CCIE 3.2.a\n...\nBlueprint: ENARSI 1.10.d (path preference)."),
                         ("ENARSI", "1.10.d"))
        self.assertEqual(vault.blueprint("> **Blueprint**: enarsi 1.10.D"), ("ENARSI", "1.10.d"))
        self.assertEqual(vault.blueprint("### Blueprint: CCIE 3.2"), ("CCIE", "3.2"))
        for text in ("", "Blueprint: none", "Blueprint: ENARSI", "Blueprint: ENARSI 1.10.d.x", "The blueprint: ENARSI 1.4",
                     "Blueprint: ENARSI 1.10.d\nBlueprint: none"):
            with self.subTest(text=text):
                self.assertIsNone(vault.blueprint(text))

    def test_a_note_never_replaces_a_file(self):
        first = self.save()
        first.write_bytes(b"the owner's own edits\n")
        names = [self.save().name for _ in range(3)]
        self.assertEqual(names, ["1.10.d Ask - Which path does OSPF prefer between areas (%d).md" % n for n in (2, 3, 4)])
        self.assertEqual(first.read_bytes(), b"the owner's own edits\n")
        # no temporary file stays behind
        self.assertEqual([name for name in self.everything(self.vault) if ".deep32-ask-" in name], [])

    def test_the_name_has_no_character_windows_or_obsidian_refuses(self):
        question = ' What  is\t"the" <best> path: a/b\\c *x? [y] #z ^w | v.\n\nand more ' + "long " * 30
        path = self.save(question=question)
        short = path.name[len("1.10.d Ask - "):-len(".md")]
        self.assertEqual(short, ("What is the best path abc x y z w v. and more " + "long " * 30)[:80].rstrip(". "))
        self.assertLessEqual(len(short), 80)
        self.assertFalse(re.search(r'[\\/:*?"<>|#^\[\]]', short))
        self.assertEqual(vault.short_question(" ...?? "), "question")
        self.assertEqual(vault.short_question("..hidden name."), "hidden name")

    def test_modifiers_are_not_part_of_the_name(self):
        typed = self.save(question='ospf nssa translation | $hv +"forwarding address" -ipv6')
        self.assertEqual(typed.name, "1.10.d Ask - ospf nssa translation.md")
        values, body = front(typed.read_bytes())
        self.assertEqual(values["modifiers"], '$hv +"forwarding address" -ipv6')
        self.assertIn('> [!question] ospf nssa translation | $hv +"forwarding address" -ipv6\n', body)
        # what the Workstation sends: its resolved sentences after the question
        sent = self.save(question="ospf area types\n\n(Deep32 console modifiers) Use half mode for the searches of "
                                  "this answer. Brief: use the compact template.")
        self.assertEqual(sent.name, "1.10.d Ask - ospf area types.md")
        values, body = front(sent.read_bytes())
        self.assertEqual(values["modifiers"],
                         "Use half mode for the searches of this answer. Brief: use the compact template.")
        self.assertIn("> [!question] ospf area types\n\n", body)
        # a Cisco pipe is part of the question
        pipe = self.save(question="show ip ospf | include area")
        self.assertEqual((pipe.name, front(pipe.read_bytes())[0]["modifiers"]),
                         ("1.10.d Ask - show ip ospf include area.md", ""))

    def test_nothing_is_written_outside_the_vault(self):
        store = vault.AnswerVault(self.vault, (("ENARSI 1.10.d", "../outside"), ("ENARSI 1.4", "Exams/../../outside"),
                                               ("CCIE 3.2.a", str(self.root / "outside"))), wall_clock=lambda: NOW)
        (self.root / "outside").mkdir()
        for answer in ("Blueprint: ENARSI 1.10.d", "Blueprint: ENARSI 1.4", "Blueprint: CCIE 3.2.a"):
            with self.subTest(answer=answer):
                self.assertEqual(self.save(store, answer=answer, question=answer).parent,
                                 (self.vault / "_inbox").resolve())
        self.assertEqual(list((self.root / "outside").iterdir()), [])

    def link(self, link, target):
        """A directory symlink, or a junction where Windows refuses the symlink; skips when neither can be made."""
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except (OSError, NotImplementedError):
            pass
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError):
            self.skipTest("this host can make neither a directory symlink nor a junction")

    def test_a_linked_topic_folder_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.link(self.vault / "Exams/Linked", outside)
        store = vault.AnswerVault(self.vault, (("ENARSI 1.10.d", "Exams/Linked"),
                                               ("ENARSI 1.4", "Exams/Linked/inner")), wall_clock=lambda: NOW)
        (outside / "inner").mkdir()
        for answer in ("Blueprint: ENARSI 1.10.d", "Blueprint: ENARSI 1.4"):
            with self.subTest(answer=answer):
                self.assertEqual(self.save(store, answer=answer, question=answer).parent,
                                 (self.vault / "_inbox").resolve())
        self.assertEqual(self.everything(outside), [])

    def test_a_linked_inbox_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.link(self.vault / "_inbox", outside)
        store = self.store()
        self.assertIsNone(store.save(dict(question=QUESTION, answer="Blueprint: none", time=NOW)))
        self.assertEqual(self.logged, [("answer-save", "unavailable", {})])
        self.assertEqual(self.everything(outside), [])

    def test_an_inbox_that_is_a_file_is_refused(self):
        (self.vault / "_inbox").write_bytes(b"synthetic\n")
        self.assertIsNone(self.store().save(dict(question=QUESTION, answer="Blueprint: none", time=NOW)))
        self.assertEqual(self.logged, [("answer-save", "unavailable", {})])

    @unittest.skipIf(os.name == "nt", "Windows files take the folder's permissions")
    def test_a_note_is_for_the_owner(self):
        mode = stat.S_IMODE(self.save().stat().st_mode)
        self.assertEqual(mode & 0o077, 0)
        self.assertEqual(mode & 0o600, 0o600)


class Note(Vault):
    def test_an_enarsi_note(self):
        path = self.save(events=fx.clean_events(), answer=fx.CLEAN_ANSWER + "\nBlueprint: ENARSI 1.10.d\n")
        values, body = front(path.read_bytes())
        title = "ENARSI 1.10.d Ask: Which path does OSPF prefer between areas"
        self.assertEqual(values, {
            "title": title, "type": "ask", "lens": "ask", "exam": "ENARSI 300-410", "ids": ["1.10.d"], "date": DATE,
            "model": "synthetic-model-9", "effort": "synthetic-effort", "modifiers": "", "quote_checks": 3,
            "docs": ["rfc9999", "exguide-1"], "tags": ["enarsi", "enarsi/sec-1", "enarsi/1-10-d"]})
        self.assertEqual(list(values), ["title", "type", "lens", "exam", "ids", "date", "model", "effort", "modifiers",
                                        "quote_checks", "docs", "tags"])
        # the heading, the question as typed, then the answer exactly as it went out, Blueprint line included
        self.assertEqual(body, "\n# " + title + "\n\n> [!question] " + QUESTION + "\n\n" + fx.CLEAN_ANSWER
                         + "\nBlueprint: ENARSI 1.10.d\n")

    def test_a_ccie_note(self):
        values, _ = front(self.save(answer="Queues.\n\nBlueprint: CCIE 3.2.a\n").read_bytes())
        self.assertEqual((values["title"], values["exam"], values["ids"], values["tags"]),
                         ("CCIE 3.2.a Ask: Which path does OSPF prefer between areas", "CCIE Enterprise Infrastructure",
                          ["3.2.a"], ["ccie", "ccie/sec-3", "ccie/3-2-a"]))
        values, _ = front(self.save(answer="Blueprint: ENARSI 1.4").read_bytes())
        self.assertEqual(values["tags"], ["enarsi", "enarsi/sec-1", "enarsi/1-4"])

    def test_an_inbox_note(self):
        values, body = front(self.save(answer="Text.\nBlueprint: none\n").read_bytes())
        self.assertEqual(values, {"title": "Ask: Which path does OSPF prefer between areas", "type": "ask",
                                  "lens": "ask", "ids": [], "date": DATE, "model": "synthetic-model-9",
                                  "effort": "synthetic-effort", "modifiers": "", "tags": ["deep32/ask"]})
        self.assertTrue(body.endswith("Text.\nBlueprint: none\n"))

    def test_docs_are_only_documents_the_tools_returned(self):
        answer = ("Claim (verified \u00b7 rfc9999.txt:118). Another [RFC 9999 \u00a73.1] and [RFC 1234 \u00a71].\n"
                  "R1#show ip ospf and rfc5555#2 and exguide-1#timers.\n")
        values, _ = front(self.save(events=fx.clean_events(), answer=answer).read_bytes())
        self.assertEqual(values["docs"], ["rfc9999", "exguide-1"])
        values, _ = front(self.save(events=[], answer=answer).read_bytes())
        self.assertEqual((values["quote_checks"], "docs" in values), (0, False))

    def test_text_that_yaml_could_misread_is_quoted(self):
        question = 'key: "value" # not a comment \\ back slash {flow} [list] - item'
        model = 'm: "x" \\ y' + chr(0x2028) + "z" + chr(0x85) + "w"     # characters YAML reads as line breaks
        raw = self.save(question=question, model=model, effort="- y").read_bytes()
        values, _ = front(raw)
        self.assertEqual((values["title"], values["model"], values["effort"]),
                         ("ENARSI 1.10.d Ask: " + vault.short_question(question), model, "- y"))
        head = raw.decode("utf-8").split("\n---\n", 1)[0]
        self.assertNotIn(chr(0x2028), head)
        self.assertNotIn(chr(0x85), head)
        self.assertEqual(len(head.split("\n")), 1 + len(values))        # the opening line and one line per key

    def test_the_blueprint_line_adds_no_check_flag(self):
        plain = answer_check.check(fx.CLEAN_ANSWER, fx.clean_events(), question=fx.QUESTION)
        tagged = answer_check.check(fx.CLEAN_ANSWER + "\nBlueprint: ENARSI 1.10.d\n", fx.clean_events(),
                                    question=fx.QUESTION)
        self.assertEqual((tagged["status"], tagged["to_check"]), (plain["status"], plain["to_check"]))


class Hook(Vault):
    def runner(self, result=0, error=None):
        calls = []

        def run(argv, **options):
            calls.append((argv, options))
            if error is not None:
                raise error
            return subprocess.CompletedProcess(argv, result)
        return calls, run

    def test_the_hook_gets_the_vault_folder_and_nothing_else(self):
        calls, run = self.runner()
        store = self.store(hook=str(self.root / "synthetic-hook"), runner=run)
        self.assertTrue(store.run_hook())
        argv, options = calls[0]
        self.assertEqual(argv, [str(self.root / "synthetic-hook"), str(self.vault.resolve())])
        self.assertEqual((options["shell"], options["timeout"], options["cwd"]), (False, 60, str(self.vault.resolve())))
        for name in ("stdin", "stdout", "stderr"):
            self.assertEqual(options[name], subprocess.DEVNULL)
        self.assertEqual(self.logged[-1][:2], ("save-hook", "none"))

    def test_a_failing_hook_is_logged_and_the_note_stays(self):
        for number, (result, error, reason) in enumerate(((3, None, "unavailable"),
                                                          (0, subprocess.TimeoutExpired("synthetic-hook", 60), "timeout"),
                                                          (0, OSError("synthetic"), "unavailable"))):
            with self.subTest(reason=reason, error=error):
                self.logged.clear()
                calls, run = self.runner(result, error)
                store = self.store(hook=str(self.root / "synthetic-hook"), runner=run)
                store.jobs.put(dict(question="Synthetic hook question %d" % number, answer=ANSWER, time=NOW))
                store.jobs.put(None)
                store._work()
                self.assertEqual(len(calls), 1)
                self.assertEqual([item[:2] for item in self.logged], [("answer-save", "none"), ("save-hook", reason)])
                self.assertTrue((self.vault / OSPF / ("1.10.d Ask - Synthetic hook question %d.md" % number)).is_file())

    def test_the_default_runner_is_looked_up_when_the_hook_runs(self):
        # The offline test base replaces subprocess.run with a refusal: the hook's failure is only a log line.
        store = self.store(hook=str(self.root / "synthetic-hook"))
        self.assertFalse(store.run_hook())
        self.assertEqual(self.logged[-1][:2], ("save-hook", "unavailable"))

    def test_answers_saved_together_are_followed_by_one_run(self):
        calls, run = self.runner()
        store = self.store(hook=str(self.root / "synthetic-hook"), runner=run)
        for number in range(3):
            store.jobs.put(dict(question="Synthetic question %d" % number, answer=ANSWER, time=NOW))
        store.jobs.put(None)
        store._work()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.everything(self.vault / OSPF)), 3)

    def test_no_hook_no_run_and_no_save_no_run(self):
        calls, run = self.runner()
        self.assertFalse(self.store(runner=run).run_hook())
        store = self.store(hook=str(self.root / "synthetic-hook"), runner=run)
        store.jobs.put(dict(question=None, answer=None, time="not a time", events="not events"))
        (self.vault / "_inbox").write_bytes(b"a file where the inbox would be\n")
        store.jobs.put(None)
        store._work()
        self.assertEqual(calls, [])

    def test_the_thread_saves_what_was_handed_over(self):
        calls, run = self.runner()
        store = self.store(hook=str(self.root / "synthetic-hook"), runner=run)
        self.assertTrue(store.submit(dict(question=QUESTION, answer=ANSWER)))
        self.assertTrue(store.close(timeout=10))
        self.assertEqual(self.everything(self.vault / OSPF), ["1.10.d Ask - Which path does OSPF prefer between areas.md"])
        self.assertGreaterEqual(len(calls), 1)
        self.assertFalse(store.submit(dict(question=QUESTION, answer=ANSWER)))      # closed
        self.assertTrue(self.store().close())                                        # never started

    def test_a_full_queue_drops_the_answer_and_says_so(self):
        store = self.store()
        store.jobs, store.worker = queue.Queue(maxsize=1), object()
        store.jobs.put({})
        self.assertFalse(store.submit(dict(question=QUESTION, answer=ANSWER)))
        self.assertEqual(self.logged, [("answer-save", "limit", {})])

    def test_the_service_log_holds_counts_only(self):
        ledger = runtime.EventLedger(self.root / "events.jsonl", validate_owner=lambda: None, clock=lambda: NOW)
        calls, run = self.runner(result=1)
        store = vault.AnswerVault(self.vault, vault.read_map(self.map), str(self.root / "synthetic-hook"),
                                  log=ledger.append, runner=run, wall_clock=lambda: NOW)
        store.jobs.put(dict(question=QUESTION, answer=ANSWER, time=NOW))
        store.jobs.put(None)
        store._work()
        raw = (self.root / "events.jsonl").read_text(encoding="ascii")
        lines = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([(line["event"], line["reason"]) for line in lines],
                         [("answer-save", "none"), ("save-hook", "unavailable")])
        for fragment in ("OSPF", "Exams", "vault", "synthetic"):
            self.assertNotIn(fragment, raw)


class Supervised(Vault):
    def turn(self, on_save, answer, tool_events, *, final=True, **options):
        self.agent, self.events = Agent(), []
        self.agent.start_hook = lambda handle: setattr(handle, "describe", lambda: dict(
            version="9.9.9", model="synthetic-resolved-model", effort="synthetic-effort"))
        supervisor = Supervisor(self.agent, self.gateway, lambda stream, event: self.events.append(event),
                                lambda ident: Scratch(self.root, ident), clock=self.clock, on_save=on_save, **options)
        supervisor.open(1, "synthetic-1", dict(request()))
        supervisor.start(1, QUESTION)
        session = supervisor.sessions[1]
        self.play(session.capability, tool_events)
        outputs = [dict(type="delta", text=answer)] + ([dict(type="final", text=answer)] if final else []) + [USAGE]
        for event in outputs:
            supervisor.output(1, session.epoch, event)
        self.gateway.capabilities.clear()
        return supervisor

    def test_the_vault_gets_the_answer_as_the_workstation_received_it(self):
        handed = []
        answer = fx.CLEAN_ANSWER + "\nBlueprint: ENARSI 1.10.d\n"
        supervisor = self.turn(handed.append, answer, fx.clean_events(), checks="footer")
        self.assertEqual(supervisor.last_result["status"], "completed")
        self.assertTrue(supervisor.capture)
        self.assertEqual(len(handed), 1)
        turn = handed[0]
        self.assertEqual(sorted(turn), ["answer", "effort", "events", "model", "question"])
        self.assertEqual((turn["question"], turn["answer"], turn["model"], turn["effort"]),
                         (QUESTION, answer, "synthetic-resolved-model", "synthetic-effort"))
        self.assertEqual([event["tool"] for event in turn["events"]], [event["tool"] for event in fx.clean_events()])
        # the Checks footer went to the Ask window after the answer, and is no part of what is saved
        self.assertEqual([event["op"] for event in self.events],
                         ["session.opened", "agent.delta", "agent.final", "agent.delta", "turn.completed"])
        self.assertNotIn("Checks", turn["answer"])
        path = Path(self.store().save(dict(turn, time=NOW)))
        values, body = front(path.read_bytes())
        self.assertEqual((path.parent, values["quote_checks"], values["docs"], values["model"]),
                         ((self.vault / OSPF).resolve(), 3, ["rfc9999", "exguide-1"], "synthetic-resolved-model"))
        self.assertTrue(body.endswith(answer))

    def test_saving_alone_runs_no_check_and_shows_no_footer(self):
        handed = []
        with mock.patch.object(answer_check, "check", side_effect=AssertionError("no check without footer or record")):
            supervisor = self.turn(handed.append, ANSWER, [], final=False)
        self.assertEqual((supervisor.last_result["status"], len(handed), handed[0]["answer"]), ("completed", 1, ANSWER))
        self.assertEqual([event["op"] for event in self.events], ["session.opened", "agent.delta", "turn.completed"])

    def test_a_vault_that_fails_never_fails_the_answer(self):
        def broken(turn):
            raise RuntimeError("synthetic")
        self.assertEqual(self.turn(broken, ANSWER, []).last_result["status"], "completed")

    def test_without_the_setting_nothing_is_kept_or_handed_over(self):
        supervisor = self.turn(None, ANSWER, fx.clean_events())
        self.assertEqual((supervisor.capture, supervisor.on_save), (False, None))
        self.assertEqual(self.everything(self.vault), [])


class Instruction(Vault):
    TEXT = "SYNTHETIC-INSTRUCTION line one\nline two\n"

    def agent(self):
        return engine.NativeAgent("claude", self.root / "profile", instruction=self.TEXT)

    def test_the_sentence(self):
        self.assertEqual(vault.INSTRUCTION, "End the answer with one line `Blueprint: <exam> <id>` naming the closest "
                         "topic of the blueprint map (for example `Blueprint: ENARSI 1.10.d`), or `Blueprint: none`.")

    def test_the_sentence_is_added_only_when_saving_is_on(self):
        agent = self.agent()
        self.assertEqual(agent.turn_instruction(), self.TEXT)
        self.assertNotIn("Blueprint", agent.turn_instruction())
        agent.instruction_extra = vault.INSTRUCTION
        self.assertEqual(agent.turn_instruction(), self.TEXT.rstrip() + "\n\n" + vault.INSTRUCTION + "\n")
        self.assertEqual(agent.instruction, self.TEXT)          # the instruction file itself is unchanged

    def test_both_agents_receive_it_with_the_turn(self):
        agent = self.agent()
        agent.instruction_extra = vault.INSTRUCTION
        claude = native.ClaudeProtocol(self.root, "synthetic-model", agent.turn_instruction())
        claude.stage = "pre-turn"
        content = json.loads(claude.turn("synthetic question", ()))["message"]["content"]
        self.assertEqual(content, self.TEXT.rstrip() + "\n\n" + vault.INSTRUCTION + "\n\n\nsynthetic question")
        codex = native.CodexProtocol(self.root, None, None, agent.turn_instruction())
        self.assertTrue(codex.instruction.endswith(vault.INSTRUCTION + "\n"))

    def test_each_turn_launches_its_agent_with_the_turn_instruction(self):
        given = []

        class Launcher:
            def prepare(self, executable, scratch, profile, **options):
                given.append(options["instruction"])
                raise RuntimeError("synthetic: stop before any process")

        class Endpoint:
            url = "http://127.0.0.1:9/synthetic"

            def __init__(self, gate, capability):
                pass

            def start(self):
                pass

            def close(self):
                return True

        for extra in ("", vault.INSTRUCTION):
            with self.subTest(extra=extra):
                agent = engine.NativeAgent("claude", self.root / "profile", instruction=self.TEXT,
                                           proxy_factory=Endpoint)
                agent.ready, agent.models, agent.efforts = True, ("synthetic-model",), ("synthetic-effort",)
                agent.launcher, agent.gateway, agent.instruction_extra = Launcher(), self.gateway, extra
                capability = type("Capability", (), dict(bearer="synthetic-bearer"))()
                with mock.patch.object(agent, "verify", return_value=True), \
                        self.assertRaisesRegex(engine.EngineError, "native-startup-unverified"):
                    agent.start("synthetic question", (), self.root, capability, model="synthetic-model",
                                effort="synthetic-effort")
                self.assertEqual(given[-1], agent.turn_instruction())
                self.assertEqual(vault.INSTRUCTION in given[-1], bool(extra))


if __name__ == "__main__":
    unittest.main()
