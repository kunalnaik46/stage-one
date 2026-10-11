import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Union
from pydantic import BaseModel

logger = logging.getLogger("stage1_pipeline.artifact_writer")


class ArtifactWriter:
    """Safely and atomically writes pipeline artifacts to disk."""

    def __init__(self, artifacts_dir: Union[str, Path]):
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, filename: str, data: Union[BaseModel, dict, list]) -> Path:
        """Atomically writes structured data to JSON in the artifacts directory.

        Args:
            filename: Target file name (e.g. 'pages.json').
            data: Data to write (Pydantic model, dictionary, or list).

        Returns:
            The Path to the written artifact file.
        """
        target_path = (self.artifacts_dir / filename).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, BaseModel):
            json_text = data.model_dump_json(indent=2)
        else:
            json_text = json.dumps(data, indent=2, ensure_ascii=False)

        # Atomic write pattern using tempfile in target directory
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f".tmp_{filename}_",
            dir=str(target_path.parent)
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(json_text)
            os.replace(temp_path, str(target_path))
            file_size = target_path.stat().st_size
            logger.info("Successfully wrote artifact '%s' (%d bytes) to %s", filename, file_size, target_path)
            return target_path
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            logger.error("Failed to write artifact '%s': %s", filename, str(e), exc_info=True)
            raise
