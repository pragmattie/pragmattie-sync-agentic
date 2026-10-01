"""Shared paging parameters for list endpoints, which all return `Page` bodies."""

from typing import Annotated

from fastapi import Query

Limit = Annotated[int, Query(ge=1, le=200)]
OpportunityLimit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]

DEFAULT_LIMIT = 25
DEFAULT_OPPORTUNITY_LIMIT = 50
