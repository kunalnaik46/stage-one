import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple
import pymupdf

from .models import (
    DocumentMetadata,
    PageMetadata,
    PageBox,
    PageDimensions
)

logger = logging.getLogger("stage1_pipeline.pdf_reader.extractor")


class PDFExtractor:
    """Robust extractor for PDF document metadata, page geometry, and native text stats."""

    def __init__(self, max_text_preview_chars: int = 300):
        self.max_text_preview_chars = max_text_preview_chars

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        """Computes SHA-256 hash using chunked streaming."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()

    def extract(self, file_path: Path, password: Optional[str] = None) -> DocumentMetadata:
        """Extracts complete document metadata and per-page metrics from a PDF file."""
        resolved_path = file_path.resolve()
        now_utc = datetime.now(timezone.utc).isoformat()

        if not resolved_path.exists():
            error_msg = f"File does not exist: {resolved_path}"
            logger.error(error_msg)
            return DocumentMetadata(
                file_path=str(resolved_path),
                file_name=resolved_path.name,
                file_size_bytes=0,
                file_hash_sha256="",
                total_pages=0,
                processing_status="FAILED",
                errors=[error_msg],
                processed_at_utc=now_utc
            )

        if not resolved_path.is_file():
            error_msg = f"Path is not a regular file: {resolved_path}"
            logger.error(error_msg)
            return DocumentMetadata(
                file_path=str(resolved_path),
                file_name=resolved_path.name,
                file_size_bytes=0,
                file_hash_sha256="",
                total_pages=0,
                processing_status="FAILED",
                errors=[error_msg],
                processed_at_utc=now_utc
            )

        file_size = resolved_path.stat().st_size
        if file_size == 0:
            error_msg = f"File is empty (0 bytes): {resolved_path}"
            logger.error(error_msg)
            return DocumentMetadata(
                file_path=str(resolved_path),
                file_name=resolved_path.name,
                file_size_bytes=0,
                file_hash_sha256="",
                total_pages=0,
                processing_status="FAILED",
                errors=[error_msg],
                processed_at_utc=now_utc
            )

        file_hash = self.compute_sha256(resolved_path)

        doc = None
        try:
            doc = pymupdf.open(str(resolved_path))
        except Exception as e:
            error_msg = f"Failed to open PDF document: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return DocumentMetadata(
                file_path=str(resolved_path),
                file_name=resolved_path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                total_pages=0,
                processing_status="FAILED",
                errors=[error_msg],
                processed_at_utc=now_utc
            )

        try:
            is_encrypted = doc.is_encrypted
            if is_encrypted:
                logger.warning("PDF document is encrypted: %s", resolved_path.name)
                authenticated = False
                if password is not None:
                    auth_res = doc.authenticate(password)
                    authenticated = bool(auth_res > 0)

                if not authenticated:
                    error_msg = "PDF is password-protected or authentication failed."
                    logger.error(error_msg)
                    return DocumentMetadata(
                        file_path=str(resolved_path),
                        file_name=resolved_path.name,
                        file_size_bytes=file_size,
                        file_hash_sha256=file_hash,
                        is_encrypted=True,
                        total_pages=0,
                        processing_status="FAILED",
                        errors=[error_msg],
                        processed_at_utc=now_utc
                    )

            # Extract raw document metadata
            raw_meta = doc.metadata or {}
            total_pages = doc.page_count
            logger.info("Opened PDF '%s': %d page(s) discovered dynamically", resolved_path.name, total_pages)

            page_records: List[PageMetadata] = []
            doc_warnings: List[str] = []
            doc_errors: List[str] = []

            for page_idx in range(total_pages):
                try:
                    page = doc[page_idx]
                    page_meta, page_warnings = self._extract_page(page, page_idx)
                    page_records.append(page_meta)
                    doc_warnings.extend(page_warnings)
                except Exception as page_err:
                    err_text = f"Error processing page {page_idx + 1}: {str(page_err)}"
                    logger.error(err_text, exc_info=True)
                    doc_errors.append(err_text)
                    page_records.append(PageMetadata(
                        page_index=page_idx,
                        page_number=page_idx + 1,
                        visual_dimensions=PageDimensions(
                            width_pt=0.0,
                            height_pt=0.0,
                            orientation="unknown",
                            aspect_ratio=0.0
                        ),
                        mediabox=PageBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0, width=0.0, height=0.0),
                        cropbox=PageBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0, width=0.0, height=0.0),
                        rotation=0,
                        has_native_text=False,
                        native_text_char_count=0,
                        native_text_word_count=0,
                        status="FAILED",
                        error_message=str(page_err)
                    ))

            # Determine overall document processing status
            if doc_errors:
                proc_status = "PARTIAL" if any(p.status == "SUCCESS" for p in page_records) else "FAILED"
            else:
                proc_status = "SUCCESS"

            return DocumentMetadata(
                file_path=str(resolved_path),
                file_name=resolved_path.name,
                file_size_bytes=file_size,
                file_hash_sha256=file_hash,
                pdf_format=raw_meta.get("format"),
                title=raw_meta.get("title") or None,
                author=raw_meta.get("author") or None,
                subject=raw_meta.get("subject") or None,
                keywords=raw_meta.get("keywords") or None,
                creator=raw_meta.get("creator") or None,
                producer=raw_meta.get("producer") or None,
                creation_date=raw_meta.get("creationDate") or None,
                mod_date=raw_meta.get("modDate") or None,
                is_encrypted=is_encrypted,
                total_pages=total_pages,
                pages=page_records,
                processing_status=proc_status,
                errors=doc_errors,
                warnings=doc_warnings,
                processed_at_utc=now_utc
            )
        finally:
            if doc is not None:
                doc.close()

    def _extract_page(self, page: pymupdf.Page, page_idx: int) -> Tuple[PageMetadata, List[str]]:
        """Extracts geometry, rotation, drawing stats, and native text for a single page."""
        warnings: List[str] = []

        rect = page.rect
        width_pt = round(rect.width, 2)
        height_pt = round(rect.height, 2)
        aspect_ratio = round(width_pt / height_pt, 4) if height_pt > 0 else 0.0

        if width_pt > height_pt + 0.5:
            orientation = "landscape"
        elif height_pt > width_pt + 0.5:
            orientation = "portrait"
        else:
            orientation = "square"

        visual_dims = PageDimensions(
            width_pt=width_pt,
            height_pt=height_pt,
            orientation=orientation,
            aspect_ratio=aspect_ratio
        )

        mbox = page.mediabox
        mediabox = PageBox(
            x0=round(mbox.x0, 2),
            y0=round(mbox.y0, 2),
            x1=round(mbox.x1, 2),
            y1=round(mbox.y1, 2),
            width=round(mbox.width, 2),
            height=round(mbox.height, 2)
        )

        cbox = page.cropbox
        cropbox = PageBox(
            x0=round(cbox.x0, 2),
            y0=round(cbox.y0, 2),
            x1=round(cbox.x1, 2),
            y1=round(cbox.y1, 2),
            width=round(cbox.width, 2),
            height=round(cbox.height, 2)
        )

        rotation = int(page.rotation)
        if rotation != 0:
            warnings.append(f"Page {page_idx + 1} has non-zero rotation: {rotation} degrees")

        # Vector drawings count
        try:
            drawings = page.get_drawings()
            drawings_count = len(drawings)
        except Exception as dwg_err:
            logger.warning("Could not count vector drawings on page %d: %s", page_idx + 1, dwg_err)
            drawings_count = 0
            warnings.append(f"Vector drawings count failed: {dwg_err}")

        # Embedded images count
        try:
            images = page.get_images()
            images_count = len(images)
        except Exception as img_err:
            logger.warning("Could not count images on page %d: %s", page_idx + 1, img_err)
            images_count = 0
            warnings.append(f"Embedded images count failed: {img_err}")

        # Native text extraction
        try:
            raw_text = page.get_text() or ""
            char_count = len(raw_text)
            words = raw_text.split()
            word_count = len(words)
            has_native_text = bool(raw_text.strip())

            # Create clean preview sample
            cleaned_text = " ".join(words)
            if len(cleaned_text) > self.max_text_preview_chars:
                sample = cleaned_text[:self.max_text_preview_chars] + "..."
            else:
                sample = cleaned_text
        except Exception as txt_err:
            logger.warning("Could not extract native text on page %d: %s", page_idx + 1, txt_err)
            has_native_text = False
            char_count = 0
            word_count = 0
            sample = ""
            warnings.append(f"Native text extraction failed: {txt_err}")

        if not has_native_text:
            warnings.append(f"Page {page_idx + 1} contains no detectable native text (may require OCR)")

        status = "WARNING" if warnings else "SUCCESS"

        metadata = PageMetadata(
            page_index=page_idx,
            page_number=page_idx + 1,
            visual_dimensions=visual_dims,
            mediabox=mediabox,
            cropbox=cropbox,
            rotation=rotation,
            has_native_text=has_native_text,
            native_text_char_count=char_count,
            native_text_word_count=word_count,
            text_sample=sample,
            vector_drawings_count=drawings_count,
            raster_images_count=images_count,
            status=status,
            warnings=warnings
        )

        return metadata, warnings
