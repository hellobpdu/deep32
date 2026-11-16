"""Adds the settings-disc reader, the console status lines and the first start to the Workstation install, with no default password (release specification section 11).

What it changes
    Adds C6 and C7 readers and first-start code; rewires the existing installer.
Usage
    apply(tree_dir), on a tree with the preceding transforms applied.
Exit
    Returns the sorted changed paths; raises on any missing or moved anchor.
"""
import hashlib
from pathlib import Path

NEW_FILES = {
    'distrib/site/etc/d32/site.d/90-node-settings.sh': ('new/distrib/site/etc/d32/site.d/90-node-settings.sh', '0ce0f500d19df55bfbdd51c0f151b68ab62dbbda83131615169e0872125a2415'),  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-c6.sh': ('new/distrib/site/usr/local/d32/libexec/d32-c6.sh', '463ebafbf441c46ac2e0b69cc0c63409712d5f8f3a63495501bfde0a1e203c8c'),  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-settings.sh': ('new/distrib/site/usr/local/d32/libexec/d32-settings.sh', 'bc152a758447996c5e54936a83c7c5d7d9ee45a8f4f27dac435292c8de0e8c9a'),  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/sbin/d32-c6': ('new/distrib/site/usr/local/d32/sbin/d32-c6', '5372718a2f8b8deb0bea0f4f1954061d78232ab25710cbcd78570ba43d3ab3c0'),  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/sbin/d32-firstboot': ('new/distrib/site/usr/local/d32/sbin/d32-firstboot', '5c1275e85fe77bbc904700fcacc6838faf6d7f1758d79581491d9353f4cd67fb'),  # sha256  # sha256 or public signing fingerprint
    'ship/install/d32-ramdisk.ksh': ('new/ship/install/d32-ramdisk.ksh', '53338f04457abea8f1ded28f10cd02c0681abb6235bfd464755068d656c60e4c'),  # sha256  # sha256 or public signing fingerprint
}

EDITS = {'distrib/site/install.site': [(b'umask 022\n', b'umask 022\nD32_C6_INSTALL=0\nif [ -r /usr/local/d32/libexec/d32-c6.sh ]; then\n    . /usr/local/d32/libexec/d32-c6.sh\n    C6_DIR=/var/run/d32/c6\n    C6_VERSION=$(sed -n \'1{s/[[:space:]].*//;p;}\' /etc/d32/release 2>/dev/null)\n    C6_NAME=$(hostname -s)\n    if c6_setup workstation stdout; then\n        D32_C6_INSTALL=1\n        c6_adopt /etc/d32-c6.conf || :\n    fi\nfi\nc6i() {\n    [ "$D32_C6_INSTALL" != 1 ] || c6_set install "$@" || :\n    return 0\n}\n', 1), (b'print -r -- "D32: start site set $(cat /etc/d32/release), $count hooks" | tee -a $log\n', b'print -r -- "D32: start site set $(cat /etc/d32/release), $count hooks" | tee -a $log\nc6i running "msg=installing: $count steps"\n', 1), (b'\t\tprint -r -- "D32: fail missing hook $need" | tee -a $log\n', b'\t\tprint -r -- "D32: fail missing hook $need" | tee -a $log\n\t\tc6i failed error=WS_INSTALL_STEP "msg=missing install step $need"\n', 1), (b'\tprint -r -- "D32: phase $n $count $name" | tee -a $log\n', b'\tprint -r -- "D32: phase $n $count $name" | tee -a $log\n\tc6i running "msg=step $n of $count: $name"\n', 1), (b'\t\tprint -r -- "D32: fail $name" | tee -a $log\n', b'\t\tprint -r -- "D32: fail $name" | tee -a $log\n\t\tc6i failed error=WS_INSTALL_STEP "msg=install step $name failed"\n', 1), (b'\tprint -r -- "D32: done site set" | tee -a $log\n', b'\tprint -r -- "D32: done site set" | tee -a $log\n\tc6i done "msg=installed; the machine restarts"\n', 1)], 'ship/site/etc/d32/site.d/66-ship-node.sh': [(b'# secrets the services need, made HERE on the target (random, root 0400): never on the medium.\n', b'# token files the services need (root 0400) with empty tokens: d32-firstboot makes them at the first start.\n', 1), (b'ctok=$(openssl rand -hex 32)\n', b'', 1), (b'mtok=$(openssl rand -hex 32)\n', b'', 1), (b"# /etc/d32/core.env (root 0400): written at install by 66-ship-node; the token is this machine's own.\n", b'# /etc/d32/core.env (root 0400): written at install by 66-ship-node; d32-firstboot makes the token at the first start.\n', 1), (b'# /etc/d32/edge.env (root 0400): written at install by 66-ship-node.\n', b'# /etc/d32/edge.env (root 0400): written at install by 66-ship-node; d32-firstboot makes the tokens at the first start.\n', 1), (b'RAG_MCP_TOKENS=$mtok\n', b'RAG_MCP_TOKENS=\n', 1), (b'print -r -- "$mtok" >/etc/d32/state/mcp.token\n', b'', 1), (b'unset ctok mtok\n', b'', 1), (b'chown root:wheel /etc/d32/core.env /etc/d32/edge.env /etc/d32/accel.env /etc/d32/state/mcp.token\n', b'chown root:wheel /etc/d32/core.env /etc/d32/edge.env /etc/d32/accel.env\n', 1), (b'chmod 0600 /etc/d32/state/mcp.token\n', b'', 1), (b'd32 "item core.env, edge.env (fresh tokens, root 0400); GPU_EXT off; remote front off; asklink installed, not enabled"\n', b'd32 "item core.env, edge.env (root 0400, no tokens yet); GPU_EXT off; remote front off; asklink installed, not enabled"\n', 1), (b'RAG_CORE_API_TOKEN=$ctok\n', b'RAG_CORE_API_TOKEN=\n', 2)], 'ship/tools/mkimage-ship.sh': [(b'perl $S/ship/install/patch-installsub.pl $W/m/install.sub >/dev/null', b'perl $S/ship/install/patch-installsub.pl $W/m/install.sub $S/distrib/site/usr/local/d32/libexec/d32-c6.sh $S/distrib/site/usr/local/d32/libexec/d32-settings.sh $S/ship/install/d32-ramdisk.ksh >/dev/null', 1), (b's/@ASK@//;s/@ASKSECRET@//;s/@DUID:[0-9a-f]*@//', b's/@ASK@//;s/@ASKSECRET@//;s/@DUID:[0-9a-f]*@//;s/@ROOTDISK:[0-9a-f]*@//;s/@SET:[a-z_]*@//;s/@ENCRYPT@//;s/@DEFAULT@//', 1)], 'ship/u4/etc/rc.local': [(b'# test images only: the first-boot check, once, on the console and in /var/log/d32-firstboot-check.log\n', b'# the first start (disc 1): follows the Tailscale sign-in and waits for disc 2, writing C6 status lines; it\n# returns at once on every start after the first start is complete (d32-firstboot)\n[ -x /usr/local/d32/sbin/d32-firstboot ] && /usr/local/d32/sbin/d32-firstboot resume </dev/null >/dev/null 2>&1 &\n\n# test images only: the first-boot check, once, on the console and in /var/log/d32-firstboot-check.log\n', 1)], 'ship/u4/usr/local/d32/sbin/d32-link': [(b'em[0-9]|ure[0-9]) ;; *) die "link.conf: ports may be em0..em9, ure0..ure9"', b'em[0-9]|ure[0-9]|hvn[0-9]|vio[0-9]|vmx[0-9]) ;; *) die "link.conf: ports may be em0..em9, ure0..ure9, hvn0..hvn9, vio0..vio9, vmx0..vmx9"', 1)]}

