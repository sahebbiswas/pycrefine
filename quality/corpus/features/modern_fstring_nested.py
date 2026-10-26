names = ["ann", "bob"]
print(f"{', '.join(f'<{n}>' for n in names)}")
print(f"{'nested "quotes" ok'}")
width = 8
print(f"{names[0]:>{width}}|{3.14159:.{2}f}")
RESULT = f"{len(names)} names"
