"""An attribute or a subscript as the target of a `for` loop or a `with` (#337).

The target was written through a placeholder name (`for _item in xs: o.b = _item`), which stayed bound
after the statement and showed in `locals()`; on 3.9 the target of a `with` was lost altogether. The target
is now written where it is: `for o.b in xs:`, `with cm as d[k]:`.
Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile

PRELUDE = '''\
class Box:
    def __init__(self):
        self.log = []


class CM:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc):
        return False


'''


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _run(namespace):
    box, table = namespace["Box"](), {}
    try:
        result = namespace["f"](box, table)
    except Exception as exc:  # pylint: disable=broad-except
        result = ("raised", type(exc).__name__)
    return result, dict(vars(box)), table


class TestMemberTargets(unittest.TestCase):

    def assert_same(self, body):
        source = PRELUDE + "def f(o, d):\n" + body
        out = decompile(source)
        ast.parse(out)
        self.assertEqual(_run(_namespace(out)), _run(_namespace(source)), out)
        self.assertNotIn("_item", out)
        return out

    def test_an_attribute_in_a_for_loop(self):
        out = self.assert_same("    for o.b in [1, 2, 3]:\n        o.log.append(o.b)\n    return o.b\n")
        self.assertIn("for o.b in", out)

    def test_a_subscript_in_a_for_loop(self):
        out = self.assert_same("    for d['k'] in 'abc':\n        d['n'] = d.get('n', 0) + 1\n    return sorted(d.items())\n")
        self.assertIn("for d['k'] in", out)

    def test_an_unpacking_into_attributes(self):
        self.assert_same("    for o.a, o.c in [(1, 2), (3, 4)]:\n        pass\n    return o.a, o.c\n")

    def test_a_nested_unpacking(self):
        self.assert_same("    for o.x, (d[1], d[2]) in [(1, (2, 3)), (4, (5, 6))]:\n        pass\n    return o.x, d[1], d[2]\n")

    def test_a_starred_target(self):
        self.assert_same("    for o.first, *d['rest'] in [(1, 2, 3)]:\n        pass\n    return o.first, d['rest']\n")

    def test_the_temporary_is_not_left_behind(self):
        out = self.assert_same("    for o.b in [1, 2]:\n        pass\n    return sorted(locals())\n")
        self.assertNotIn("del ", out)

    def test_an_empty_loop_leaves_the_target_alone(self):
        self.assert_same("    o.b = 'before'\n    for o.b in []:\n        pass\n    return o.b\n")

    def test_a_break_out_of_the_loop(self):
        self.assert_same("    for o.b in [1, 2, 3]:\n        if o.b == 2:\n            break\n    return o.b\n")

    def test_the_object_is_looked_up_for_every_item(self):
        self.assert_same(
            "    boxes = [Box(), Box()]\n    for boxes[0].b in [1, 2]:\n        pass\n    return boxes[0].b, hasattr(boxes[1], 'b')\n")

    def test_an_attribute_in_a_with(self):
        out = self.assert_same("    with CM(5) as o.f:\n        o.log.append(o.f)\n    return o.f\n")
        self.assertIn("as o.f:", out)

    def test_a_subscript_in_a_with(self):
        self.assert_same("    with CM(7) as d['w']:\n        pass\n    return d['w']\n")

    def test_an_unpacking_in_a_with(self):
        self.assert_same("    with CM((1, 2)) as (o.p, d['q']):\n        pass\n    return o.p, d['q']\n")

    def test_a_with_in_a_function_that_returns_inside_it(self):
        self.assert_same("    with CM(3) as o.f:\n        return o.f + 1\n")

    def test_a_with_with_two_items(self):
        self.assert_same("    with CM(1) as o.a, CM(2) as d['b']:\n        pass\n    return o.a, d['b']\n")

    def test_a_with_whose_body_raises(self):
        self.assert_same("    with CM(4) as o.f:\n        raise ValueError('x')\n")

    def test_a_receiver_that_binds_more_loosely_than_the_subscript(self):
        out = self.assert_same("    o.log.append(0)\n    for (o.log + [])[0] in [5, 6]:\n        pass\n    return o.log\n")
        self.assertIn("(o.log + [])[0]", out)

    def test_a_receiver_that_is_itself_an_attribute_in_a_with(self):
        # the handler protects the receiver's loads, so the body is found from there (not from the store);
        # `log` is the second name used, so its LOAD_ATTR argument is odd, which is not a method flag before 3.12
        out = self.assert_same(
            "    o.pad = 0\n    o.log.append(0)\n    with CM(9) as o.log[0]:\n        pass\n    try:\n        d['z'] = 1\n"
            "    except ValueError:\n        d['z'] = 2\n    return o.log, d\n")
        self.assertIn("as o.log[0]:", out)

    def test_statements_after_a_with_on_a_member_stay_outside_it(self):
        out = self.assert_same(
            "    with CM(3) as o.f:\n        d['in'] = o.f\n    d['after'] = 1\n    return o.f, d\n")
        self.assertIn("as o.f:", out)

    def test_a_slice_in_a_for_loop(self):
        out = self.assert_same(
            "    o.log.extend([0, 1, 2, 3])\n    for o.log[1:3] in [[7], [8, 9]]:\n        pass\n    return o.log\n")
        self.assertIn("for o.log[1:3] in", out)

    def test_a_slice_with_an_open_end_in_a_for_loop(self):
        out = self.assert_same(
            "    o.log.extend([0, 1, 2, 3])\n    for o.log[:2] in [[7], [8, 9]]:\n        pass\n    return o.log\n")
        self.assertIn("for o.log[:2] in", out)

    def test_a_slice_in_a_with(self):
        out = self.assert_same(
            "    o.log.extend([0, 1, 2, 3])\n    with CM([7, 8]) as o.log[1:3]:\n        pass\n    try:\n"
            "        d['z'] = 1\n    except ValueError:\n        d['z'] = 2\n    return o.log, d\n")
        self.assertIn("as o.log[1:3]:", out)


if __name__ == "__main__":
    unittest.main()
