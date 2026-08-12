"""Windows Job Object limits for an Extension worker process."""

from __future__ import annotations

import os


class WindowsJobError(RuntimeError):
    pass


class WindowsJob:
    def __init__(self, process, *, memory_mb: int, cpu_ms: int, max_processes: int = 1) -> None:
        if os.name != "nt":
            raise WindowsJobError("Windows Job Objects are only available on Windows")
        import ctypes
        from ctypes import wintypes

        ULONG_PTR = ctypes.c_size_t
        SIZE_T = ctypes.c_size_t

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ULONG_PTR),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", SIZE_T),
                ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T),
                ("PeakJobMemoryUsed", SIZE_T),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise WindowsJobError("CreateJobObjectW failed: {0}".format(ctypes.get_last_error()))
        information = EXTENDED_LIMITS()
        JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        information.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_TIME
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        information.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_ms) * 10000
        information.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
        information.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
        try:
            if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(information), ctypes.sizeof(information),
            ):
                raise WindowsJobError("SetInformationJobObject failed: {0}".format(ctypes.get_last_error()))
            process_handle = wintypes.HANDLE(int(process._handle))
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise WindowsJobError("AssignProcessToJobObject failed: {0}".format(ctypes.get_last_error()))
        except Exception:
            kernel32.CloseHandle(handle)
            raise
        self._kernel32 = kernel32
        self._handle = handle

    @property
    def active(self) -> bool:
        return self._handle is not None

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle is not None:
            self._kernel32.TerminateJobObject(self._handle, int(exit_code))

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel32.CloseHandle(handle)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
