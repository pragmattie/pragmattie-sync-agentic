from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from app.stages import Stage

T = TypeVar("T")

MAX_INT = 2_147_483_647

# Money goes out as a JSON string with exactly two decimal places, e.g. "12500000.00".
Money = Annotated[
    Decimal,
    PlainSerializer(
        lambda value: str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        return_type=str,
        when_used="json",
    ),
]

AccountName = Annotated[str, Field(min_length=1, max_length=200)]
Industry = Annotated[str, Field(min_length=1, max_length=80)]
EmployeeCount = Annotated[int, Field(ge=1, le=MAX_INT)]
Revenue = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Region = Annotated[str, Field(min_length=1, max_length=50)]
Website = Annotated[str, Field(max_length=200)]
PersonName = Annotated[str, Field(min_length=1, max_length=80)]
Email = Annotated[EmailStr, StringConstraints(max_length=200)]
Company = Annotated[str, Field(min_length=1, max_length=200)]
JobTitle = Annotated[str, Field(max_length=120)]
Score = Annotated[int, Field(ge=0, le=100)]
DealName = Annotated[str, Field(min_length=1, max_length=200)]
Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Probability = Annotated[int, Field(ge=0, le=100)]

LeadStatus = Literal["new", "working", "qualified", "disqualified", "converted"]
EditableLeadStatus = Literal["new", "working", "qualified", "disqualified"]
LeadSource = Literal["web", "referral", "event", "outbound", "partner"]


class Schema(BaseModel):
    """Base for every request and response body; field docstrings become their descriptions."""

    model_config = ConfigDict(use_attribute_docstrings=True)


class Page(Schema, Generic[T]):
    items: list[T]
    """The items on this page."""
    total: int
    """How many items match the filters, across all pages."""


