"""Flight search tool backed by deterministic mock data.

The same query always returns the same flights, so traces and evaluations are reproducible.
"""

import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

AIRPORTS = {
    "SFO": "San Francisco",
    "LAX": "Los Angeles",
    "SEA": "Seattle",
    "DEN": "Denver",
    "ORD": "Chicago",
    "JFK": "New York",
    "LHR": "London",
    "CDG": "Paris",
    "LIS": "Lisbon",
    "FCO": "Rome",
    "NRT": "Tokyo",
    "SYD": "Sydney",
}

AIRLINES = [
    ("United", "UA"),
    ("Delta", "DL"),
    ("American", "AA"),
    ("Alaska", "AS"),
    ("British Airways", "BA"),
    ("Air France", "AF"),
    ("TAP Air Portugal", "TP"),
    ("Lufthansa", "LH"),
    ("ANA", "NH"),
    ("Qantas", "QF"),
]

Cabin = Literal["economy", "premium_economy", "business", "first"]

CABIN_PRICE_MULTIPLIER = {
    "economy": 1.0,
    "premium_economy": 1.6,
    "business": 3.5,
    "first": 6.0,
}


class FlightSearchInput(BaseModel):
    origin: str = Field(description="Origin airport IATA code, for example SFO.")
    destination: str = Field(description="Destination airport IATA code, for example LIS.")
    depart_date: str = Field(description="Departure date in YYYY-MM-DD format.")
    return_date: str | None = Field(
        default=None, description="Return date in YYYY-MM-DD format. Omit for one-way trips."
    )
    passengers: int = Field(default=1, description="Number of passengers, 1 to 9.")
    cabin: Cabin = Field(default="economy", description="Cabin class.")
    max_price_usd: float | None = Field(
        default=None, description="Maximum total price in USD for all passengers."
    )


class FlightOption(BaseModel):
    airline: str
    flight_number: str
    depart_time: str
    arrive_time: str
    duration_minutes: int
    stops: int
    cabin: Cabin
    price_per_passenger_usd: float
    total_price_usd: float


class FlightSearchResult(BaseModel):
    status: Literal["ok", "no_results", "invalid_input"]
    query: FlightSearchInput
    trip_type: Literal["one_way", "round_trip"]
    options: list[FlightOption]
    message: str


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def find_flights(params: FlightSearchInput, today: date | None = None) -> FlightSearchResult:
    """Validate the request and return mock flight options sorted by price."""
    today = today or date.today()
    origin = params.origin.strip().upper()
    destination = params.destination.strip().upper()
    query = params.model_copy(update={"origin": origin, "destination": destination})
    trip_type = "round_trip" if params.return_date else "one_way"

    def result(status, message, options=None):
        return FlightSearchResult(
            status=status, query=query, trip_type=trip_type, options=options or [], message=message
        )

    depart = _parse_date(params.depart_date)
    if depart is None:
        return result("invalid_input", f"depart_date '{params.depart_date}' is not a YYYY-MM-DD date.")
    if depart < today:
        return result("invalid_input", f"depart_date {depart} is in the past (today is {today}).")
    if params.return_date:
        ret = _parse_date(params.return_date)
        if ret is None:
            return result("invalid_input", f"return_date '{params.return_date}' is not a YYYY-MM-DD date.")
        if ret < depart:
            return result("invalid_input", "return_date is before depart_date.")
    if not 1 <= params.passengers <= 9:
        return result("invalid_input", "passengers must be between 1 and 9.")
    if origin == destination:
        return result("invalid_input", "origin and destination are the same airport.")

    unsupported = [code for code in (origin, destination) if code not in AIRPORTS]
    if unsupported:
        supported = ", ".join(f"{code} ({city})" for code, city in AIRPORTS.items())
        return result(
            "no_results",
            f"No flight data for {', '.join(unsupported)}. Supported airports: {supported}.",
        )

    route = _rng(*sorted((origin, destination)))
    base_fare = route.randint(150, 1100)
    base_minutes = route.randint(90, 840)

    rng = _rng(origin, destination, depart, params.cabin)
    options = []
    for _ in range(rng.randint(3, 6)):
        airline, code = rng.choice(AIRLINES)
        stops = rng.choices([0, 1, 2], weights=[5, 4, 1])[0]
        duration = base_minutes + stops * rng.randint(60, 180)
        departs = datetime.combine(depart, datetime.min.time()) + timedelta(
            hours=rng.randint(6, 22), minutes=rng.choice([0, 15, 30, 45])
        )
        fare = base_fare * CABIN_PRICE_MULTIPLIER[params.cabin] * rng.uniform(0.8, 1.35)
        fare *= 0.9 if stops else 1.0
        fare *= 1.85 if trip_type == "round_trip" else 1.0
        per_passenger = round(fare, 2)
        options.append(
            FlightOption(
                airline=airline,
                flight_number=f"{code}{rng.randint(100, 9999)}",
                depart_time=departs.isoformat(timespec="minutes"),
                arrive_time=(departs + timedelta(minutes=duration)).isoformat(timespec="minutes"),
                duration_minutes=duration,
                stops=stops,
                cabin=params.cabin,
                price_per_passenger_usd=per_passenger,
                total_price_usd=round(per_passenger * params.passengers, 2),
            )
        )
    options.sort(key=lambda o: o.total_price_usd)

    if params.max_price_usd is not None:
        within_budget = [o for o in options if o.total_price_usd <= params.max_price_usd]
        if not within_budget:
            return result(
                "no_results",
                f"No flights at or under ${params.max_price_usd:,.2f}. "
                f"The cheapest option is ${options[0].total_price_usd:,.2f}.",
            )
        options = within_budget

    return result("ok", f"Found {len(options)} {trip_type.replace('_', '-')} options.", options)


@tool("search_flights", args_schema=FlightSearchInput)
def search_flights(**kwargs) -> dict:
    """Search for flights between two airports on a given date.

    Use this for any request to find, compare, or price flights. Requires IATA airport codes
    and a departure date. Returns structured options sorted by total price, plus a status of
    "ok", "no_results", or "invalid_input" with a message explaining the outcome.
    """
    return find_flights(FlightSearchInput(**kwargs)).model_dump(mode="json")
