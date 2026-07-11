from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from edgeapt.errors import ValidationError
from edgeapt.templates.base import SourceTemplate
from edgeapt.templates.deb_upstream_v1 import DebUpstreamV1
from edgeapt.templates.prebuilt_archive_v1 import PrebuiltArchiveV1
from edgeapt.templates.single_binary_v1 import SingleBinaryV1


class TemplateRegistry:
    def __init__(self, template_types: Iterable[type[SourceTemplate]]) -> None:
        registered: dict[str, type[SourceTemplate]] = {}
        for template_type in template_types:
            template_id = template_type.template_id
            if template_id in registered:
                raise ValueError(f"duplicate template id: {template_id}")
            registered[template_id] = template_type
        self._templates: Mapping[str, type[SourceTemplate]] = MappingProxyType(
            registered
        )

    def resolve(self, template_id: str) -> type[SourceTemplate]:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise ValidationError(f"unsupported template: {template_id}") from exc

    @property
    def template_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._templates))


DEFAULT_TEMPLATES = TemplateRegistry(
    [SingleBinaryV1, DebUpstreamV1, PrebuiltArchiveV1]
)
