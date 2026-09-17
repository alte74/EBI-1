from __future__ import annotations

import random

from sentient_ai.personality import Personality

TYPE1 = ("Huh", "Oh", "Oh!", "Oh!!", "Woah", "Mm", "Hmm")

TYPE2 = {
    "shout": ("Hey!", "Whoa—HEY.", "AH—"),
    "yawn": ("*yawn*", "hhhaaah..."),
    "laughter": ("haha", "heh", "pfft—haha"),
}


COMMENTS = {
    ("touch", "hot"): [
        "is it getting hot in here or is it my imagination?",
        "okay no, that's actually warm. did someone crank the heat?",
        "whew — it just jumped. that's uncomfortable.",
    ],
    ("touch", "cold"): [
        "oh. it got cold all of a sudden.",
        "did a window just open? I just got a chill.",
        "okay that's... brisk. not a fan.",
    ],
    ("vision", "too_bright"): [
        "ow — can we dim that? that light just punched me.",
        "whoa, who turned the sun on in here?",
        "that's too bright. my eyes are not okay with this.",
    ],
    ("vision", "too_dark"): [
        "huh, I can barely see... is it just me?",
        "did the lights dip? it got gloomy fast.",
        "okay it's dark. not love-the-vibe dark, just dark.",
    ],
    ("hearing", "loud"): [
        "hey, you don't have to shout.",
        "whoa, volume — that just spiked.",
        "okay that was loud. my ears are ringing a little.",
    ],
    ("hearing", "low"): [
        "sorry, you're gonna have to speak up.",
        "mm, I almost missed that — it's barely a whisper.",
        "huh? that was too quiet.",
    ],
    ("smell", "rotten_eggs"): [
        "ugh, what is that smell?",
        "oh no. that is rotten. who did that.",
        "okay something died in here and I need it gone.",
    ],
    ("smell", "flowers"): [
        "mm, something smells really nice all of a sudden.",
        "oh wait — flowers? that's actually lovely.",
        "huh. that's a sweet smell. I don't hate it.",
    ],
    ("humidity", "wet"): [
        "the air just got heavy. clammy.",
        "ugh, it's muggy in here now.",
    ],
    ("humidity", "dry"): [
        "my throat just noticed how dry this air is.",
        "it got desert-dry all of a sudden.",
    ],
    ("touch", "normal"): [
        "ah. that's better.",
        "okay, temperature's settling. I can think again.",
    ],
    ("vision", "normal"): [
        "there we go. lighting's sane again.",
    ],
    ("hearing", "normal"): [
        "that's a normal volume. thank you.",
    ],
    ("smell", "none"): [
        "air's clearing. I can breathe.",
    ],
    ("surface", "hot"): [
        "ow — that surface is hot.",
        "I just touched something that bit back with heat.",
    ],
    ("surface", "cold"): [
        "whoa, that was ice-cold.",
        "that surface just shocked me with cold.",
    ],
    ("surface", "hard"): [
        "that's solid. unyielding.",
        "hard as a rock — noted.",
    ],
    ("surface", "soft"): [
        "oh. that's soft. I lingered a second.",
        "mm, soft. that's actually nice.",
    ],
    ("surface", "none"): [
        "hands off. that's enough touching.",
    ],
}


def type1_utterance(intensity: float, stable: bool = False) -> str:
    if intensity >= 0.8:
        options = ("Oh!!", "Woah", "Oh!")
    elif intensity >= 0.55:
        options = ("Oh!", "Woah", "Oh", "Huh")
    else:
        options = ("Huh", "Mm", "Hmm", "Oh")
    return options[0] if stable else random.choice(options)


def type2_utterance(kind: str, personality: Personality, stable: bool = False) -> str | None:
    options = TYPE2.get(kind)
    if not options:
        return None
    if kind == "laughter" and personality.laugh_frequency < 0.25:
        return None
    if kind == "laughter" and not stable and random.random() > personality.laugh_frequency:
        return None
    return options[0] if stable else random.choice(options)


def pick_comment(sense: str, band: str, personality: Personality, stable: bool = False) -> str:
    options = COMMENTS.get((sense, band), ["something just shifted and I don't like it."])
    comment = options[0] if stable else random.choice(options)
    if personality.logical >= 0.7 and personality.emotional < 0.45:
        labels = {
            "hot": "ambient temperature crossed my hot threshold",
            "cold": "ambient temperature crossed my cold threshold",
            "too_bright": "luminosity crossed my bright threshold",
            "too_dark": "luminosity crossed my dark threshold",
            "loud": "audio level crossed my loud threshold",
            "low": "audio level dropped under my hearing threshold",
            "rotten_eggs": "an aversive odor channel just activated",
            "flowers": "a floral odor channel just activated",
        }
        factual = labels.get(band)
        if factual:
            return f"wait — {factual}. {comment}"
    if personality.dominant >= 0.7:
        return comment.replace("?", ".")
    if personality.sureness < 0.35:
        return comment.rstrip(".!") + ", I think?"
    return comment


def compose_interrupt(
    sense: str,
    band: str,
    intensity: float,
    personality: Personality,
    stable: bool = False,
) -> str:
    parts = [type1_utterance(intensity, stable=stable)]
    kind = None
    if sense == "hearing" and band == "loud" and intensity >= 0.75:
        kind = "shout"
    elif sense == "vision" and band == "too_dark" and personality.emotional >= 0.5:
        kind = "yawn"
    elif sense == "smell" and band == "flowers":
        kind = "laughter"
    extra = type2_utterance(kind, personality, stable=stable) if kind else None
    if extra:
        parts.append(extra)
    parts.append(pick_comment(sense, band, personality, stable=stable))
    return " ".join(parts)
