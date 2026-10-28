"""The validator's continuity rules (agent-message-composition spec, "Continuity
numbers and dates are accepted only when traceable to ContinuitySummary"; design D10).

Additive on top of `test_message_validation.py`: a day count and a prior date
are accepted only when they come from the request's `ContinuitySummary`, and
every other rule is unchanged. Two groups of cases:

- **Hand-built** cases, each the template's own output with one sentence
  appended, so the failing assertion names the rule that fired.
- **Live regression** cases: the nine AFTER messages captured from the deployed
  runtime on 2026-10-24. Before this change the validator rejected all six
  day-2 and day-3 messages with `UNKNOWN_NUMBER`, because "2 días consecutivos"
  traced to nothing. They must pass with the summary the cycle would have built,
  and fail with a wrong or empty one.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.continuity import (
    EMPTY_CONTINUITY,
    ContinuitySummary,
    PriorAlert,
    Trend,
    summarize,
)
from rain_alert.domain.message_validation import ValidationRule, validate_message
from rain_alert.domain.messages import AlertMessage, AlertRecord, ForecastSummary, MessageRequest, WarningSummary
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))
LIMA = "America/Lima"
CITY = "Ferreñafe"


def _summary(days: int, *priors: datetime) -> ContinuitySummary:
    return ContinuitySummary(
        consecutive_days=days,
        previous_level=Level.PREPARE,
        escalated=False,
        trend=Trend.STEADY,
        prior_alerts=tuple(PriorAlert(sent_at=sent_at, level=Level.PREPARE, warning_level=None) for sent_at in priors),
    )


def request(continuity: ContinuitySummary = EMPTY_CONTINUITY) -> MessageRequest:
    return MessageRequest(
        city=CITY,
        timezone=LIMA,
        level=Level.PREPARE,
        window=WINDOW,
        reasons=(ForecastThresholdReason(accumulated_mm=12.4, hours=24, probability_pct=60),),
        forecast=None,
        warning=None,
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
        continuity=continuity,
    )


def with_sentence(message_request: MessageRequest, sentence: str) -> AlertMessage:
    """The template's output with `sentence` added as a paragraph after the first one."""
    base = MessageComposer().compose(message_request)
    first, _, rest = base.body.partition("\n")
    return replace(base, body=f"{first}\n{sentence}\n{rest}")


def rules(message_request: MessageRequest, candidate: AlertMessage) -> set[ValidationRule]:
    return {violation.rule for violation in validate_message(message_request, candidate)}


# 20/10/2026 08:00 and 21/10/2026 14:00 in Lima. The request's own window is 22/10 to 24/10 at 07:00.
PRIOR_A = datetime(2026, 10, 20, 13, tzinfo=UTC)
PRIOR_B = datetime(2026, 10, 21, 19, tzinfo=UTC)


