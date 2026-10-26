def parse(text):
    try:
        return int(text)
    except ValueError:
        return None
def divide(a, b):
    try:
        result = a / b
    except ZeroDivisionError as exc:
        print("error:", exc)
        result = float("inf")
    return result
print(parse("12"), parse("x"))
print(divide(1, 4), divide(1, 0))
try:
    {}["missing"]
except (KeyError, IndexError) as err:
    print("caught", type(err).__name__)
RESULT = parse("7")
