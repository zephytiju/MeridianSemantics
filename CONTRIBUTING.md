<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Changes must preserve the one-repository/one-distribution boundary, Apache-2.0
headers and metadata, exact Core pin, deterministic wire contracts, and Catalog
authority boundaries. Public contract changes require an approved authoritative
design update before implementation.

Install `.[test]`, run the commands in the README, and include tests and contract
fixtures for behavioral changes. Pull requests must pass all supported Python
versions and the reproducible-package job. Do not commit generated artifacts,
credentials, provider endpoints, Engine-specific options, or cross-repository
source copies.
