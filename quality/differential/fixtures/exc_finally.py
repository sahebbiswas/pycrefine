log = []
def work(fail):
    try:
        log.append("start")
        if fail:
            raise RuntimeError("boom")
        log.append("ok")
    except RuntimeError:
        log.append("handled")
    finally:
        log.append("cleanup")
    return len(log)
print(work(False), work(True))
print(log)
def early():
    try:
        return "from try"
    finally:
        print("finally runs")
print(early())
RESULT = log[:]
