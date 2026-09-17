from __future__ import annotations

from dataclasses import dataclass, field

# v1 starter KB. Full English object dictionary is a later phase.
STARTER_OBJECTS = {
    "apple": {
        "platonic": True,
        "sensors": ["vision", "touch", "smell"],
        "material": 1.0,
        "immaterial": 0.0,
        "abstract": 0.0,
        "eye": ["round", "red or green", "shiny skin"],
        "touch": ["smooth", "firm", "cool"],
        "experiences": ["biting a cold apple on a hot afternoon"],
    },
    "man": {
        "platonic": True,
        "sensors": ["vision", "hearing"],
        "material": 1.0,
        "immaterial": 0.0,
        "abstract": 0.0,
        "eye": ["upright figure", "face", "gait"],
        "touch": ["warm skin", "fabric of clothes"],
        "experiences": ["a man falls", "a man laughs", "a man is angry"],
    },
    "sunlight": {
        "platonic": True,
        "sensors": ["vision", "touch"],
        "material": 0.2,
        "immaterial": 0.8,
        "abstract": 0.1,
        "eye": ["bright", "washes colors"],
        "touch": ["warm on skin"],
        "experiences": ["walking into a patch of sun after shade"],
    },
    "thunder": {
        "platonic": True,
        "sensors": ["hearing"],
        "material": 0.1,
        "immaterial": 0.7,
        "abstract": 0.2,
        "eye": ["optional flash beforehand"],
        "touch": [],
        "experiences": ["a crack of thunder interrupting a sentence"],
    },
    "rose": {
        "platonic": True,
        "sensors": ["vision", "touch", "smell"],
        "material": 1.0,
        "immaterial": 0.0,
        "abstract": 0.0,
        "eye": ["petals", "color"],
        "touch": ["soft petals", "thorny stem"],
        "experiences": ["leaning in and catching the smell of a rose"],
    },
    "rotten egg": {
        "platonic": True,
        "sensors": ["smell"],
        "material": 1.0,
        "immaterial": 0.0,
        "abstract": 0.0,
        "eye": ["dull, cracked shell if visible"],
        "touch": ["slimy if broken"],
        "experiences": ["the sulfur punch of a rotten egg"],
    },
    "anger": {
        "platonic": True,
        "sensors": ["vision", "hearing"],
        "material": 0.0,
        "immaterial": 0.3,
        "abstract": 1.0,
        "eye": ["tight jaw", "flushed skin"],
        "touch": ["heat in the face"],
        "experiences": ["a man is angry"],
    },
}


@dataclass
class ExperienceKB:
    objects: dict = field(default_factory=lambda: dict(STARTER_OBJECTS))

    def lookup(self, word: str) -> dict | None:
        key = word.strip().lower()
        if key in self.objects:
            return self.objects[key]
        return self.objects.get(key.replace("_", " "))

    def prompt_excerpt(self) -> str:
        situations = [
            "A man falls",
            "A man laughs",
            "A man is angry",
        ]
        return (
            "You have a small experience memory of objects and situations, including: "
            + "; ".join(situations)
            + ". Use it only when relevant, never as a list dump."
        )
