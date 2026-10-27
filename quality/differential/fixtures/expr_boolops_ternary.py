def pick(a, b, c):
    first = a or b
    second = a and b
    third = (a and b) or c
    fourth = a if b else c
    return first, second, third, fourth
for args in [(0, 0, 0), (1, 0, 2), (0, 3, 4), (5, 6, 7)]:
    print(pick(*args))
print(not 0, not 1, 1 if 0 else 2)
RESULT = pick(1, 2, 3)
