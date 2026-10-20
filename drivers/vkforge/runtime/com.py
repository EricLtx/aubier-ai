"""The COM adapter of a learned driver (L-P1): the only module that touches COM. Vendored into every
generated driver as vendor/vkforge_runtime/com.py (L-P19): the stdlib, vkdriver.protocol and, lazily
and on Windows only, comtypes. It names no application (L-P31): what differs between Word and
PowerPoint at this level is binding["application"], the ontology's applications block for the app.
The instance it starts, it identifies by its process — the one new process of the application's
executable between a Toolhelp32 snapshot before the start and one after, its parent the DcomLaunch
service's process (D-03, PF-4) — and holds in a kill-on-close Job of its own (D-04): that instance's
dialog boxes are the only ones it closes, and it ends with the driver however the driver ends, save
the one strictly bounded exception of a clean end that hands it to the user (D-35). An instance it
attached to, or could not identify, it never quits, holds, or closes a box of."""
import ctypes
import gc
import hashlib
import os
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

from vkdriver.protocol import DriverRefusal

CALL_DEADLINE_S = 30.0   # one COM call (L-P33); a qualification pass has 90 s (qualify.PASS_DEADLINE_S)
CLEANUP_DEADLINE_S = 5.0  # the close that ends an action, and the end of the session: often after a timeout
QUIT_WAIT_S = 3.0        # the clean-end window (D-35 (1)), under the 5 s grace of a host (L-P49) and of qualify
COM_TRUE = -1            # VARIANT_TRUE, also PowerPoint's msoTrue: true for a bool and an MsoTriState alike (L-P31)
DIALOG_CLASS = "#32770"  # the Win32 dialog box class: a sign-in, an overwrite, a repair prompt
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x00000002
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9           # JOBOBJECTINFOCLASS
WAIT_OBJECT_0 = 0x00000000
WAIT_FAILED = 0xFFFFFFFF
ERROR_NO_MORE_FILES = 18                            # a Toolhelp32 walk's end; any other error cuts it short
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SC_STATUS_PROCESS_INFO = 0                          # SC_STATUS_TYPE


def executables() -> set:
    """The executables this driver drives: its manifest's `applications`, which the host puts in
    VK_DRIVER_APPLICATIONS (Task 1) and `vkforge qualify` too (Task 8)."""
    return {e.strip().upper() for e in os.environ.get("VK_DRIVER_APPLICATIONS", "").split(",") if e.strip()}


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


class _BASIC_LIMIT(ctypes.Structure):               # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _EXTENDED_LIMIT(ctypes.Structure):            # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _FILETIME(ctypes.Structure):                  # FILETIME: 100 ns intervals since 1601, in two DWORDs
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _SERVICE_STATUS_PROCESS(ctypes.Structure):    # SERVICE_STATUS_PROCESS: nine DWORDs
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode", "dwServiceSpecificExitCode",
        "dwCheckPoint", "dwWaitHint", "dwProcessId", "dwServiceFlags")]


_K32 = None


def _kernel32():
    """kernel32 with the signatures the snapshot and the Job use, made once (Windows only)."""
    global _K32
    if _K32 is None:
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        k.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
        k.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
        k.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(_FILETIME),) * 4)
        k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        _K32 = k
    return _K32


_ADVAPI32 = None


def _advapi32():
    """advapi32 with the signatures the service query uses, made once (Windows only)."""
    global _ADVAPI32
    if _ADVAPI32 is None:
        from ctypes import wintypes

        a = ctypes.WinDLL("advapi32", use_last_error=True)
        a.OpenSCManagerW.restype = wintypes.HANDLE
        a.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        a.OpenServiceW.restype = wintypes.HANDLE
        a.OpenServiceW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
        a.QueryServiceStatusEx.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD))
        a.CloseServiceHandle.argtypes = (wintypes.HANDLE,)
        _ADVAPI32 = a
    return _ADVAPI32


def processes():
    """Every process of the machine as (id, parent id, executable name in upper case), from one
    Toolhelp32 snapshot (ctypes, no psutil); None off Windows, when the snapshot fails, or when its walk
    stops on anything but its end (ERROR_NO_MORE_FILES): a short list read as complete would let a
    process already running pass for a new one (PF-4: fail closed)."""
    if sys.platform != "win32":
        return None
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        found = []
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.upper()))
            ok = k.Process32NextW(snap, ctypes.byref(entry))
        if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
            return None
        return found
    finally:
        k.CloseHandle(snap)


def pids_of(exes: set):
    """{process id: parent process id} of the processes running one of `exes`; None when there is no
    executable to look for or no snapshot — the process the adapter starts cannot then be identified
    (PF-4)."""
    listed = processes() if exes else None
    return None if listed is None else {pid: parent for pid, parent, exe in listed if exe in exes}


