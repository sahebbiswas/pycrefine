def fact(n):
    return 1 if n <= 1 else n * fact(n - 1)
def fib(n, memo={}):
    if n < 2:
        return n
    if n not in memo:
        memo[n] = fib(n - 1) + fib(n - 2)
    return memo[n]
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
print(fact(6), fib(20), gcd(84, 36))
def flatten(items):
    out = []
    for item in items:
        if isinstance(item, list):
            out.extend(flatten(item))
        else:
            out.append(item)
    return out
print(flatten([1, [2, [3, 4]], 5]))
RESULT = fib(25)
