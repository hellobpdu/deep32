"""Whole-process network and child-process guard for the offline suite."""
import os
from pathlib import Path
import socket
import subprocess

_installed = False

def install():
    global _installed
    if _installed:
        return
    _installed = True
    def wrap(original):
        def guarded(self, address, *args, **kwargs):
            if not isinstance(address,tuple) or address[0] != '127.0.0.1':
                raise AssertionError('non-loopback network refused')
            return original(self,address,*args,**kwargs)
        return guarded
    for name in ('connect','connect_ex','bind'):
        setattr(socket.socket,name,wrap(getattr(socket.socket,name)))
    original = subprocess.Popen
    class GuardedPopen(original):
        def __init__(self, args, *pos, **kwargs):
            program = args[0] if isinstance(args,(list,tuple)) else args.split()[0]
            name = Path(os.fspath(program)).name.lower()
            if name.startswith(('tailscale','ssh','wsl','docker','ragops')) or kwargs.get('shell'):
                raise AssertionError('process refused')
            super().__init__(args,*pos,**kwargs)
    subprocess.Popen = GuardedPopen