def dcom_launcher_pid():
    """The process id of the DcomLaunch service — the parent of every local server COM starts, hence of
    the instance a CreateObject starts (ruling PF-4) — read with QueryServiceStatusEx; None off
    Windows, when the service cannot be queried, or when it reports no process."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    a = _advapi32()
    manager = a.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        return None
    try:
        service = a.OpenServiceW(manager, "DcomLaunch", SERVICE_QUERY_STATUS)
        if not service:
            return None
        try:
            status, needed = _SERVICE_STATUS_PROCESS(), wintypes.DWORD()
            if not a.QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO, ctypes.byref(status),
                                          ctypes.sizeof(status), ctypes.byref(needed)):
                return None
            return status.dwProcessId or None
        finally:
            a.CloseServiceHandle(service)
    finally:
        a.CloseServiceHandle(manager)


def identified(before, after, launcher) -> tuple:
    """The process a start added, positively identified (D-03, ruling PF-4): (pid, None) when exactly
    one process of the executable is in `after` and not in `before` (snapshots {pid: parent}) and its
    parent is `launcher`, the DcomLaunch service's process; (None, why) for anything else — no
    snapshot, no launcher, none or several new (another start at the same moment), a parent that is
    not the launcher (a start from the shell: the user's)."""
    if before is None or after is None:
        return None, "no snapshot of the application's processes"
    if launcher is None:
        return None, "the DcomLaunch service's process is unknown"
    new = sorted(set(after) - set(before))
    if len(new) != 1:
        return None, f"{len(new)} new processes of the application"
    if after[new[0]] != launcher:
        return None, f"process {new[0]} was not started by the DCOM launcher"
    return new[0], None


def _limit(job, flags: int) -> None:
    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = flags
    if not _kernel32().SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


class Job:
    """A Job object of the adapter's own holding the Office process it started and identified (founder
    decisions D-04 and D-35): created with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, its handle not
    inheritable (no security attributes) and held by this driver process alone. However this process
    ends — a qualification pass killed at its deadline, the host's kill (a STOP, an act's deadline, an
    umount during an act, the end of its 5 s grace), a crash — the OS closes the handle and the process
    in the Job ends with it; a clean end closes it too, after Quit, and what is left of the process
    ends the same way. The one exception is D-35's: at a clean end inside the clean-end window, the
    driver's own documents closed and a foreign one positively identified, the instance is handed to
    the user and `disarm` clears the limit of this instance's Job alone — its process then outlives the
    handles. Any failure before that disarm leaves the Job armed. Never made for an instance the adapter
    attached to or could not identify (PF-4); and when Windows refuses it, the instance just started is
    quit, or ended by its PID once checked to be the process identified, and the session refused
    (rulings PF-16, PF-23): no instance of the driver's runs outside a Job."""

    def __init__(self, job, process, pid: int):
        self.job, self.process, self.pid = job, process, pid

    @classmethod
    def hold(cls, pid: int) -> "Job":
        """Process `pid` assigned to a new kill-on-close Job; OSError when Windows refuses a step."""
        if sys.platform != "win32":
            raise OSError("Job objects are Windows'")
        k = _kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _limit(job, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            process = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            if not k.AssignProcessToJobObject(job, process):
                error = ctypes.WinError(ctypes.get_last_error())
                k.CloseHandle(process)
                raise error
        except BaseException:
            k.CloseHandle(job)
            raise
        return cls(job, process, pid)

    def wait(self, timeout_s: float) -> bool:
        """Whether the held process ended within `timeout_s` (a negative one waits nothing); OSError when
        Windows refuses the wait itself (a closed handle), never read as a process still running."""
        k = _kernel32()
        result = k.WaitForSingleObject(self.process, max(0, int(timeout_s * 1000)))
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == WAIT_OBJECT_0

    def disarm(self) -> None:
        """The kill-on-close limit cleared, on this Job alone: its process outlives the handles (D-35 (4))."""
        _limit(self.job, 0)

    def close(self) -> None:
        """Both handles closed, once; an armed Job then ends what is left in it."""
        k = _kernel32()
        for name in ("process", "job"):
            handle = getattr(self, name)
            if handle:
                setattr(self, name, None)
                k.CloseHandle(handle)


class Owned:
    """The instance this adapter started and identified (D-03, PF-4): its process id, and the Job that
    ends it with the driver (D-04) — always one: a start whose Job Windows refuses goes no further
    (ruling PF-16)."""

    def __init__(self, pid: int, job):
        self.pid, self.job = pid, job


# The Jobs of identified instances whose start was refused at its deadline (LF-8, PF-4, PF-17): each ends
# at the next start, or with this process.
_HELD = []


def _hold(pid: int):
    """The Job that ends `pid` with this driver (D-04). Windows' refusal is raised: D-04 has no exception
    (rulings PF-16, PF-23), so the start that made `pid` goes no further, and its caller says how that
    instance ended, on the `job: process … not held` line."""
    return Job.hold(pid)


def _identity(pid: int, handle):
    """What names process `pid` beyond its number (ruling PF-23): (executable name, parent id, creation
    time), the image and the creation time read through `handle`, the parent from a snapshot; None when
    any of them cannot be read."""
    k = _kernel32()
    image, size = ctypes.create_unicode_buffer(32768), ctypes.c_ulong(32768)
    if not k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        return None
    created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                             ctypes.byref(user)):
        return None
    parent = next((parent for listed, parent, _ in processes() or () if listed == pid), None)
    if parent is None:
        return None
    return PureWindowsPath(image.value).name.upper(), parent, created.dwHighDateTime << 32 | created.dwLowDateTime


def identity(pid: int):
    """Process `pid`'s identity (`_identity`), taken now: when the adapter identifies the process its
    start added (ruling PF-23). None off Windows or when it cannot be read."""
    if sys.platform != "win32":
        return None
    k = _kernel32()
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return _identity(pid, handle)
    finally:
        k.CloseHandle(handle)


def end_by_pid(pid: int, known):
    """Process `pid` terminated by its PID (TerminateProcess; ruling PF-23, D-04 to the letter), only while
    it is still the process identified: `known`, its identity taken then, is read again — the same image,
    the same parent, the same creation time — through the very handle that ends it, which pins the
    process, so a PID reused since is never hit. None when it was ended; else why nothing was terminated:
    a check that fails, or cannot be made."""
    if known is None:
        return "its identity was not taken when it was identified"
    if sys.platform != "win32":
        return "no process to end off Windows"
    k = _kernel32()
    handle = k.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"process {pid} cannot be opened (error {ctypes.get_last_error()})"
    try:
        now = _identity(pid, handle)
        if now is None:
            return f"process {pid} cannot be checked again"
        if now != known:
            return f"process {pid} is no longer the one identified"
        if not k.TerminateProcess(handle, 1):
            return f"process {pid} cannot be terminated (error {ctypes.get_last_error()})"
        return None
    finally:
        k.CloseHandle(handle)


class JobRefused(OSError):
    """Windows refused to hold process `pid` in a Job (D-04): its error, with that process and its
    identity as taken when it was identified (rulings PF-16, PF-23)."""

    def __init__(self, pid: int, known, error: OSError):
        super().__init__(str(error))
        self.pid, self.known = pid, known


def own(started: bool, before, after, launcher, hold=_hold):
    """What the adapter owns of the instance it has (D-03; rulings LF-5 and PF-4): Owned(pid, hold(pid))
    only when it started the instance and identified its process positively (`identified`); None —
    attached — otherwise: an instance it found running (GetActiveObject), or one it cannot tell from the
    user's (CreateObject handed back a running PowerPoint, several new processes, a parent other than
    the DcomLaunch service, no snapshot, no launcher). An attached instance is never quit, never held in
    a Job, its dialog boxes never closed; a start that cannot be told apart is one stderr line. The
    identity of the process identified is taken there and then (`identity`), and a Job Windows refuses
    is raised as JobRefused, with them, for the caller to end that instance (rulings PF-16, PF-23)."""
    if not started:
        return None
    pid, why = identified(before, after, launcher)
    if pid is None:
        sys.stderr.write(f"start: treated as attached: {why}\n")
        return None
    known = identity(pid)
    try:
        return Owned(pid, hold(pid))
    except OSError as e:
        raise JobRefused(pid, known, e) from e


def _end_held(exes: set) -> None:
    """The instances refused starts left held (LF-8) end before another start, each Job closed armed and
    its process given CLEANUP_DEADLINE_S to leave the snapshot: a retry must not meet the driver's own
    half-started instance and take it for the user's — PowerPoint hands its running instance to
    CreateObject."""
    while _HELD:
        job = _HELD.pop()
        job.close()
        deadline = time.monotonic() + CLEANUP_DEADLINE_S
        while job.pid in (pids_of(exes) or {}) and time.monotonic() < deadline:
            time.sleep(0.05)


def _start(progid: str, get_active, create, errors: tuple, deadline_s: float) -> tuple:
    """The instance a session runs on and what the adapter owns of it, (application, Owned or None)
    (D-03, D-04; rulings PF-4, PF-16, PF-17): the running instance when `get_active(progid)` finds one —
    attached, nothing started — else `create(progid)`, a start, owned only when its one new process is
    positively identified (`own`), and then held in a Job. Which call the start reached is written down
    before `create` is called: held past `deadline_s` before that, the start started nothing of the
    driver's, so no box is closed and no process held (PF-17); past it, a box of the new process
    positively identified is closed, and that process held in _HELD, to end at the next start or with
    this driver (LF-8). A start identified whose Job Windows refuses is quit at once — no document is in
    it yet — or, with no reference to quit it (a start past its deadline) or a quit that fails, ended by
    its PID once checked to be the process identified; the session refuses `precondition` (PF-16,
    PF-23). `get_active` and `create` are comtypes' in a driver, stubs in the tests; `errors`, what a
    COM call raises."""
    exes, launcher = executables(), dcom_launcher_pid()
    _end_held(exes)
    before = pids_of(exes)                              # before get_active: an attach starts nothing
    reached = {"create": False}

    def run():
        try:
            return get_active(progid), False
        except errors:
            reached["create"] = True                    # PF-17: from here on, a start of the driver's
            return create(progid), True

    def owned(started: bool, after):
        if started and not exes:
            sys.stderr.write("start: treated as attached: no executable named (VK_DRIVER_APPLICATIONS is empty)\n")
            return None
        return own(started, before, after, launcher, _hold)

    def started_pid():
        return identified(before, pids_of(exes), launcher)[0] if reached["create"] else None

    def unheld(e: JobRefused, app=None) -> DriverRefusal:
        """The instance Windows will not hold ends at once (D-04 to the letter): quit through the
        reference its start returned (PF-16); with none — a start past its deadline — or a quit that
        fails, ended by its PID once checked to be the process identified (PF-23). One line says how;
        the session refuses `precondition`."""
        how = "no reference to quit it"
        if app is not None:
            try:
                guarded(lambda: app.Quit(), f"quit {progid}", CLEANUP_DEADLINE_S, owner=e.pid)
                how = "quit"
            except (DriverRefusal, *errors) as q:
                how = f"not quit ({q})"
        if how != "quit":
            why = end_by_pid(e.pid, e.known)
            how += "; ended by its PID" if why is None else f"; not ended: {why}"
        sys.stderr.write(f"job: process {e.pid} not held: {e}; {how}\n")
        return DriverRefusal("precondition", f"{progid} cannot be started: process {e.pid} cannot be held in a "
                                             f"Job: {e}")

    try:
        # A box raised while the instance starts (a first-run prompt) is closed only when the start
        # reached CreateObject and its new process is positively identified by then (D-03, PF-4, PF-17).
        app, started = guarded(run, f"start {progid}", deadline_s, owner=started_pid)
    except DriverRefusal:
        try:
            held = owned(reached["create"], pids_of(exes))     # LF-8: a positively owned start only
        except JobRefused as e:                         # PF-23: no Job, and no reference to quit it with
            raise unheld(e) from e
        if held is not None:
            _HELD.append(held.job)                      # it ends at the next start, or with this driver
        raise
    after = pids_of(exes) if started else None
    try:
        return app, owned(started, after)
    except JobRefused as e:                             # PF-16: no Job, no session
        raise unheld(e, app) from e


def dialogs(exes: set) -> list:
    """The visible top-level dialog boxes (class #32770) of the processes running one of `exes`, each as
    (window, process id): the guard closes only those of the instance the adapter started and identified
    (D-03, PF-4). The processes come from one snapshot (pids_of); without one, no box is seen."""
    if sys.platform != "win32" or not exes:
        return []
    pids = pids_of(exes)
    if not pids:
        return []
    from ctypes import wintypes

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = (enum_proc, wintypes.LPARAM)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    found = []

    def each(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == DIALOG_CLASS and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                found.append((hwnd, pid.value))
        return True

    user32.EnumWindows(enum_proc(each), 0)
    return found


def close_window(hwnd) -> None:
    """WM_CLOSE, posted: the box's own close, never one of its answers (spec §6)."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def guarded(fn, what: str, deadline_s: float = CALL_DEADLINE_S, find_dialogs=None, close=None, owner=None):
    """fn() on its own thread, joined for `deadline_s` (L-P33). Past it, the dialog boxes of the
    application's executables are listed with their process (D-03): those of `owner` — the process of
    the instance the adapter started, or a callable naming it at that moment — are closed, never
    answered, and the action refuses `dialog`. A box of any other process is never touched (LF-6):
    with no owner known (an attached instance: the user's, or one the adapter could not identify) a box of the
    application still means `dialog`, said to be left open; with an owner known, a box elsewhere did
    not hold this call, and the action refuses `timeout`, as it does with none open. A thread that
    finds comtypes loaded joins the multithreaded apartment that Application keeps alive, so the
    proxies one call's thread made serve the next."""
    find_dialogs = find_dialogs or (lambda: dialogs(executables()))
    close = close or close_window
    box = {}

    def run():
        com = sys.modules.get("comtypes")    # loaded by Application only: the fake's hook runs without COM
        if com is not None:
            com.CoInitializeEx(com.COINIT_MULTITHREADED)
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — raised again on the caller's thread
            box["error"] = e
        finally:
            if com is not None:
                com.CoUninitialize()

    worker = threading.Thread(target=run, name=f"com {what}", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        pid = owner() if callable(owner) else owner
        seen = find_dialogs()
        ours = [hwnd for hwnd, process in seen if pid is not None and process == pid]
        for hwnd in ours:
            close(hwnd)
        if ours:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the instance the driver started held the call; "
                                          "closed, never answered")
        if seen and pid is None:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the application is open; left open: "
                                          "the instance is not one the driver owns")
        raise DriverRefusal("timeout", f"{what}: no answer within {deadline_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _com_errors() -> tuple:
    """What a failed COM call raises: OSError and, once Application has loaded comtypes, COMError."""
    com = sys.modules.get("comtypes")
    return (OSError,) if com is None else (OSError, com.COMError)


def _version_key(name: str):
    major, _, minor = name.partition(".")
    return int(major, 16), int(minor or "0", 16)          # registry versions are hexadecimal `major.minor`


def _not_registered(guid: str) -> DriverRefusal:
    return DriverRefusal("precondition", f"type library {guid} is not registered; remedy: install Microsoft 365 desktop")


def typelib_path(guid: str) -> str:
    """inventory.typelib_path, line for line (this module is vendored and cannot import it), so the
    probe hashes the very file inventory hashed (L-P22): HKCR\\TypeLib\\{guid}, its parsable versions
    highest first (hexadecimal `major.minor`, any other subkey skipped), 0\\win64 then 0\\win32."""
    if sys.platform != "win32":
        raise _not_registered(guid)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}") as key:
            versions = []
            try:
                while True:
                    versions.append(winreg.EnumKey(key, len(versions)))
            except OSError:                                 # the end of the enumeration
                pass
        parsable = []
        for version in versions:
            try:
                parsable.append((_version_key(version), version))
            except ValueError:                              # a subkey that is not `major.minor`: not a version
                continue
        for _, version in sorted(parsable, reverse=True):
            for bitness in ("win64", "win32"):
                try:
                    return winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}\{version}\0\{bitness}")
                except OSError:
                    continue
    except OSError:
        pass
    raise _not_registered(guid)


def live_typelib_sha256(guid: str) -> str:
    """The type library file the registry names for `guid` today (typelib_path), hashed as the
    catalogue's `typelib_sha256` is (L-P22)."""
    path = typelib_path(guid)
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise DriverRefusal("precondition", f"type library file of {guid} cannot be read; "
                                            "remedy: repair Microsoft 365 desktop") from None


def probe(binding: dict) -> None:
    """L-P22: the type library on this node is the one the driver was learned on (binding.json is in its
    driver_id); else every action refuses before its document is opened."""
    if live_typelib_sha256(binding["typelib"]["guid"]) != binding["typelib_sha256"]:
        raise DriverRefusal("precondition", "office version changed; remedy: vk driver learn again")


class Application:
    """The application binding["application"] names: the user's own instance when one is running
    (spike verdict 3), else one the adapter starts. The adapter changes only its DisplayAlerts, read
    first and put back by quit() whenever the instance is left running (L-P47), and the Visible of the
    instance it hands to the user (D-35, PF-12). An instance it started and identified by its process
    (D-03, PF-4) is the only one whose dialog boxes it closes, and is held in a kill-on-close Job of
    the adapter's (D-04) — a start whose Job Windows refuses is quit at once and the session refused
    (PF-16): quit() ends it — save D-35's bounded exception — and a driver that dies takes it along.
    Any other instance is attached: never quit, never held. `connect` returns (application, owned),
    owned an Owned or None; the default is the running instance, else a new one. What the driver
    opens is written down for the end (D-35): `opened`, the FullName of every document it opened;
    `mine`, those it has not closed; `unnamed_opens`, its opens whose document it never named."""

    def __init__(self, binding: dict, connect=None):
        self.binding, self.spec = binding, binding["application"]
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        self.deadline_s, self.cleanup_deadline_s = CALL_DEADLINE_S, CLEANUP_DEADLINE_S
        self.app, self.owned, self.alerts_before, self.failed = None, None, None, None
        self.opened, self.mine, self.unnamed_opens = set(), [], 0   # what the driver opened (D-35)
        progid = self.spec["progid"]
        try:
            self.app, self.owned = (connect or self._connect)()
            self.alerts_before = self._guard(lambda: self.app.DisplayAlerts, "DisplayAlerts")
            self._guard(lambda: setattr(self.app, "DisplayAlerts", self.spec["alerts_off"]), "DisplayAlerts")
        except BaseException as e:
            if self.owned is not None:
                self.owned.job.close()                  # LF-8: the instance started for a session that cannot begin
            if isinstance(e, _com_errors()):
                raise DriverRefusal("precondition", f"{progid} cannot be started: {e}") from e
            raise

    @property
    def pid(self):
        """The process of the instance this adapter started and identified (D-03, PF-4); None otherwise."""
        return None if self.owned is None else self.owned.pid

    def _guard(self, fn, what: str, deadline_s: float | None = None):
        """guarded() with this session's call deadline by default, and the started process as the one
        whose dialog boxes it may close (D-03)."""
        return guarded(fn, what, self.deadline_s if deadline_s is None else deadline_s, owner=self.pid)

    def _connect(self) -> tuple:
        progid = self.spec["progid"]
        if sys.platform != "win32":
            raise DriverRefusal("precondition", f"{progid} cannot be started: not Windows")
        cookie = ctypes.c_void_p()
        ctypes.oledll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie))   # the calls' apartment lives with the driver
        import comtypes
        import comtypes.client

        return _start(progid, comtypes.client.GetActiveObject, comtypes.client.CreateObject,
                      (OSError, comtypes.COMError), self.deadline_s)

    def _documents(self):
        return getattr(self.app, self.spec["collection"])

    def probe(self) -> None:
        probe(self.binding)

    def open(self, path: str):
        """The document, opened. A failure while opening (no such file, a damaged one, the application's
        refusal) is `precondition`, as the fake's; a failure inside a verb is `internal` in both. The
        plan's "not open elsewhere" holds in this very instance too: there Open hands back a document the
        user already has open, unsaved edits included; refused, and that document is not closed. What
        the driver opens is written down for the end of the session (D-35): its FullName, read back after
        the Open, in `opened`, and the document in `mine` until it is closed; an Open whose document is
        never named — refused by its guard, or its name unreadable — stays counted in `unnamed_opens`,
        a doubt that keeps the instance from being handed over."""
        self.failed = None                                  # a new action begins with its document
        try:
            open_before = self._guard(lambda: {d.FullName for d in _items(self._documents())}, "open")
            self.unnamed_opens += 1                         # D-35 (3): until its document is named
            try:
                doc = self._guard(lambda: self._documents().Open(path, **self.spec["open"]), "open")
            except _com_errors():
                self.unnamed_opens -= 1                     # the application refused: it opened nothing
                raise
            name = self._guard(lambda: doc.FullName, "open")
            self.unnamed_opens -= 1
            already = name in open_before
            if not already:
                self.opened.add(name)                       # D-35 (3): the driver's exact list
                self.mine.append(doc)                       # D-35 (2): closed at the end if still open
            read_only = not already and self._guard(lambda: bool(doc.ReadOnly), "open")
        except _com_errors() as e:
            raise DriverRefusal("precondition", f"document cannot be opened: {e}") from e
        if already:
            raise DriverRefusal("precondition", "document already open in the application (the user's window)")
        if read_only:
            self.failed = "open"
            self.close(doc)
            raise DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
        return doc

    def call(self, verb: str, doc, **args):
        try:
            return self._guard(lambda: VERBS[verb](self, doc, **args), verb)
        except BaseException as e:
            self.failed = verb                              # the action refuses: its close is a cleanup
            if isinstance(e, _com_errors()):
                raise DriverRefusal("internal", f"{verb}: {e}") from e
            raise

    def close(self, doc) -> None:
        """Closed without saving. After a refusal in this action, the close is a cleanup against an
        application that may be wedged: the short cleanup deadline, its own refusal written to stderr,
        never raised, so the action's first refusal is the one the host gets. After a successful action,
        the normal deadline, and a failure is `internal` "close failed: …": a document the driver opened
        must not stay open, hidden, in the user's instance without the host hearing of it. One that did
        not close stays in `mine`: in the instance the adapter started, it is closed again at the end of
        the session (D-35 (2)); an attached instance's `mine` is released then, not closed."""
        if self.failed is not None:
            try:
                self._guard(lambda: doc.Close(**self.spec["close"]), "close", self.cleanup_deadline_s)
            except (DriverRefusal, *_com_errors()) as e:
                sys.stderr.write(f"close: {e}\n")
                return
        else:
            try:
                self._guard(lambda: doc.Close(**self.spec["close"]), "close")
            except (DriverRefusal, *_com_errors()) as e:
                raise DriverRefusal("internal", f"close failed: {e}") from e
        self._closed(doc)

    def _closed(self, doc) -> None:
        """`doc` is closed: no longer the driver's to close at the end (D-35 (2))."""
        self.mine = [d for d in self.mine if d is not doc]

    def quit(self) -> None:
        """The end of the session, once (L-P46, L-P47; founder decisions D-04 and D-35). An attached
        instance — the user's, or one whose process the adapter could not identify (PF-4) — is never
        quit and gets its DisplayAlerts back. The instance the adapter started and identified ends with
        the driver unless D-35's five bounds all hold (`_end_started`, inside the clean-end window that
        starts here); one handed to the user gets its DisplayAlerts back too, best effort, after the
        disarm (PF-2, PF-12), and a box that holds that call is left open: the instance is the user's
        from then on (D-03). Every COM reference the adapter holds — the application, and any document
        of its own still listed — is then released, and the Job's handles closed: armed, the Job ends
        what is left of the instance — after a Quit, once its process has had what is left of the
        window; disarmed, the instance is the user's: visible, in a disarmed Job no handle reaches any
        more (a process never leaves a Job), no automation reference left (PF-12). A failure is written
        to stderr, never raised: this runs when serve returns and at exit."""
        if self.app is None:
            return
        owned, end = self.owned, time.monotonic() + QUIT_WAIT_S
        ending = "attached" if owned is None else self._end_started(owned, end)
        try:
            if ending in ("attached", "kept") and self.alerts_before is not None:
                # the user's instance now, attached or handed over: no owner, a box that holds it stays (D-03)
                guarded(lambda: setattr(self.app, "DisplayAlerts", self.alerts_before), "DisplayAlerts",
                        self.cleanup_deadline_s)
        except (DriverRefusal, *_com_errors()) as e:
            sys.stderr.write(f"quit: {e}\n")
        finally:
            held, self.app, self.owned, self.mine = [self.app, *self.mine], None, None, []
            try:      # every reference the adapter holds, dropped in the apartment it was made in (PF-12)
                self._guard(lambda: (held.clear(), gc.collect()), "release", self.cleanup_deadline_s)
            except DriverRefusal as e:
                sys.stderr.write(f"quit: {e}\n")
            if owned is not None:
                self._close_job(owned, end, ending)

    def _bounded(self, fn, what: str, end: float):
        """fn() under the guard, given what is left of the clean-end window that ends at `end` (D-35
        (1)): the window over is refused like any timeout."""
        left = end - time.monotonic()
        if left <= 0:
            raise DriverRefusal("timeout", f"{what}: the clean-end window is over")
        return self._guard(fn, what, left)

    def _end_started(self, owned, end: float) -> str:
        """The bounded end of the instance this adapter started and identified (founder decision D-35,
        rulings PF-2 and PF-12): inside the clean-end window, before any other call, in this order —
        (2) every document the driver opened and still holds is closed without saving (the binding's
        close: Word's SaveChanges=False; PowerPoint's Close saves nothing); (3) the instance's documents
        are listed by FullName (`_inventory`, its Protected View windows' included, PF-21), one foreign
        only when its FullName is not in `opened`; (4) with a
        foreign document, the instance is handed to the user — Visible set to COM_TRUE and read back
        true: True, as comtypes gives Word's VARIANT_BOOL, or COM_TRUE, PowerPoint's msoTrue; never
        UserControl, which Word's Application cannot set and PowerPoint's does not have — and then,
        only then, this instance's Job alone is disarmed; (5) one stderr line says so, the process id
        and the count, never a name. Returns "kept" (handed over), "quit" (nothing foreign: Quit sent)
        or "ended": a failure, a timeout or a doubt before the disarm — an open the driver never named,
        a document of its own that will not close, a listing that fails, a Visible that cannot be set
        or reads back false, the window over — means no disarm, and the Job, closed, takes the instance
        (D-04)."""
        try:
            if self.unnamed_opens:
                raise DriverRefusal("internal",
                                    f"{self.unnamed_opens} open(s) whose document the driver never named")
            for doc in list(self.mine):                                                  # (2)
                self._bounded(lambda d=doc: d.Close(**self.spec["close"]), "close", end)
                self._closed(doc)
            self._bounded(gc.collect, "quit", end)
            names = self._bounded(self._inventory, "inventory", end)
            foreign = sum(1 for name in names if name not in self.opened)                # (3)
            if not foreign:
                self._bounded(lambda: self.app.Quit(), "quit", end)
                return "quit"
            self._bounded(lambda: setattr(self.app, "Visible", COM_TRUE), "hand-over", end)      # (4)
            shown = self._bounded(lambda: self.app.Visible, "hand-over", end)
            if shown is not True and shown != COM_TRUE:     # Word's reads back True, PowerPoint's -1 (PF-12)
                raise DriverRefusal("internal", f"hand-over: Visible reads back {shown!r}")
            owned.job.disarm()
        except Exception as e:  # noqa: BLE001 — any failure before the disarm: no disarm (PF-2)
            sys.stderr.write(f"quit: process {owned.pid} not handed over ({e}); its Job ends it\n")
            return "ended"
        sys.stderr.write(f"quit: process {owned.pid} handed to the user, {foreign} foreign document(s) "
                         "open in it; its Job disarmed\n")                                # (5)
        return "kept"

    def _inventory(self) -> list:
        """The FullName of every document open in the instance (D-35 (3)): its document collection's, and
        those of its Protected View windows, which that collection does not list (ruling PF-21). A
        Protected View window's document is the member named after the collection's item — Document
        in Word's, Presentation in PowerPoint's (the type libraries' ProtectedViewWindow) — so the
        binding's collection names it, and the adapter still names no application (L-P31)."""
        names = [d.FullName for d in _items(self._documents())]
        item = self.spec["collection"][:-1]
        return names + [getattr(w, item).FullName for w in _items(self.app.ProtectedViewWindows)]

    def _close_job(self, owned, end: float, ending: str) -> None:
        """The started instance's Job, once every reference is released (D-04): after a Quit, its
        process waited for what is left of the clean-end window; then both handles closed — armed, the
        Job ends what is left of the process; disarmed (D-35), the process is the user's."""
        try:
            if ending == "quit" and not owned.job.wait(max(0.0, end - time.monotonic())):
                sys.stderr.write(f"quit: process {owned.pid} still running at the end of the clean-end "
                                 "window; its Job ends it\n")
        except OSError as e:
            sys.stderr.write(f"quit: {e}\n")
        finally:
            owned.job.close()


