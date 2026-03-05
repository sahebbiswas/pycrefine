def outer(x):
    def inner(y):
        return x + y
    return inner(10)

print(outer(5))
