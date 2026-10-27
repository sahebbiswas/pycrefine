def check(n):
    assert n > 0, "n must be positive"
    if __debug__:
        print("debug on")
    return n * 2


print(check(3))
try:
    print(check(-1))
except AssertionError as exc:
    print("assertion:", exc)
RESULT = check(1)
