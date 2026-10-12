"""Offline tests for the nearby-dates price grid (``payload[6]``).

When Google inlines no flight rows — routinely for a party with children or
infants — the search page still carries a grid of full-party prices for nearby
date pairs. These tests cover decoding it, the guard that keeps it out of
filtered searches, and where it is used: ``SearchFlights.nearby_date_prices``
and the MCP ``nearby_dates`` field.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fli.mcp.server import FlightSearchParams, _execute_flight_search
from fli.models import (
    Airline,
    Airport,
    FlightSearchFilters,
    FlightSegment,
    PassengerInfo,
    TimeRestrictions,
    TripType,
)
from fli.search import SearchFlights
from fli.search._decoders import GridPrice, parse_date_grid
from fli.search._tfs import has_client_side_filters
from tests.search._pages import as_search_page
from tests.search.test_date_sweep import StubClient, _page, _row

# Real cells captured 2026-10-12 from the SIN→JED round-trip page for
# 2 adults + 1 child + 1 lap infant, 2027-01-03 → 2027-01-10 (no rows inlined).
# The currency token decodes to SGD.
_SGD_TOKEN = (
    "CjRIc0lMYUlIakQ3d3NBdF9FdEFCRy0tLS0tLS1zZW94OS1uMUFBQUFBR3JNcGE4TXZiSWtBEh9ub3Jlc3Vs"
    "dHN8YWdnOjIwMjcwMTAyLTIwMjcwMTA3GgsIqbsSEAIaA1NHRDgdcOe1Dg=="
)
_REAL_CELLS = [
    ["2027-01-02", "2027-01-09", [[None, 3581], _SGD_TOKEN], [1, 1], [1, 1]],
    ["2027-01-04", "2027-01-12", [[None, 3026], _SGD_TOKEN], [1, 2], [1, 2]],
    ["2027-01-02", "2027-01-07", [[None, 3026], _SGD_TOKEN], [1, 1], [3, 1]],
]


def _grid_payload(cells: list, rows: list | None = None) -> list:
    """Build a ``ds:1`` payload with no rows (unless given) and ``cells`` at ``[6][0][4][0]``."""
    payload: list = [None] * 24
    if rows:
        payload[2] = [rows]
    payload[6] = [[None, None, None, None, [cells, []]]]
    return payload


class TestParseDateGrid:
    def test_decodes_real_round_trip_cells(self):
        cells = parse_date_grid(_grid_payload(_REAL_CELLS))
        assert cells[0] == GridPrice(date(2027, 1, 2), date(2027, 1, 9), 3581.0, "SGD")
        assert {(c.outbound, c.inbound, c.price) for c in cells} == {
            (date(2027, 1, 2), date(2027, 1, 9), 3581.0),
            (date(2027, 1, 4), date(2027, 1, 12), 3026.0),
            (date(2027, 1, 2), date(2027, 1, 7), 3026.0),
        }

    def test_one_way_cells_have_no_inbound(self):
        cells = parse_date_grid(_grid_payload([["2027-01-02", None, [[None, 1378], _SGD_TOKEN]]]))
        assert cells == [GridPrice(date(2027, 1, 2), None, 1378.0, "SGD")]

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            [None] * 10,
            [None, None, None, None, None, None, "not-a-list"],
            _grid_payload("not-a-list"),
        ],
        ids=["none", "empty", "no-grid", "grid-not-list", "cells-not-list"],
    )
    def test_absent_or_odd_grid_yields_nothing(self, payload):
        assert parse_date_grid(payload) == []

    def test_malformed_cells_are_skipped_not_fatal(self):
        cells = parse_date_grid(
            _grid_payload(
                [
                    ["not-a-date", None, [[None, 100], _SGD_TOKEN]],
                    ["2027-01-02", None, [[], _SGD_TOKEN]],  # no price computed
                    ["2027-01-02", None, [[None, "x"], _SGD_TOKEN]],
                    ["2027-01-02", None],
                    "junk",
                    ["2027-01-05", None, [[None, 900], _SGD_TOKEN]],
                ]
            )
        )
        assert cells == [GridPrice(date(2027, 1, 5), None, 900.0, "SGD")]


def _flight_filters(**kwargs) -> FlightSearchFilters:
    return FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=2, children=1, infants_on_lap=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.SIN, 0]],
                arrival_airport=[[Airport.JED, 0]],
                travel_date=(datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),
                time_restrictions=kwargs.pop("time_restrictions", None),
            )
        ],
        **kwargs,
    )


class TestHasClientSideFilters:
    def test_plain_search_has_none(self):
        assert has_client_side_filters(_flight_filters()) is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"airlines": [Airline.WY]},
            {"airlines_exclude": [Airline.TR]},
            {"max_duration": 600},
            {"time_restrictions": TimeRestrictions(earliest_departure=6)},
        ],
        ids=["airlines", "exclude", "duration", "window"],
    )
    def test_each_client_side_filter_counts(self, kwargs):
        assert has_client_side_filters(_flight_filters(**kwargs)) is True


class TestSearchFlightsNearbyDates:
    def _search(self, page: str) -> SearchFlights:
        client = SearchFlights()
        client.client = StubClient(page)
        return client

    def test_empty_search_exposes_grid_cheapest_first(self):
        client = self._search(as_search_page(_grid_payload(_REAL_CELLS)))
        assert client.search(_flight_filters()) is None
        assert [c.price for c in client.nearby_date_prices] == [3026.0, 3026.0, 3581.0]

    def test_filtered_search_exposes_no_grid(self):
        client = self._search(as_search_page(_grid_payload(_REAL_CELLS)))
        client.search(_flight_filters(airlines=[Airline.WY]))
        assert client.nearby_date_prices == []

    def test_search_with_rows_resets_the_grid(self):
        client = self._search(as_search_page(_grid_payload(_REAL_CELLS)))
        client.search(_flight_filters())
        assert client.nearby_date_prices
        client.client = StubClient(_page([_row(300.0)]))
        client.search(_flight_filters())
        assert client.nearby_date_prices == []


class TestMcpNearbyDates:
    def test_empty_search_response_lists_nearby_dates(self, monkeypatch):
        cells = [
            GridPrice(date(2027, 1, 4), date(2027, 1, 12), 3026.0, "SGD"),
            GridPrice(date(2027, 1, 2), date(2027, 1, 9), 3581.0, "SGD"),
        ]
        monkeypatch.setattr(SearchFlights, "search", lambda self, *a, **k: None)
        monkeypatch.setattr(SearchFlights, "nearby_date_prices", property(lambda self: cells))
        result = _execute_flight_search(
            FlightSearchParams(
                origin="SIN",
                destination="JED",
                departure_date=(datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),
                passengers=2,
                children=1,
                infants_on_lap=1,
                currency="SGD",
            )
        )
        assert result["success"] is True
        assert result["flights"] == []
        assert [
            (d["departure_date"], d["return_date"], d["price"]) for d in result["nearby_dates"]
        ] == [
            ("2027-01-04", "2027-01-12", 3026.0),
            ("2027-01-02", "2027-01-09", 3581.0),
        ]
        assert result["nearby_dates"][0]["booking_url"].startswith(
            "https://www.google.com/travel/flights"
        )
        assert "nearby_dates" in result["note"]

    def test_no_grid_means_no_field(self, monkeypatch):
        monkeypatch.setattr(SearchFlights, "search", lambda self, *a, **k: None)
        result = _execute_flight_search(
            FlightSearchParams(
                origin="SIN",
                destination="JED",
                departure_date=(datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),
            )
        )
        assert "nearby_dates" not in result
