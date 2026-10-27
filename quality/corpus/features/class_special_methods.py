class Vec:
    def __init__(self, x, y):
        self.x, self.y = x, y
    def __add__(self, other):
        return Vec(self.x + other.x, self.y + other.y)
    def __eq__(self, other):
        return (self.x, self.y) == (other.x, other.y)
    def __len__(self):
        return 2
    def __iter__(self):
        yield self.x
        yield self.y
    def __getitem__(self, i):
        return (self.x, self.y)[i]
    def __str__(self):
        return "Vec(%s, %s)" % (self.x, self.y)
v = Vec(1, 2) + Vec(3, 4)
print(v, len(v), list(v), v[0], v == Vec(4, 6))
@staticmethod
def helper():
    return 1
RESULT = list(v)
