#!/usr/bin/perl
# patch-installsub.pl INSTALL.SUB LIB...: extend the OpenBSD response reader.
# The three libraries provide C6, C7 and console answers. No stored password.
use strict; use warnings;
my $f = shift or die "usage: patch-installsub.pl INSTALL.SUB LIB...\n";
die "patch-installsub: want three libraries\n" unless @ARGV == 3;
open(my $in, '<', $f) or die "$f: $!\n"; local $/; my $s = <$in>; close $in;
my $old = qq{\t\t[[ \$_q == \@(|*[[:blank:]])"\$_key"\@([[:blank:]?]*|) ]] &&\n\t\t\tresp=\$_val && cat && return\n};
my $new = qq{\t\tif [[ \$_q == \@(|*[[:blank:]])"\$_key"\@([[:blank:]?]*|) ]]; then\n\t\t\tresp=\$_val\n\t\t\tcat\n\t\t\t_d32resp "\$1" "\$_def"\n\t\t\treturn\n\t\tfi\n};
my $n = () = $s =~ /\Q$old\E/g;
die "patch-installsub: the _autorespond match line was found $n times (want 1)\n" unless $n == 1;
$s =~ s/\Q$old\E/$new/;
my $fn = '';
my $before = 0;
for my $lib (@ARGV) {
    open(my $fh, '<:raw', $lib) or die "$lib: $!\n";
    my $text = <$fh>; close $fh;
    die "patch-installsub: library must be ASCII with LF: $lib\n"
        if !defined($text) || $text =~ /[^\x00-\x7f]|\r/ || $text !~ /\n\z/;
    my ($first) = split /\n/, $text;
    my $name = $lib; $name =~ s{.*[/\\]}{};
    die "patch-installsub: library first line must name its file: $lib\n"
        unless $first =~ /^#.*\Q$name\E/;
    $before += length($text);
    my @lines = split /\n/, $text;
    $fn .= shift(@lines) . "\n";
    $fn .= join('', map { "$_\n" } grep { !/^\s*(?:#|$)/ } @lines);
}
# Optional root maintenance, no stored or shipped key. The ramdisk has ksh's
# print built-in, but no printf utility; keep all shell output on that built-in.
my $d32_maint = <<'D32_MAINT_KSH';
d32_maint_key_ok() {
    [ "$#" = 1 ] || return 1
    case $1 in ''|*[![:print:]]*) return 1;; esac
    # ksh print is available even when the ramdisk has no printf utility.
    print -r -- "$1" | LC_ALL=C grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]{68}={0,2}( [A-Za-z0-9@._-]{1,64})?$'
}

_d32maint_choose() {
    local _d32answer= _d32key= _d32line _d32count=0
    D32_MAINT_KEY=
    print -rn -- 'Allow root maintenance over SSH with your own key? (off unless you choose it) [no] ' >>"${D32_TTY_OUT:-/dev/tty}"
    IFS= read -r _d32answer <"${D32_TTY_IN:-/dev/tty}" || _d32answer=no
    case $_d32answer in y|yes) ;; *) resp=none; return 0;; esac
    # maint_key is a response-file field, separate from C7 version-1 settings.
    while IFS= read -r _d32line; do
        case $_d32line in 'maint_key = '*)
            _d32count=$((_d32count + 1)); _d32key=${_d32line#'maint_key = '};;
        esac
    done <"${D32_MAINT_RESPONSE:-/tmp/ai/ai.conf.tmp}"
    [ "$_d32count" -le 1 ] || return 1
    case $_d32key in ''|none|'@ASK@')
        print -rn -- 'Public ssh key for root account? ' >>"${D32_TTY_OUT:-/dev/tty}"
        IFS= read -r _d32key <"${D32_TTY_IN:-/dev/tty}" || return 1;;
    esac
    d32_maint_key_ok "$_d32key" || return 1
    D32_MAINT_KEY=$_d32key
    # The separate maintenance daemon owns its key; system sshd stays disabled.
    resp=none
}

_d32maint_carry() {
    local _d32record=${D32_ROOT-}/mnt/etc/d32-maint-key
    [ "${MODE-}" = install ] || return 0
    [ -n "${D32_MAINT_KEY-}" ] || return 0
    d32_maint_key_ok "$D32_MAINT_KEY" || return 1
    [ ! -e "$_d32record" ] && [ ! -L "$_d32record" ] || return 1
    (umask 077; print -r -- "$D32_MAINT_KEY" >"$_d32record") || return 1
    unset D32_MAINT_KEY
}

_d32resp() {
    if [ "$resp" = '@MAINT@' ]; then
        case $1 in 'Public ssh key for root account'*) ;; *) return 1;; esac
        _d32maint_choose || { err_exit 'maint_key'; return 1; }
    else
        _d32base_resp "$@"
    fi
}
D32_MAINT_KSH
my $d32_resp = () = $fn =~ /^_d32resp\(\) \{/mg;
die "patch-installsub: maintenance wants one response reader\n" unless $d32_resp == 1;
$fn =~ s/^_d32resp\(\) \{/_d32base_resp() {/m;
$fn .= $d32_maint;
my $d32_carry_old = "\tif [[ -x /mnt/\$MODE.site ]]; then\n";
my $d32_carry_new = "\t_d32maint_carry || err_exit 'maint_key'\n" . $d32_carry_old;
my $d32_carry_count = () = $s =~ /\Q$d32_carry_old\E/g;
die "patch-installsub: maintenance wants one site handover\n" unless $d32_carry_count == 1;
$s =~ s/\Q$d32_carry_old\E/$d32_carry_new/;
my $anchor = "_autorespond() {\n";
$n = () = $s =~ /^\Q$anchor\E/mg;
die "patch-installsub: _autorespond() found $n times (want 1)\n" unless $n == 1;
$s =~ s/^\Q$anchor\E/$fn$anchor/m;
open(my $out, '>:raw', $f) or die "$f: $!\n"; print $out $s; close $out;
print "patch-installsub: inserted " . scalar(@ARGV) . " files: $before bytes, " . length($fn) . " bytes without blank and comment lines\n";
