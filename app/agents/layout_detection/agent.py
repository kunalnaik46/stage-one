import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List
import pymupdf

from app.core.artifact_writer import ArtifactWriter
from app.core.config import AppConfig
from app.agents.pdf_reader.extractor import PDFExtractor
from .detector import LayoutDetector
from .models import LayoutManifest, PageLayoutResult

logger = logging.getLogger("stage1_pipeline.layout_detection.agent")


class LayoutDetectionAgent:
    """Agent 3: Layout Detection Agent.

    Responsibilities:
    - Analyzes document layout without fixed coordinate or template assumptions.
    - Uses native PDF text positions, vector geometry, and page structure as primary evidence.
    - Localizes drawing regions, title blocks, revision blocks, tables, general notes, headers, and footers.
    - Generates and persists outputs/artifacts/layout.json with documented evidence and confidence.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        artifacts_dir = self.config.resolve_path(self.config.paths.artifacts_dir)
        self.artifact_writer = ArtifactWriter(artifacts_dir)
        self.detector = LayoutDetector(config=self.config.layout_detection)

    def run(self, pdf_path: Optional[Path] = None, password: Optional[str] = None) -> Tuple[LayoutManifest, Path]:
        """Executes Layout Detection Agent workflow and outputs layout.json.

        Args:
            pdf_path: Optional path to PDF file. If None, uses configured input_pdf_path.
            password: Optional password for encrypted documents.

        Returns:
            Tuple of (LayoutManifest, Path to layout.json).
        """
        if pdf_path is None:
            resolved_pdf_path = self.config.resolve_path(self.config.paths.input_pdf_path)
        else:
            resolved_pdf_path = Path(pdf_path).resolve()

        now_utc = datetime.now(timezone.utc).isoformat()
        logger.info("LayoutDetectionAgent: Beginning layout analysis for %s", resolved_pdf_path)

        if not resolved_pdf_path.exists() or not resolved_pdf_path.is_file():
            err_msg = f"Target PDF does not exist or is not a file: {resolved_pdf_path}"
            logger.error(err_msg)
            manifest = LayoutManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256="",
                total_pages=0,
                detector_config=self.config.layout_detection.model_dump(),
                processing_status="FAILED",
                errors=[err_msg],
                detected_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("layout.json", manifest)
            return manifest, artifact_path

        file_hash = PDFExtractor.compute_sha256(resolved_pdf_path)

        doc = None
        try:
            doc = pymupdf.open(str(resolved_pdf_path))
        except Exception as e:
            err_msg = f"Failed to open PDF document: {str(e)}"
            logger.error(err_msg, exc_info=True)
            manifest = LayoutManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=0,
                detector_config=self.config.layout_detection.model_dump(),
                processing_status="FAILED",
                errors=[err_msg],
                detected_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("layout.json", manifest)
            return manifest, artifact_path

        try:
            if doc.is_encrypted:
                auth = False
                if password:
                    auth = bool(doc.authenticate(password) > 0)
                if not auth:
                    err_msg = "PDF is password-protected or authentication failed."
                    logger.error(err_msg)
                    manifest = LayoutManifest(
                        file_path=str(resolved_pdf_path),
                        file_name=resolved_pdf_path.name,
                        file_hash_sha256=file_hash,
                        total_pages=0,
                        detector_config=self.config.layout_detection.model_dump(),
                        processing_status="FAILED",
                        errors=[err_msg],
                        detected_at_utc=now_utc
                    )
                    artifact_path = self.artifact_writer.write_json("layout.json", manifest)
                    return manifest, artifact_path

            total_pages = doc.page_count
            logger.info("LayoutDetectionAgent: Processing layout on %d page(s)", total_pages)

            page_layouts: List[PageLayoutResult] = []
            doc_warnings: List[str] = []
            doc_errors: List[str] = []

            for page_idx in range(total_pages):
                page = doc[page_idx]
                try:
                    regions = self.detector.detect_page_layout(page, page_idx)
                    page_layout = PageLayoutResult(
                        page_index=page_idx,
                        page_number=page_idx + 1,
                        width_pt=round(page.rect.width, 2),
                        height_pt=round(page.rect.height, 2),
                        rotation=int(page.rotation),
                        status="SUCCESS",
                        regions_count=len(regions),
                        regions=regions
                    )
                    page_layouts.append(page_layout)
                except Exception as p_err:
                    err_text = f"Error detecting layout on page {page_idx + 1}: {str(p_err)}"
                    logger.error(err_text, exc_info=True)
                    doc_errors.append(err_text)
                    page_layouts.append(PageLayoutResult(
                        page_index=page_idx,
                        page_number=page_idx + 1,
                        width_pt=round(page.rect.width, 2),
                        height_pt=round(page.rect.height, 2),
                        rotation=int(page.rotation),
                        status="FAILED",
                        regions_count=0,
                        regions=[],
                        errors=[err_text]
                    ))

            proc_status = "FAILED" if (doc_errors and not page_layouts) else ("PARTIAL" if doc_errors else "SUCCESS")

            manifest = LayoutManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=total_pages,
                detector_config=self.config.layout_detection.model_dump(),
                pages=page_layouts,
                processing_status=proc_status,
                errors=doc_errors,
                warnings=doc_warnings,
                detected_at_utc=now_utc
            )

            artifact_path = self.artifact_writer.write_json("layout.json", manifest)
            total_regions = sum(p.regions_count for p in page_layouts)
            logger.info(
                "LayoutDetectionAgent: Completed layout analysis (%d total regions detected). Manifest: %s",
                total_regions, artifact_path
            )
            return manifest, artifact_path

        finally:
            if doc is not None:
                doc.close()
