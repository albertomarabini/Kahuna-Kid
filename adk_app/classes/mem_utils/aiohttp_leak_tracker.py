# file: aiohttp_leak_tracker.py
import aiohttp, asyncio, atexit, traceback, weakref, logging, sys, warnings, gc

logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
warnings.simplefilter("always", ResourceWarning)

_ACTIVE = {}  # id(session) -> (weakref(session), creation_stack)

_orig_init = aiohttp.ClientSession.__init__
_orig_close = aiohttp.ClientSession.close
_orig_aclose = getattr(aiohttp.ClientSession, "aclose", None)

def _mark_session(session):
    stack = "".join(traceback.format_stack(limit=25))
    _ACTIVE[id(session)] = (weakref.ref(session), stack)

def _unmark_session(session):
    _ACTIVE.pop(id(session), None)

def _wrap_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    _mark_session(self)
    logging.debug(f"[ClientSession NEW] id={id(self)}")

async def _async_close(self):
    try:
        await _orig_close(self)
    finally:
        _unmark_session(self)
        logging.debug(f"[ClientSession CLOSED] id={id(self)}")

def _wrap_close(self):
    # Support both sync/async close signatures across aiohttp versions
    res = _orig_close(self)
    if asyncio.iscoroutine(res):
        async def _runner():
            try:
                await res
            finally:
                _unmark_session(self)
                logging.debug(f"[ClientSession CLOSED] id={id(self)}")
        return _runner()
    else:
        _unmark_session(self)
        logging.debug(f"[ClientSession CLOSED] id={id(self)}")
        return res

aiohttp.ClientSession.__init__ = _wrap_init
aiohttp.ClientSession.close = _wrap_close
if _orig_aclose:
    async def _wrap_aclose(self):
        try:
            await _orig_aclose(self)
        finally:
            _unmark_session(self)
            logging.debug(f"[ClientSession A-CLOSED] id={id(self)}")
    aiohttp.ClientSession.aclose = _wrap_aclose

def _report_open_sessions():
    # force GC so finalized sessions disappear
    gc.collect()
    leaks = []
    for sid, (w, stack) in list(_ACTIVE.items()):
        sess = w()
        if sess is None:
            _ACTIVE.pop(sid, None)
            continue
        if getattr(sess, "closed", False):
            _ACTIVE.pop(sid, None)
            continue
        leaks.append((sid, stack))
    if leaks:
        print("\n=== AIOHTTP SESSION LEAK REPORT ===", file=sys.stderr)
        for sid, stack in leaks:
            print(f"\nLeaked ClientSession id={sid}\nCreated at:\n{stack}", file=sys.stderr)
        print("=== END REPORT ===\n", file=sys.stderr)

atexit.register(_report_open_sessions)
