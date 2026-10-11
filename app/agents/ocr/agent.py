import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List
import pymupdf

from app.core.artifact_writer import ArtifactWriter
from app.core.config import AppConfig
from app.agents.pdf_reader.extractor import PDFExtractor
from .extractor import TextExtractor
from .models import TextExtractionManifest, PageTextResult

logger = logging.getLogger("stage1_pipeline.ocr.agent")


class OCRAgent:
    """Agent 4: OCR / Text Extraction Agent.

    Responsibilities:
    - Primary native PDF text extraction using PyMuPDF.
    - Word-level and span-level bounding box localization in visual PDF page space.
    - Preservation of native fonts, sizes, and writing directions.
    - Honest assessment of native text adequacy and OCR requirement.
    - Fallback OCR behavior reporting.
    - Generation and atomic persistence of outputs/artifacts/ocr.json.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        artifacts_dir = self.config.resolve_path(self.config.paths.artifacts_dir)
        self.artifact_writer = ArtifactWriter(artifacts_dir)
        self.extractor = TextExtractor(config=self.config.ocr)

    def run(self, pdf_path: Optional[Path] = None, password: Optional[str] = None) -> Tuple[TextExtractionManifest, Path]:
        """Executes Text Extraction Agent workflow and outputs ocr.json.

        Args:
            pdf_path: Optional path to PDF file. If None, uses configured input_pdf_path.
            password: Optional password for encrypted documents.

        Returns:
            Tuple of (TextExtractionManifest, Path to ocr.json).
        """
        if pdf_path is None:
            resolved_pdf_path = self.config.resolve_path(self.config.paths.input_pdf_path)
        else:
            resolved_pdf_path = Path(pdf_path).resolve()

        now_utc = datetime.now(timezone.utc).isoformat()
        logger.info("OCRAgent: Beginning text extraction for %s", resolved_pdf_path)

        if not resolved_pdf_path.exists() or not resolved_pdf_path.is_file():
            err_msg = f"Target PDF does not exist or is not a file: {resolved_pdf_path}"
            logger.error(err_msg)
            manifest = TextExtractionManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256="",
                total_pages=0,
                processing_status="FAILED",
                extraction_config=self.config.ocr.model_dump(),
                extraction_method_summary="Aborted: file missing",
                total_records_count=0,
                errors=[err_msg],
                extracted_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("ocr.json", manifest)
            return manifest, artifact_path

        file_hash = PDFExtractor.compute_sha256(resolved_pdf_path)

        doc = None
        try:
            doc = pymupdf.open(str(resolved_pdf_path))
        except Exception as e:
            err_msg = f"Failed to open PDF document: {str(e)}"
            logger.error(err_msg, exc_info=True)
            manifest = TextExtractionManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=0,
                processing_status="FAILED",
                extraction_config=self.config.ocr.model_dump(),
                extraction_method_summary=f"Aborted: {str(e)}",
                total_records_count=0,
                errors=[err_msg],
                extracted_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("ocr.json", manifest)
            return manifest, artifact_path

        try:
            if doc.is_encrypted:
                auth = False
                if password:
                    auth = bool(doc.authenticate(password) > 0)
                if not auth:
                    err_msg = "PDF is password-protected or authentication failed."
                    logger.error(err_msg)
                    manifest = TextExtractionManifest(
                        file_path=str(resolved_pdf_path),
                        file_name=resolved_pdf_path.name,
                        file_hash_sha256=file_hash,
                        total_pages=0,
                        processing_status="FAILED",
                        extraction_config=self.config.ocr.model_dump(),
                        extraction_method_summary="Aborted: password protected",
                        total_records_count=0,
                        errors=[err_msg],
                        extracted_at_utc=now_utc
                    )
                    artifact_path = self.artifact_writer.write_json("ocr.json", manifest)
                    return manifest, artifact_path

            total_pages = doc.page_count
            logger.info("OCRAgent: Extracting text from %d page(s)", total_pages)

            page_results: List[PageTextResult] = []
            doc_warnings: List[str] = []
            doc_errors: List[str] = []

            for page_idx in range(total_pages):
                page = doc[page_idx]
                try:
                    p_res = self.extractor.extract_page_text(page, page_idx)
                    page_results.append(p_res)
                    if p_res.warnings:
                        doc_warnings.extend(p_res.warnings)
                except Exception as p_err:
                    err_text = f"Error extracting text on page {page_idx + 1}: {str(p_err)}"
                    logger.error(err_text, exc_info=True)
                    doc_errors.append(err_text)
                    page_results.append(PageTextResult(
                        page_index=page_idx,
                        page_number=page_idx + 1,
                        width_pt=round(page.rect.width, 2),
                        height_pt=round(page.rect.height, 2),
                        rotation=int(page.rotation),
                        native_stats={"character_count": 0, "word_count": 0, "block_count": 0, "is_sufficient": False},
                        ocr_decision={"ocr_attempted": False, "ocr_skipped": True, "reason": err_text, "backend_available": False},
                        records_count=0,
                        records=[],
                        status="FAILED",
                        errors=[err_text]
                    ))

            total_records = sum(p.records_count for p in page_results)
            proc_status = "FAILED" if (doc_errors and not page_results) else ("PARTIAL" if doc_errors else "SUCCESS")

            # Method summary
            all_skipped = all(p.ocr_decision.ocr_skipped for p in page_results)
            if all_skipped:
                method_summary = "Native vector text extraction via PyMuPDF (OCR skipped: native stream sufficient)"
            else:
                method_summary = "Mixed extraction (Native vector text + OCR fallback)"

            manifest = TextExtractionManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=total_pages,
                processing_status=proc_status,
                extraction_config=self.config.ocr.model_dump(),
                extraction_method_summary=method_summary,
                total_records_count=total_records,
                pages=page_results,
                errors=doc_errors,
                warnings=doc_warnings,
                extracted_at_utc=now_utc
            )

            artifact_path = self.artifact_writer.write_json("ocr.json", manifest)
            logger.info(
                "OCRAgent: Completed extraction (%d records extracted). Manifest: %s",
                total_records, artifact_path
            )
            return manifest, artifact_path

        finally:
            if doc is not None:
                doc.close()
