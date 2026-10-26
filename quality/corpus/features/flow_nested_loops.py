def table(rows, cols):
    out = []
    for r in range(rows):
        row = []
        for c in range(cols):
            if (r + c) % 2 == 0:
                row.append(r * c)
            else:
                row.append(-1)
        out.append(row)
    return out
for row in table(3, 4):
    print(row)
pairs = []
for a in range(4):
    for b in range(a, 4):
        if a + b == 4:
            continue
        pairs.append((a, b))
print(pairs)
RESULT = len(pairs)
