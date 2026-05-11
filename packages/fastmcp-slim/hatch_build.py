from __future__ import annotations

from hatchling.metadata.plugin.interface import MetadataHookInterface


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        version = metadata["version"]
        metadata["dependencies"] = [
            dependency.replace("{{ version }}", version)
            for dependency in self.config["dependencies"]
        ]
