import sys
print("to stdout", 1, 2, sep="-", end="!\n")
sys.stdout.write("direct write\n")
sys.stderr.write("to stderr\n")
print("err too", file=sys.stderr)
for i in range(3):
    print(i, end=" ")
print()
RESULT = "done"
