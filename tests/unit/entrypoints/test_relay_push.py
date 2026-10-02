"""The off-AWS producer `rain-alert-relay-push` (design D42).

Every test injects the fetcher and the S3 client: the offline suite never
reaches SENAMHI or AWS, and `put_object` call counts prove that a failed run
leaves the last good object untouched.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError, ProfileNotFound

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.s3_relay_reader import RELAY_BUCKET_ENV_VAR, S3RelayReader
from rain_alert.adapters.senamhi_relay import MAX_OBJECT_BYTES, OBJECT_KEY, RelayWarningProvider
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper, warnings_url_for
from rain_alert.domain.sources import Available
from rain_alert.domain.values import UnavailableReason
from rain_alert.entrypoints import relay_push
from tests.support.fake_s3 import FakeS3Client, client_error
from tests.support.fakes import FakeHtmlFetcher
from tests.support.fixtures import (
    SENAMHI_ACTIVE_WARNING,
    SENAMHI_BROKEN_STRUCTURE,
    SENAMHI_PRECIPITATION_COAST,
)

BUCKET = "a-relay-bucket"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_ambient_relay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RELAY_BUCKET_ENV_VAR, raising=False)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RELAY_BUCKET_ENV_VAR, BUCKET)


class TestTheBucketVariable:
    def test_an_absent_variable_exits_1_without_a_put(self) -> None:
        client = FakeS3Client()
        fetcher = FakeHtmlFetcher("<html/>")

        code = relay_push.main([], fetcher=fetcher, client=client)

        assert code == 1
        assert client.put_calls == []
        assert fetcher.urls == []

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_or_blank_variable_exits_1_without_a_put(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(RELAY_BUCKET_ENV_VAR, value)
        client = FakeS3Client()

        assert relay_push.main([], fetcher=FakeHtmlFetcher("<html/>"), client=client) == 1
        assert client.put_calls == []


@pytest.mark.usefixtures("configured")
class TestTheGateBeforeThePut:
    @pytest.mark.parametrize(
        "reason",
        [UnavailableReason.TIMEOUT, UnavailableReason.TRANSPORT_ERROR, UnavailableReason.BAD_STATUS],
    )
    def test_a_fetch_failure_exits_2_without_a_put(self, reason: UnavailableReason) -> None:
        client = FakeS3Client()
        fetcher = FakeHtmlFetcher(FetchError(reason, "boom"))

        code = relay_push.main([], fetcher=fetcher, client=client, now=NOW)

        assert code == 2
        assert client.put_calls == []
        assert fetcher.urls == [warnings_url_for("Lambayeque")]

    def test_a_page_that_does_not_parse_exits_2_without_a_put(self) -> None:
        client = FakeS3Client()
        page = SENAMHI_BROKEN_STRUCTURE.read_text(encoding="utf-8")

        code = relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        assert code == 2
        assert client.put_calls == []

    def test_the_failure_line_names_the_reason_and_carries_no_page_content(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        page = SENAMHI_BROKEN_STRUCTURE.read_text(encoding="utf-8")

        relay_push.main([], fetcher=FakeHtmlFetcher(page), client=FakeS3Client(), now=NOW)

        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert UnavailableReason.STRUCTURE_UNRECOGNIZED.value in err
        assert page[:200] not in err


def _valid_page() -> str:
    return SENAMHI_ACTIVE_WARNING.read_text(encoding="utf-8")


@pytest.mark.usefixtures("configured")
class TestTheSizeCap:
    def test_a_body_over_the_cap_exits_3_without_a_put(self) -> None:
        client = FakeS3Client()
        page = _valid_page() + "<!--" + "x" * MAX_OBJECT_BYTES + "-->"

        code = relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        assert code == 3
        assert client.put_calls == []

    def test_the_cap_is_measured_in_encoded_bytes_not_characters(self) -> None:
        client = FakeS3Client()
        # Fewer characters than the cap, more UTF-8 bytes than the cap.
        page = _valid_page() + "<!--" + "\u00f1" * (MAX_OBJECT_BYTES // 2 + 1) + "-->"
        assert len(page) < MAX_OBJECT_BYTES < len(page.encode("utf-8"))

        code = relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        assert code == 3
        assert client.put_calls == []

    def test_a_body_exactly_at_the_cap_is_pushed(self) -> None:
        client = FakeS3Client()
        base = _valid_page()
        filler = MAX_OBJECT_BYTES - len(base.encode("utf-8")) - len("<!---->")
        page = base + "<!--" + "x" * filler + "-->"
        assert len(page.encode("utf-8")) == MAX_OBJECT_BYTES

        assert relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW) == 0
        assert len(client.put_calls) == 1


@pytest.mark.usefixtures("configured")
class TestASuccessfulPush:
    def test_exactly_one_put_with_the_contract_headers_and_metadata(self) -> None:
        client = FakeS3Client()
        page = _valid_page()
        body = page.encode("utf-8")

        code = relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        assert code == 0
        assert len(client.put_calls) == 1
        put = client.put_calls[0]
        assert put["Bucket"] == BUCKET
        assert put["Key"] == OBJECT_KEY
        assert put["Body"] == body
        assert put["ContentType"] == "text/html; charset=utf-8"
        assert put["ChecksumAlgorithm"] == "SHA256"
        assert put["Metadata"] == {
            "sha256": hashlib.sha256(body).hexdigest(),
            "fetched-at": "2026-09-04T12:00:00+00:00",
            "producer": relay_push.PRODUCER_ID,
        }

    def test_a_non_ascii_page_is_sent_as_utf_8_and_hashed_over_those_bytes(self) -> None:
        client = FakeS3Client()
        page = _valid_page() + "<p>Lluvias en Ferre\u00f1afe \u2014 aviso</p>"
        assert page != _valid_page()

        relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        put = client.put_calls[0]
        assert put["Body"] == page.encode("utf-8")
        assert put["Metadata"]["sha256"] == hashlib.sha256(page.encode("utf-8")).hexdigest()

    def test_the_fetched_at_claim_is_utc_whatever_zone_the_clock_carries(self) -> None:
        client = FakeS3Client()
        lima = datetime(2026, 9, 4, 7, 0, tzinfo=timezone(timedelta(hours=-5)))

        relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=lima)

        assert client.put_calls[0]["Metadata"]["fetched-at"] == "2026-09-04T12:00:00+00:00"

    def test_one_summary_line_with_version_bytes_and_rows_seen(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = FakeS3Client(version_id="ver-42")
        page = _valid_page()

        relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW)

        out = capsys.readouterr().out
        assert out.count("\n") == 1
        assert "version=ver-42" in out
        assert f"bytes={len(page.encode('utf-8'))}" in out
        assert "rows_seen=" in out
        assert "<html" not in out


@pytest.mark.usefixtures("configured")
def test_an_s3_supplied_version_id_cannot_break_out_of_the_summary_line(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeS3Client(version_id="v1\nforged: line")

    relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=NOW)

    assert capsys.readouterr().out.count("\n") == 1


@pytest.mark.usefixtures("configured")
class TestAFailedWrite:
    def test_a_client_error_exits_4_naming_only_the_code(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = FakeS3Client(put_error=client_error("AccessDenied", "PutObject"))

        code = relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=NOW)

        err = capsys.readouterr().err
        assert code == 4
        assert len(client.put_calls) == 1
        assert err.count("\n") == 1
        assert "AccessDenied" in err
        assert "fake AccessDenied" not in err

    def test_a_botocore_error_exits_4(self) -> None:
        error = EndpointConnectionError(endpoint_url="https://s3.example.invalid")
        client = FakeS3Client(put_error=error)

        assert relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=NOW) == 4

    def test_an_unexpected_error_exits_4_without_leaking_its_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = FakeS3Client(put_error=RuntimeError("secret page content"))

        code = relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=NOW)

        err = capsys.readouterr().err
        assert code == 4
        assert "RuntimeError" in err
        assert "secret page content" not in err
        assert err.count("\n") == 1


class _RecordingSession:
    instances: list[_RecordingSession] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.client_calls: list[tuple[str, dict[str, Any]]] = []
        _RecordingSession.instances.append(self)

    def client(self, service: str, **kwargs: Any) -> FakeS3Client:
        self.client_calls.append((service, kwargs))
        return FakeS3Client()


@pytest.fixture
def recording_session(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingSession]:
    _RecordingSession.instances = []
    monkeypatch.setattr(relay_push.boto3, "Session", _RecordingSession)
    return _RecordingSession


@pytest.mark.usefixtures("configured")
class TestTheRealClient:
    def test_it_is_built_from_the_requested_profile_and_region_with_a_bounded_config(
        self, recording_session: type[_RecordingSession]
    ) -> None:
        code = relay_push.main(
            ["--profile", "ferrenafe-relay", "--region", "us-east-2"],
            fetcher=FakeHtmlFetcher(_valid_page()),
            now=NOW,
        )

        assert code == 0
        (session,) = recording_session.instances
        assert session.kwargs == {"profile_name": "ferrenafe-relay", "region_name": "us-east-2"}
        ((service, kwargs),) = session.client_calls
        assert service == "s3"
        config = kwargs["config"]
        assert config.connect_timeout == relay_push.CONNECT_TIMEOUT
        assert config.read_timeout == relay_push.READ_TIMEOUT
        assert config.retries["total_max_attempts"] == relay_push.TOTAL_MAX_ATTEMPTS

    def test_without_arguments_the_default_chain_decides_profile_and_region(
        self, recording_session: type[_RecordingSession]
    ) -> None:
        relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), now=NOW)

        assert recording_session.instances[0].kwargs == {"profile_name": None, "region_name": None}

    def test_a_failed_gate_never_builds_a_client(self, recording_session: type[_RecordingSession]) -> None:
        fetcher = FakeHtmlFetcher(FetchError(UnavailableReason.TIMEOUT, "boom"))

        assert relay_push.main([], fetcher=fetcher, now=NOW) == 2
        assert recording_session.instances == []

    def test_an_unknown_profile_exits_4_without_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def explode(**_: Any) -> None:
            raise ProfileNotFound(profile="nope")

        monkeypatch.setattr(relay_push.boto3, "Session", explode)

        code = relay_push.main(["--profile", "nope"], fetcher=FakeHtmlFetcher(_valid_page()), now=NOW)

        assert code == 4
        assert "ProfileNotFound" in capsys.readouterr().err


def test_a_usage_error_exits_1_not_the_gate_code() -> None:
    with pytest.raises(SystemExit) as raised:
        relay_push.main(["--bogus"])

    assert raised.value.code == 1


@pytest.mark.usefixtures("configured")
class TestTheProducerReaderRoundTrip:
    """Pins that the Lambda parses byte-for-byte what the producer's gate accepted (D37)."""

    def test_what_the_producer_writes_the_reader_accepts_and_parses_like_the_scraper(self) -> None:
        # The coast fixture carries one flood-relevant warning in force, so the
        # comparison below is between non-empty results.
        page = SENAMHI_PRECIPITATION_COAST.read_text(encoding="utf-8") + "<p>Lluvias en Ferre\u00f1afe \u2014 aviso</p>"
        client = FakeS3Client(last_modified=NOW)
        assert relay_push.main([], fetcher=FakeHtmlFetcher(page), client=client, now=NOW) == 0

        relayed = RelayWarningProvider(S3RelayReader(BUCKET, client=client)).fetch_current_warnings("Lambayeque", NOW)
        direct = SenamhiWarningScraper(FakeHtmlFetcher(page)).fetch_current_warnings("Lambayeque", NOW)

        assert isinstance(relayed, Available)
        assert isinstance(direct, Available)
        assert relayed.data == direct.data
        assert len(relayed.data) == 1
        assert relayed.notes == direct.notes

    def test_the_reader_read_the_object_the_producer_wrote(self) -> None:
        client = FakeS3Client(last_modified=NOW)
        relay_push.main([], fetcher=FakeHtmlFetcher(_valid_page()), client=client, now=NOW)

        obj = S3RelayReader(BUCKET, client=client).read()

        assert obj.text == _valid_page()
        assert obj.claimed_fetched_at == NOW
        assert client.get_calls == [{"Bucket": BUCKET, "Key": OBJECT_KEY}]

    def test_a_failed_push_leaves_the_previous_object_readable(self) -> None:
        client = FakeS3Client(
            body=b"<html>last good</html>",
            metadata={"sha256": hashlib.sha256(b"<html>last good</html>").hexdigest()},
            last_modified=NOW,
        )

        code = relay_push.main([], fetcher=FakeHtmlFetcher(FetchError(UnavailableReason.TIMEOUT, "x")), client=client)

        assert code == 2
        assert S3RelayReader(BUCKET, client=client).read().text == "<html>last good</html>"


def test_the_console_script_is_registered_and_points_at_main() -> None:
    import tomllib

    from tests.hygiene.test_repo_hygiene import REPO_ROOT

    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]

    assert scripts["rain-alert-relay-push"] == "rain_alert.entrypoints.relay_push:main"
    assert scripts["rain-alert-cycle"] == "rain_alert.entrypoints.cli:main"
