# Synthetic reference-check test descriptions below.
"""Exercise the offline checker with virtual files; no build tool is run."""

import importlib.util
from pathlib import Path
import sys
import unittest


_SPEC = importlib.util.spec_from_file_location("tree_references", Path(__file__).with_name("tree_references.py"))
refs = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = refs
_SPEC.loader.exec_module(refs)


class ReferenceTests(unittest.TestCase):
    def check(self, files, names=(), merged=(), deleted=()):
        contents = {name: value.encode() for name, value in files.items()}
        return refs.check_references(contents, set(files) | set(names), set(merged), set(deleted))

    def test_selection_excludes_assets(self):
        for name in ("x.c", "x.h", "x.inc", "x.cpp", "x.mm", "x/Makefile", "GNUmakefile", "x/rules.mk"):
            self.assertTrue(refs.is_reference_file(name), name)
        for name in ("poster.png", "index.json", "README.md"):
            self.assertFalse(refs.is_reference_file(name), name)

    def test_include_searches_make_flags_and_merged_names(self):
        files = {"app/Makefile": "CPPFLAGS += -I../headers\n", "app/src/a.c": '#include "public.h"\n'}
        self.assertEqual(self.check(files, merged={"headers/public.h"}), [])

    def test_deleted_basename_refuses_despite_alternate_match(self):
        rows = self.check({"x/a.c": '#include "old.h"\n'}, names={"x/old.h"}, deleted={"library/src/old.h"})
        self.assertEqual(rows[0]["kind"], "quoted-include")

    def test_template_include_directive_is_checked_when_content_is_supplied(self):
        rows = self.check({"source.c.in": '#include "gone.h"\n'})
        self.assertEqual(rows[0]["reference"], "gone.h")

    def test_same_basename_make_prerequisite_in_another_directory_survives(self):
        self.assertEqual(self.check({"Makefile": "all: other/old.h\n"}, names={"other/old.h"}, deleted={"library/src/old.h"}), [])

    def test_comments_strings_and_angle_includes_are_not_references(self):
        files = {"x.c": '/*\n#include "gone.h"\n*/\n// #include "gone.h"\nconst char *s = "#include \\\"gone.h\\\"";\n#include <system.h>\n'}
        self.assertEqual(self.check(files), [])

    def test_make_ignores_assignments_comments_and_recipes(self):
        files = {"Makefile": "UNUSED=gone.c\n# target: gone.c\nall:\n\techo 'target: gone.c'\n"}
        self.assertEqual(self.check(files), [])

    def test_expanded_concrete_source_is_checked(self):
        rows = self.check({"Makefile": "SRC=gone.c\nall: $(SRC)\n"})
        self.assertEqual([row["reference"] for row in rows], ["gone.c"])

    def test_list_functions_and_pattern_outputs(self):
        files = {"app/Makefile": "SRC=$(filter-out src/main.c,$(wildcard src/*.c))\nOBJ=$(patsubst src/%.c,build/%.o,$(SRC))\nall: $(OBJ)\nbuild/%.o: src/%.c\n"}
        self.assertEqual(self.check(files, names={"app/src/main.c", "app/src/a.c"}), [])

    def test_generated_pattern_requires_its_source(self):
        rows = self.check({"Makefile": "all: build/gone.o\nbuild/%.o: src/%.c\n"})
        self.assertEqual([row["reference"] for row in rows], ["build/gone.o"])

    def test_module_name_in_patsubst_is_not_a_file_reference(self):
        files = {"library/Makefile": "MODULES=$(patsubst room_documents,room_cisco,room_documents)\nall:\n\techo $(MODULES)\n"}
        self.assertEqual(self.check(files, deleted={"library/src/room_cisco.c"}), [])

    def test_function_cannot_hide_missing_concrete_reference(self):
        rows = self.check({"Makefile": "all: $(addprefix src/,$(notdir gone.c))\n"})
        self.assertEqual([row["reference"] for row in rows], ["src/gone.c"])

    def test_unknown_function_reports_coverage_and_concrete_argument(self):
        rows = self.check({"Makefile": "all: $(unknown gone.c)\n"})
        self.assertEqual({row["kind"] for row in rows}, {"make-coverage", "make-prerequisite"})
        self.assertIn("gone.c", {row["reference"] for row in rows})

    def test_shell_is_never_executed_and_prerequisite_is_opaque(self):
        rows = self.check({"Makefile": "SRC=$(shell echo gone.c)\nall: $(SRC)\n"})
        self.assertEqual(rows[0]["kind"], "make-coverage")

    def test_static_shell_output_declaration_resolves_generated_list(self):
        files = {"Makefile": "OBJ=build\n$(shell echo names > $(OBJ)/modules.list)\nall: $(OBJ)/modules.list\n"}
        self.assertEqual(self.check(files), [])

    def test_bsd_loop_and_library_declarations(self):
        files = {"lib/Makefile": "LIB=demo\nSRCS=demo.c\nTESTDIR=../tests\n.include <bsd.lib.mk>\n.for name in a b\ntest-${name}: ${TESTDIR}/test_${name}.c lib${LIB}.a\n.endfor\nall: test-a test-b\n"}
        self.assertEqual(self.check(files, names={"lib/demo.c", "tests/test_a.c", "tests/test_b.c"}), [])

    def test_bsd_source_search_path(self):
        files = {"dlg/Makefile": "PROG=demo\nSRCS=ui.c sound.c\n.PATH: ../sound\n.include <bsd.prog.mk>\n"}
        self.assertEqual(self.check(files, names={"dlg/ui.c", "sound/sound.c"}), [])

    def test_raw_committed_source_alias(self):
        self.assertEqual(self.check({"ship/a.c": '#include "../../shared/public.h"\n'}, merged={"src/shared/public.h"}), [])

    def test_dynamic_conditional_checks_both_possible_source_branches(self):
        rows = self.check({"Makefile": "HAVE=$(shell probe)\nSRC=$(if $(HAVE),present.c,gone.c)\nall: $(SRC)\n"}, names={"present.c"})
        self.assertEqual([row["reference"] for row in rows], ["gone.c"])

    def test_missing_cisco_types_refuses_in_make_and_c(self):
        files = {"library/Makefile": "build/q_finder.o: src/lib/cisco_types.inc\n", "library/src/lib/q_finder.c": '#include "cisco_types.inc"\n'}
        self.assertEqual(len(self.check(files)), 2)
        self.assertEqual(self.check(files, names={"library/src/lib/cisco_types.inc"}), [])


if __name__ == "__main__":
    unittest.main()
