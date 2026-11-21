"""Owned process-tree launch and cancellation.

Windows creates the child suspended, assigns a non-breakaway kill-on-close Job,
then resumes it. Linux/macOS start a new session and kill its process group on
cancel/close, then wait for the root. A descendant that leaves the group can
outlive cancellation; with built-in tools disabled agents start no tool processes.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time


class ProcessError(RuntimeError):
    pass


class WindowsJob:
    def __init__(self):
        if os.name != "nt":
            raise ProcessError("tree-containment-unavailable")
        from ctypes import wintypes as w
        self.w = w
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.lock = threading.RLock()
        self.process = self.job = None
        self.stdin = self.stdout = self.stderr = None
        self.closed = False
        self.pid = None
        self._bind()

    def _bind(self):
        w, k = self.w, self.k
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD]
        k.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
        k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.ResumeThread.argtypes = [w.HANDLE]
        k.ResumeThread.restype = w.DWORD
        k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]

    def start(self, argv, *, cwd, env):
        # Cancellation cannot certify an empty tree while startup is still
        # creating or assigning its suspended root process.
        with self.lock:
            return self._start(argv, cwd=cwd, env=env)

    def _start(self, argv, *, cwd, env):
        """No shell or ambient environment; only stdin/out/err are inherited."""
        from ctypes import wintypes as w
        import msvcrt
        if (self.closed or self.process is not None or not argv or not os.path.isabs(argv[0])
                or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
                or any(not isinstance(k, str) or not isinstance(v, str) or "=" in k or "\0" in k + v for k, v in env.items())):
            raise ProcessError("invalid-process-plan")
        class SA(ctypes.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]
        class SI(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                ("x", w.DWORD), ("y", w.DWORD), ("xs", w.DWORD), ("ys", w.DWORD),
                ("xc", w.DWORD), ("yc", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", ctypes.c_void_p),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]
        class SIX(ctypes.Structure):
            _fields_ = [("startup", SI), ("attributes", ctypes.c_void_p)]
        class PI(ctypes.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", w.DWORD),
                ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("active", w.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        k = self.k
        k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(SIX), ctypes.POINTER(PI)]
        k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        raw_handles, streams, attributes = [], [], None
        info = PI()
        try:
            self.job = k.CreateJobObjectW(None, None)
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags.
            if not self.job or not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ProcessError("tree-containment-unavailable")
            sa = SA(ctypes.sizeof(SA), None, True)
            pairs = []
            for index in range(3):
                read, write = w.HANDLE(), w.HANDLE()
                if not k.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(sa), 0):
                    raise ProcessError("process-start-failed")
                raw_handles.extend((read.value, write.value))
                parent = write if index == 0 else read
                child = read if index == 0 else write
                if not k.SetHandleInformation(parent, 1, 0):
                    raise ProcessError("process-start-failed")
                pairs.append((parent.value, child.value))
            length = ctypes.c_size_t()
            k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(length))
            attributes = ctypes.create_string_buffer(length.value)
            if not k.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(length)):
                raise ProcessError("process-start-failed")
            handles = (w.HANDLE * 3)(*[pair[1] for pair in pairs])
            if not k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, ctypes.sizeof(handles), None, None):
                raise ProcessError("process-start-failed")
            startup = SIX()
            startup.startup.cb = ctypes.sizeof(SIX)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            environment = ctypes.create_unicode_buffer("\0".join(k + "=" + env[k] for k in sorted(env, key=str.upper)) + "\0\0")
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            flags = 0x4 | 0x400 | 0x80000 | 0x08000000  # suspended, Unicode, extended startup, hidden
            if not k.CreateProcessW(argv[0], command, None, None, True, flags, environment, str(cwd), ctypes.byref(startup), ctypes.byref(info)):
                raise ProcessError("process-start-failed")
            self.process, self.pid = info.process, info.pid
            if not k.AssignProcessToJobObject(self.job, self.process):
                raise ProcessError("tree-containment-unavailable")
            for index, (parent, child) in enumerate(pairs):
                k.CloseHandle(child)
                raw_handles.remove(child)
                mode = os.O_WRONLY if index == 0 else os.O_RDONLY
                fd = msvcrt.open_osfhandle(parent, mode | os.O_BINARY)
                raw_handles.remove(parent)
                streams.append(os.fdopen(fd, "wb" if index == 0 else "rb", buffering=0))
            self.stdin, self.stdout, self.stderr = streams
            if k.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise ProcessError("process-start-failed")
            return self
        except Exception:
            # cancel verifies both the Job and the exact root handle. A failed
            # assignment leaves the suspended root outside the Job; an empty Job
            # is therefore never enough to certify successful cleanup.
            self.cancel()
            raise ProcessError("process-start-failed") from None
        finally:
            if info.thread:
                k.CloseHandle(info.thread)
            if attributes is not None:
                k.DeleteProcThreadAttributeList(attributes)
            for handle in raw_handles:
                k.CloseHandle(handle)

    def poll(self):
        with self.lock:
            if not self.process:
                return 1 if self.closed else None
            code = self.w.DWORD()
            if not self.k.GetExitCodeProcess(self.process, ctypes.byref(code)):
                raise ProcessError("process-observation-failed")
            return None if code.value == 259 else code.value

    def active_count(self):
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")]
            _fields_ += [(name, self.w.DWORD) for name in ("faults", "total", "active", "terminated")]
        value = Accounting()
        if not self.k.QueryInformationJobObject(self.job, 1, ctypes.byref(value), ctypes.sizeof(value), None):
            raise ProcessError("process-observation-failed")
        return value.active

    def _empty(self):
        try:
            return self.active_count() == 0
        except ProcessError:
            return False

    def cancel(self, grace=2.0):
        with self.lock:
            if self.closed:
                return True
            # No cooperation is needed; TerminateJobObject targets the owned
            # kernel object, never a name/PID that could have been recycled.
            clean = self.job is None
            end = time.monotonic() + min(max(grace, 0), 2.0)
            if self.job:
                self.k.TerminateJobObject(self.job, 1)
                while True:
                    clean = self._empty()
                    if clean or time.monotonic() >= end:
                        break
                    time.sleep(0.01)
                if not clean:
                    return False
            if self.process:
                # Assignment may have failed, leaving this exact suspended
                # root outside the Job. Do not close its handle or report an
                # empty tree until the kernel confirms that it has terminated.
                signalled = self.k.WaitForSingleObject(self.process, 0) == 0
                if not signalled:
                    self.k.TerminateProcess(self.process, 1)
                    wait_ms = max(0, int((end - time.monotonic()) * 1000))
                    signalled = self.k.WaitForSingleObject(self.process, wait_ms) == 0
                if not signalled:
                    return False
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream:
                    stream.close()
            if self.process:
                self.k.CloseHandle(self.process)
                self.process = None
            if self.job:
                self.k.CloseHandle(self.job)
                self.job = None
            self.closed = True
            return clean


class PosixGroup:
    """R15 session/group cancellation, without a hostile-descendant observer."""
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.stdin = self.stdout = self.stderr = None
        self.closed = False
        self.pid = None

    def start(self, argv, *, cwd, env):
        with self.lock:
            if (self.closed or self.process is not None or not argv or not os.path.isabs(argv[0])
                    or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
                    or any(not isinstance(k, str) or not isinstance(v, str) or "=" in k or "\0" in k + v
                           for k, v in env.items())):
                raise ProcessError("invalid-process-plan")
            try:
                self.process = subprocess.Popen(list(argv), cwd=str(cwd), env=dict(env),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, close_fds=True, start_new_session=True, bufsize=0)
            except (OSError, ValueError):
                raise ProcessError("process-start-failed") from None
            self.pid = self.process.pid
            self.stdin, self.stdout, self.stderr = self.process.stdin, self.process.stdout, self.process.stderr
            return self

    def poll(self):
        with self.lock:
            return self.process.poll() if self.process is not None else (1 if self.closed else None)

    def cancel(self, grace=2.0):
        with self.lock:
            if self.closed:
                return True
            if self.process is not None:
                try:
                    # Signal the original group even when its leader exited;
                    # ordinary children can still hold its output pipes open.
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    return False
                try:
                    self.process.wait(timeout=min(max(grace, 0), 2.0))
                except (OSError, subprocess.TimeoutExpired):
                    return False
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream:
                    stream.close()
            self.closed = True
            return True

    close = cancel


def native_tree(platform):
    if platform == "windows" and os.name == "nt":
        return WindowsJob()
    if platform in ("linux", "macos") and os.name == "posix":
        return PosixGroup()
    raise ProcessError("tree-containment-unavailable")
