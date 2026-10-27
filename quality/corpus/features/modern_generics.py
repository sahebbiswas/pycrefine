def first[T](items: list[T]) -> T:
    return items[0]


class Box[T]:
    def __init__(self, value: T):
        self.value = value

    def get(self) -> T:
        return self.value


type Pair = tuple[int, int]
print(first([4, 5]), Box("x").get())
RESULT = first(["a"])