# --- the verbs (L-P40) -------------------------------------------------------------------------------
# Each verb takes its member chain and its constants from the binding (`app.verbs[verb]`): the chain's
# head is reached from the opened document, every member after it is walked as the binding names it.
# What the runtime knows is how to reach a head and what a verb does with the object at the chain's
# end (a font's Name and Size, a header's text); it holds no member table of its own. Every verb runs
# inside Application.call, hence under `guarded`.

_DRIVEN = ("bound", "partial")
_COLLECTIONS = {"Section": "Sections", "Slide": "Slides"}   # heads that are every item of a document collection
_MASTER = "SlideMaster"                                      # the `Master` head: the deck's slide master
_SELECTED = ("Paragraph", "Shape")                           # heads a selector picks
_PLACEHOLDER = {"title": "Title", "body": "Body"}            # a Shape selector: the placeholder type it names
_PRIMARY = "Primary"                                         # the header or footer of a section's body pages


def _entry(app, verb: str) -> dict:
    entry = app.verbs.get(verb)
    if entry is None or entry.get("status") not in _DRIVEN or not entry.get("member"):
        raise DriverRefusal("precondition", f"verb {verb} is not bound on this application")
    return entry


def _chain(entry: dict) -> tuple:
    head, *names = entry["member"].split(".")
    return head, names


