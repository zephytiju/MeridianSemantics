# SPDX-License-Identifier: Apache-2.0
"""The public Schema API accepts stores independent of the reference class."""

from __future__ import annotations

import pytest

from meridian_storage.semantics import (
    IncompatibleSchema,
    InMemoryMetadataRepository,
    MetadataRepository,
    PublishedSchema,
    PublishResult,
    RegistrySnapshot,
    SchemaAPI,
    SchemaDocument,
    SchemaReference,
    SchemaRepository,
    SemanticsSchemaProvider,
)
from tests.support import FINGERPRINT, NOW, schema_definition


class ExternalSchemaStore:
    """A composition-only schema store, intentionally without Collection methods."""

    def __init__(self) -> None:
        self.store = InMemoryMetadataRepository(clock=lambda: NOW)

    @property
    def revision(self) -> int:
        return self.store.revision

    def publish_schema(
        self,
        document: SchemaDocument,
        *,
        expected_revision: int | None = None,
        allow_breaking: bool = False,
    ) -> PublishResult:
        return self.store.publish_schema(
            document, expected_revision=expected_revision, allow_breaking=allow_breaking
        )

    def get_schema(
        self, reference: SchemaReference, *, include_deprecated: bool = False
    ) -> PublishedSchema:
        return self.store.get_schema(reference, include_deprecated=include_deprecated)

    def list_schema_versions(
        self, reference: SchemaReference, *, include_deprecated: bool = True
    ) -> tuple[PublishedSchema, ...]:
        return self.store.list_schema_versions(reference, include_deprecated=include_deprecated)

    def deprecate_schema(
        self, reference: SchemaReference, *, expected_revision: int | None = None
    ) -> PublishedSchema:
        return self.store.deprecate_schema(reference, expected_revision=expected_revision)

    def snapshot(self) -> RegistrySnapshot:
        return self.store.snapshot()


def test_injected_schema_only_store_and_provider() -> None:
    repository: SchemaRepository = ExternalSchemaStore()
    assert isinstance(repository, SchemaRepository)
    assert not isinstance(repository, MetadataRepository)
    assert isinstance(InMemoryMetadataRepository(), MetadataRepository)
    api = SchemaAPI(repository)
    result = api.publish(
        namespace="example", name="customer", version="1.0.0", definition=schema_definition()
    )
    assert api.registry_revision == 1
    assert api.read(namespace="example", name="customer") == result.publication
    assert api.versions(namespace="example", name="customer") == (result.publication,)
    live = SemanticsSchemaProvider(repository).load_live()
    assert live.schemas == (result.publication.document.to_core_definition(),)
    assert live.resources == ()
    assert live.extensions["registryFingerprint"] == repository.snapshot().fingerprint
    assert api.deprecate(namespace="example", name="customer", version="1.0.0").deprecated_at


def test_exact_schema_read_checks_its_own_fingerprint() -> None:
    api = SchemaAPI(ExternalSchemaStore())
    result = api.publish(
        namespace="example", name="customer", version="1.0.0", definition=schema_definition()
    )
    assert (
        api.get(
            namespace="example",
            name="customer",
            version="1.0.0",
            expected_fingerprint=result.publication.fingerprint,
        )
        == result.publication
    )
    with pytest.raises(IncompatibleSchema) as error:
        api.read(
            namespace="example", name="customer", version="1.0.0", expected_fingerprint=FINGERPRINT
        )
    assert error.value.requirement == "schema.fingerprint"
    with pytest.raises(ValueError, match="exact version"):
        api.read(namespace="example", name="customer", expected_fingerprint=FINGERPRINT)
