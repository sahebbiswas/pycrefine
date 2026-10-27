def classify(n):
    if 0 < n < 10:
        return "small"
    if n == 10 or n == 20:
        return "round"
    if n is None:
        return "none"
    if n not in (1, 2, 3) and n >= 100:
        return "big"
    return "other"
for value in (5, 10, 20, 100, 50, 0):
    print(value, classify(value))
print(classify(None) if False else "skip")
RESULT = [classify(v) for v in (1, 15, 150)]
