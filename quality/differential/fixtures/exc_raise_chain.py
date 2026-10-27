class AppError(Exception):
    pass
def load(value):
    try:
        return int(value)
    except ValueError as exc:
        raise AppError(f"bad value {value!r}") from exc
for item in ("4", "four"):
    try:
        print(load(item))
    except AppError as err:
        print("AppError:", err, type(err.__cause__).__name__)
def check(n):
    assert n > 0, "n must be positive"
    return n
print(check(2))
RESULT = None
check(-1)
