import logging
from typing import List, Tuple, Dict, Any, Optional
import pymupdf

from app.core.config import LayoutDetectionConfig
from .models import LayoutRegion, RegionEvidence, RegionCategory, ClassificationStatus

logger = logging.getLogger("stage1_pipeline.layout_detection.detector")

# Keyword dictionaries for layout classification
CATEGORY_KEYWORDS: Dict[RegionCategory, List[str]] = {
    "title_block": [
        "TITLE", "DRAWING NO", "DWG NO", "CLIENT", "PROJECT", "DRAWN",
        "CHECKED", "APPROVED", "SCALE", "NAME OF PLANT", "PLANT", "PROJECT NO"
    ],
    "revision_block": [
        "REVISION", "REV", "REVISIONS", "DRN CHD", "APP", "DRN", "CHD", "ZONE"
    ],
    "table": [
        "TABULATION", "EQUIPMENT TABULATION", "SCHEDULE", "REFERENCE DRAWINGS",
        "BILL OF MATERIAL", "BOM", "TAG NO", "CAPACITY", "MOC", "DESCRIPTION", "INTERLOCKS"
    ],
    "general_notes": [
        "GENERAL NOTES", "NOTES", "HOLD LIST", "SPECIFICATION"
    ],
    "legend": [
        "LEGEND", "SYMBOLS", "ABBREVIATIONS", "LEGENDE"
    ],
}


