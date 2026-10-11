import importlib.util
import logging
import shutil
from typing import List, Tuple, Dict, Any, Optional
import pymupdf

from app.core.config import OCRConfig
from .models import (
    ExtractedTextRecord,
    NativeTextStats,
    OCRDecision,
    PageTextResult
)

logger = logging.getLogger("stage1_pipeline.ocr.extractor")


class TextExtractor:
    """Extracts text records from PDF pages prioritizing native vector text with honest OCR fallback."""

    def __init__(self, config: Optional[OCRConfig] = None):
        self.config = config or OCRConfig()
        self.ocr_backend_available, self.ocr_backend_name = self._detect_ocr_backend()

    @staticmethod
    def _detect_ocr_backend() -> Tuple[bool, Optional[str]]:
        """Detects whether a local OCR engine (such as pytesseract + tesseract binary) is available."""
        has_pytesseract = importlib.util.find_spec("pytesseract") is not None
        has_tesseract_bin = shutil.which("tesseract") is not None
        if has_pytesseract and has_tesseract_bin:
            return True, "tesseract"
        return False, None

    def extract_page_text(self, page: pymupdf.Page, page_index: int) -> PageTextResult:
        """Extracts text records for a single page with coordinates in canonical visual space.
        
        Supports both native vector font text streams (TrueType/Type1) and native AutoCAD
        SHX vector text annotations embedded in the PDF.
        """
        rect = page.rect
        width_pt = round(rect.width, 2)
        height_pt = round(rect.height, 2)
        rotation = int(page.rotation)
        rot_mat = page.rotation_matrix
        page_num = page_index + 1

        warnings: List[str] = []
        errors: List[str] = []

        # 1. Native PyMuPDF Text Stream Analysis (TrueType / Type 1 Fonts)
        raw_words = page.get_text("words") or []
        dict_data = page.get_text("dict") or {}
        dict_blocks = dict_data.get("blocks", [])

        # Build font metadata lookup: (block_idx, line_idx) -> span_info
        span_lookup: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for b_idx, block in enumerate(dict_blocks):
            for l_idx, line in enumerate(block.get("lines", [])):
                spans = line.get("spans", [])
                if spans:
                    first_span = spans[0]
                    span_lookup[(b_idx, l_idx)] = {
                        "font": first_span.get("font"),
                        "size": round(first_span.get("size", 0.0), 2),
                        "flags": first_span.get("flags"),
                        "dir": [round(d, 4) for d in line.get("dir", [1.0, 0.0])]
                    }

        all_candidates: List[Dict[str, Any]] = []

        for w_idx, w in enumerate(raw_words):
            raw_rect = pymupdf.Rect(w[0], w[1], w[2], w[3])
            vis_rect = raw_rect * rot_mat

            cx0 = max(0.0, min(width_pt, round(vis_rect.x0, 2)))
            cy0 = max(0.0, min(height_pt, round(vis_rect.y0, 2)))
            cx1 = max(cx0, min(width_pt, round(vis_rect.x1, 2)))
            cy1 = max(cy0, min(height_pt, round(vis_rect.y1, 2)))

            word_text = str(w[4]).strip()
            if not word_text:
                continue

            b_no = int(w[5])
            l_no = int(w[6])
            w_no = int(w[7]) if len(w) > 7 else w_idx
            meta = span_lookup.get((b_no, l_no), {})

            all_candidates.append({
                "text": word_text,
                "bbox": [cx0, cy0, cx1, cy1],
                "granularity": "word",
                "source": "native_pdf",
                "font_name": meta.get("font") or "StandardVectorFont",
                "font_size": meta.get("size"),
                "flags": meta.get("flags"),
                "direction": meta.get("dir"),
                "block_index": b_no,
                "line_index": l_no,
                "word_index": w_no,
                "is_shx": False
            })

        # 2. Native AutoCAD SHX Vector Text Extraction (Annotations)
        raw_shx_annots: List[Dict[str, Any]] = []
        try:
            for annot in page.annots():
                info = annot.info or {}
                title = info.get("title", "")
                subject = info.get("subject", "")
                content = (info.get("content") or "").strip()

                if ("AutoCAD SHX Text" in title or "AutoCAD SHX Text" in subject or title == "AutoCAD SHX Text") and content:
                    vis_rect = annot.rect * rot_mat
                    x0 = max(0.0, min(width_pt, round(vis_rect.x0, 2)))
                    y0 = max(0.0, min(height_pt, round(vis_rect.y0, 2)))
                    x1 = max(x0, min(width_pt, round(vis_rect.x1, 2)))
                    y1 = max(y0, min(height_pt, round(vis_rect.y1, 2)))

                    raw_shx_annots.append({
                        "text": content,
                        "rect": pymupdf.Rect(x0, y0, x1, y1)
                    })
        except Exception as annot_err:
            warnings.append(f"Notice: Failed to enumerate PDF annotations: {str(annot_err)}")

        # Deduplicate & consolidate contiguous/overlapping multi-stroke SHX fragments
        used_shx = set()
        consolidated_shx: List[Dict[str, Any]] = []

        for i, a in enumerate(raw_shx_annots):
            if i in used_shx:
                continue
            cur_text = a["text"]
            cur_rect = a["rect"]
            used_shx.add(i)

            for j in range(i + 1, len(raw_shx_annots)):
                if j in used_shx:
                    continue
                b = raw_shx_annots[j]
                if b["text"] == cur_text:
                    # If same text and proximity <= 6 pt or overlapping, merge into unified bounding box
                    expanded = pymupdf.Rect(cur_rect.x0 - 6, cur_rect.y0 - 6, cur_rect.x1 + 6, cur_rect.y1 + 6)
                    if expanded.intersects(b["rect"]):
                        cur_rect = cur_rect | b["rect"]
                        used_shx.add(j)

            consolidated_shx.append({
                "text": cur_text,
                "rect": cur_rect
            })

        for s_idx, s in enumerate(consolidated_shx):
            bx0 = round(s["rect"].x0, 2)
            by0 = round(s["rect"].y0, 2)
            bx1 = round(s["rect"].x1, 2)
            by1 = round(s["rect"].y1, 2)
            f_size = round(max(by1 - by0, 6.0), 2)

            all_candidates.append({
                "text": s["text"],
                "bbox": [bx0, by0, bx1, by1],
                "granularity": "word" if " " not in s["text"] else "line",
                "source": "native_pdf",
                "font_name": "AutoCAD SHX Text",
                "font_size": f_size,
                "flags": 0,
                "direction": [1.0, 0.0] if (bx1 - bx0) >= (by1 - by0) else [0.0, 1.0],
                "block_index": 1000 + (s_idx // 10),
                "line_index": s_idx % 10,
                "word_index": 0,
                "is_shx": True
            })

        # 3. Sort records in reading order (top-to-bottom, left-to-right)
        all_candidates.sort(key=lambda c: (round(c["bbox"][1] / 12) * 12, c["bbox"][0]))

        records: List[ExtractedTextRecord] = []
        for idx, item in enumerate(all_candidates):
            rec = ExtractedTextRecord(
                record_id=f"p{page_num}_t{idx + 1:04d}",
                text=item["text"],
                bounding_box=item["bbox"],
                granularity=item["granularity"],
                source="native_pdf",
                confidence=None,  # Honest engineering: native vector text has no synthetic confidence
                font_name=item["font_name"],
                font_size=item["font_size"],
                flags=item["flags"],
                direction=item["direction"],
                block_index=item["block_index"],
                line_index=item["line_index"],
                word_index=item["word_index"]
            )
            records.append(rec)

        # 4. Native Statistics & Adequacy
        total_words = len(records)
        total_chars = sum(len(r.text) for r in records)
        total_blocks = len(dict_blocks) + (len(consolidated_shx) // 10)

        is_sufficient = (
            total_words >= self.config.min_native_words_threshold and
            total_chars >= self.config.min_native_chars_threshold
        )

        native_stats = NativeTextStats(
            character_count=total_chars,
            word_count=total_words,
            block_count=total_blocks,
            is_sufficient=is_sufficient
        )

        # 5. Determine OCR Execution or Skip
        if is_sufficient and not self.config.force_ocr:
            ocr_decision = OCRDecision(
                ocr_attempted=False,
                ocr_skipped=True,
                reason=(
                    f"Native vector text is fully sufficient ({total_words} records extracted across "
                    f"native font streams and AutoCAD SHX text annotations). "
                    f"OCR skipped to preserve native vector precision."
                ),
                backend_available=self.ocr_backend_available,
                backend_name=self.ocr_backend_name
            )
            logger.info("Page %d: %s", page_num, ocr_decision.reason)
        else:
            if self.ocr_backend_available:
                ocr_decision = OCRDecision(
                    ocr_attempted=True,
                    ocr_skipped=False,
                    reason=f"Native text insufficient ({total_words} records). Executing OCR fallback via {self.ocr_backend_name}.",
                    backend_available=True,
                    backend_name=self.ocr_backend_name
                )
            else:
                ocr_decision = OCRDecision(
                    ocr_attempted=True,
                    ocr_skipped=False,
                    reason=(
                        f"Native text insufficient or forced, but no local OCR engine (pytesseract/tesseract) "
                        f"is installed in the environment. Falling back to native vector extraction."
                    ),
                    backend_available=False,
                    backend_name=None
                )
                warnings.append(ocr_decision.reason)
                logger.warning("Page %d: %s", page_num, ocr_decision.reason)

        status = "WARNING" if warnings else "SUCCESS"
        logger.info(
            "Page %d: Extracted %d total text records (Status: %s)",
            page_num, len(records), status
        )

        return PageTextResult(
            page_index=page_index,
            page_number=page_num,
            width_pt=width_pt,
            height_pt=height_pt,
            rotation=rotation,
            native_stats=native_stats,
            ocr_decision=ocr_decision,
            records_count=len(records),
            records=records,
            status=status,
            warnings=warnings,
            errors=errors
        )
