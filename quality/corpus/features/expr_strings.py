name, n = "pyc", 3
print("hello " + name, name * n, name.upper(), name[::-1])
print(f"{name}-{n}", f"{n:>4}|{n:<4}|{n:^5}", f"{name!r}")
print("%s has %d items" % (name, n), "{} and {}".format(name, n))
s = "a,b,c"
print(s.split(","), ",".join(["x", "y"]), s.replace(",", ";"))
RESULT = len(s)
