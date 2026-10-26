def squares(limit):
    for i in range(limit):
        yield i * i
print(list(squares(5)))
gen = (x + 1 for x in range(3))
print(next(gen), next(gen), list(gen))
def countdown(n):
    while n > 0:
        yield n
        n -= 1
print(sum(countdown(4)), any(x > 3 for x in countdown(4)), all(x > 1 for x in countdown(4)))
print(max((len(s), s) for s in ["aa", "b", "cccc"]))
RESULT = list(countdown(3))
