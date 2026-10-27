nums = list(range(10))
print([n * n for n in nums if n % 2 == 0])
print({n: n % 3 for n in nums if n > 5})
print(sorted({n % 4 for n in nums}))
print([(a, b) for a in range(3) for b in range(3) if a != b])
print([[c for c in range(r)] for r in range(4)])
words = ["apple", "bob", "cat"]
print({w: len(w) for w in words}, [w.upper() for w in words if len(w) > 3])
RESULT = sum(n for n in nums if n % 3 == 0)