def _walk(obj, names):
    for name in names:
        obj = getattr(obj, name)
    return obj


def _items(collection) -> list:
    return [collection.Item(i) for i in range(1, collection.Count + 1)]


def _constant(entry: dict, value) -> int:
    """The constant the binding carries for `value`: the member of the verb's enum whose lower-case name
    ends with `value`'s, the least qualified when several do (wdStyleTitle, not wdStyleBookTitle or
    wdStyleSubtitle; ppPlaceholderBody, not ppPlaceholderVerticalBody). A built-in style is reached by
    its constant, never by a localised name (L-P42). None, or two equally qualified: `internal`."""
    enums = entry.get("constants") or {}
    if isinstance(value, str) and value:
        wanted = value.lower()
        for enum in sorted(enums):
            found = sorted((len(name), name) for name in enums[enum] if name.lower().endswith(wanted))
            if len(found) == 1 or (len(found) > 1 and found[0][0] < found[1][0]):
                return enums[enum][found[0][1]]
    raise DriverRefusal("internal", f"no unique constant for {value} in {', '.join(sorted(enums)) or 'no enum'}")


def _paragraphs(app, doc, selector: str) -> list:
    """The paragraphs a selector names, from the collection the binding's select_paragraph reaches."""
    root, names = _chain(_entry(app, "select_paragraph"))
    if root != _chain(_entry(app, "save"))[0]:
        raise DriverRefusal("precondition", "select_paragraph does not start at the document on this application")
    paragraphs = _walk(doc, names)
    count = paragraphs.Count
    if selector in ("first", "title"):
        wanted = [1]
    elif selector == "body":
        wanted = list(range(2, count + 1))
    elif selector == "all":
        wanted = list(range(1, count + 1))
    elif selector.startswith("index:") and selector[6:].isdigit():
        wanted = [int(selector[6:])]
    else:
        raise DriverRefusal("precondition", f"selector {selector} is not one of first, body, all, title, index:<n>")
    if any(not 1 <= i <= count for i in wanted):
        raise DriverRefusal("precondition", f"selector {selector} selects no paragraph of this document")
    return [paragraphs.Item(i) for i in wanted]


