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
my $anchor = "_autorespond() {\n";
$n = () = $s =~ /^\Q$anchor\E/mg;
die "patch-installsub: _autorespond() found $n times (want 1)\n" unless $n == 1;
$s =~ s/^\Q$anchor\E/$fn$anchor/m;
open(my $out, '>:raw', $f) or die "$f: $!\n"; print $out $s; close $out;
print "patch-installsub: inserted " . scalar(@ARGV) . " files: $before bytes, " . length($fn) . " bytes without blank and comment lines\n";
