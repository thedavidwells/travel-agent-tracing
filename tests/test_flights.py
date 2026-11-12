import json
from datetime import date

from flights import FlightSearchInput, find_flights, search_flights

TODAY = date(2026, 11, 11)


def search(**kwargs):
    params = {"origin": "SFO", "destination": "LIS", "depart_date": "2026-12-10", **kwargs}
    return find_flights(FlightSearchInput(**params), today=TODAY)


def test_known_route_returns_options_sorted_by_price():
    result = search()
    assert result.status == "ok"
    assert result.trip_type == "one_way"
    assert len(result.options) >= 3
    prices = [o.total_price_usd for o in result.options]
    assert prices == sorted(prices)


def test_results_are_deterministic():
    assert search() == search()


def test_city_codes_are_normalized():
    result = search(origin=" sfo ", destination="lis")
    assert result.status == "ok"
    assert (result.query.origin, result.query.destination) == ("SFO", "LIS")


def test_round_trip_costs_more_than_one_way():
    one_way = search().options[0].price_per_passenger_usd
    round_trip = search(return_date="2026-12-20")
    assert round_trip.trip_type == "round_trip"
    assert round_trip.options[0].price_per_passenger_usd > one_way


def test_total_price_scales_with_passengers():
    option = search(passengers=3).options[0]
    assert option.total_price_usd == round(option.price_per_passenger_usd * 3, 2)


def test_unknown_airport_returns_no_results_with_supported_list():
    result = search(destination="XYZ")
    assert result.status == "no_results"
    assert result.options == []
    assert "XYZ" in result.message and "LIS (Lisbon)" in result.message


def test_budget_below_cheapest_returns_no_results():
    cheapest = search().options[0].total_price_usd
    result = search(max_price_usd=cheapest - 1)
    assert result.status == "no_results"
    assert f"{cheapest:,.2f}" in result.message


def test_budget_filters_options():
    options = search().options
    budget = options[1].total_price_usd
    result = search(max_price_usd=budget)
    assert result.status == "ok"
    assert all(o.total_price_usd <= budget for o in result.options)


def test_invalid_inputs():
    assert search(depart_date="2026-01-01").status == "invalid_input"
    assert search(depart_date="next friday").status == "invalid_input"
    assert search(return_date="2026-12-01").status == "invalid_input"
    assert search(destination="SFO").status == "invalid_input"
    assert search(passengers=0).status == "invalid_input"


def test_tool_returns_json_serializable_structured_output():
    output = search_flights.invoke(
        {"origin": "JFK", "destination": "LHR", "depart_date": "2099-06-01", "cabin": "business"}
    )
    assert output["status"] == "ok"
    assert output["options"][0]["cabin"] == "business"
    json.dumps(output)
