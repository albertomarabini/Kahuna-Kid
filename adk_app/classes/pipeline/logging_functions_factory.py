import os, threading
from datetime import datetime
import asyncio, inspect, threading

class LoggingFunctionsFactory:
    """
lf = LoggingFunctionsFactory(event_sink=my_sink)
status_fn = lf.make_status_fn()
logger_fn = lf.make_logger_fn("./logs", "job42_")

# 1) Plain function sink the engine will call
def my_sink(current_value, completion_value, status):
    print(...)

# 2) Build factory, wire the sink
lf = LoggingFunctionsFactory(event_sink=my_sink)

# 3) With a class
class Host:
    def __init__(self):
        self.lf = LoggingFunctionsFactory(event_sink=self.on_event)
        self.status_fn = self.lf.make_status_fn()
        self.log = self.lf.make_logger_fn("./logs", "job42_")

    def on_event(current_value, completion_value, status):
        print(f"[Host] {name}: {payload}")

lf = LoggingFunctionsFactory(event_sink=my_host.on_event)

status_fn(5, 100, "warming up")
logger_fn("INFO", "starting up")
    """
    def __init__(self, loop= None):
        self.current_value = 0
        self.completion_value = None
        self.status = ""
        self.loop = loop


    def set_event_sink(self, sink):
        self._event_sink = sink

    def fire_event(self, payload):
        sink = self._event_sink
        if sink is None:
            print(payload["current_value"], payload["completion_value"], payload["status"])  # fallback
            return None
        return sink(payload["current_value"], payload["completion_value"], payload["status"])  # can be sync or async

    def make_status_fn(self, event_sink):
        """
        Returns a single callable: fn(x, y, status_string)
        - x -> current_value (increment or set, per rules below)
        - y -> completion_value
        - status_string -> status message
        Safe to call from other threads / async workers. Fire-and-forget.
        """
        lock = threading.RLock()
        try:
            loop = self.loop or asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # no running loop at creation time

        self._event_sink = event_sink

        # ensure baseline fields exist
        if getattr(self, "current_value", None) is None:
            self.current_value = 0
        if getattr(self, "completion_value", None) is None:
            self.completion_value = None
        if getattr(self, "status", None) is None:
            self.status = ""

        def _emit(payload):
            try:
                res = self.fire_event(payload)
                if inspect.isawaitable(res):
                    if loop is not None:
                        asyncio.run_coroutine_threadsafe(res, loop)
                    else:
                        # fire-and-forget on a tiny helper thread
                        threading.Thread(target=lambda: asyncio.run(res), daemon=True).start()
            except Exception as e:
                print(f"Exception while printing status:{e}")
                pass  # never break the worker

        def fn(x=None, y=None, status_string=None):
            with lock:
                # Mirror your set_status semantics
                if y is None and status_string is None:
                    # increment mode
                    if x is not None:
                        self.current_value = max(0, int(self.current_value) + int(x))
                    else:
                        self.current_value = int(self.current_value) + 1
                else:
                    # set current_value when combined with y and/or status
                    if x is not None:
                        self.current_value = int(x)

                if y is not None:
                    self.completion_value = int(y)

                temp_status = ""
                if status_string is not None:
                    if y is None and x is None:
                        temp_status = status_string
                    else:
                        self.status = status_string
                        temp_status = ""

                _emit({
                    "current_value": self.current_value,
                    "completion_value": self.completion_value,
                    "status": (self.status or "") + (": " + temp_status if temp_status else "")
                })

        return fn

    def make_logger_fn(self, log_dir: str, run_id: str):
        """
        Returns fn(label: str, message: str) that writes to:
        <log_dir>/<run_id><YYYY-MM-DD>.log
        """
        os.makedirs(log_dir, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filepath = os.path.join(log_dir, f"{run_id}.log")
        lock = threading.RLock()

        def fn(label: str, message: str) -> None:
            line = f"[{label}] {message}\n"
            with lock:
                with open(filepath, "a", encoding="utf-8") as f:
                    f.write(line)
        fn("LOG",f"Start:{run_id}{date_str}")
        return fn

