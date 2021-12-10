import random


def get_interval(value):
    if isinstance(value, list):
        return random.randint(*value)
    return value
