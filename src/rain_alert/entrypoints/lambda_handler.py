"""`lambda_handler.handler` — the Lambda entry point for the six-hourly
scheduled cycle (design.md D29; `scheduled-execution` spec).

**`event` is read for nothing.** A schedule payload carries no fact the
cycle needs, and treating it as a configuration channel would give anything
that can invoke the function a say in what the cycle does. The cycle's
inputs are SSM, the two sources, and the table (`scheduled-execution` spec).

**It catches nothing.** An exception propagates out of `handler`, Lambda
records it on the `Errors` metric, and the CDK stack's Alarm 1 (design.md
D33) fires on it. Swallowing it here would make that metric permanently
zero and the alarm decorative — this is the load-bearing coupling between
this function's contract and observability
(`scheduled-execution` spec, "A cloud dependency construction failure fails
the invocation visibly").

**Forbidden from importing `entrypoints.cli`,
`adapters.local.json_alert_repository` or
`adapters.local.static_config_repository`**
(`tests/architecture/test_layer_boundaries.py`,
`test_lambda_handler_never_imports_local_only_modules`). A cloud path that
reached any of those would inherit the CLI's local, ephemeral state instead
of the deployed adapters — dedup would go silently inert in production, with
nothing failing, logging, or looking wrong.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from rain_alert.entrypoints.run_once import as_json, run_once
from rain_alert.entrypoints.wiring import build_cloud_deps, select_snapshot_publisher


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001 — event/context read for nothing, on purpose
    """Run one scheduled cycle.

    `event` and `context` are Lambda's own invocation payload and runtime
    context; neither is read (see the module docstring). Returns a small
    summary dict for a manual `aws lambda invoke`; the CloudWatch Logs
    record for the cycle is the `as_json` line printed below, not this
    return value.
    """
    deps = build_cloud_deps()
    publisher = select_snapshot_publisher(os.environ)

    result, config = run_once(deps, publisher, report=sys.stderr)

    print(as_json(result, config))

    return {
        "sent": result.sent,
        "level": result.assessment.level.value,
        "degraded": result.assessment.degraded,
    }
