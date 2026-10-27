"""A tiny expression tokenizer and evaluator."""
import operator

OPERATORS = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


class SyntaxProblem(Exception):
    pass


def tokenize(text):
    tokens = []
    number = ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
            continue
        if number:
            tokens.append(float(number))
            number = ""
        if char in OPERATORS or char in "()":
            tokens.append(char)
        elif not char.isspace():
            raise SyntaxProblem("unexpected character %r" % char)
    if number:
        tokens.append(float(number))
    return tokens


def to_postfix(tokens):
    output, stack = [], []
    for token in tokens:
        if isinstance(token, float):
            output.append(token)
        elif token == "(":
            stack.append(token)
        elif token == ")":
            while stack and stack[-1] != "(":
                output.append(stack.pop())
            if not stack:
                raise SyntaxProblem("unbalanced )")
            stack.pop()
        else:
            while stack and stack[-1] != "(" and PRECEDENCE[stack[-1]] >= PRECEDENCE[token]:
                output.append(stack.pop())
            stack.append(token)
    while stack:
        top = stack.pop()
        if top == "(":
            raise SyntaxProblem("unbalanced (")
        output.append(top)
    return output


def evaluate(text):
    values = []
    for token in to_postfix(tokenize(text)):
        if isinstance(token, float):
            values.append(token)
        else:
            right, left = values.pop(), values.pop()
            values.append(OPERATORS[token](left, right))
    return values[0] if len(values) == 1 else None


if __name__ == "__main__":
    for source in ("1 + 2 * 3", "(1 + 2) * 3", "10 / 4 - 1", "2 * (3 + 4) * 5", "1 +", "2 $ 3", "(1"):
        try:
            print(source, "=", evaluate(source))
        except SyntaxProblem as exc:
            print(source, "->", exc)
        except IndexError:
            print(source, "-> incomplete")
RESULT = evaluate("1 + 1")
