"""The enum types of the database (Phase2/3createEnum.sql) as Python enums.

The values must match pg_enum exactly. tests/test_contract.py compares both sides.
"""
from enum import StrEnum


class UserRole(StrEnum):
    user = "user"
    moderator = "moderator"
    admin = "admin"


class LentStatus(StrEnum):
    requested = "requested"
    accepted = "accepted"
    rejected = "rejected"
    active = "active"
    returned = "returned"


class ShippingStatus(StrEnum):
    pending = "pending"
    shipped = "shipped"
    delivered = "delivered"


class ProtocolType(StrEnum):
    lending = "lending"
    returning = "returning"
    create_book = "create_book"
    update_book = "update_book"
    delete_book = "delete_book"
    create_user = "create_user"
    update_user = "update_user"
    delete_user = "delete_user"
    create_request = "create_request"
    accept_request = "accept_request"
    reject_request = "reject_request"
    send_message = "send_message"
    read_message = "read_message"
    create_review = "create_review"


class ShippingAvailability(StrEnum):
    available = "available"
    not_available = "not_available"


class BookCondition(StrEnum):
    new = "new"
    like_new = "like_new"
    good = "good"
    fair = "fair"
    poor = "poor"


class BookLanguage(StrEnum):
    english = "english"
    german = "german"
    french = "french"
    spanish = "spanish"
    italian = "italian"
    other = "other"


class Genre(StrEnum):
    Fiktion = "Fiktion"
    Roman = "Roman"
    Romantik = "Romantik"
    Drama = "Drama"
    Komoedie = "Komödie"
    Horror = "Horror"
    Science_Fiction = "Science-Fiction"
    Fantasy = "Fantasy"
    Mystery = "Mystery"
    Liebesroman = "Liebesroman"
    Thriller = "Thriller"
    Biografie = "Biografie"
    Geschichte = "Geschichte"
    Kinderbuecher = "Kinderbücher"
    Jugendbuecher = "Jugendbücher"
    Ratgeber = "Ratgeber"
    Gesundheit = "Gesundheit"
    Reisen = "Reisen"
    Kochen = "Kochen"
    Kunst = "Kunst"
    Fotografie = "Fotografie"
    Lyrik = "Lyrik"
    Religion = "Religion"
    Philosophie = "Philosophie"


# Name of the enum type in the database -> Python enum
DB_ENUMS: dict[str, type[StrEnum]] = {
    "user_role": UserRole,
    "lent_status": LentStatus,
    "shipping_status": ShippingStatus,
    "protocol_type": ProtocolType,
    "shipping_availability": ShippingAvailability,
    "book_condition": BookCondition,
    "book_language": BookLanguage,
    "genre": Genre,
}
