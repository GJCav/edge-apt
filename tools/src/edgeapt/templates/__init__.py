from edgeapt.templates.deb_upstream_v1 import DebUpstreamV1
from edgeapt.templates.prebuilt_archive_v1 import PrebuiltArchiveV1
from edgeapt.templates.registry import DEFAULT_TEMPLATES, TemplateRegistry
from edgeapt.templates.single_binary_v1 import SingleBinaryV1
from edgeapt.templates.single_binary_v1_1 import SingleBinaryV11

__all__ = [
    "DEFAULT_TEMPLATES",
    "DebUpstreamV1",
    "PrebuiltArchiveV1",
    "SingleBinaryV1",
    "SingleBinaryV11",
    "TemplateRegistry",
]
