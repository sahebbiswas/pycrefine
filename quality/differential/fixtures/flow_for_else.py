def find(items, target):
    for index, item in enumerate(items):
        if item == target:
            break
    else:
        return -1
    return index
print(find([4, 5, 6], 5), find([4, 5, 6], 9))
total = 0
for i in range(10):
    if i % 2:
        continue
    total += i
print(total)
for i in range(3):
    for j in range(3):
        if j > i:
            break
        print(i, j)
RESULT = total
