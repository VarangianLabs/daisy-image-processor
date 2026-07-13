#!/usr/bin/env bash
# build_vendor.sh — Regenerate vendor/ packages for the Lambda runtime.
#
# Downloads binary wheels compiled for Amazon Linux x86_64 (manylinux2014).
# Must be run from the repository root:
#
#   bash scripts/build_vendor.sh
#   make vendor           ← preferred (calls this script)
#
# The --platform flag forces pip to download the Linux build regardless of the
# host OS. This ensures PIL/_imaging.so and pillow.libs/ are the Amazon Linux
# binaries, not the developer's macOS or Windows native builds.

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
VENDOR_DIR="vendor"
REQUIREMENTS="src/requirements.txt"
PYTHON_VERSION="3.12"
PLATFORM="manylinux2014_x86_64"

# ── Validate invocation directory ──────────────────────────────────────────────
if [[ ! -f "${REQUIREMENTS}" ]]; then
  echo "ERROR: ${REQUIREMENTS} not found." >&2
  echo "       Run this script from the repository root." >&2
  exit 1
fi

# ── Build ──────────────────────────────────────────────────────────────────────
echo "──────────────────────────────────────────"
echo " Building vendor packages"
echo " → source : ${REQUIREMENTS}"
echo " → target : ${VENDOR_DIR}/"
echo " → platform: ${PLATFORM} / Python ${PYTHON_VERSION}"
echo "──────────────────────────────────────────"

rm -rf "${VENDOR_DIR}"
mkdir -p "${VENDOR_DIR}"

pip install \
  -r "${REQUIREMENTS}" \
  -t "${VENDOR_DIR}" \
  --platform "${PLATFORM}" \
  --implementation cp \
  --python-version "${PYTHON_VERSION}" \
  --only-binary :all: \
  --quiet

ENTRY_COUNT=$(find "${VENDOR_DIR}" -maxdepth 1 -mindepth 1 | wc -l)
DISK_USAGE=$(du -sh "${VENDOR_DIR}" | cut -f1)

echo "──────────────────────────────────────────"
echo "✓ Vendor build complete"
echo "  ${ENTRY_COUNT} top-level entries  |  ${DISK_USAGE} on disk"
echo "──────────────────────────────────────────"