class RepOut(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The rep's id."""
    name: str
    """The rep's full name."""
    email: str
    """The rep's work email."""
    region: str
    """The sales region the rep covers."""
    quarterly_quota: Money
    """The rep's sales quota for each quarter."""


class OwnerOut(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The rep's id."""
    name: str
    """The rep's full name."""


class AccountCreate(Schema):
    name: AccountName
    """The company's name."""
    industry: Industry
    """The company's industry."""
    employee_count: EmployeeCount
    """How many people the company employs."""
    annual_revenue: Revenue
    """The company's yearly revenue."""
    region: Region
    """The sales region the company is in."""
    website: Website | None = None
    """The company's website."""
    owner_id: int | None = None
    """The id of the rep who owns the account."""


class AccountUpdate(Schema):
    name: AccountName | None = None
    """The company's name."""
    industry: Industry | None = None
    """The company's industry."""
    employee_count: EmployeeCount | None = None
    """How many people the company employs."""
    annual_revenue: Revenue | None = None
    """The company's yearly revenue."""
    region: Region | None = None
    """The sales region the company is in."""
    website: Website | None = None
    """The company's website."""
    owner_id: int | None = None
    """The id of the rep who owns the account."""

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "AccountUpdate":
        for field in ("name", "industry", "employee_count", "annual_revenue", "region"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AccountOut(Schema):
    id: int
    """The account's id."""
    name: str
    """The company's name."""
    industry: str
    """The company's industry."""
    employee_count: int
    """How many people the company employs."""
    annual_revenue: Money
    """The company's yearly revenue."""
    region: str
    """The sales region the company is in."""
    website: str | None
    """The company's website."""
    owner_id: int | None
    """The id of the rep who owns the account."""
    owner: OwnerOut | None
    """The rep who owns the account."""
    created_at: datetime
    """When the account was created."""
    open_pipeline: Money
    """The total amount of the account's open opportunities."""
    contact_count: int
    """How many contacts the account has."""


class ContactCreate(Schema):
    account_id: int
    """The id of the contact's account."""
    first_name: PersonName
    """The contact's first name."""
    last_name: PersonName
    """The contact's last name."""
    email: Email
    """The contact's email."""
    title: Annotated[str, Field(max_length=120)] | None = None
    """The contact's job title."""
    phone: Annotated[str, Field(max_length=40)] | None = None
    """The contact's phone number."""


class ContactOut(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The contact's id."""
    account_id: int
    """The id of the contact's account."""
    first_name: str
    """The contact's first name."""
    last_name: str
    """The contact's last name."""
    email: str
    """The contact's email."""
    title: str | None
    """The contact's job title."""
    phone: str | None
    """The contact's phone number."""
    created_at: datetime
    """When the contact was created."""


class AccountRef(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The account's id."""
    name: str
    """The company's name."""


class OpportunityCreate(Schema):
    account_id: int
    """The id of the deal's account."""
    name: DealName
    """The deal's name."""
    amount: Amount
    """The deal's value."""
    stage: Stage = "prospecting"
    """The deal's pipeline stage."""
    probability: Probability | None = None
    """The chance of winning, 0-100; defaults to the stage's probability."""
    close_date: date
    """The date the deal is expected to close."""
    owner_id: int | None = None
    """The id of the rep who owns the deal."""


class OpportunityUpdate(Schema):
    account_id: int | None = None
    """The id of the deal's account."""
    name: DealName | None = None
    """The deal's name."""
    amount: Amount | None = None
    """The deal's value."""
    stage: Stage | None = None
    """The deal's pipeline stage."""
    probability: Probability | None = None
    """The chance of winning, 0-100; a new stage sets its own unless this is sent."""
    close_date: date | None = None
    """The date the deal is expected to close."""
    owner_id: int | None = None
    """The id of the rep who owns the deal."""

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "OpportunityUpdate":
        for field in ("account_id", "name", "amount", "stage", "probability", "close_date"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class OpportunityOut(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The deal's id."""
    account_id: int
    """The id of the deal's account."""
    account: AccountRef
    """The deal's account."""
    name: str
    """The deal's name."""
    amount: Money
    """The deal's value."""
    stage: Stage
    """The deal's pipeline stage."""
    probability: int
    """The chance of winning, 0-100."""
    close_date: date
    """The date the deal is expected to close."""
    owner_id: int | None
    """The id of the rep who owns the deal."""
    owner: OwnerOut | None
    """The rep who owns the deal."""
    created_at: datetime
    """When the deal was created."""


class AccountDetail(AccountOut):
    contacts: list[ContactOut]
    """The account's contacts."""
    opportunities: list[OpportunityOut]
    """The account's opportunities."""


class LeadCreate(Schema):
    first_name: PersonName
    """The lead's first name."""
    last_name: PersonName
    """The lead's last name."""
    email: Email
    """The lead's email."""
    company: Company
    """The lead's company."""
    title: JobTitle | None = None
    """The lead's job title."""
    source: LeadSource = "web"
    """Where the lead came from."""
    score: Score = 0
    """How promising the lead is, 0-100."""
    owner_id: int | None = None
    """The id of the rep who owns the lead."""


class LeadUpdate(Schema):
    first_name: PersonName | None = None
    """The lead's first name."""
    last_name: PersonName | None = None
    """The lead's last name."""
    email: Email | None = None
    """The lead's email."""
    company: Company | None = None
    """The lead's company."""
    title: JobTitle | None = None
    """The lead's job title."""
    source: LeadSource | None = None
    """Where the lead came from."""
    status: EditableLeadStatus | None = None
    """Where the lead is in qualification; use convert to convert it."""
    score: Score | None = None
    """How promising the lead is, 0-100."""
    owner_id: int | None = None
    """The id of the rep who owns the lead."""

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "LeadUpdate":
        for field in ("first_name", "last_name", "email", "company", "source", "status", "score"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class LeadOut(Schema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    """The lead's id."""
    first_name: str
    """The lead's first name."""
    last_name: str
    """The lead's last name."""
    email: str
    """The lead's email."""
    company: str
    """The lead's company."""
    title: str | None
    """The lead's job title."""
    source: LeadSource
    """Where the lead came from."""
    status: LeadStatus
    """Where the lead is in qualification."""
    score: int
    """How promising the lead is, 0-100."""
    owner_id: int | None
    """The id of the rep who owns the lead."""
    owner: OwnerOut | None
    """The rep who owns the lead."""
    converted_account_id: int | None
    """The id of the account the lead was converted into, if any."""
    created_at: datetime
    """When the lead was created."""


class LeadConvertRequest(Schema):
    industry: Industry = "Unknown"
    """The new account's industry."""
    employee_count: EmployeeCount = 50
    """How many people the new account's company employs."""
    annual_revenue: Revenue = Decimal("0")
    """The new account's yearly revenue."""
    region: Region = "North America"
    """The new account's sales region."""
    opportunity_name: DealName | None = None
    """The new deal's name; defaults to "<company> - New business"."""
    opportunity_amount: Amount | None = None
    """The new deal's value; leave it out to create no deal."""
    opportunity_close_date: date | None = None
    """The new deal's expected close date; defaults to 60 days from today."""


class LeadConvertResponse(Schema):
    lead: LeadOut
    """The lead, now converted."""
    account_id: int
    """The id of the new account."""
    contact_id: int
    """The id of the new contact."""
    opportunity_id: int | None
    """The id of the new deal, if one was created."""


class MonthForecast(Schema):
    month: str
    """The month, as "YYYY-MM"."""
    won: Money
    """Won deals closing in the month."""
    commit: Money
    """Won plus negotiation deals closing in the month."""
    best_case: Money
    """Commit plus proposal deals closing in the month."""
    weighted: Money
    """Won plus probability-weighted open deals closing in the month."""


class RepForecast(Schema):
    rep: OwnerOut
    """The rep."""
    quota: Money
    """The rep's quarterly quota."""
    won: Money
    """The rep's won deals."""
    commit: Money
    """The rep's won plus negotiation deals."""
    weighted: Money
    """The rep's won plus probability-weighted open deals."""
    attainment_pct: float
    """Won as a percentage of quota, to one decimal place."""


class StageForecast(Schema):
    stage: Stage
    """The open pipeline stage."""
    count: int
    """How many deals are in the stage."""
    amount: Money
    """The total value of the stage's deals."""


class ForecastOut(Schema):
    quarter: str
    """The quarter, as "YYYY-Qn"."""
    start: date
    """The quarter's first day."""
    end: date
    """The quarter's last day."""
    quota: Money
    """Every rep's quarterly quota added together."""
    won: Money
    """Won deals closing in the quarter."""
    commit: Money
    """Won plus negotiation deals."""
    best_case: Money
    """Commit plus proposal deals."""
    pipeline: Money
    """Every open deal closing in the quarter."""
    weighted: Money
    """Won plus each open deal's amount times its probability."""
    by_month: list[MonthForecast]
    """The categories for each of the quarter's three months."""
    by_rep: list[RepForecast]
    """Each rep's figures, highest weighted first."""
    by_stage: list[StageForecast]
    """Count and value for each open stage."""


class SummaryOut(Schema):
    quarter: str
    """The current quarter, as "YYYY-Qn"."""
    leads_by_status: dict[str, int]
    """How many leads have each status."""
    open_leads: int
    """Leads that are new, working or qualified."""
    open_pipeline: Money
    """The total value of every open deal."""
    open_deals: int
    """How many deals are open."""
    won_this_quarter: Money
    """Won deals closing in the current quarter."""