def _placeholders(app, doc, selector: str) -> list:
    """A Shape selector resolves over the master's placeholders (L-P40: title_font is mined from the
    master): the shapes whose placeholder type, read through the apply_paragraph_style chain, is the
    binding's constant for the selector."""
    if selector not in _PLACEHOLDER:
        raise DriverRefusal("precondition", f"selector {selector} names no placeholder of the master")
    entry = _entry(app, "apply_paragraph_style")
    _, names = _chain(entry)
    wanted = _constant(entry, _PLACEHOLDER[selector])
    found = []
    for shape in _items(getattr(doc, _MASTER).Shapes):
        try:
            kind = _walk(shape, names)
        except _com_errors():               # not a placeholder
            continue
        if kind == wanted:
            found.append(shape)
    return found


def _heads(app, doc, verb: str, head: str, selector=None) -> list:
    """The objects `head` names in the opened document (L-P40)."""
    if head == _chain(_entry(app, "save"))[0]:   # the document's own type: the type `open` returns
        return [doc]
    if head in _SELECTED:
        if not isinstance(selector, str):
            raise DriverRefusal("precondition", f"verb {verb} needs a selector")
        return (_paragraphs if head == "Paragraph" else _placeholders)(app, doc, selector)
    if head in _COLLECTIONS:
        return _items(getattr(doc, _COLLECTIONS[head]))
    if head == "Master":
        return [getattr(doc, _MASTER)]
    raise DriverRefusal("precondition", f"the runtime does not reach {head} from the document")


