"""Offline tests for pricing parties with children or infants in the MCP tools.

Google inlines no rows for many such parties, so ``search_flights`` came back
empty and ``search_dates`` silently showed only the few airlines Google prices
client-side. These tests pin the fallback (every traveler priced as an adult,
labelled as upper bounds) and the coverage notes.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta

from fli.mcp.server import (
    DateSearchParams,
    FlightSearchParams,
    _execute_date_search,
    _execute_flight_search,
)
from fli.search import SearchDates, SearchFlights
from fli.search._decoders import parse_flight_row
from tests.search.test_date_sweep import _row

_DEP = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
_END = (datetime.now() + timedelta(days=62)).strftime("%Y-%m-%d")


def _family_params(**kwargs) -> FlightSearchParams:
    return FlightSearchParams(
        origin="JFK",
        destination="LAX",
        departure_date=_DEP,
        passengers=2,
        children=1,
        infants_on_lap=1,
        **kwargs,
    )


def _fake_search(calls: list):
    """SearchFlights.search stand-in: empty for children/infants, one row for adults."""

    def search(self, filters, *args, **kwargs):
        party = filters.passenger_info
        calls.append(party)
        if party.children or party.infants_in_seat or party.infants_on_lap:
            self._sparse_passenger_mix = True
            return None
        return [parse_flight_row(_row(2406.0))]

    return search


class TestFlightSearchFallback:
    def test_family_search_falls_back_to_adult_pricing(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(SearchFlights, "search", _fake_search(calls))
        result = _execute_flight_search(_family_params())

        assert result["success"] is True
        assert [f["price"] for f in result["flights"]] == [2406.0]
        assert result["prices_are_upper_bounds"] is True
        assert result["price_basis"] == "all_travelers_as_adults"
        assert "upper bound" in result["note"]
        # Second call priced all four travelers as adults.
        assert (calls[1].adults, calls[1].children, calls[1].infants_on_lap) == (4, 0, 0)

    def test_booking_link_keeps_the_real_party(self, monkeypatch):
        """The deep link must open priced for 2 adults + child + lap infant, not 4 adults."""
        monkeypatch.setattr(SearchFlights, "search", _fake_search([]))
        url = _execute_flight_search(_family_params())["flights"][0]["booking_url"]
        tfs = url.split("tfs=")[1].split("&")[0]
        raw = base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))
        # Field 8 passenger codes: adult=1, adult=1, child=2, lap infant=3.
        assert b"@\x01@\x01@\x02@\x03" in raw

    def test_adults_only_empty_search_does_not_fall_back(self, monkeypatch):
        calls: list = []

        def search(self, filters, *args, **kwargs):
            calls.append(filters.passenger_info)
            return None

        monkeypatch.setattr(SearchFlights, "search", search)
        result = _execute_flight_search(
            FlightSearchParams(origin="JFK", destination="LAX", departure_date=_DEP)
        )
        assert result["flights"] == []
        assert len(calls) == 1
        assert "prices_are_upper_bounds" not in result

    def test_family_search_with_rows_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(
            SearchFlights, "search", lambda self, *a, **k: [parse_flight_row(_row(900.0))]
        )
        result = _execute_flight_search(_family_params())
        assert [f["price"] for f in result["flights"]] == [900.0]
        assert "prices_are_upper_bounds" not in result


def _date_params(**kwargs) -> DateSearchParams:
    return DateSearchParams(
        origin="JFK", destination="LAX", start_date=_DEP, end_date=_END, **kwargs
    )


class TestDateSearchFamilyPricing:
    def _capture(self, monkeypatch) -> list:
        seen: list = []

        def search(self, filters, *args, **kwargs):
            seen.append(filters.passenger_info)
            return None

        monkeypatch.setattr(SearchDates, "search", search)
        return seen

    def test_flag_prices_children_as_adults(self, monkeypatch):
        seen = self._capture(monkeypatch)
        result = _execute_date_search(
            _date_params(passengers=2, children=1, infants_on_lap=1, price_children_as_adults=True)
        )
        assert (seen[0].adults, seen[0].children, seen[0].infants_on_lap) == (4, 0, 0)
        assert result["prices_are_upper_bounds"] is True
        assert result["price_basis"] == "all_travelers_as_adults"

    def test_family_search_without_flag_warns_about_coverage(self, monkeypatch):
        seen = self._capture(monkeypatch)
        result = _execute_date_search(_date_params(passengers=2, children=1, infants_on_lap=1))
        assert (seen[0].adults, seen[0].children, seen[0].infants_on_lap) == (2, 1, 1)
        assert "price_children_as_adults=true" in result["note"]
        assert "prices_are_upper_bounds" not in result

    def test_adults_only_has_no_note_and_ignores_flag(self, monkeypatch):
        seen = self._capture(monkeypatch)
        result = _execute_date_search(_date_params(passengers=2, price_children_as_adults=True))
        assert seen[0].adults == 2
        assert "note" not in result
        assert "prices_are_upper_bounds" not in result
