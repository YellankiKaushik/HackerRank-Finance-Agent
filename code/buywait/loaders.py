from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from .dates import parse_date, parse_iso_timestamp, parse_optional_date
from .enums import AffordabilityStatus, Direction, EventStatus, PaymentMethod, RequestType
from .errors import IntegrityError, ParseError, SchemaError
from .models import (
    ExchangeRate,
    ExpectedSampleOutput,
    FinancialEvent,
    FinancialProfile,
    ImageRecord,
    MessageRecord,
    PaymentOption,
    Request,
    SampleRequest,
)
from .money import parse_money, parse_optional_money


REQUEST_COLUMNS = [
    "request_id",
    "user_id",
    "request_date",
    "request_type",
    "requested_amount",
    "desired_completion_date",
    "allows_partial_payment",
    "request_text",
]
SAMPLE_COLUMNS = REQUEST_COLUMNS + [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]
PROFILE_COLUMNS = [
    "user_id",
    "home_currency",
    "current_available_balance",
    "minimum_balance_to_keep",
    "financial_priorities",
    "expense_categories_to_protect",
    "expense_categories_user_is_willing_to_reduce",
    "expense_categories_user_is_willing_to_stop",
    "payment_methods_user_will_consider",
    "max_installment_months",
]
EVENT_COLUMNS = [
    "event_id",
    "user_id",
    "event_type",
    "description",
    "category",
    "direction",
    "amount",
    "currency",
    "event_date",
    "settlement_date",
    "status",
    "linked_event_id",
    "flexibility",
    "minimum_allowed_amount",
]
PAYMENT_OPTION_COLUMNS = [
    "payment_option_id",
    "request_id",
    "payment_method",
    "payment_amount",
    "number_of_payments",
    "first_payment_date",
    "payment_frequency_days",
    "financing_fee",
    "total_payable_amount",
]
EXCHANGE_RATE_COLUMNS = ["rate_date", "from_currency", "to_currency", "rate"]
MESSAGE_COLUMNS = ["message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"]
IMAGE_COLUMNS = ["image_id", "user_id", "request_id", "related_event_id"]


T = TypeVar("T")


def repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def default_dataset_dir() -> Path:
    return repo_root_from_here() / "dataset"


