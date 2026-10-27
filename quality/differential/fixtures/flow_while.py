def collatz(n):
    steps = 0
    while n != 1:
        if n % 2:
            n = 3 * n + 1
        else:
            n //= 2
        steps += 1
    return steps
print([collatz(n) for n in (1, 6, 7, 27)])
count = 0
while True:
    count += 1
    if count == 3:
        continue
    if count > 5:
        break
    print("count", count)
n = 5
while n:
    n -= 1
else:
    print("done", n)
RESULT = collatz(9)
