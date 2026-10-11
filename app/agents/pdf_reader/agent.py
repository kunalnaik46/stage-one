import logging
from pathlib import Path
from typing import Optional, Tuple

from app.core.artifact_writer import ArtifactWriter
from app.core.config import AppConfig
from .extractor import PDFExtractor
from .models import DocumentMetadata

logger = logging.getLogger("stage1_pipeline.pdf_reader.agent")


class PDFReaderAgent:
    """Agent 1: PDF Reader Agent.
    
    Responsibilities:
    - Safely open the target engineering PDF document.
    - Dynamically discover page count and layout geometry.
    - Extract document metadata, page dimensions, rotation, crop boxes, and native text presence.
    - Preserve page identity across downstream operations.
    - Generate and persist pages.json artifact.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        artifacts_dir = self.config.resolve_path(self.config.paths.artifacts_dir)
        self.artifact_writer = ArtifactWriter(artifacts_dir)
        self.extractor = PDFExtractor(
            max_text_preview_chars=self.config.pdf_reader.max_text_preview_chars
        )

    def run(self, pdf_path: Optional[Path] = None, password: Optional[str] = None) -> Tuple[DocumentMetadata, Path]:
        """Executes PDF Reader Agent workflow and outputs pages.json.

        Args:
            pdf_path: Optional path to the PDF file. If None, uses configured input_pdf_path.
            password: Optional password for encrypted documents.

        Returns:
            Tuple of (DocumentMetadata, Path to pages.json).
        """
        if pdf_path is None:
            resolved_pdf_path = self.config.resolve_path(self.config.paths.input_pdf_path)
        else:
            resolved_pdf_path = Path(pdf_path).resolve()

        logger.info("PDFReaderAgent: Beginning extraction for %s", resolved_pdf_path)

        metadata = self.extractor.extract(resolved_pdf_path, password=password)

        # Persist artifact as pages.json
        artifact_path = self.artifact_writer.write_json("pages.json", metadata)
        logger.info(
            "PDFReaderAgent: Completed extraction for %s (Status: %s, Total Pages: %d). Artifact: %s",
            resolved_pdf_path.name,
            metadata.processing_status,
            metadata.total_pages,
            artifact_path
        )

        return metadata, artifact_path
