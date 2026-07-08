from threading import Lock


_startup_scan_db_lock = Lock()
_startup_scan_observers = []


def retain_startup_scan_observer(observer) -> None:
    _startup_scan_observers.append(observer)


def run_with_startup_scan_lock(callback, *args, **kwargs):
    with _startup_scan_db_lock:
        return callback(*args, **kwargs)
