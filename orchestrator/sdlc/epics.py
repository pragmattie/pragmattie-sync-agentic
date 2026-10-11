"""How pages name an epic.

A real epic is a GitHub milestone titled with its milestone code first, such as
"M5 Delivery forecasting"; pages show it as "Delivery forecasting (M5)".
"""

import re
import sys

MILESTONE_TITLE = re.compile(r"^(M\d+)\s+(\S.*)$")


def epic_label(name: str) -> str:
    """ "M5 Delivery forecasting" becomes "Delivery forecasting (M5)"; other names are unchanged."""
    match = MILESTONE_TITLE.match(name.strip())
    return f"{match.group(2).strip()} ({match.group(1)})" if match else name


def milestone_order(name: str) -> tuple[int, str]:
    """M5 before M6 before M10 (by the number after M); untitled milestones last, by name."""
    match = MILESTONE_TITLE.match(name.strip())
    return (int(match.group(1)[1:]) if match else sys.maxsize, name)