def _ends(app, doc, verb: str, selector=None) -> list:
    """The objects at the end of `verb`'s chain, one per head."""
    head, names = _chain(_entry(app, verb))
    return [_walk(target, names) for target in _heads(app, doc, verb, head, selector)]


def _bands(app, doc, verb: str) -> list:
    """The text of the headers or footers `verb` names: the primary one of each section when the chain
    ends on a collection indexed by the binding's constants (Section.Footers), else the object itself
    (Master.HeadersFooters.Footer)."""
    entry = _entry(app, verb)
    bands = _ends(app, doc, verb)
    if entry.get("constants"):
        bands = [band.Item(_constant(entry, _PRIMARY)) for band in bands]
    return [band.Range if hasattr(band, "Range") else band for band in bands]


def _lines(text: str) -> list:
    """Text as its non-blank lines: a COM text range ends its paragraphs with \\r, the miner joins them with \\n."""
    return [line for line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").split("\n")
            if line.strip()]


def apply_paragraph_style(app, doc, selector: str, style: str) -> None:
    entry = _entry(app, "apply_paragraph_style")
    head, names = _chain(entry)
    value = _constant(entry, style)
    for target in _heads(app, doc, "apply_paragraph_style", head, selector):
        setattr(_walk(target, names[:-1]), names[-1], value)


