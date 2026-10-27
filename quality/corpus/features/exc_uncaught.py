def risky(n):
    if n > 2:
        raise ValueError("too big: %d" % n)
    return n
for i in range(5):
    print(risky(i))
