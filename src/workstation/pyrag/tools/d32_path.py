# pytest plugin (diagnostic rerun only): deep32-engine's tests hard-code PATH=/usr/bin:/bin for the build_root.py
# subprocess, a Linux layout; OpenBSD's git is /usr/local/bin/git. Append /usr/local/bin to exactly that PATH.
import subprocess

_init = subprocess.Popen.__init__


def _patched(self, *args, **kwargs):
    env = kwargs.get("env")
    if env is not None and env.get("PATH") == "/usr/bin:/bin":
        kwargs["env"] = {**env, "PATH": "/usr/bin:/bin:/usr/local/bin"}
    _init(self, *args, **kwargs)


subprocess.Popen.__init__ = _patched
