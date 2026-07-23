# tiles.py
# Official English Scrabble tile distribution

import random


def create_tile_bag():
    """
    Creates and returns a shuffled Scrabble tile bag.
    Total: 100 tiles including blanks.
    """

    distribution = {
        "A": 9,
        "B": 2,
        "C": 2,
        "D": 4,
        "E": 12,
        "F": 2,
        "G": 3,
        "H": 2,
        "I": 9,
        "J": 1,
        "K": 1,
        "L": 4,
        "M": 2,
        "N": 6,
        "O": 8,
        "P": 2,
        "Q": 1,
        "R": 6,
        "S": 4,
        "T": 6,
        "U": 4,
        "V": 2,
        "W": 2,
        "X": 1,
        "Y": 2,
        "Z": 1,
        "?": 2  # blank tiles
    }

    bag = []

    for letter, amount in distribution.items():
        bag.extend([letter] * amount)

    random.shuffle(bag)

    return bag


def draw_tiles(bag, number):
    """
    Removes and returns tiles from the bag.
    """

    drawn = []

    for _ in range(min(number, len(bag))):
        drawn.append(bag.pop())

    return drawn