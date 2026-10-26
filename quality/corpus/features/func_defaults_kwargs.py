def describe(name, age=30, *tags, city="Paris", **extra):
    return name, age, tags, city, sorted(extra.items())
print(describe("ann"))
print(describe("bob", 41, "x", "y", city="Rome", zip=1, area=2))
print(describe(*["cy", 5], **{"city": "Oslo"}))
def only_kw(*, a, b=2):
    return a + b
print(only_kw(a=1), only_kw(a=1, b=5))
def mutate(item, bucket=None):
    bucket = [] if bucket is None else bucket
    bucket.append(item)
    return bucket
print(mutate(1), mutate(2, [0]))
RESULT = describe("z", city="Rio")
