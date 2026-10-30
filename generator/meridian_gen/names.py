"""Fictional company and person names. All domains use the reserved .example TLD."""

from __future__ import annotations

import random
import re

_PREFIX = [
    "North", "Blue", "Iron", "Silver", "Cedar", "Harbor", "Summit", "Bright", "Clear",
    "Stone", "Maple", "River", "Granite", "Copper", "Lumen", "Polar", "Vertex", "Oak",
    "Atlas", "Beacon", "Crescent", "Delta", "Ember", "Falcon", "Golden", "Hollow",
    "Juniper", "Keystone", "Lattice", "Meadow", "Nimbus", "Orchard", "Pioneer", "Quarry",
    "Redwood", "Sable", "Tidal", "Union", "Valley", "Willow", "Yarrow", "Zephyr",
    "Arbor", "Birch", "Canyon", "Drift", "Echo", "Fable", "Glacier", "Heron", "Indigo",
]
_SUFFIX = [
    "wind", "field", "bridge", "point", "stone", "works", "line", "path", "gate", "crest",
    "light", "ridge", "brook", "haven", "forge", "mark", "wave", "peak", "port", "grove",
]
_NOUN = [
    "Analytics", "Logistics", "Health", "Labs", "Systems", "Partners", "Supply", "Foods",
    "Robotics", "Capital", "Learning", "Media", "Energy", "Studio", "Freight", "Clinics",
    "Retail", "Software", "Networks", "Consulting", "Manufacturing", "Outfitters", "Group",
]
_LEGAL = ["Inc", "LLC", "Ltd", "GmbH", "Co", "Corp", "", "", "", ""]

FIRST = [
    "Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Isla", "Lucas", "Nora", "Mateo",
    "Priya", "Arjun", "Mei", "Kenji", "Sofia", "Diego", "Amara", "Kofi", "Elena", "Omar",
    "Hana", "Leo", "Ines", "Felix", "Maya", "Jonas", "Chloe", "Ravi", "Yuki", "Sam",
]
LAST = [
    "Reyes", "Chen", "Patel", "Okafor", "Novak", "Silva", "Kim", "Larsen", "Haddad",
    "Moreau", "Tanaka", "Walsh", "Iyer", "Fischer", "Costa", "Nguyen", "Brennan", "Sato",
    "Adeyemi", "Kowalski", "Rossi", "Lindqvist", "Mendez", "Ward", "Ali", "Becker",
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class NameFactory:
    def __init__(self, rng: random.Random):
        self.r = rng
        self.used_domains: set[str] = set()

    def company(self) -> tuple[str, str]:
        """Return (account_name, domain)."""
        for _ in range(200):
            base = self.r.choice(_PREFIX) + self.r.choice(_SUFFIX)
            noun = self.r.choice(_NOUN)
            legal = self.r.choice(_LEGAL)
            name = f"{base} {noun}" + (f" {legal}" if legal else "")
            domain = f"{_slug(base)}{_slug(noun)[:4]}.example"
            if domain not in self.used_domains:
                self.used_domains.add(domain)
                return name, domain
        # fall back to a numbered domain if the word space is exhausted
        n = len(self.used_domains)
        domain = f"{_slug(base)}{n}.example"
        self.used_domains.add(domain)
        return f"{base} {noun} {n}", domain

    def variant(self, name: str) -> str:
        """A second spelling of the same company, as a human would type it again."""
        options = [
            name.upper(),
            name.replace(" Inc", ", Inc.").replace(" LLC", ", LLC"),
            re.sub(r"\s+(Inc|LLC|Ltd|GmbH|Co|Corp)$", "", name),
            name + " (new account)",
            name.lower(),
        ]
        v = self.r.choice(options)
        return v if v != name else name + " "   # trailing space is a classic

    def person(self) -> tuple[str, str]:
        first, last = self.r.choice(FIRST), self.r.choice(LAST)
        return first, last
