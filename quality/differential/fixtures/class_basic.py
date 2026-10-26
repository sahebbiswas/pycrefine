class Shape:
    sides = 0
    def __init__(self, name):
        self.name = name
    def describe(self):
        return f"{self.name} with {self.sides} sides"
class Square(Shape):
    sides = 4
    def __init__(self, size):
        super().__init__("square")
        self.size = size
    def area(self):
        return self.size ** 2
    def __repr__(self):
        return f"Square({self.size})"
s = Square(3)
print(s.describe(), s.area(), repr(s), isinstance(s, Shape))
RESULT = s.area()