def verify_paragraph_style(app, doc, selector: str, style: str) -> bool:
    entry = _entry(app, "verify_paragraph_style")
    value = _constant(entry, style)
    got = _ends(app, doc, "verify_paragraph_style", selector)
    return bool(got) and all(_is_style(doc, g, value) for g in got)


def _is_style(doc, got, value: int) -> bool:
    """A number read back is compared with the constant; a style object by its name in the node's
    language against the name of the document's style for that constant, never a name of ours (L-P42)."""
    if isinstance(got, int):
        return got == value
    return got.NameLocal == doc.Styles.Item(value).NameLocal


def set_font_run(app, doc, selector: str, font: dict) -> None:
    for f in _ends(app, doc, "set_font_run", selector):
        f.Name = font["name"]
        f.Size = font["size"]


def verify_font_run(app, doc, selector: str, font: dict) -> bool:
    got = _ends(app, doc, "verify_font_run", selector)
    return bool(got) and all(f.Name == font["name"] and float(f.Size) == float(font["size"]) for f in got)


def set_header(app, doc, text: str) -> None:
    for band in _bands(app, doc, "set_header"):
        band.Text = text.replace("\n", "\r")


def set_footer(app, doc, text: str) -> None:
    for band in _bands(app, doc, "set_footer"):
        band.Text = text.replace("\n", "\r")


def verify_header(app, doc, text: str) -> bool:
    bands = _bands(app, doc, "verify_header")
    return bool(bands) and all(_lines(band.Text) == _lines(text) for band in bands)


def verify_footer(app, doc, text: str) -> bool:
    bands = _bands(app, doc, "verify_footer")
    return bool(bands) and all(_lines(band.Text) == _lines(text) for band in bands)


def save(app, doc) -> None:
    for method in _ends(app, doc, "save"):
        method()


def export(app, doc, format: str, path: str) -> None:
    value = _constant(_entry(app, "export"), format)
    for method in _ends(app, doc, "export"):
        method(os.path.abspath(path), value)         # (output path, fixed format): the order both libraries share


def verify_pdf(app, doc, path: str) -> bool:
    _entry(app, "verify_pdf")
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


VERBS = {"apply_paragraph_style": apply_paragraph_style, "set_font_run": set_font_run,
         "set_header": set_header, "set_footer": set_footer, "save": save, "export": export,
         "verify_paragraph_style": verify_paragraph_style, "verify_font_run": verify_font_run,
         "verify_header": verify_header, "verify_footer": verify_footer, "verify_pdf": verify_pdf}
