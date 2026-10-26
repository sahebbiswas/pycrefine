"""A traffic-light style state machine with guards and an event log."""


class Machine:
    TRANSITIONS = {
        ("green", "timer"): "yellow",
        ("yellow", "timer"): "red",
        ("red", "timer"): "green",
        ("red", "fault"): "flashing",
        ("green", "fault"): "flashing",
        ("flashing", "reset"): "red",
    }

    def __init__(self):
        self.state = "red"
        self.log = []
        self.ticks = 0

    def send(self, event):
        key = (self.state, event)
        target = self.TRANSITIONS.get(key)
        if target is None:
            self.log.append("ignored %s in %s" % (event, self.state))
            return False
        self.log.append("%s -> %s" % (self.state, target))
        self.state = target
        if event == "timer":
            self.ticks += 1
        return True

    def run(self, events):
        accepted = 0
        for event in events:
            if self.send(event):
                accepted += 1
            elif event == "halt":
                break
        return accepted


if __name__ == "__main__":
    machine = Machine()
    count = machine.run(["timer", "timer", "bogus", "fault", "timer", "reset", "timer", "halt", "timer"])
    print(count, machine.state, machine.ticks)
    for line in machine.log:
        print(" ", line)
RESULT = Machine().TRANSITIONS[("red", "timer")]
