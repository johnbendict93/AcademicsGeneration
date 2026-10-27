"""
keep_awake.py — stop Windows from going to sleep while a unit is generated (10-20 minutes).

Live run #4 (2026-09-27): the PC slept during generation and the run died (Groq/browser connections
dropped). Windows' own SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) keeps the system
awake while the flag is set (the screen may still turn off); ES_CONTINUOUS alone hands control back.
Limits: only IDLE sleep is prevented — closing the lid or pressing Sleep still sleeps the PC.
Not Windows, or Windows refuses -> a quiet no-op; this never breaks generation.
"""
import contextlib
import os

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _kernel32():
    if os.name != "nt":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32
    except Exception:                        # noqa: BLE001
        return None


@contextlib.contextmanager
def keep_awake(kernel32=None):
    """with keep_awake() as awake: ...   awake is True when Windows accepted the request."""
    k = kernel32 if kernel32 is not None else _kernel32()
    awake = False
    if k is not None:
        try:
            awake = bool(k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        except Exception:                    # noqa: BLE001
            awake = False
    try:
        yield awake
    finally:
        if k is not None:
            try:
                k.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:                # noqa: BLE001
                pass
