#!/usr/bin/env bash
#
# Builds `infra/build/lambda/` — the asset `ScheduledCycleStack`'s
# `lambda.Code.fromAsset('build/lambda')` deploys (design.md D30).
#
# No Docker. `awscrt` is deliberately excluded: it is the one compiled wheel
# in this project's dependency graph, and it exists only because botocore's
# *login* credential provider (`aws login`, this project's documented local
# authentication — see the root `pyproject.toml` comment on the
# `botocore[crt]` extra) refuses to load without it. The deployed Lambda uses
# an IAM execution role and never touches that credential provider, so
# without `awscrt` every remaining distribution is pure Python, and the
# artifact is architecture-independent — no cross-compilation, no Docker.
#
# The script FAILS if the built tree contains any `*.so` file or any
# `awscrt*` directory: the day a compiled dependency arrives, this stops the
# build instead of shipping an x86 wheel to the ARM_64 runtime, where it
# would fail at import on the first real cycle and on no test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "${SCRIPT_DIR}")"
REPO_ROOT="$(dirname "${INFRA_DIR}")"
BUILD_DIR="${INFRA_DIR}/build/lambda"

echo "build-lambda.sh: repo root is ${REPO_ROOT}"
echo "build-lambda.sh: build target is ${BUILD_DIR}"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

REQUIREMENTS_FILE="$(mktemp)"
trap 'rm -f "${REQUIREMENTS_FILE}"' EXIT

(
  cd "${REPO_ROOT}"
  # `--no-emit-package awscrt`: the one compiled wheel, excluded here rather
  # than filtered out of the exported requirements file by hand, per the
  # module docstring above.
  uv export --frozen --no-dev --no-emit-project --no-emit-package awscrt > "${REQUIREMENTS_FILE}"
)

echo "build-lambda.sh: resolved dependencies (excluding awscrt):"
grep -E '^[a-zA-Z0-9_.-]+==' "${REQUIREMENTS_FILE}" | sed 's/ \\$//'

uv pip install \
  --target "${BUILD_DIR}" \
  --python-version 3.12 \
  --python-platform aarch64-manylinux2014 \
  --no-deps \
  --requirements "${REQUIREMENTS_FILE}"

cp -R "${REPO_ROOT}/src/rain_alert" "${BUILD_DIR}/"

# The guard this whole design decision depends on (D30): fail loudly on any
# compiled artifact rather than shipping one silently.
FOUND_SO_FILES="$(find "${BUILD_DIR}" -name '*.so' -print)"
FOUND_AWSCRT_DIRS="$(find "${BUILD_DIR}" -type d -iname 'awscrt*' -print)"

if [[ -n "${FOUND_SO_FILES}" || -n "${FOUND_AWSCRT_DIRS}" ]]; then
  echo "build-lambda.sh: FAILED — compiled artifact(s) found in ${BUILD_DIR}:" >&2
  [[ -n "${FOUND_SO_FILES}" ]] && printf '%s\n' "${FOUND_SO_FILES}" >&2
  [[ -n "${FOUND_AWSCRT_DIRS}" ]] && printf '%s\n' "${FOUND_AWSCRT_DIRS}" >&2
  echo "build-lambda.sh: the ARM_64 runtime cannot import an x86 (or any) compiled wheel silently — refusing to ship it." >&2
  exit 1
fi

echo "build-lambda.sh: OK — no compiled artifact found. Build complete: ${BUILD_DIR}"
