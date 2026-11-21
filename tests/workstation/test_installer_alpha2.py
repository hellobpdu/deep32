#!/usr/bin/env python3
"""Offline generated-installer regression tests; no OpenBSD or VM is required.

Set D32_TEST_WORK to keep transient fixtures in an explicitly selected directory.
On Windows the only shell used is Git for Windows' full Bash path. The harness
implements ksh print with Bash's builtin printf and rejects any generated printf
command. Native OpenBSD runs use ksh directly. All power commands are fixtures.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "src/workstation/ship/install"
DIFF = ROOT / "src/workstation/distrib/install/install.sub-halt.diff"
STAGES = ROOT / "src/build/s5-build-driver/stages"
WINDOWS = os.name == "nt"
GIT_BIN = Path("C:/Program Files/Git/usr/bin")
SHELL = Path("C:/Program Files/Git/bin/bash.exe") if WINDOWS else Path(shutil.which("ksh") or "/bin/ksh")
PERL = GIT_BIN / "perl.exe" if WINDOWS else Path(shutil.which("perl") or "/usr/bin/perl")
PATCH = GIT_BIN / "patch.exe" if WINDOWS else Path(shutil.which("patch") or "/usr/bin/patch")


def shell_path(path):
    value = Path(path).resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if WINDOWS else value


def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


# Keep the actual OpenBSD anchors exact, including tabs. Both transforms must
# refuse drift; using a minimal fixture avoids bundling third-party install.sub.
BASE = r'''#!/bin/ksh
_autorespond() {
    local _q=$1 _key=$1 _def=$2 _val=$3
\t\t[[ $_q == @(|*[[:blank:]])"$_key"@([[:blank:]?]*|) ]] &&
\t\t\tresp=$_val && cat && return
}
d32_site_handover() {
\tif [[ -x /mnt/$MODE.site ]]; then
        :
    fi
}
d32_install_end() {

\tprep_root_mail /tmp/ai/ai.log "$(hostname) $AI_MODE log"

\texec reboot
}

'''.replace("\\t", "\t")

PRINT_SHIM = r'''
print() {
    local newline=1
    case ${1-} in -rn|-nr) newline=0; shift;; -r) shift;; esac
    [ "${1-}" != -- ] || shift
    if [ "$newline" = 1 ]; then builtin printf '%s\n' "$*"; else builtin printf '%s' "$*"; fi
}
'''

COMMON = r'''
printf() { echo GENERATED_PRINTF_IS_FORBIDDEN >&2; return 97; }
err_exit() { echo "ERROR:$*" >&2; return 1; }
_d32waiting() { :; }
_d32running() { :; }
_d32record() { :; }
_d32load() { D32_SETTINGS_DISC=${SETTINGS:-absent}; D32S_EFF_hw_profile=vm; }
get_dkdevs_root() { print -r -- "$DISKS"; }
get_dkdevs() { print -r -- "$DISKS"; }
sysctl() { print -r -- 'sd0:media,sd1:target1,sd2:target2'; }
D32_TEST_MADE_DEVS=
make_dev() { D32_TEST_MADE_DEVS="$D32_TEST_MADE_DEVS $*"; }
# Like the ramdisk: a disk has no device node until make_dev creates it.
disklabel() {
    case " $D32_TEST_MADE_DEVS " in *" $1 "*) ;; *) echo "disklabel: /dev/r$1: No such file or directory" >&2; return 4;; esac
    print -r -- 'bytes/sector: 512'; print -r -- "total sectors: ${SECTORS:-4194304}"
}
diskinfo() { print -r -- "DISKINFO:$*"; }
hostname() { print -r -- test-host; }
prep_root_mail() { :; }
AI_MODE=autoinstall
MODE=install
'''


class InstallerAlpha2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in (SHELL, PERL, PATCH):
            if not tool.is_file():
                raise unittest.SkipTest("required offline test tool missing: " + str(tool))
        base = os.environ.get("D32_TEST_WORK")
        cls._temp = tempfile.TemporaryDirectory(prefix="installer-alpha2-", dir=base)
        cls.work = Path(cls._temp.name)
        cls.generated = cls.work / "install.sub"
        cls.generated.write_text(BASE, encoding="ascii", newline="\n")
        cls.patch_input = cls.work / "halt.patch"
        cls.patch_input.write_text("\n".join(line for line in DIFF.read_text().splitlines() if not line.startswith("#")) + "\n", encoding="ascii", newline="\n")
        patch = subprocess.run([str(PATCH), "-t", "-F", "0", "-p0", "-i", str(cls.patch_input)], cwd=cls.work, text=True, capture_output=True)
        if patch.returncode:
            raise AssertionError(patch.stdout + patch.stderr)
        libraries = []
        for name in ("c6-test.ksh", "settings-test.ksh"):
            lib = cls.work / name
            lib.write_text("# " + name + "\n", encoding="ascii", newline="\n")
            libraries.append(lib)
        libraries.append(INSTALL / "d32-ramdisk.ksh")
        generated = subprocess.run([str(PERL), str(INSTALL / "patch-installsub.pl"), str(cls.generated)] + [str(p) for p in libraries], text=True, capture_output=True)
        if generated.returncode:
            raise AssertionError(generated.stdout + generated.stderr)
        cls.generated_text = cls.generated.read_text()
        cls.fake_bin = cls.work / "fake-bin"
        cls.fake_bin.mkdir()
        for name in ("halt", "reboot"):
            tool = cls.fake_bin / name
            tool.write_text("#!/bin/sh\necho FIXTURE_" + name.upper() + ":\"$*\"\n", encoding="ascii", newline="\n")
            tool.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def run_shell(self, code, answers="", *, disks="sd0 sd1", settings="absent", sectors="4194304"):
        case = self.work / self.id().rsplit(".", 1)[-1]
        case.mkdir()
        (case / "disklabel.ship").write_text("/ 1G\n", encoding="ascii", newline="\n")
        (case / "input").write_text(answers, encoding="ascii", newline="\n")
        (case / "output").write_text("", encoding="ascii")
        (case / "maint-response").write_text("maint_key = @ASK@\n", encoding="ascii", newline="\n")
        (case / "mnt/etc").mkdir(parents=True)
        script = case / "case.ksh"
        prefix = PRINT_SHIM if WINDOWS else ""
        prefix += ". " + quote(shell_path(self.generated)) + "\n" + COMMON
        prefix += "\n".join("%s=%s" % (key, quote(value)) for key, value in {
            "D32_ROOT": shell_path(case),
            "D32_TTY_IN": shell_path(case / "input"),
            "D32_TTY_OUT": shell_path(case / "output"),
            "D32_MAINT_RESPONSE": shell_path(case / "maint-response"),
            "DISKS": disks, "SETTINGS": settings, "SECTORS": sectors,
        }.items()) + "\n"
        prefix += "PATH=" + quote(shell_path(self.fake_bin)) + ':"$PATH"\n'
        # Reopening a tty reads the next input, while reopening a regular file
        # restarts it. Duplicate one open descriptor to model the tty stream.
        prefix += 'exec 3<"$D32_TTY_IN"\nD32_TTY_IN=/dev/fd/3\n'
        # Git Bash reopens /dev/fd as a file on Windows. Maintenance opens
        # the tty separately for its two reads; preserve its stream semantics.
        prefix += r'''
read() {
    case ${2-} in
    _d32answer|_d32key) builtin read "$@" <&3;;
    *) builtin read "$@";;
    esac
}
'''
        script.write_text(prefix + "\n" + code + "\n", encoding="ascii", newline="\n")
        args = [str(SHELL), "--noprofile", "--norc", "-O", "extglob", shell_path(script)] if WINDOWS else [str(SHELL), str(script)]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertNotIn("GENERATED_PRINTF_IS_FORBIDDEN", result.stderr)
        return result, (case / "output").read_text(), case

    def test_tailscale_package_root(self):
        roots = [line for line in (INSTALL / "pkg-roots").read_text().splitlines() if line and not line.startswith("#")]
        self.assertEqual(roots.count("tailscale"), 1)
        # The build installs roots before collecting the closure; stage 70 only
        # packages the two system packs. This executes stage 80's exact extraction.
        stage = (STAGES / "80-image.sh").read_text()
        extraction = next(line.strip() for line in stage.splitlines() if line.strip().startswith("roots=$("))
        result, _, _ = self.run_shell("S=" + quote(shell_path(ROOT / "src/workstation")) + "\n" + extraction + '\ncase " $roots " in *" tailscale "*) exit 0;; *) exit 1;; esac')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PKG_PATH=/build/pkgrepo/ pkg_add -I $roots", stage)
        site = (ROOT / "src/workstation/distrib/site/etc/d32/site.d/20-packages.sh").read_text()
        self.assertIn("repo=/mnt/packages/amd64", site)
        self.assertIn("PKG_PATH=$repo/ pkg_add -I -m $roots", site)

    def test_generated_maintenance_decline_uses_builtins(self):
        result, output, _ = self.run_shell("_autorespond 'Public ssh key for root account' none '@MAINT@' </dev/null || exit 1\nprint -r -- RESULT:$resp", "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:none", result.stdout)
        self.assertIn("[no]", output)

    def test_generated_maintenance_key_and_carry_use_builtins(self):
        key = "ssh-ed25519 " + "A" * 68 + " test-key"
        result, output, case = self.run_shell("_autorespond 'Public ssh key for root account' none '@MAINT@' </dev/null || exit 1\n_d32maint_carry || exit 2\n[ -z \"${D32_MAINT_KEY-}\" ] || exit 3\nprint -r -- RESULT:$resp", "yes\n" + key + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((case / "mnt/etc/d32-maint-key").read_text(), key + "\n")
        self.assertIn("RESULT:none", result.stdout)
        self.assertIn("Public ssh key", output)

    def test_generated_maintenance_invalid_key_refused(self):
        result, _, case = self.run_shell("_autorespond 'Public ssh key for root account' none '@MAINT@' </dev/null\nexit $?", "yes\ninvalid\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((case / "mnt/etc/d32-maint-key").exists())

    def test_empty_keyboard_answer_is_default(self):
        result, output, _ = self.run_shell("kbd() { echo UNEXPECTED_KBD >&2; return 99; }\n_autorespond 'Choose your keyboard layout [default]' default '@SET:keyboard@' </dev/null || exit 1\nprint -r -- RESULT:$resp", "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:default", result.stdout)
        self.assertNotIn("cannot be used", output)
        self.assertNotIn("UNEXPECTED_KBD", result.stderr)

    def test_explicit_default_keyboard_answer_still_works(self):
        result, _, _ = self.run_shell("_autorespond 'Choose your keyboard layout [default]' default '@SET:keyboard@' </dev/null || exit 1\nprint -r -- RESULT:$resp", "default\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:default", result.stdout)

    def test_sole_target_is_default_and_requires_confirmation(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null || exit 1\nprint -r -- RESULT:$resp/$D32_GUIDED_DISK_READY", "\nsd1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:sd1/1", result.stdout)
        self.assertIn("[sd1]", output)
        self.assertNotIn("[sd0]", output)
        self.assertIn("Type the disk name to confirm", output)

    def test_medium_is_never_an_accepted_target(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null || exit 1\nprint -r -- RESULT:$resp", "sd0\nsd1\nsd1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:sd1", result.stdout)
        self.assertIn("That disk cannot be used", output)

    def test_multiple_targets_have_no_default(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null || exit 1\nprint -r -- RESULT:$resp", "\nsd2\nsd2\n", disks="sd0 sd1 sd2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:sd2", result.stdout)
        self.assertNotIn("[sd0]", output)
        self.assertNotIn("[sd1]", output)
        self.assertNotIn("[sd2]", output)
        self.assertIn("That disk cannot be used", output)

    def test_no_target_has_no_default_and_cannot_continue(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null\nexit $?", "\n", disks="sd0")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[sd0]", output)
        self.assertIn("That disk cannot be used", output)

    def test_sole_small_target_cannot_continue(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null\nexit $?", "\n", sectors="1024")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("guided install needs at least", output)
        self.assertNotIn("whole disk will be erased", output)

    def test_settings_disc_sole_target_keeps_automatic_selection(self):
        result, output, _ = self.run_shell("_autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null || exit 1\nprint -r -- RESULT:$resp/$D32_GUIDED_DISK_READY", settings="present")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT:sd1/1", result.stdout)
        self.assertEqual(output, "")

    def test_guided_completion_flag_reaches_poweroff_scope(self):
        # The real install runs in a child process of do_autoinstall: answer the disk question in a subshell.
        result, _, _ = self.run_shell("( _autorespond 'Which disk is the root disk? [sd0]' sd0 '@ROOTDISK:media@' </dev/null ) || exit 1\nd32_install_end", "\nsd1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Remove the install medium before starting the installed system.", result.stdout)
        self.assertIn("FIXTURE_HALT:-p", result.stdout)
        self.assertNotIn("FIXTURE_REBOOT", result.stdout)

    def test_other_completion_keeps_original_reboot(self):
        result, _, _ = self.run_shell("d32_install_end")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FIXTURE_REBOOT:", result.stdout)
        self.assertNotIn("FIXTURE_HALT", result.stdout)

    def test_boot_text_matches_release_and_guided_questions(self):
        boot = (INSTALL / "boot.conf.guided").read_text()
        self.assertIn("# guided medium boot sentence; release ID only.", boot)
        self.assertIn("Deep32 Workstation 0.1.0-alpha.2 (alpha). Guided install:", boot)
        self.assertIn("it asks for the disk, the passphrases and the passwords.", boot)
        self.assertIn("set image /bsd\n", boot)
        self.assertIn("# install completion, before the first start.", self.generated_text)

    def test_patch_refuses_altered_autorespond_anchor(self):
        altered = self.work / "altered-install.sub"
        altered.write_text(BASE.replace("resp=$_val && cat && return", "resp=$_val && return"), encoding="ascii", newline="\n")
        libs = [self.work / "c6-test.ksh", self.work / "settings-test.ksh", INSTALL / "d32-ramdisk.ksh"]
        result = subprocess.run([str(PERL), str(INSTALL / "patch-installsub.pl"), str(altered)] + [str(p) for p in libs], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("want 1", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
