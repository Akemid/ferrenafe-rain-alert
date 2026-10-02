"""The relay `WarningProvider`: SENAMHI warnings read from one S3 object.

SENAMHI blocks AWS egress (docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md),
so an off-AWS producer pushes the page into S3 and the Lambda reads it from
there. Constants below mirror `contracts/senamhi-relay.json`; a test pins them
equal, because a one-character disagreement between the grant and the reader
fails as `AccessDenied` on every cycle (design.md D37).
"""

from __future__ import annotations

from datetime import timedelta

#: The single object the Lambda may read. Not configurable by env: the IAM grant
#: names exactly this key, so an override could only ever produce a denial.
OBJECT_KEY = "senamhi/lambayeque/latest.html"

#: At or beyond this age the object is stale, and a stale page is never parsed.
MAX_AGE = timedelta(seconds=10800)

#: Largest object the reader accepts, and the producer uploads (4 MiB).
MAX_OBJECT_BYTES = 4194304

#: Producer-vs-S3 clock disagreement above which the operator is told.
SKEW_TOLERANCE = timedelta(seconds=900)