class TestADayCountIsAcceptedOnlyWhenItIsTheConsecutiveDays:
    def test_the_matching_count_is_accepted(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Van 3 días consecutivos de alerta.")) == set()

    def test_the_matching_count_is_accepted_for_another_value(self) -> None:
        message_request = request(_summary(5, PRIOR_A))

        assert rules(message_request, with_sentence(message_request, "Esta es la continuación de 5 días.")) == set()

    def test_a_different_count_is_rejected(self) -> None:
        message_request = request(_summary(2, PRIOR_A))

        found = rules(message_request, with_sentence(message_request, "Van 3 días consecutivos de alerta."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_count_with_no_history_is_rejected(self) -> None:
        message_request = request()

        found = rules(message_request, with_sentence(message_request, "Van 2 días consecutivos de alerta."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_one_day_is_never_a_continuity_claim(self) -> None:
        """`consecutive_days == 1` means "today only"; stating "1 día" is not continuity."""
        message_request = request(_summary(1, PRIOR_A))

        found = rules(message_request, with_sentence(message_request, "Lleva 1 día de alerta."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_the_day_count_is_not_usable_as_millimetres(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "Podrían caer 3 mm de lluvia."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_the_day_count_is_not_usable_as_a_percentage_or_hours(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Hay 3 % de probabilidad.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }
        assert rules(message_request, with_sentence(message_request, "Durante 3 horas más.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_a_request_value_is_not_usable_as_a_day_count(self) -> None:
        """60 % is on the request; 60 días is not a claim any summary supports."""
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "Lleva 60 días de alerta."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_bare_figure_equal_to_the_day_count_is_not_accepted(self) -> None:
        """The unit-blind fallback must not pick the day count up."""
        message_request = request(_summary(7, PRIOR_A))

        found = rules(message_request, with_sentence(message_request, "Se anota el número 7 en el registro."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}


class TestAnOrdinalDigitBeforeDiaIsADayCount:
    """`3er día` and `día 3` state a day count. Without a unit they fell back to the union of every
    unit's values and could borrow a millimetre figure."""

    @staticmethod
    def _with_forecast(summary: ContinuitySummary) -> MessageRequest:
        forecast = ForecastSummary(mm_24h=3.0, mm_48h=5.0, peak_at=NOW + timedelta(hours=6), peak_probability_pct=60)
        return replace(request(summary), forecast=forecast)

    @pytest.mark.parametrize(
        "sentence",
        ["Es el 3er día de lluvia.", "Es el 3º día de lluvia.", "Es la 3ra día de lluvia.", "Hoy es el día 3."],
    )
    def test_a_figure_the_forecast_carries_cannot_stand_in_for_the_day_count(self, sentence: str) -> None:
        message_request = self._with_forecast(_summary(2, PRIOR_A))

        found = rules(message_request, with_sentence(message_request, sentence))

        assert ValidationRule.UNKNOWN_NUMBER in found

    @pytest.mark.parametrize("sentence", ["Es el 3er día de lluvia.", "Hoy es el día 3."])
    def test_the_consecutive_days_value_is_accepted(self, sentence: str) -> None:
        message_request = self._with_forecast(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, sentence)) == set()


class TestDaysWrittenInWordsAreRefused:
    @pytest.mark.parametrize("word", ["dos", "tres", "cinco", "veintiún"])
    def test_a_spelled_out_continuity_count_is_rejected_even_when_it_is_true(self, word: str) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, f"Van {word} días consecutivos."))

        assert found == {ValidationRule.WORD_NUMBER}

    @pytest.mark.parametrize("ordinal", ["primer", "segundo", "tercer", "cuarto", "tercera", "décimo"])
    def test_an_ordinal_before_dia_is_rejected(self, ordinal: str) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, f"Es el {ordinal} día de alerta."))

        assert found == {ValidationRule.WORD_NUMBER}

    def test_a_spelled_out_count_in_ordinary_prose_is_left_alone(self) -> None:
        """The existing suite pins this: `dos días` outside a continuity claim is not this rule's concern."""
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "Almacena agua para dos días."))

        assert found == set()

    def test_a_spelled_out_count_in_a_clause_about_the_alert_continuing_is_rejected(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(
            message_request, with_sentence(message_request, "La situación se agrava tras tres días de alertas.")
        )

        assert found == {ValidationRule.WORD_NUMBER}

    @pytest.mark.parametrize("days", [0, 1, 2, 3])
    @pytest.mark.parametrize(
        "sentence",
        [
            "Hace tres días que llueve.",
            "Van cuatro días de lluvia.",
            "Llueve por tres jornadas.",
            "Hace cuatro noches que llueve.",
            "Llueve desde hace tres semanas.",
        ],
    )
    def test_a_number_word_before_a_day_word_is_rejected_unless_the_checklist_says_it(
        self, sentence: str, days: int
    ) -> None:
        """No continuity marker in these clauses, so only the verbatim-checklist rule can refuse them."""
        message_request = request(_summary(days, PRIOR_A, PRIOR_B) if days else EMPTY_CONTINUITY)

        found = rules(message_request, with_sentence(message_request, sentence))

        assert ValidationRule.WORD_NUMBER in found

    def test_a_phrase_the_checklist_carries_verbatim_is_accepted(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Guarda provisiones para dos días.")) == set()

    def test_the_exemption_is_the_checklists_own_phrase_not_the_day_word(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "Guarda provisiones para tres días."))

        assert found == {ValidationRule.WORD_NUMBER}

    def test_the_exemption_follows_the_requests_own_checklist(self) -> None:
        message_request = replace(request(_summary(3, PRIOR_A, PRIOR_B)), checklist=("Almacena agua para tres días.",))

        assert rules(message_request, with_sentence(message_request, "Guarda provisiones para dos días.")) == {
            ValidationRule.WORD_NUMBER
        }

    def test_the_word_dia_without_a_number_is_fine(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "La alerta sigue vigente de día y de noche."))

        assert found == set()

    def test_the_checklist_days_are_still_exempt(self) -> None:
        """ "al menos dos días" is the operator's own text and is a verbatim span."""
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert any("dos días" in item for item in message_request.checklist)
        assert rules(message_request, MessageComposer().compose(message_request)) == set()


class TestAPriorDateIsAcceptedOnlyWhenItIsInTheSummary:
    def test_a_prior_date_is_accepted(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Ya hubo aviso el 20/10/2026.")) == set()
        assert rules(message_request, with_sentence(message_request, "Y también el 21/10/2026.")) == set()

    def test_a_prior_time_is_accepted_beside_its_own_date(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "El aviso del 20/10/2026 08:00 sigue.")) == set()
        assert rules(message_request, with_sentence(message_request, "Y el del 21/10/2026 14:00.")) == set()

    def test_a_prior_time_with_a_single_digit_hour_and_date_is_read_like_strftime(self) -> None:
        early = datetime(2026, 10, 7, 13, tzinfo=UTC)  # 07/10/2026 08:00 in Lima
        message_request = request(_summary(2, early))

        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 7/10/2026 8:00.")) == set()

    def test_a_prior_time_on_its_own_is_rejected(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "El aviso de las 08:00 sigue vigente.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_a_prior_date_and_another_priors_time_cannot_be_mixed(self) -> None:
        """08:00 belongs to the 20th and 14:00 to the 21st: neither may borrow the other's date."""
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Pico el 20/10/2026 14:00.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }
        assert rules(message_request, with_sentence(message_request, "Pico el 21/10/2026 08:00.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_a_prior_date_alone_stays_acceptable(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 20/10/2026.")) == set()

    def test_only_the_five_most_recent_priors_count(self) -> None:
        """The agent's tool lists at most five, so a sixth is something the model was never shown."""
        sent = [datetime(2026, 10, day, 13, tzinfo=UTC) for day in (21, 20, 19, 18, 17, 16)]  # newest first
        message_request = request(_summary(7, *sent))

        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 17/10/2026 08:00.")) == set()
        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 16/10/2026.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }
        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 16/10/2026 08:00.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_a_date_in_neither_the_window_nor_the_summary_is_rejected(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "Hubo un aviso el 19/10/2026."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_time_in_neither_the_window_nor_the_summary_is_rejected(self) -> None:
        message_request = request(_summary(3, PRIOR_A, PRIOR_B))

        found = rules(message_request, with_sentence(message_request, "El aviso de las 09:30 sigue vigente."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_prior_date_with_no_history_is_rejected(self) -> None:
        message_request = request()

        found = rules(message_request, with_sentence(message_request, "Ya hubo aviso el 20/10/2026."))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    def test_the_date_is_read_in_the_requests_timezone(self) -> None:
        """03:30Z on the 21st is 22:30 on the 20th in Lima: the 20th is accepted, the 21st is not."""
        late = datetime(2026, 10, 21, 3, 30, tzinfo=UTC)
        message_request = request(_summary(2, late))

        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 20/10/2026 22:30.")) == set()
        assert rules(message_request, with_sentence(message_request, "Hubo aviso el 21/10/2026.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }


class TestTheTemplateStillPassesWithASummary:
    @pytest.mark.parametrize("days", [0, 1, 2, 3, 7])
    def test_every_summary_leaves_the_templates_own_output_valid(self, days: int) -> None:
        message_request = request(_summary(days, PRIOR_A, PRIOR_B) if days else EMPTY_CONTINUITY)

        assert validate_message(message_request, MessageComposer().compose(message_request)) == ()


# --- The nine messages captured live on 2026-10-24 ---------------------------

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "recent_alerts" / "after_messages.json").read_text(
        encoding="utf-8"
    )
)
SEEDED = (
    # (sent_at, level, warning level): what `seed_demo_history.py --day N` wrote.
    (datetime(2026, 10, 20, 13, tzinfo=UTC), Level.PREPARE, WarningLevel.YELLOW),
    (datetime(2026, 10, 21, 13, tzinfo=UTC), Level.IMMINENT, WarningLevel.ORANGE),
)
LIVE_ZONE = ZoneInfo(LIMA)


def _record(sent_at: datetime, level: Level, warning: WarningLevel) -> AlertRecord:
    return AlertRecord(
        city_slug="demo-ferrenafe",
        level=level,
        window=TimeWindow(start=sent_at, end=sent_at + timedelta(hours=48)),
        sent_at=sent_at,
        message=AlertMessage(title="t", body="b", level=level, valid_until=sent_at + timedelta(hours=48)),
        reasons=(WarningReason(level=warning, title="seed"),),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


def _live_request(day: int, continuity: ContinuitySummary) -> MessageRequest:
    scenario = FIXTURE["days"][str(day)]
    start = datetime.fromisoformat(scenario["as_of"])
    window = TimeWindow(start=start, end=start + timedelta(days=scenario["window_days"]))
    warning = WarningLevel(scenario["warning_level"])
    return MessageRequest(
        city=FIXTURE["city"],
        timezone=FIXTURE["timezone"],
        level=Level(scenario["level"]),
        window=window,
        reasons=(
            WarningReason(level=warning, title=scenario["warning_title"]),
            ForecastThresholdReason(
                accumulated_mm=scenario["mm_24h"], hours=24, probability_pct=scenario["probability_pct"]
            ),
        ),
        forecast=ForecastSummary(
            mm_24h=scenario["mm_24h"],
            mm_48h=scenario["mm_48h"],
            peak_at=datetime.fromisoformat(scenario["peak_at"]),
            peak_probability_pct=scenario["probability_pct"],
        ),
        warning=WarningSummary(source_id="live", level=warning, title=scenario["warning_title"], window=window),
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
        continuity=continuity,
    )


def _live_summary(day: int) -> ContinuitySummary:
    """The summary `RunAlertCycle` would build on `day`, from the records seeded for the days before it."""
    scenario = FIXTURE["days"][str(day)]
    return summarize(
        tuple(_record(*seeded) for seeded in SEEDED[: day - 1]),
        as_of=datetime.fromisoformat(scenario["as_of"]),
        tz=LIVE_ZONE,
        current_level=Level(scenario["level"]),
        current_warning_level=WarningLevel(scenario["warning_level"]),
    )


def _messages(*days: int) -> list[Any]:
    return [pytest.param(m, id=f"day-{m['day']}-run-{m['run']}") for m in FIXTURE["messages"] if m["day"] in days]


def _candidate(live: dict[str, Any], day: int) -> AlertMessage:
    request_for_day = _live_request(day, EMPTY_CONTINUITY)
    return AlertMessage(
        title=live["title"], body=live["body"], level=request_for_day.level, valid_until=request_for_day.window.end
    )


class TestTheLiveAfterMessages:
    def test_the_fixture_holds_nine_messages_three_per_day(self) -> None:
        assert sorted((m["day"], m["run"]) for m in FIXTURE["messages"]) == [
            (day, run) for day in (1, 2, 3) for run in (1, 2, 3)
        ]

    def test_the_summaries_built_from_the_seeded_days_match_the_tool_results(self) -> None:
        """Pins the fixture's premise: these are the facts the live tool reported to the model."""
        day2, day3 = _live_summary(2), _live_summary(3)

        assert (day2.consecutive_days, day2.previous_level, day2.trend) == (2, Level.PREPARE, Trend.RISING)
        assert (day3.consecutive_days, day3.previous_level, day3.trend) == (3, Level.IMMINENT, Trend.RISING)
        assert _live_summary(1) == EMPTY_CONTINUITY

    @pytest.mark.parametrize("live", _messages(2, 3))
    def test_a_continuity_message_passes_with_the_matching_summary(self, live: dict[str, Any]) -> None:
        day = live["day"]

        violations = validate_message(_live_request(day, _live_summary(day)), _candidate(live, day))

        assert violations == ()

    @pytest.mark.parametrize("live", _messages(2, 3))
    def test_a_continuity_message_fails_with_no_summary(self, live: dict[str, Any]) -> None:
        day = live["day"]

        found = rules(_live_request(day, EMPTY_CONTINUITY), _candidate(live, day))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    @pytest.mark.parametrize("live", _messages(2, 3))
    def test_a_continuity_message_fails_with_another_days_summary(self, live: dict[str, Any]) -> None:
        day = live["day"]
        other = _live_summary(5 - day)  # day 2 sees day 3's summary and vice versa

        found = rules(_live_request(day, other), _candidate(live, day))

        assert found == {ValidationRule.UNKNOWN_NUMBER}

    @pytest.mark.parametrize("live", _messages(1))
    def test_a_day_one_message_still_passes_with_no_history(self, live: dict[str, Any]) -> None:
        violations = validate_message(_live_request(1, EMPTY_CONTINUITY), _candidate(live, 1))

        assert violations == ()

    @pytest.mark.parametrize("live", _messages(1))
    def test_a_day_one_message_makes_no_continuity_claim_so_a_summary_changes_nothing(
        self, live: dict[str, Any]
    ) -> None:
        assert validate_message(_live_request(1, _live_summary(3)), _candidate(live, 1)) == ()
