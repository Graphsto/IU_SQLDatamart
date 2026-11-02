"""Request and response models. Field names are the column names of the database (typos included)."""
from datetime import date, datetime, time
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .enums import (
    BookCondition,
    BookLanguage,
    Genre,
    LentStatus,
    ProtocolType,
    ShippingAvailability,
    ShippingStatus,
    UserRole,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
BigInt = Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    ),
]


class InputModel(BaseModel):
    """Request bodies reject unknown fields, so `role`, `is_active` or `user_id` cannot be smuggled in."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _example(*examples: dict[str, Any]) -> ConfigDict:
    return ConfigDict(json_schema_extra={"examples": list(examples)})


def _at_least_one(model: BaseModel) -> None:
    if not any(getattr(model, name) is not None for name in model.model_fields_set):
        raise ValueError("Mindestens ein Feld muss gesetzt sein.")


# --------------------------------------------------------------------------- generic


class ErrorResponse(BaseModel):
    detail: str = Field(examples=["Book copy with ID 99 does not exist."])
    sqlstate: str | None = Field(
        None, description="SQLSTATE bei Datenbankfehlern, z. B. P0001 (RAISE EXCEPTION) oder 23505 (UNIQUE)."
    )
    constraint: str | None = Field(None, description="Name des verletzten Constraints (falls bekannt).")


class Link(BaseModel):
    name: str
    url: str


class RootInfo(BaseModel):
    name: str
    version: str
    warning: str
    links: list[Link]


class DatabaseInfo(BaseModel):
    user: str = Field(examples=["app_user"])
    database: str = Field(examples=["buechertausch"])
    version: str = Field(description="PostgreSQL-Versionsnummer (ohne Build-Angaben).", examples=["17.11"])


class Health(BaseModel):
    status: str = Field(examples=["ok"])
    auth: str = Field(description="Immer `dev-header`: die Identität kommt ungeprüft aus X-User-Id.")
    database: DatabaseInfo


class EnumValues(BaseModel):
    enums: dict[str, list[str]] = Field(description="Name des DB-Enum-Typs -> erlaubte Werte in Reihenfolge.")


class ContractCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class ContractReport(BaseModel):
    ok: bool
    checks: list[ContractCheck]


# --------------------------------------------------------------------------- users


class UserCreate(InputModel):
    model_config = InputModel.model_config | _example(
        {"username": "Maria", "first_name": "Maria", "last_name": "Muster", "email": "maria@example.com"}
    )
    username: str = Field(min_length=3, max_length=40, pattern=r"^[\w.\-]+$")
    first_name: Name
    last_name: Name
    email: Email


class UserUpdate(InputModel):
    model_config = InputModel.model_config | _example({"email": "neu@example.com"})
    first_name: Name | None = None
    last_name: Name | None = None
    email: Email | None = None

    @model_validator(mode="after")
    def _check(self) -> "UserUpdate":
        _at_least_one(self)
        return self


class UserAdminUpdate(InputModel):
    model_config = InputModel.model_config | _example({"role": "moderator"}, {"is_active": False})
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> "UserAdminUpdate":
        _at_least_one(self)
        return self


class UserView(BaseModel):
    """Public part always; the rest only for the user itself and for moderators/admins."""

    user_id: int
    username: str
    created_at: datetime
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    updated_at: datetime | None = None


class UserMe(BaseModel):
    user_id: int
    username: str
    first_name: str
    last_name: str
    email: str
    role: UserRole = Field(description="Rolle, wie sie in der Datenbank steht.")
    effective_role: UserRole = Field(description="Rolle für diese Anfrage (X-Test-Role überschreibt `role`).")
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserStats(BaseModel):
    user_id: int
    username: str
    email: str
    created_at: datetime
    total_books: int
    total_lents: int
    total_reviews: int
    total_messages: int


# --------------------------------------------------------------------------- addresses


class CollectionAddressCreate(InputModel):
    model_config = InputModel.model_config | _example(
        {
            "streetname": "Steingasse",
            "streetnummber": "1",
            "city": "Kassel",
            "state": "HE",
            "postal_code": "34117",
            "country": "DE",
            "latitude": 51.312711,
            "longitude": 9.479746,
        }
    )
    streetname: Name
    streetnummber: ShortText
    city: Name
    state: Name
    postal_code: ShortText
    country: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _coordinates(self) -> "CollectionAddressCreate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude und longitude nur gemeinsam angeben (Constraint coordinates_complete).")
        return self


class CollectionAddressUpdate(InputModel):
    """PATCH: only the fields that are present are changed. `country` and the coordinates may be set to null."""

    model_config = InputModel.model_config | _example({"city": "Göttingen"})
    streetname: Name | None = None
    streetnummber: ShortText | None = None
    city: Name | None = None
    state: Name | None = None
    postal_code: ShortText | None = None
    country: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _check(self) -> "CollectionAddressUpdate":
        if not self.model_fields_set:
            raise ValueError("Mindestens ein Feld muss gesetzt sein.")
        for name in ("streetname", "streetnummber", "city", "state", "postal_code"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} darf nicht null sein.")
        if ("latitude" in self.model_fields_set) != ("longitude" in self.model_fields_set):
            raise ValueError("latitude und longitude nur gemeinsam angeben (Constraint coordinates_complete).")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude und longitude nur gemeinsam angeben (Constraint coordinates_complete).")
        return self


class CollectionAddress(BaseModel):
    address_id: int
    user_id: int
    streetname: str
    streetnummber: str
    city: str
    state: str
    postal_code: str
    country: str | None
    latitude: float | None
    longitude: float | None
    created_at: datetime
    updated_at: datetime


class TimeWindowCreate(InputModel):
    model_config = InputModel.model_config | _example({"weekday": 2, "start_time": "10:00:00", "end_time": "12:00:00"})
    weekday: int = Field(ge=0, le=6, description="0 = Sonntag … 6 = Samstag (wie EXTRACT(DOW)).")
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def _order(self) -> "TimeWindowCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time muss vor end_time liegen (Constraint window_valid).")
        return self


class TimeWindow(BaseModel):
    window_id: int
    address_id: int
    weekday: int
    start_time: time
    end_time: time


class UserAddressCreate(InputModel):
    model_config = InputModel.model_config | _example(
        {"street_address": "Buchenweg 17", "city": "Kassel", "state": "HE", "postal_code": "34117", "country": "DE"}
    )
    street_address: Name
    city: Name
    state: Name
    postal_code: ShortText
    country: Name


class UserAddress(BaseModel):
    address_id: int
    user_id: int
    street_address: str
    city: str
    state: str
    postal_code: str
    country: str
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- catalog


class AuthorCreate(InputModel):
    model_config = InputModel.model_config | _example({"name": "Terry Pratchett"})
    name: Name


class Author(BaseModel):
    author_id: int
    name: str
    book_count: int | None = None


class BookAuthor(BaseModel):
    author_id: int
    name: str


class BookItem(BaseModel):
    book_id: int
    title: str
    published_date: date | None
    genre: Genre | None
    language: BookLanguage | None
    authors: list[BookAuthor]
    copies_total: int = Field(description="Exemplare aktiver Besitzer.")
    copies_available: int = Field(description="Davon aktuell nicht angenommen/verliehen (View available_books).")
    avg_rating: float | None
    review_count: int


class BookReview(BaseModel):
    review_id: int
    lent_id: int
    rating: int
    comment: str | None
    created_at: datetime
    reviewer: str


class CopyBase(BaseModel):
    book_copie_id: int
    book_id: int
    title: str
    authors: str | None
    genre: Genre | None
    language: BookLanguage | None
    condition: BookCondition | None
    max_lent_duration: int
    shipping_availability: ShippingAvailability | None
    shipping_cost: float | None
    city: str
    postal_code: str
    owner_id: int
    owner: str


class AvailableCopy(CopyBase):
    distance_km: float | None = Field(None, description="Nur bei Umkreissuche (Luftlinie, Haversine).")


class CopyDetail(CopyBase):
    available: bool = Field(description="true, wenn das Exemplar in der View available_books vorkommt.")
    user_firstname: str | None = Field(None, description="Nur für den Besitzer und Moderatoren/Admins.")
    user_lastname: str | None = Field(None, description="Nur für den Besitzer und Moderatoren/Admins.")


class BookDetail(BookItem):
    available_copies: list[AvailableCopy]


class BookInput(InputModel):
    title: Name
    authors: list[Name] = Field(min_length=1, max_length=10)
    published_date: date | None = None
    genre: Genre | None = None
    language: BookLanguage | None = None


class CopyCreate(InputModel):
    model_config = InputModel.model_config | _example(
        {
            "book": {
                "title": "Die Wahrheit",
                "authors": ["Terry Pratchett"],
                "published_date": "2000-01-01",
                "genre": "Fantasy",
                "language": "german",
            },
            "collection_address_id": 1,
            "condition": "good",
            "max_lent_duration": 30,
            "shipping_availability": "available",
            "shipping_cost": 4.5,
        },
        {"book_id": 4, "collection_address_id": 1},
    )
    book_id: BigInt | None = Field(None, description="Vorhandenes Buch verwenden …")
    book: BookInput | None = Field(None, description="… oder Buch anlegen/finden (Titel + Autoren).")
    collection_address_id: BigInt = Field(description="Muss eine Abholadresse des Aufrufers sein.")
    condition: BookCondition | None = None
    max_lent_duration: int | None = Field(None, ge=1, le=120, description="Tage; Datenbank-Standard 60.")
    shipping_availability: ShippingAvailability | None = None
    shipping_cost: float | None = Field(None, ge=0, le=9.99, description="Euro; Datenbank-Standard 4.00.")

    @model_validator(mode="after")
    def _one_book_source(self) -> "CopyCreate":
        if (self.book_id is None) == (self.book is None):
            raise ValueError("Entweder book_id oder book angeben (genau eins von beiden).")
        return self


class CopyUpdate(InputModel):
    model_config = InputModel.model_config | _example({"condition": "fair", "max_lent_duration": 45})
    condition: BookCondition | None = None
    max_lent_duration: int | None = Field(None, ge=1, le=120)
    collection_address_id: BigInt | None = Field(None, description="Nur eigene Abholadressen.")
    shipping_availability: ShippingAvailability | None = None
    shipping_cost: float | None = Field(None, ge=0, le=9.99)

    @model_validator(mode="after")
    def _check(self) -> "CopyUpdate":
        _at_least_one(self)
        return self


class CopyRecord(BaseModel):
    """Row of Book_copy."""

    book_copie_id: int
    book_id: int
    condition: BookCondition | None
    max_lent_duration: int
    collection_address_id: int
    shipping_availability: ShippingAvailability | None
    shipping_cost: float | None
    created_at: datetime
    updated_at: datetime


class UserBookCopy(BaseModel):
    """Result row of getAllBooksbyUser()."""

    book_copie_id: int
    book_id: int
    title: str
    authors: str | None
    genre: Genre | None
    language: BookLanguage | None
    condition: BookCondition | None
    max_lent_duration: int
    shipping_availability: ShippingAvailability | None
    shipping_cost: float | None
    city: str
    postal_code: str


# --------------------------------------------------------------------------- lents


class LentCreate(InputModel):
    model_config = InputModel.model_config | _example({"book_copie_id": 2, "shipping": False})
    book_copie_id: BigInt
    shipping: bool = Field(False, description="true = Versand statt Abholung (nur wenn das Exemplar Versand anbietet).")


class LentRow(BaseModel):
    """Result row of getAllLentsbyUser() plus my_role."""

    lent_id: int
    book_copie_id: int
    book_id: int
    title: str
    authors: str | None
    genre: Genre | None
    language: BookLanguage | None
    condition: BookCondition | None
    max_lent_duration: int
    shipping_availability: ShippingAvailability | None
    shipping_cost: float | None
    city: str
    postal_code: str
    lent_to: int
    lent_from: int
    lent_status: LentStatus
    lent_requesttimestamp: datetime
    lent_startdate: datetime
    lent_enddate: datetime
    return_date: datetime | None
    my_role: str = Field(description="`borrower` (Ausleiher) oder `owner` (Besitzer) in dieser Ausleihe.")


class ShippingOut(BaseModel):
    shipping_id: int
    lent_id: int
    shipping_status: ShippingStatus
    tracking_number: str | None
    created_at: datetime
    updated_at: datetime


class ShippingUpdate(InputModel):
    model_config = InputModel.model_config | _example(
        {"shipping_status": "shipped", "tracking_number": "DHL-00340434161094049999"}
    )
    shipping_status: ShippingStatus
    tracking_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None


class ReviewCreate(InputModel):
    model_config = InputModel.model_config | _example({"rating": 5, "comment": "Great book!"})
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(None, max_length=4000)


class ReviewOut(BaseModel):
    review_id: int
    lent_id: int
    rating: int
    comment: str | None
    created_at: datetime


class PickupAddress(BaseModel):
    address_id: int
    streetname: str
    streetnummber: str
    city: str
    state: str
    postal_code: str
    country: str | None


class Pickup(BaseModel):
    address: PickupAddress
    time_windows: list[TimeWindow]


class BorrowerAddress(BaseModel):
    """Not a snapshot: the schema does not link a lent to a delivery address (see report F6)."""

    address_id: int
    street_address: str
    city: str
    state: str
    postal_code: str
    country: str
    created_at: datetime


class LentDetail(BaseModel):
    lent_id: int
    lent_status: LentStatus
    book_copie_id: int
    book_id: int
    title: str
    authors: str | None
    borrower_id: int
    borrower: str
    owner_id: int
    owner: str
    shipping_requested: bool | None
    lent_requesttimestamp: datetime
    lent_startdate: datetime
    lent_enddate: datetime
    return_date: datetime | None
    overdue: bool
    shipping: ShippingOut | None = None
    review: ReviewOut | None = None
    pickup: Pickup | None = Field(
        None,
        description=(
            "Abholadresse und Zeitfenster. Besitzer/Moderator bei accepted/active, "
            "Ausleiher nur bei accepted und Abholung (kein Versand)."
        ),
    )
    borrower_current_address: BorrowerAddress | None = Field(
        None,
        description=(
            "Jüngste Wohnadresse des Ausleihers (veränderlich, kein Snapshot). "
            "Nur Besitzer/Moderator, nur bei Versand und accepted/active."
        ),
    )


class OverdueStatus(BaseModel):
    lent_id: int
    must_be_returned: bool = Field(description="Ergebnis von book_must_be_returned().")
    lent_status: LentStatus
    lent_enddate: datetime


# --------------------------------------------------------------------------- messages


class MessageCreate(InputModel):
    model_config = InputModel.model_config | _example({"receiver_id": 2, "content": "Hallo Bob, ist das Buch noch frei?"})
    receiver_id: BigInt
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    message_id: int
    sender_id: int
    receiver_id: int
    content: str
    sent_at: datetime
    read_status: bool


# --------------------------------------------------------------------------- reports


class MonthlyReport(BaseModel):
    year: int
    month: int
    total_users: int
    total_books: int
    total_lents: int
    total_reviews: int
    total_messages: int
    total_active_lents: int
    total_overdue_lents: int


class OverdueLent(BaseModel):
    lent_id: int
    title: str
    borrower_id: int
    borrower: str
    borrower_email: str
    owner_id: int
    owner: str
    lent_enddate: datetime
    days_overdue: int


class ProtocolOut(BaseModel):
    protocol_id: int
    book_copie_id: int | None
    user_id: int | None
    protocol_type: ProtocolType
    details: str | None
    created_at: datetime
