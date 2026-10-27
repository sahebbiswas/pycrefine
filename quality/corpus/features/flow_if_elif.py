def grade(score):
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    else:
        return "F"
def sign(n):
    if n > 0:
        label = "positive"
    elif n < 0:
        label = "negative"
    else:
        label = "zero"
    return label
print([grade(s) for s in (95, 85, 75, 10)])
print([sign(n) for n in (3, -3, 0)])
RESULT = grade(88)