REWRITES = {
    'distrib/site/etc/d32/site.d/80-firstboot.sh': (
        '2bae1f1f8a3499f1569b2892c4f754f4bd042842a7ccd4ad5422ff531780dc10',  # sha256 input guard  # sha256 or public signing fingerprint
        'rewrite/distrib/site/etc/d32/site.d/80-firstboot.sh',
        '97d704f49a424d082e1abc838758bd9d60b4be51feeee7b819aea5589c0f6562',  # sha256 source payload  # sha256 or public signing fingerprint
    ),
    'ship/install/auto_install-guided.conf.in': (
        '611c407e5467116abc6578f6dcceba699495755bd292276614345c4e6f41af78',  # sha256 input guard  # sha256 or public signing fingerprint
        'rewrite/ship/install/auto_install-guided.conf.in',
        'd3130b91c268d1ba434ccbe39b1aff3aa21e3242902c91d0a5d8c509dba4c627',  # sha256 source payload  # sha256 or public signing fingerprint
    ),
    'ship/install/patch-installsub.pl': (
        'f73f2ce702ae7045188891d64e812ba4e28d28e215af334ba1e101e0ef46b229',  # sha256 input guard  # sha256 or public signing fingerprint
        'rewrite/ship/install/patch-installsub.pl',
        '3edf1edb18507cc35c58729794a9cb6a0194ac04eae7b2fc70b25b64c2ef75e8',  # sha256 source payload  # sha256 or public signing fingerprint
    ),
}


def source_payload(rel, digest):
    data = (Path(__file__).resolve().parent / '901-install-settings-and-first-boot-sources' / rel).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('source payload hash: ' + rel)
    return data

def apply(tree_dir):
    root = Path(tree_dir)
    pending = {}
    for name, (source_rel, digest) in NEW_FILES.items():
        data = source_payload(source_rel, digest)
        if (root / name).exists():
            raise FileExistsError(name)
        pending[name] = data
    for name, items in EDITS.items():
        data = (root / name).read_bytes()
        for old, new, count in items:
            if data.count(old) != count:
                raise ValueError('anchor count: ' + name)
            data = data.replace(old, new)
        pending[name] = data
    for name, (guard, source_rel, digest) in REWRITES.items():
        data = source_payload(source_rel, digest)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != guard:
            raise ValueError('guard: ' + name)
        pending[name] = data
    for name, data in sorted(pending.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return sorted(pending)
