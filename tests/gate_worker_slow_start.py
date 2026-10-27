"""A decompiling process that is slow to start, for the correctness gate's runner tests.

The sleep runs when the child imports this module, before the child can say it is ready,
so it models a slow interpreter start-up or slow imports on a busy CI runner.
"""
import time

time.sleep(2)


def echo(conn):
    """Answers every path with a one-line module."""
    while True:
        path = conn.recv()
        if path is None:
            return
        conn.send(("ok", f"x = {path!r}\n"))
