def make_counter(start=0):
    count = start
    def step(by=1):
        nonlocal count
        count += by
        return count
    return step
a, b = make_counter(), make_counter(10)
print(a(), a(), a(5), b(), b(-3))
def adders():
    return [lambda x, n=n: x + n for n in range(3)]
print([f(10) for f in adders()])
def outer():
    items = []
    for i in range(3):
        def inner(i=i):
            return i * i
        items.append(inner)
    return [f() for f in items]
RESULT = outer()
