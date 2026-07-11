from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError as PydanticValidationError

from edgeapt.errors import ValidationError
from edgeapt.templates.base import SourceDocument, SourceTemplate
from edgeapt.templates.registry import DEFAULT_TEMPLATES, TemplateRegistry
from edgeapt.util import relative_to_root


def load_source_documents(
    sources_dir: Path,
    *,
    root: Path,
    templates: TemplateRegistry = DEFAULT_TEMPLATES,
) -> tuple[SourceDocument, ...]:
    if not sources_dir.exists():
        return ()
    documents = tuple(
        load_source_document(path, root=root, templates=templates)
        for path in sorted(sources_dir.glob("*.yaml"))
        if path.is_file()
    )
    counts = Counter(document.source.id for document in documents)
    duplicates = sorted(source_id for source_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValidationError(f"Duplicate source id(s): {', '.join(duplicates)}")
    return documents


def load_source_document(
    path: Path,
    *,
    root: Path,
    templates: TemplateRegistry = DEFAULT_TEMPLATES,
) -> SourceDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{path}: source must be a YAML mapping")
    data = cast(dict[str, Any], raw)
    template_id = data.get("template")
    if not isinstance(template_id, str) or template_id == "":
        raise ValidationError(f"{path}: template must be a non-empty string")
    template_type = templates.resolve(template_id)
    try:
        source: SourceTemplate = template_type.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(f"{path}: {exc}") from exc
    return SourceDocument(
        source=source,
        source_file=relative_to_root(path, root),
    )
