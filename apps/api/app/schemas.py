from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from app.stages import Stage

T = TypeVar("T")

MAX_INT = 2_147_483_647

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


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class RepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    region: str
    quarterly_quota: Decimal


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class AccountCreate(BaseModel):
    name: AccountName
    industry: Industry
    employee_count: EmployeeCount
    annual_revenue: Revenue
    region: Region
    website: Website | None = None
    owner_id: int | None = None


class AccountUpdate(BaseModel):
    name: AccountName | None = None
    industry: Industry | None = None
    employee_count: EmployeeCount | None = None
    annual_revenue: Revenue | None = None
    region: Region | None = None
    website: Website | None = None
    owner_id: int | None = None

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "AccountUpdate":
        for field in ("name", "industry", "employee_count", "annual_revenue", "region"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AccountOut(BaseModel):
    id: int
    name: str
    industry: str
    employee_count: int
    annual_revenue: Decimal
    region: str
    website: str | None
    owner_id: int | None
    owner: OwnerOut | None
    created_at: datetime
    open_pipeline: Decimal
    contact_count: int


class ContactCreate(BaseModel):
    account_id: int
    first_name: PersonName
    last_name: PersonName
    email: Email
    title: Annotated[str, Field(max_length=120)] | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    first_name: str
    last_name: str
    email: str
    title: str | None
    phone: str | None
    created_at: datetime


class AccountRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class OpportunityCreate(BaseModel):
    account_id: int
    name: DealName
    amount: Amount
    stage: Stage = "prospecting"
    probability: Probability | None = None
    close_date: date
    owner_id: int | None = None


class OpportunityUpdate(BaseModel):
    account_id: int | None = None
    name: DealName | None = None
    amount: Amount | None = None
    stage: Stage | None = None
    probability: Probability | None = None
    close_date: date | None = None
    owner_id: int | None = None

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "OpportunityUpdate":
        for field in ("account_id", "name", "amount", "stage", "probability", "close_date"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    account: AccountRef
    name: str
    amount: Decimal
    stage: Stage
    probability: int
    close_date: date
    owner_id: int | None
    owner: OwnerOut | None
    created_at: datetime


class AccountDetail(AccountOut):
    contacts: list[ContactOut]
    opportunities: list[OpportunityOut]


class LeadCreate(BaseModel):
    first_name: PersonName
    last_name: PersonName
    email: Email
    company: Company
    title: JobTitle | None = None
    source: LeadSource = "web"
    score: Score = 0
    owner_id: int | None = None


class LeadUpdate(BaseModel):
    first_name: PersonName | None = None
    last_name: PersonName | None = None
    email: Email | None = None
    company: Company | None = None
    title: JobTitle | None = None
    source: LeadSource | None = None
    status: EditableLeadStatus | None = None
    score: Score | None = None
    owner_id: int | None = None

    @model_validator(mode="after")
    def required_fields_are_not_null(self) -> "LeadUpdate":
        for field in ("first_name", "last_name", "email", "company", "source", "status", "score"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: str
    company: str
    title: str | None
    source: LeadSource
    status: LeadStatus
    score: int
    owner_id: int | None
    owner: OwnerOut | None
    converted_account_id: int | None
    created_at: datetime


class LeadConvertRequest(BaseModel):
    industry: Industry = "Unknown"
    employee_count: EmployeeCount = 50
    annual_revenue: Revenue = Decimal("0")
    region: Region = "North America"
    opportunity_name: DealName | None = None
    opportunity_amount: Amount | None = None
    opportunity_close_date: date | None = None


class LeadConvertResponse(BaseModel):
    lead: LeadOut
    account_id: int
    contact_id: int
    opportunity_id: int | None
