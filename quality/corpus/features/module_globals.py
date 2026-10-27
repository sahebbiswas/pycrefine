import math
import os.path as osp
from collections import OrderedDict
PI2 = math.pi * 2
counter = 0
def bump():
    global counter
    counter += 1
    return counter
bump(); bump()
print(round(PI2, 3), counter, osp.basename("/a/b/c.txt"), list(OrderedDict(a=1)))
del PI2
print("PI2" in globals())
RESULT = counter
