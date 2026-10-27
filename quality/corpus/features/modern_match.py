def describe(value):
    match value:
        case 0:
            return "zero"
        case [x, y]:
            return "pair %s %s" % (x, y)
        case {"kind": kind}:
            return "kind " + kind
        case str() as text:
            return "text " + text
        case _:
            return "other"


for item in (0, [1, 2], {"kind": "k"}, "hi", 3.5):
    print(describe(item))
RESULT = describe([7, 8])
