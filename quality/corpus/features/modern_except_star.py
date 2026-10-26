def run(items):
    caught = []
    try:
        raise ExceptionGroup("group", [ValueError("a"), TypeError("b"), ValueError("c")])
    except* ValueError as group:
        caught.append(("value", len(group.exceptions)))
    except* TypeError as group:
        caught.append(("type", len(group.exceptions)))
    return caught


print(run(None))
RESULT = run(None)
