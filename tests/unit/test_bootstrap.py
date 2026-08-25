# SPDX-License-Identifier: Apache-2.0

from meridian_storage.semantics import __version__


def test_public_baseline_imports() -> None:
    assert __version__ == "1.0.0"
