from typing import Literal

Stage = Literal[
    "prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"
]

# Pipeline stages in order, each with its default win probability.
STAGE_PROBABILITY: dict[str, int] = {
    "prospecting": 10,
    "qualification": 25,
    "proposal": 50,
    "negotiation": 75,
    "closed_won": 100,
    "closed_lost": 0,
}

OPEN_STAGES = ("prospecting", "qualification", "proposal", "negotiation")
