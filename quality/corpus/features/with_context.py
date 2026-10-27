class Tracker:
    events = []
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        Tracker.events.append("enter " + self.name)
        return self
    def __exit__(self, exc_type, exc, tb):
        Tracker.events.append("exit " + self.name)
        return exc_type is KeyError
with Tracker("a") as t:
    print("inside", t.name)
with Tracker("b"):
    raise KeyError("swallowed")
with Tracker("c"), Tracker("d"):
    pass
print(Tracker.events)
RESULT = len(Tracker.events)
