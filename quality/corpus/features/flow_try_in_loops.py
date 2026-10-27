def scan(rows):
    kept, errors = [], 0
    for row in rows:
        try:
            value = int(row)
        except ValueError:
            errors += 1
            continue
        if value < 0:
            break
        kept.append(value)
    else:
        kept.append(-1)
    return kept, errors


print(scan(["1", "x", "3"]))
print(scan(["4", "-5", "6"]))
n = 0
while n < 5:
    n += 1
    try:
        if n == 2:
            continue
        if n == 4:
            break
    finally:
        print("tick", n)
RESULT = scan(["9"])
