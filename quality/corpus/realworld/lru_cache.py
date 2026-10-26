"""A small least-recently-used cache with hit statistics."""
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key, default=None):
        if key in self._items:
            self._items.move_to_end(key)
            self.hits += 1
            return self._items[key]
        self.misses += 1
        return default

    def put(self, key, value):
        if key in self._items:
            self._items.move_to_end(key)
        self._items[key] = value
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def __len__(self):
        return len(self._items)

    def __contains__(self, key):
        return key in self._items

    def stats(self):
        total = self.hits + self.misses
        ratio = self.hits / total if total else 0.0
        return {"hits": self.hits, "misses": self.misses, "ratio": round(ratio, 2)}


def memoize(fn, capacity=8):
    cache = LRUCache(capacity)

    def wrapper(*args):
        value = cache.get(args)
        if value is None:
            value = fn(*args)
            cache.put(args, value)
        return value

    wrapper.cache = cache
    return wrapper


if __name__ == "__main__":
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    print(sorted(k for k in "abc" if k in cache), len(cache), cache.stats())
    square = memoize(lambda n: n * n)
    print([square(n) for n in (2, 3, 2, 3)], square.cache.stats())
    try:
        LRUCache(0)
    except ValueError as exc:
        print("error:", exc)
RESULT = LRUCache(3).capacity
