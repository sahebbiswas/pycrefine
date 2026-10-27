class Temperature:
    def __init__(self, celsius):
        self._celsius = celsius

    @property
    def fahrenheit(self):
        return self._celsius * 9 / 5 + 32

    @fahrenheit.setter
    def fahrenheit(self, value):
        self._celsius = (value - 32) * 5 / 9

    @classmethod
    def boiling(cls):
        return cls(100)

    @staticmethod
    def scale():
        return "C"


t = Temperature.boiling()
print(t.fahrenheit, Temperature.scale())
t.fahrenheit = 32
print(t._celsius)
RESULT = t.fahrenheit
