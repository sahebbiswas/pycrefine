name = "ada"
width = 8
items = {"k": 3}
print(t"Hello {name!r:>{width}}!")
print(t"{items['k'] + 1} {name!s} {name!a:^{width}} {{literal}}")
tpl = t"{name}={width}"
RESULT = (tpl.strings, [(i.expression, i.value, i.conversion, i.format_spec) for i in tpl.interpolations])
