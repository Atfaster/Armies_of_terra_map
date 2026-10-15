"""Automatic parallelism targets 80% of logical CPUs, constrained by free RAM."""
import ctypes
import os


def available_memory():
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = Memory(); status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available
    return None


def choose_workers(config, count, estimated_bytes=0):
    requested = config.get('surface_workers')
    if requested is not None:
        if type(requested) != int or not 1 <= requested <= 64:
            raise ValueError('surface_workers must be between 1 and 64 or auto')
        return min(requested, count, 61 if os.name == 'nt' else 64)
    workers = max(1, int((os.cpu_count() or 1) * 0.8))
    free = available_memory()
    if free:
        per_worker = max(512 * 1024**2, estimated_bytes * 256)
        workers = min(workers, max(1, int(free * 0.8) // per_worker))
    return min(workers, count, 61 if os.name == 'nt' else workers)