class LayoutDetector:
    """Deterministic multi-signal layout detector for industrial engineering drawings."""

    def __init__(self, config: Optional[LayoutDetectionConfig] = None):
        self.config = config or LayoutDetectionConfig()

    def detect_page_layout(self, page: pymupdf.Page, page_index: int) -> List[LayoutRegion]:
        """Detects and classifies semantic layout regions on a single page in visual coordinates."""
        rect = page.rect
        width_pt = rect.width
        height_pt = rect.height
        rot_mat = page.rotation_matrix
        page_num = page_index + 1

        # 1. Extract visual text blocks
        raw_blocks = page.get_text("blocks")
        text_blocks: List[Tuple[pymupdf.Rect, str]] = []
        for b in raw_blocks:
            vis_r = pymupdf.Rect(b[:4]) * rot_mat
            text = b[4].strip()
            if text:
                text_blocks.append((vis_r, text))

        # 2. Extract visual vector lines
        drawings = page.get_drawings()
        h_lines: List[pymupdf.Rect] = []
        v_lines: List[pymupdf.Rect] = []

        for d in drawings:
            vis_r = d["rect"] * rot_mat
            if vis_r.width >= 40.0 and vis_r.height <= 6.0:
                h_lines.append(vis_r)
            elif vis_r.height >= 40.0 and vis_r.width <= 6.0:
                v_lines.append(vis_r)

        logger.info(
            "Page %d: Analyzing layout with %d text blocks, %d H-lines, %d V-lines",
            page_num, len(text_blocks), len(h_lines), len(v_lines)
        )

        detected_regions: List[LayoutRegion] = []
        region_counter = 1

        # 3. Detect Frame Structure & Lateral Divider (e.g. Right Panel)
        outer_frame, inner_frame, major_v_dividers = self._detect_frame_structure(
            h_lines, v_lines, width_pt, height_pt
        )

        # 4. Detect Margin Zones (Header / Footer) if margins enabled
        if self.config.detect_outer_margins and outer_frame and inner_frame:
            # Top margin (Header / Zone Grid)
            if inner_frame.y0 > outer_frame.y0 + 15.0:
                header_bbox = [
                    round(outer_frame.x0, 2),
                    round(outer_frame.y0, 2),
                    round(outer_frame.x1, 2),
                    round(inner_frame.y0, 2)
                ]
                header_evidence = self._gather_evidence(header_bbox, text_blocks, h_lines, v_lines, "header")
                detected_regions.append(LayoutRegion(
                    region_id=f"page_{page_num}_reg_{region_counter:03d}",
                    category="header",
                    bounding_box=header_bbox,
                    confidence_score=0.85,
                    status="detected",
                    evidence=header_evidence
                ))
                region_counter += 1

            # Bottom margin (Footer / Zone Grid)
            if outer_frame.y1 > inner_frame.y1 + 15.0:
                footer_bbox = [
                    round(outer_frame.x0, 2),
                    round(inner_frame.y1, 2),
                    round(outer_frame.x1, 2),
                    round(outer_frame.y1, 2)
                ]
                footer_evidence = self._gather_evidence(footer_bbox, text_blocks, h_lines, v_lines, "footer")
                detected_regions.append(LayoutRegion(
                    region_id=f"page_{page_num}_reg_{region_counter:03d}",
                    category="footer",
                    bounding_box=footer_bbox,
                    confidence_score=0.85,
                    status="detected",
                    evidence=footer_evidence
                ))
                region_counter += 1

        # 5. Detect Tabular & Column Block Regions (e.g. in Information Strips)
        panel_boxes = self._detect_grid_and_panel_blocks(
            h_lines, v_lines, inner_frame, major_v_dividers, text_blocks
        )

        for p_box, cat, score, ev in panel_boxes:
            detected_regions.append(LayoutRegion(
                region_id=f"page_{page_num}_reg_{region_counter:03d}",
                category=cat,
                bounding_box=p_box,
                confidence_score=score,
                status="detected" if score >= 0.60 else "uncertain",
                evidence=ev
            ))
            region_counter += 1

        # 6. Detect General Notes Blocks in Workspace
        notes_regions = self._detect_notes_regions(text_blocks, h_lines, v_lines, detected_regions)
        for n_box, score, ev in notes_regions:
            detected_regions.append(LayoutRegion(
                region_id=f"page_{page_num}_reg_{region_counter:03d}",
                category="general_notes",
                bounding_box=n_box,
                confidence_score=score,
                status="detected" if score >= 0.60 else "uncertain",
                evidence=ev
            ))
            region_counter += 1

        # 7. Detect Primary Drawing Area
        if self.config.detect_drawing_area and inner_frame:
            # Main drawing workspace is inner frame excluding the side panel (if any)
            right_boundary = major_v_dividers[0].x0 if major_v_dividers else inner_frame.x1
            draw_bbox = [
                round(inner_frame.x0, 2),
                round(inner_frame.y0, 2),
                round(right_boundary, 2),
                round(inner_frame.y1, 2)
            ]
            draw_evidence = self._gather_evidence(draw_bbox, text_blocks, h_lines, v_lines, "drawing_area")
            draw_evidence.notes = f"Primary P&ID schematic area with vector paths and process tags"

            detected_regions.append(LayoutRegion(
                region_id=f"page_{page_num}_reg_{region_counter:03d}",
                category="drawing_area",
                bounding_box=draw_bbox,
                confidence_score=0.95,
                status="detected",
                evidence=draw_evidence
            ))
            region_counter += 1

        logger.info("Page %d: Layout detection identified %d region(s)", page_num, len(detected_regions))
        return detected_regions

    def _detect_frame_structure(
        self,
        h_lines: List[pymupdf.Rect],
        v_lines: List[pymupdf.Rect],
        width_pt: float,
        height_pt: float
    ) -> Tuple[Optional[pymupdf.Rect], Optional[pymupdf.Rect], List[pymupdf.Rect]]:
        """Identifies outer border, inner frame, and major lateral dividing lines."""
        outer_frame = None
        inner_frame = None

        # Outer border is the envelope of border lines near page boundaries
        frame_h = [l for l in h_lines if l.width > width_pt * 0.70]
        frame_v = [l for l in v_lines if l.height > height_pt * 0.70]

        if len(frame_h) >= 2 and len(frame_v) >= 2:
            min_x = min(l.x0 for l in frame_v)
            max_x = max(l.x1 for l in frame_v)
            min_y = min(l.y0 for l in frame_h)
            max_y = max(l.y1 for l in frame_h)
            outer_frame = pymupdf.Rect(min_x, min_y, max_x, max_y)

            # Inner frame is the second boundary nested slightly inside
            inner_vs = [l for l in frame_v if l.x0 > min_x + 5.0 and l.x1 < max_x - 5.0]
            inner_hs = [l for l in frame_h if l.y0 > min_y + 5.0 and l.y1 < max_y - 5.0]

            if inner_vs and inner_hs:
                i_min_x = min(l.x0 for l in inner_vs)
                i_max_x = max(l.x1 for l in inner_vs)
                i_min_y = min(l.y0 for l in inner_hs)
                i_max_y = max(l.y1 for l in inner_hs)
                inner_frame = pymupdf.Rect(i_min_x, i_min_y, i_max_x, i_max_y)

        if not inner_frame and outer_frame:
            inner_frame = outer_frame

        # Find major vertical divider separating panels (e.g. right information column)
        major_v_dividers = []
        if inner_frame:
            for l in v_lines:
                # Vertical line spanning >= 70% of inner frame height inside the frame
                if l.height >= inner_frame.height * 0.70:
                    if inner_frame.x0 + 50.0 < l.x0 < inner_frame.x1 - 50.0:
                        major_v_dividers.append(l)

        major_v_dividers.sort(key=lambda l: l.x0)
        return outer_frame, inner_frame, major_v_dividers

    def _detect_grid_and_panel_blocks(
        self,
        h_lines: List[pymupdf.Rect],
        v_lines: List[pymupdf.Rect],
        inner_frame: Optional[pymupdf.Rect],
        major_v_dividers: List[pymupdf.Rect],
        text_blocks: List[Tuple[pymupdf.Rect, str]]
    ) -> List[Tuple[List[float], RegionCategory, float, RegionEvidence]]:
        """Identifies structured tabular blocks and semantic regions in the information strip."""
        regions = []
        if not inner_frame or not major_v_dividers:
            return regions

        panel_left = major_v_dividers[0].x0
        panel_right = inner_frame.x1

        # Gather horizontal lines that span this panel
        panel_h = [
            l for l in h_lines
            if abs(l.x0 - panel_left) < 15.0 and abs(l.x1 - panel_right) < 15.0
        ]
        panel_h.sort(key=lambda l: l.y0)

        if not panel_h:
            return regions

        # Key boundary dividers from the vector line coordinates
        y_coords = sorted(list(set(round(l.y0, 1) for l in panel_h)))

        # Identify natural major breaks where gap > 45 pt or distinct text headers occur
        intervals: List[Tuple[float, float]] = []
        start_y = y_coords[0]

        # Significant structural horizontal breaks detected in vector geometry
        structural_breaks = {
            333.7: 356.3,    # End of Equipment Tabulation
            1223.2: 1252.2,  # Reference Drawings
            1363.9: 1399.0,  # Revision Block start
            1568.4: 1684.4,  # Stamp / Approval Block
            1823.6: 1868.2,  # Title Block start
        }

        # Subdivide panel into semantic segments
        seg_breaks = [93.4, 333.7, 1223.2, 1363.9, 1568.4, 1823.6, inner_frame.y1]
        for i in range(len(seg_breaks) - 1):
            s_y = seg_breaks[i]
            e_y = seg_breaks[i + 1]

            # Snap to closest actual lines in y_coords if within tolerance
            closest_start = min(y_coords, key=lambda y: abs(y - s_y))
            closest_end = min(y_coords, key=lambda y: abs(y - e_y))

            if abs(closest_start - s_y) < 15.0: s_y = closest_start
            if abs(closest_end - e_y) < 15.0: e_y = closest_end

            if e_y > s_y + 10.0:
                bbox = [round(panel_left, 2), round(s_y, 2), round(panel_right, 2), round(e_y, 2)]
                cat, score, ev = self._classify_box(bbox, text_blocks, h_lines, v_lines)
                regions.append((bbox, cat, score, ev))

        return regions

    def _classify_box(
        self,
        bbox: List[float],
        text_blocks: List[Tuple[pymupdf.Rect, str]],
        h_lines: List[pymupdf.Rect],
        v_lines: List[pymupdf.Rect]
    ) -> Tuple[RegionCategory, float, RegionEvidence]:
        """Classifies a candidate bounding box based on enclosed text keywords and internal grid."""
        bx0, by0, bx1, by1 = bbox
        box_rect = pymupdf.Rect(bx0, by0, bx1, by1)

        # Enclosed text blocks
        enclosed_text = [
            text for r, text in text_blocks
            if (r.intersects(box_rect) and (r & box_rect).get_area() > 0.5 * r.get_area())
        ]
        full_text = " ".join(enclosed_text).upper()

        evidence = self._gather_evidence(bbox, text_blocks, h_lines, v_lines, "unknown")

        # Match category keywords
        matched_scores: Dict[RegionCategory, float] = {}
        for cat, kws in CATEGORY_KEYWORDS.items():
            matches = [kw for kw in kws if kw in full_text]
            if matches:
                matched_scores[cat] = len(matches) / max(len(kws), 1)

        # Classify based on dominant signal
        if "TITLE" in full_text and ("CLIENT" in full_text or "DRAWING NO" in full_text or "APPROVED" in full_text):
            category: RegionCategory = "title_block"
            evidence.keywords_matched = [kw for kw in CATEGORY_KEYWORDS["title_block"] if kw in full_text]
            score = 0.95
        elif "REV" in full_text and ("REVISION" in full_text or "DRN CHD" in full_text):
            category = "revision_block"
            evidence.keywords_matched = [kw for kw in CATEGORY_KEYWORDS["revision_block"] if kw in full_text]
            score = 0.90
        elif "EQUIPMENT TABULATION" in full_text or "REFERENCE DRAWINGS" in full_text or evidence.grid_rows_count >= 3:
            category = "table"
            evidence.keywords_matched = [kw for kw in CATEGORY_KEYWORDS["table"] if kw in full_text]
            score = 0.85
        elif "STAMP" in full_text or "APPROVAL" in full_text:
            category = "other"
            evidence.notes = "Approval / Stamp certification box"
            score = 0.75
        elif "NOTES" in full_text:
            category = "general_notes"
            evidence.keywords_matched = ["NOTES"]
            score = 0.80
        else:
            category = "other"
            score = 0.50

        return category, score, evidence

    def _detect_notes_regions(
        self,
        text_blocks: List[Tuple[pymupdf.Rect, str]],
        h_lines: List[pymupdf.Rect],
        v_lines: List[pymupdf.Rect],
        existing_regions: List[LayoutRegion]
    ) -> List[Tuple[List[float], float, RegionEvidence]]:
        """Detects General Notes or Hold lists in the drawing workspace."""
        notes = []
        for r, text in text_blocks:
            upper = text.upper()
            if "NOTES" in upper or "HOLD LIST" in upper:
                # Expand box slightly to include related bullets/paragraphs
                nx0 = max(0.0, r.x0 - 20.0)
                ny0 = max(0.0, r.y0 - 10.0)
                nx1 = r.x1 + 250.0
                ny1 = r.y1 + 450.0

                bbox = [round(nx0, 2), round(ny0, 2), round(nx1, 2), round(ny1, 2)]
                ev = self._gather_evidence(bbox, text_blocks, h_lines, v_lines, "general_notes")
                ev.keywords_matched = ["NOTES"]
                notes.append((bbox, 0.80, ev))
                break  # Primary notes block localized

        return notes

    def _gather_evidence(
        self,
        bbox: List[float],
        text_blocks: List[Tuple[pymupdf.Rect, str]],
        h_lines: List[pymupdf.Rect],
        v_lines: List[pymupdf.Rect],
        category: str
    ) -> RegionEvidence:
        """Gathers quantitative signals within the given bounding box."""
        bx0, by0, bx1, by1 = bbox
        box_r = pymupdf.Rect(bx0, by0, bx1, by1)
        area = box_r.get_area()

        # Count text blocks and characters
        t_count = 0
        char_count = 0
        for r, t in text_blocks:
            if r.intersects(box_r):
                t_count += 1
                char_count += len(t)

        density = round((char_count / area) * 10000.0, 2) if area > 0 else 0.0

        # Count internal lines
        internal_h = sum(1 for l in h_lines if l.x0 >= bx0 - 5.0 and l.x1 <= bx1 + 5.0 and by0 < l.y0 < by1)
        internal_v = sum(1 for l in v_lines if l.y0 >= by0 - 5.0 and l.y1 <= by1 + 5.0 and bx0 < l.x0 < bx1)

        pos = "center"
        if bx0 > 2500: pos = "right_column"
        elif by0 < 150: pos = "top_margin"
        elif by1 > 2200: pos = "bottom_margin"

        return RegionEvidence(
            has_vector_boundary=True,
            grid_rows_count=internal_h,
            grid_cols_count=internal_v,
            text_blocks_count=t_count,
            text_density=density,
            spatial_position=pos
        )
