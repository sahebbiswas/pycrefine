stack, queue = [], []
for i in range(5):
    stack.append(i)
    queue.insert(0, i)
print(stack.pop(), queue.pop(), stack, queue)
matrix = [[r * 3 + c for c in range(3)] for r in range(3)]
print([row[::-1] for row in matrix], list(zip(*matrix)))
d = {"b": 2, "a": 1}
d.update(c=3)
print(sorted(d), {**d, "z": 26}, [*d, "q"])
s = {1, 2, 3} | {3, 4}
print(sorted(s), sorted(s & {2, 4, 9}), frozenset(s) == s)
t = (1, 2, 3)
print(t[1:], t + (4,), len(t), 3 in t)
RESULT = matrix
