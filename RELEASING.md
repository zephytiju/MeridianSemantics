<!-- SPDX-License-Identifier: Apache-2.0 -->

# Releasing

1. Update the version, changelog, compatibility/public API ledgers, and contracts
   together; run every README gate.
2. Merge a green pull request to protected `main`.
3. Create and push an annotated `vX.Y.Z` tag on that merge commit.
4. The release workflow verifies the tag/version, reruns quality and conformance,
   builds twice with a fixed timestamp, compares artifacts byte-for-byte, verifies
   contents, emits an SPDX SBOM, creates provenance attestations, and publishes a
   GitHub release.
5. PyPI publication uses GitHub OIDC only when repository variable
   `PYPI_TRUSTED_PUBLISHING_ENABLED=true` and the `pypi` environment/publisher are
   owner-configured. Never bypass namespace ownership, MFA, or trusted publishing.

Subsequent releases use the same CI path; do not manually replace release assets.
