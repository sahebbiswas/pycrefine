data = [3, 8, 12, 5]
if (n := len(data)) > 3:
    print("long", n)
print([y for x in data if (y := x * 2) > 10])
total = 0
while (total := total + 5) < 20:
    pass
print(total)
RESULT = total
