"""Windows process containment. Imported only on Windows; handles never reach workers."""
import ctypes as C
from ctypes import wintypes as W
import time
from uuid import uuid4

k = C.WinDLL('kernel32', use_last_error=True)
CREATE_SUSPENDED = 0x00000004  # WinBase.h; subprocess does not export this flag.


def api(name, result, *args):
    fn = getattr(k, name)
    fn.restype, fn.argtypes = result, args
    return fn


close = api('CloseHandle', W.BOOL, W.HANDLE)
open_process = api('OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
wait = api('WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)
create_job = api('CreateJobObjectW', W.HANDLE, C.c_void_p, W.LPCWSTR)
open_job = api('OpenJobObjectW', W.HANDLE, W.DWORD, W.BOOL, W.LPCWSTR)
set_job = api('SetInformationJobObject', W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
query_job = api('QueryInformationJobObject', W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p)
assign_job = api('AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE)
terminate_job = api('TerminateJobObject', W.BOOL, W.HANDLE, W.UINT)
in_job = api('IsProcessInJob', W.BOOL, W.HANDLE, W.HANDLE, C.POINTER(W.BOOL))
process_id = api('GetProcessId', W.DWORD, W.HANDLE)
open_thread = api('OpenThread', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
thread_process_id = api('GetProcessIdOfThread', W.DWORD, W.HANDLE)
resume_thread = api('ResumeThread', W.DWORD, W.HANDLE)


class BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_longlong), ('job_time', C.c_longlong), ('flags', W.DWORD),
                ('min_ws', C.c_size_t), ('max_ws', C.c_size_t), ('active_limit', W.DWORD),
                ('affinity', C.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [('basic', BasicLimits), ('io', C.c_ulonglong * 6),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t),
                ('peak_process', C.c_size_t), ('peak_job', C.c_size_t)]


class Accounting(C.Structure):
    _fields_ = [('user', C.c_longlong), ('kernel', C.c_longlong),
                ('period_user', C.c_longlong), ('period_kernel', C.c_longlong),
                ('faults', W.DWORD), ('total', W.DWORD), ('active', W.DWORD), ('terminated', W.DWORD)]


def checked(result):
    if not result:
        raise C.WinError(C.get_last_error())
    return result


def active(handle):
    info = Accounting()
    checked(query_job(handle, 1, C.byref(info), C.sizeof(info), None))
    return info.active


def process_ids(handle):
    capacity = 16
    while capacity <= 65536:
        class Members(C.Structure):
            _fields_ = [('assigned', W.DWORD), ('count', W.DWORD), ('ids', C.c_size_t * capacity)]
        info = Members()
        if query_job(handle, 3, C.byref(info), C.sizeof(info), None):
            return list(info.ids[:info.count])
        if C.get_last_error() != 234:  # ERROR_MORE_DATA
            raise C.WinError(C.get_last_error())
        capacity *= 2
    raise RuntimeError('worker job process list exceeds supported size')


def _initial_thread(pid):
    """The sole initial thread of an owned process created suspended, never a PID kill authority."""
    class Entry(C.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('tid', W.DWORD), ('owner', W.DWORD),
                    ('base_priority', W.LONG), ('delta_priority', W.LONG), ('flags', W.DWORD)]
    snapshot = api('CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)(4, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    first = api('Thread32First', W.BOOL, W.HANDLE, C.POINTER(Entry))
    next_entry = api('Thread32Next', W.BOOL, W.HANDLE, C.POINTER(Entry))
    found = []
    try:
        entry = Entry(); entry.size = C.sizeof(entry)
        more = first(snapshot, C.byref(entry))
        if not more and C.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise C.WinError(C.get_last_error())
        while more:
            if entry.owner == pid:
                found.append(entry.tid)
            entry.size = C.sizeof(entry)
            more = next_entry(snapshot, C.byref(entry))
        if C.get_last_error() != 18:
            raise C.WinError(C.get_last_error())
    finally:
        close(snapshot)
    if len(found) != 1:
        raise RuntimeError('suspended worker does not have one verified initial thread')
    return found[0]


def alive(pid):
    handle = open_process(0x100000, False, int(pid))  # SYNCHRONIZE
    if not handle:
        if C.get_last_error() == 87:  # invalid PID
            return False
        return True  # access denied/unknown must not authorize cleanup
    try:
        return wait(handle, 0) != 0  # signalled means exited, even with retained handles
    finally:
        close(handle)


class WindowsJob:
    def __init__(self):
        self.name = 'Local\\FarmBot-' + uuid4().hex
        self.handle = checked(create_job(None, self.name))
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags
            checked(set_job(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        checked(assign_job(self.handle, int(process._handle)))

    def resume(self, process):
        """Resume only our assigned, suspended creation object before opening its gate.

        Python's Windows venv executable can launch a base interpreter before
        post-spawn assignment; an input handshake cannot contain that startup.
        CREATE_SUSPENDED keeps even the redirector inside this non-breakaway Job.
        Thread ownership is checked on retained handles, and any ambiguity fails
        closed. The caller terminates the owned Job/process on startup failure.
        """
        if not self.handle:
            raise RuntimeError('worker startup requires a live owned Job')
        handle = int(process._handle)
        member = W.BOOL()
        checked(in_job(handle, self.handle, C.byref(member)))
        pid = checked(process_id(handle))
        status = wait(handle, 0)
        if status == 0xffffffff:
            raise C.WinError(C.get_last_error())
        if not member.value or pid != process.pid or status != 258:  # WAIT_TIMEOUT: live
            raise RuntimeError('worker startup requires its live assigned creation object')
        thread = checked(open_thread(0x0002 | 0x0800, False, _initial_thread(pid)))
        try:
            if checked(thread_process_id(thread)) != pid:
                raise RuntimeError('suspended worker thread ownership changed')
            previous = resume_thread(thread)
            if previous == 0xffffffff:
                raise C.WinError(C.get_last_error())
            if previous != 1:
                raise RuntimeError('worker initial thread was not suspended exactly once')
        finally:
            close(thread)

    def terminate_and_wait(self, timeout=5):
        # Accounting may hit zero just before the final process object is signalled.
        # Hold process handles across termination so PID reuse cannot affect this wait.
        handles = []
        try:
            for pid in process_ids(self.handle):
                handle = open_process(0x100000, False, pid)
                if handle:
                    handles.append(handle)
                elif C.get_last_error() != 87:
                    raise C.WinError(C.get_last_error())
            checked(terminate_job(self.handle, 1))
            deadline = time.monotonic() + timeout
            while active(self.handle) or any(wait(handle, 0) != 0 for handle in handles):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Windows worker job still has active processes')
                time.sleep(.02)
        finally:
            for handle in handles:
                close(handle)

    def close(self):
        if self.handle:
            checked(close(self.handle))
            self.handle = None

    @staticmethod
    def empty(name):
        if not isinstance(name, str) or not name.startswith('Local\\FarmBot-'):
            raise RuntimeError('invalid worker job identity')
        handle = open_job(4, False, name)  # JOB_OBJECT_QUERY
        if not handle:
            if C.get_last_error() == 2:
                return True  # destroyed jobs have no handles or associated processes
            raise C.WinError(C.get_last_error())
        try:
            return active(handle) == 0
        finally:
            close(handle)


def descendants(pid):
    """Snapshot ancestry for diagnostics; job membership is the cleanup authority."""
    class Entry(C.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]
    snapshot = api('CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    first = api('Process32FirstW', W.BOOL, W.HANDLE, C.POINTER(Entry))
    next_entry = api('Process32NextW', W.BOOL, W.HANDLE, C.POINTER(Entry))
    children = {}
    try:
        entry = Entry(); entry.size = C.sizeof(entry)
        more = first(snapshot, C.byref(entry))
        while more:
            children.setdefault(entry.parent, []).append(entry.pid)
            more = next_entry(snapshot, C.byref(entry))
    finally:
        close(snapshot)
    found, stack, seen = [], [pid], {pid}
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child); found.append(child); stack.append(child)
    return found