def _read_rows(path: Path, expected_columns: list[str]) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        raise SchemaError(f"Missing required CSV file: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected_columns:
            raise SchemaError(
                f"{path.name} header mismatch: expected {expected_columns}, got {reader.fieldnames}"
            )
        return [(idx, row) for idx, row in enumerate(reader, start=2)]


def _required_text(row: dict[str, str], field: str, *, file_name: str, source_row: int) -> str:
    value = row[field]
    if value == "":
        raise ParseError(f"{file_name}:{source_row} field {field} is required and cannot be blank")
    return value


def _optional_text(row: dict[str, str], field: str) -> str | None:
    return row[field] or None


def _parse_bool(value: str, *, field: str, file_name: str, source_row: int) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ParseError(f"{file_name}:{source_row} field {field} must be true or false, got {value!r}")


def _parse_int(value: str, *, field: str, file_name: str, source_row: int) -> int:
    if value == "":
        raise ParseError(f"{file_name}:{source_row} field {field} is required and cannot be blank")
    try:
        return int(value)
    except ValueError as exc:
        raise ParseError(f"{file_name}:{source_row} field {field} must be an integer, got {value!r}") from exc


def _parse_optional_int(value: str, *, field: str, file_name: str, source_row: int) -> int | None:
    if value == "":
        return None
    return _parse_int(value, field=field, file_name=file_name, source_row=source_row)


def _split_pipe(value: str) -> tuple[str, ...]:
    if value == "":
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _parse_payment_methods(value: str, *, file_name: str, source_row: int, field: str) -> tuple[PaymentMethod, ...]:
    methods = []
    for item in _split_pipe(value):
        methods.append(PaymentMethod.parse(item, field=f"{file_name}:{source_row} {field}"))
    return tuple(methods)


def _request_from_row(row: dict[str, str], source_row: int, file_name: str) -> Request:
    return Request(
        request_id=_required_text(row, "request_id", file_name=file_name, source_row=source_row),
        user_id=_required_text(row, "user_id", file_name=file_name, source_row=source_row),
        request_date=parse_date(row["request_date"], field=f"{file_name}:{source_row} request_date"),
        request_type=RequestType.parse(row["request_type"], field=f"{file_name}:{source_row} request_type"),
        requested_amount=parse_money(row["requested_amount"], field=f"{file_name}:{source_row} requested_amount"),
        desired_completion_date=parse_date(row["desired_completion_date"], field=f"{file_name}:{source_row} desired_completion_date"),
        allows_partial_payment=_parse_bool(row["allows_partial_payment"], field="allows_partial_payment", file_name=file_name, source_row=source_row),
        request_text=_required_text(row, "request_text", file_name=file_name, source_row=source_row),
        source_row=source_row,
    )


def load_requests(dataset_dir: Path | None = None) -> list[Request]:
    dataset_dir = dataset_dir or default_dataset_dir()
    return [_request_from_row(row, line, "requests.csv") for line, row in _read_rows(dataset_dir / "requests.csv", REQUEST_COLUMNS)]


def load_sample_requests(dataset_dir: Path | None = None) -> list[SampleRequest]:
    dataset_dir = dataset_dir or default_dataset_dir()
    samples = []
    for line, row in _read_rows(dataset_dir / "sample_requests.csv", SAMPLE_COLUMNS):
        expected = ExpectedSampleOutput(
            amount_safe_to_pay=parse_money(row["amount_safe_to_pay"], field=f"sample_requests.csv:{line} amount_safe_to_pay"),
            affordability_status=AffordabilityStatus.parse(row["affordability_status"], field=f"sample_requests.csv:{line} affordability_status"),
            recommended_payment_method=PaymentMethod.parse(row["recommended_payment_method"], field=f"sample_requests.csv:{line} recommended_payment_method"),
            payment_plan=_required_text(row, "payment_plan", file_name="sample_requests.csv", source_row=line),
            earliest_date_for_full_payment=parse_optional_date(row["earliest_date_for_full_payment"], field=f"sample_requests.csv:{line} earliest_date_for_full_payment"),
            spending_changes_needed=_required_text(row, "spending_changes_needed", file_name="sample_requests.csv", source_row=line),
            decision_explanation=_required_text(row, "decision_explanation", file_name="sample_requests.csv", source_row=line),
        )
        samples.append(SampleRequest(request=_request_from_row(row, line, "sample_requests.csv"), expected=expected))
    return samples


def load_financial_profiles(dataset_dir: Path | None = None) -> list[FinancialProfile]:
    dataset_dir = dataset_dir or default_dataset_dir()
    records = []
    for line, row in _read_rows(dataset_dir / "financial_profiles.csv", PROFILE_COLUMNS):
        records.append(
            FinancialProfile(
                user_id=_required_text(row, "user_id", file_name="financial_profiles.csv", source_row=line),
                home_currency=_required_text(row, "home_currency", file_name="financial_profiles.csv", source_row=line),
                current_available_balance=parse_money(row["current_available_balance"], field=f"financial_profiles.csv:{line} current_available_balance"),
                minimum_balance_to_keep=parse_money(row["minimum_balance_to_keep"], field=f"financial_profiles.csv:{line} minimum_balance_to_keep"),
                financial_priorities=_split_pipe(row["financial_priorities"]),
                expense_categories_to_protect=_split_pipe(row["expense_categories_to_protect"]),
                expense_categories_user_is_willing_to_reduce=_split_pipe(row["expense_categories_user_is_willing_to_reduce"]),
                expense_categories_user_is_willing_to_stop=_split_pipe(row["expense_categories_user_is_willing_to_stop"]),
                payment_methods_user_will_consider=_parse_payment_methods(
                    row["payment_methods_user_will_consider"],
                    file_name="financial_profiles.csv",
                    source_row=line,
                    field="payment_methods_user_will_consider",
                ),
                max_installment_months=_parse_optional_int(
                    row["max_installment_months"],
                    field="max_installment_months",
                    file_name="financial_profiles.csv",
                    source_row=line,
                ),
                source_row=line,
            )
        )
    return records


def load_financial_events(dataset_dir: Path | None = None) -> list[FinancialEvent]:
    dataset_dir = dataset_dir or default_dataset_dir()
    records = []
    for line, row in _read_rows(dataset_dir / "financial_events.csv", EVENT_COLUMNS):
        records.append(
            FinancialEvent(
                event_id=_required_text(row, "event_id", file_name="financial_events.csv", source_row=line),
                user_id=_required_text(row, "user_id", file_name="financial_events.csv", source_row=line),
                event_type=_required_text(row, "event_type", file_name="financial_events.csv", source_row=line),
                description=_required_text(row, "description", file_name="financial_events.csv", source_row=line),
                category=_required_text(row, "category", file_name="financial_events.csv", source_row=line),
                direction=Direction.parse(row["direction"], field=f"financial_events.csv:{line} direction"),
                amount=parse_optional_money(row["amount"], field=f"financial_events.csv:{line} amount"),
                currency=_required_text(row, "currency", file_name="financial_events.csv", source_row=line),
                event_date=parse_date(row["event_date"], field=f"financial_events.csv:{line} event_date"),
                settlement_date=parse_optional_date(row["settlement_date"], field=f"financial_events.csv:{line} settlement_date"),
                status=EventStatus.parse(row["status"], field=f"financial_events.csv:{line} status"),
                linked_event_id=_optional_text(row, "linked_event_id"),
                flexibility=_optional_text(row, "flexibility"),
                minimum_allowed_amount=parse_optional_money(row["minimum_allowed_amount"], field=f"financial_events.csv:{line} minimum_allowed_amount"),
                source_row=line,
            )
        )
    return records


def load_payment_options(dataset_dir: Path | None = None) -> list[PaymentOption]:
    dataset_dir = dataset_dir or default_dataset_dir()
    records = []
    for line, row in _read_rows(dataset_dir / "request_payment_options.csv", PAYMENT_OPTION_COLUMNS):
        records.append(
            PaymentOption(
                payment_option_id=_required_text(row, "payment_option_id", file_name="request_payment_options.csv", source_row=line),
                request_id=_required_text(row, "request_id", file_name="request_payment_options.csv", source_row=line),
                payment_method=PaymentMethod.parse(row["payment_method"], field=f"request_payment_options.csv:{line} payment_method"),
                payment_amount=parse_money(row["payment_amount"], field=f"request_payment_options.csv:{line} payment_amount"),
                number_of_payments=_parse_int(row["number_of_payments"], field="number_of_payments", file_name="request_payment_options.csv", source_row=line),
                first_payment_date=parse_date(row["first_payment_date"], field=f"request_payment_options.csv:{line} first_payment_date"),
                payment_frequency_days=_parse_optional_int(row["payment_frequency_days"], field="payment_frequency_days", file_name="request_payment_options.csv", source_row=line),
                financing_fee=parse_money(row["financing_fee"], field=f"request_payment_options.csv:{line} financing_fee"),
                total_payable_amount=parse_money(row["total_payable_amount"], field=f"request_payment_options.csv:{line} total_payable_amount"),
                source_row=line,
            )
        )
    return records


def load_exchange_rates(dataset_dir: Path | None = None) -> list[ExchangeRate]:
    dataset_dir = dataset_dir or default_dataset_dir()
    return [
        ExchangeRate(
            rate_date=parse_date(row["rate_date"], field=f"exchange_rates.csv:{line} rate_date"),
            from_currency=_required_text(row, "from_currency", file_name="exchange_rates.csv", source_row=line),
            to_currency=_required_text(row, "to_currency", file_name="exchange_rates.csv", source_row=line),
            rate=parse_money(row["rate"], field=f"exchange_rates.csv:{line} rate"),
            source_row=line,
        )
        for line, row in _read_rows(dataset_dir / "exchange_rates.csv", EXCHANGE_RATE_COLUMNS)
    ]


def load_messages(dataset_dir: Path | None = None) -> list[MessageRecord]:
    dataset_dir = dataset_dir or default_dataset_dir()
    return [
        MessageRecord(
            message_id=_required_text(row, "message_id", file_name="messages.csv", source_row=line),
            user_id=_required_text(row, "user_id", file_name="messages.csv", source_row=line),
            request_id=_optional_text(row, "request_id"),
            related_event_id=_optional_text(row, "related_event_id"),
            sent_at=parse_iso_timestamp(row["sent_at"], field=f"messages.csv:{line} sent_at"),
            source_type=_required_text(row, "source_type", file_name="messages.csv", source_row=line),
            message_text=_required_text(row, "message_text", file_name="messages.csv", source_row=line),
            source_row=line,
        )
        for line, row in _read_rows(dataset_dir / "messages.csv", MESSAGE_COLUMNS)
    ]


def load_images(dataset_dir: Path | None = None) -> list[ImageRecord]:
    dataset_dir = dataset_dir or default_dataset_dir()
    return [
        ImageRecord(
            image_id=_required_text(row, "image_id", file_name="images.csv", source_row=line),
            user_id=_required_text(row, "user_id", file_name="images.csv", source_row=line),
            request_id=_optional_text(row, "request_id"),
            related_event_id=_optional_text(row, "related_event_id"),
            source_row=line,
        )
        for line, row in _read_rows(dataset_dir / "images.csv", IMAGE_COLUMNS)
    ]


@dataclass(frozen=True)
class LoadedDataset:
    dataset_dir: Path
    requests: tuple[Request, ...]
    sample_requests: tuple[SampleRequest, ...]
    profiles: tuple[FinancialProfile, ...]
    events: tuple[FinancialEvent, ...]
    payment_options: tuple[PaymentOption, ...]
    exchange_rates: tuple[ExchangeRate, ...]
    messages: tuple[MessageRecord, ...]
    images: tuple[ImageRecord, ...]
    profile_by_user: dict[str, FinancialProfile]
    request_by_id: dict[str, Request]
    sample_request_by_id: dict[str, SampleRequest]
    events_by_user: dict[str, tuple[FinancialEvent, ...]]
    event_by_id: dict[str, FinancialEvent]
    payment_options_by_request: dict[str, tuple[PaymentOption, ...]]
    messages_by_user: dict[str, tuple[MessageRecord, ...]]
    messages_by_request: dict[str, tuple[MessageRecord, ...]]
    messages_by_related_event: dict[str, tuple[MessageRecord, ...]]
    images_by_user: dict[str, tuple[ImageRecord, ...]]
    images_by_request: dict[str, tuple[ImageRecord, ...]]
    images_by_related_event: dict[str, tuple[ImageRecord, ...]]
    exchange_rates_by_date_and_pair: dict[tuple[date, str, str], ExchangeRate]

    def image_path(self, image_id: str) -> Path:
        return self.dataset_dir / "media" / "images" / f"{image_id}.png"

    def require_image_path(self, image_id: str) -> Path:
        path = self.image_path(image_id)
        if not path.exists():
            raise IntegrityError(f"Missing PNG for image_id {image_id}: {path}")
        return path

    def require_exchange_rate(self, rate_date: date, from_currency: str, to_currency: str) -> ExchangeRate:
        key = (rate_date, from_currency, to_currency)
        try:
            return self.exchange_rates_by_date_and_pair[key]
        except KeyError as exc:
            raise IntegrityError(f"Missing exchange rate for {rate_date} {from_currency}->{to_currency}") from exc

    def counts(self) -> dict[str, int]:
        return {
            "requests": len(self.requests),
            "sample_requests": len(self.sample_requests),
            "profiles": len(self.profiles),
            "events": len(self.events),
            "payment_options": len(self.payment_options),
            "exchange_rates": len(self.exchange_rates),
            "messages": len(self.messages),
            "images": len(self.images),
        }


def _unique_by(items: Iterable[T], key_fn: Callable[[T], str], label: str) -> dict[str, T]:
    seen: dict[str, T] = {}
    for item in items:
        key = key_fn(item)
        if key in seen:
            raise IntegrityError(f"Duplicate {label}: {key}")
        seen[key] = item
    return seen


def _group_by(items: Iterable[T], key_fn: Callable[[T], str | None]) -> dict[str, tuple[T, ...]]:
    grouped: dict[str, list[T]] = defaultdict(list)
    for item in items:
        key = key_fn(item)
        if key is not None:
            grouped[key].append(item)
    return {key: tuple(value) for key, value in grouped.items()}


def load_dataset(dataset_dir: Path | None = None) -> LoadedDataset:
    dataset_dir = dataset_dir or default_dataset_dir()
    dataset_dir = dataset_dir.resolve()
    requests = tuple(load_requests(dataset_dir))
    samples = tuple(load_sample_requests(dataset_dir))
    profiles = tuple(load_financial_profiles(dataset_dir))
    events = tuple(load_financial_events(dataset_dir))
    options = tuple(load_payment_options(dataset_dir))
    rates = tuple(load_exchange_rates(dataset_dir))
    messages = tuple(load_messages(dataset_dir))
    images = tuple(load_images(dataset_dir))
    dataset = LoadedDataset(
        dataset_dir=dataset_dir,
        requests=requests,
        sample_requests=samples,
        profiles=profiles,
        events=events,
        payment_options=options,
        exchange_rates=rates,
        messages=messages,
        images=images,
        profile_by_user=_unique_by(profiles, lambda item: item.user_id, "profile user_id"),
        request_by_id=_unique_by(requests, lambda item: item.request_id, "request_id"),
        sample_request_by_id=_unique_by(samples, lambda item: item.request.request_id, "sample request_id"),
        events_by_user=_group_by(events, lambda item: item.user_id),
        event_by_id=_unique_by(events, lambda item: item.event_id, "event_id"),
        payment_options_by_request=_group_by(options, lambda item: item.request_id),
        messages_by_user=_group_by(messages, lambda item: item.user_id),
        messages_by_request=_group_by(messages, lambda item: item.request_id),
        messages_by_related_event=_group_by(messages, lambda item: item.related_event_id),
        images_by_user=_group_by(images, lambda item: item.user_id),
        images_by_request=_group_by(images, lambda item: item.request_id),
        images_by_related_event=_group_by(images, lambda item: item.related_event_id),
        exchange_rates_by_date_and_pair=_unique_by(
            rates,
            lambda item: (item.rate_date.isoformat() + "|" + item.from_currency + "|" + item.to_currency),
            "exchange rate date/currency pair",
        ),
    )
    object.__setattr__(
        dataset,
        "exchange_rates_by_date_and_pair",
        {(item.rate_date, item.from_currency, item.to_currency): item for item in rates},
    )
    validate_integrity(dataset)
    return dataset


def validate_integrity(dataset: LoadedDataset) -> None:
    all_request_ids = set(dataset.request_by_id) | set(dataset.sample_request_by_id)
    profile_ids = set(dataset.profile_by_user)
    event_ids = set(dataset.event_by_id)
    for request in dataset.requests:
        if request.user_id not in profile_ids:
            raise IntegrityError(f"Request {request.request_id} references missing profile {request.user_id}")
    for sample in dataset.sample_requests:
        if sample.request.user_id not in profile_ids:
            raise IntegrityError(f"Sample request {sample.request.request_id} references missing profile {sample.request.user_id}")
    for option in dataset.payment_options:
        if option.request_id not in all_request_ids:
            raise IntegrityError(f"Payment option {option.payment_option_id} references unknown request {option.request_id}")
        if option.number_of_payments == 1 and option.payment_frequency_days is not None:
            raise IntegrityError(f"One-payment option {option.payment_option_id} should have blank payment_frequency_days")
        if option.number_of_payments > 1 and option.payment_frequency_days is None:
            raise IntegrityError(f"Multi-payment option {option.payment_option_id} requires payment_frequency_days")
    for event in dataset.events:
        if event.user_id not in profile_ids:
            raise IntegrityError(f"Event {event.event_id} references missing profile {event.user_id}")
        if event.linked_event_id and event.linked_event_id not in event_ids:
            raise IntegrityError(f"Event {event.event_id} links to missing event {event.linked_event_id}")
    for message in dataset.messages:
        if message.user_id not in profile_ids:
            raise IntegrityError(f"Message {message.message_id} references missing profile {message.user_id}")
        if message.request_id and message.request_id not in all_request_ids:
            raise IntegrityError(f"Message {message.message_id} references unknown request {message.request_id}")
        if message.related_event_id and message.related_event_id not in event_ids:
            raise IntegrityError(f"Message {message.message_id} references unknown event {message.related_event_id}")
    image_ids = set()
    for image in dataset.images:
        if image.image_id in image_ids:
            raise IntegrityError(f"Duplicate image_id: {image.image_id}")
        image_ids.add(image.image_id)
        if image.user_id not in profile_ids:
            raise IntegrityError(f"Image {image.image_id} references missing profile {image.user_id}")
        if image.request_id and image.request_id not in all_request_ids:
            raise IntegrityError(f"Image {image.image_id} references unknown request {image.request_id}")
        if image.related_event_id and image.related_event_id not in event_ids:
            raise IntegrityError(f"Image {image.image_id} references unknown event {image.related_event_id}")
        dataset.require_image_path(image.image_id)

