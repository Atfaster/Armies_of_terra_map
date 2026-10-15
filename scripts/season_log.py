"""Readable console messages; ANSI colours are enabled only for terminals."""
import os
import sys
import time


def enable_console():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.GetStdHandle.restype = wintypes.HANDLE
        ctypes.windll.kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ulong)]
        ctypes.windll.kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.c_ulong]
        for number in (-11, -12):
            handle = ctypes.windll.kernel32.GetStdHandle(number)
            mode = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 4)


def message(text, colour, stream=None):
    stream = stream or sys.stdout
    enabled = os.environ.get('SEASON_COLOR') == '1' or (stream.isatty() and 'NO_COLOR' not in os.environ)
    print(f'\033[{colour}m{text}\033[0m' if enabled else text, file=stream, flush=True)


def success(text): message('[OK] ' + text, '32')
def error(text): message('[ERROR] ' + text, '31', sys.stderr)


def run_stage(label, function, *args, **kwargs):
    started = time.perf_counter()
    try:
        result = function(*args, **kwargs)
    except Exception:
        error(label + ' failed')
        raise
    success(f'{label}: {time.perf_counter()-started:.1f}s')
    return result


if __name__ == '__main__':
    enable_console()
    (error if sys.argv[1] == 'error' else success)(sys.argv[2])
