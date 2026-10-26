people = [("ann", 31), ("bob", 25), ("cy", 31), ("di", 19)]
print(sorted(people, key=lambda p: (-p[1], p[0])))
print(list(map(lambda x: x * 2, range(4))), list(filter(lambda x: x % 2, range(7))))
compose = lambda f, g: lambda x: f(g(x))
inc, dbl = (lambda x: x + 1), (lambda x: x * 2)
print(compose(inc, dbl)(5), compose(dbl, inc)(5))
RESULT = max(people, key=lambda p: p[1])
