"""Stand-in decompiling processes for the correctness gate's runner tests.

They live in an importable module because the runner starts its child with the ``spawn``
method, which finds the child's function by module name.
"""
import os
import time


def hang(conn):
    """Never answers."""
    conn.recv()
    time.sleep(3600)


def die(conn):
    """Exits without answering."""
    conn.recv()
    os._exit(7)


def echo(conn):
    """Answers every path with a one-line module."""
    while True:
        path = conn.recv()
        if path is None:
            return
        conn.send(("ok", f"x = {path!r}\n"))
