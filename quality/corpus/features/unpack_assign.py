a, b = 1, 2
a, b = b, a
first, *rest = [10, 20, 30, 40]
*init, last = "wxyz"
x, (y, z) = 1, (2, 3)
print(a, b, first, rest, init, last, x, y, z)
d = {}
d["k"], d["j"] = "v", "w"
nums = [0, 0, 0]
nums[0], nums[2] = 5, 6
print(sorted(d.items()), nums)
for i, (p, q) in enumerate([(1, 2), (3, 4)]):
    print(i, p, q)
RESULT = (first, rest)
