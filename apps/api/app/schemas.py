from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

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


class AccountDetail(AccountOut):
    contacts: list[ContactOut]
    opportunities: list[dict[str, Any]]
