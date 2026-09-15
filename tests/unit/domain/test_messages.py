"""`OutageRecord.is_dual` — the only behaviour in `domain/messages.py`
(design.md section 3, mirrors design spec 7.2/7.4).

Every other type in that module is a frozen dataclass with no behaviour, so
the field-presence tests that used to live here were pruned as tautological:
they only asserted that a frozen dataclass returns what was passed in, which
`mypy --strict` already covers for field renames.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from rain_alert.domain.messages import AlertMessage, OutageRecord
from rain_alert.domain.values import ComposerName, Level, SourceName


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


def _message(**overrides: object) -> AlertMessage:
    defaults: dict[str, object] = {
        "title": "Alerta",
        "body": "Cuerpo",
        "level": Level.PREPARE,
        "valid_until": _utc(48),
    }
    defaults.update(overrides)
    return AlertMessage(**defaults)  # type: ignore[arg-type]


class TestAlertMessageProvenance:
    """`composed_by` is the channel from composer to use case (D13).

    The port must not change and `RunAlertCycle` must not grow a branch per
    composer, so the only route left for "who wrote this" is the return
    value. Two things about it are behaviour rather than field presence, and
    both are asserted here.
    """

    def test_provenance_defaults_to_the_template(self) -> None:
        """The default is the conservative value: a composer that forgets to
        set it under-claims agent involvement rather than over-claiming it.
        It is also what keeps the field purely additive for the construction
        sites already merged."""
        assert _message().composed_by is ComposerName.TEMPLATE

    def test_two_messages_with_the_same_text_but_different_provenance_are_not_equal(self) -> None:
        """Value equality now includes provenance, and that is intended.

        The fault-injection table asserts a fallback message is byte-identical
        to the template's output *and* attributed to the template. If equality
        ignored provenance, a composer that returned the right text under the
        wrong attribution would pass every one of those rows.
        """
        from_template = _message()
        from_agent = replace(from_template, composed_by=ComposerName.AGENT)

        assert from_agent != from_template
        assert (from_agent.title, from_agent.body) == (from_template.title, from_template.body)


class TestOutageRecord:
    def test_is_dual_true_when_two_sources_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
            opened_at=_utc(0),
            notified_at=_utc(0),
        )
        assert record.is_dual() is True

    def test_is_dual_false_when_one_source_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=_utc(0),
            notified_at=None,
        )
        assert record.is_dual() is False
