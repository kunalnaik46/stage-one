import logging
import math
import re
from pathlib import Path
from typing import Optional, Tuple
import pymupdf

from app.core.config import PageRendererConfig
from .coordinates import CoordinateTransformer
from .models import PageRenderResult, CoordinateConventions

logger = logging.getLogger("stage1_pipeline.page_renderer.renderer")


class PageRenderer:
    """Renders complete PDF pages into full-page images without tiling."""

    def __init__(self, config: PageRendererConfig, images_dir: Path, project_root: Path):
        self.config = config
        self.images_dir = Path(images_dir)
        self.project_root = Path(project_root)
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def render_page(
        self,
        page: pymupdf.Page,
        page_idx: int,
        doc_stem: str
    ) -> PageRenderResult:
        """Renders a single PDF page into a complete image file and calculates coordinate mappings."""
        page_number = page_idx + 1
        rect = page.rect
        width_pt = round(rect.width, 2)
        height_pt = round(rect.height, 2)
        rotation = int(page.rotation)
        mbox = page.mediabox
        cbox = page.cropbox

        mediabox_list = [round(mbox.x0, 2), round(mbox.y0, 2), round(mbox.x1, 2), round(mbox.y1, 2)]
        cropbox_list = [round(cbox.x0, 2), round(cbox.y0, 2), round(cbox.x1, 2), round(cbox.y1, 2)]
        visual_rect_list = [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]

        warnings = []
        if rotation != 0:
            warnings.append(f"Page {page_number} has internal rotation: {rotation} degrees")

        # 1. Pre-estimation & Resource Limit Validation
        est_width_px = math.ceil(width_pt * self.config.dpi / 72.0)
        est_height_px = math.ceil(height_pt * self.config.dpi / 72.0)
        est_total_pixels = est_width_px * est_height_px

        logger.info(
            "Page %d: Dimensions %.1fx%.1f pt, Rotation %d deg. Estimated render: %dx%d px (%d pixels) at %d DPI",
            page_number, width_pt, height_pt, rotation, est_width_px, est_height_px, est_total_pixels, self.config.dpi
        )

        if est_total_pixels > self.config.max_pixels_per_page:
            err_msg = (
                f"Page {page_number} estimated size ({est_total_pixels:,} pixels) exceeds "
                f"configured limit ({self.config.max_pixels_per_page:,} pixels) at {self.config.dpi} DPI. "
                f"Rendering aborted to prevent memory exhaustion (no tiling permitted)."
            )
            logger.error(err_msg)
            return PageRenderResult(
                page_index=page_idx,
                page_number=page_number,
                image_path="",
                image_file_name="",
                file_size_bytes=0,
                image_width_px=0,
                image_height_px=0,
                total_pixels=0,
                dpi=self.config.dpi,
                image_format=self.config.image_format,
                color_space=self.config.color_space,
                original_rotation_deg=rotation,
                original_mediabox=mediabox_list,
                original_cropbox=cropbox_list,
                visual_rect=visual_rect_list,
                visual_width_pt=width_pt,
                visual_height_pt=height_pt,
                scale_x=0.0,
                scale_y=0.0,
                matrix_pixel_to_visual=[0.0] * 6,
                matrix_pixel_to_unrotated=[0.0] * 6,
                matrix_visual_to_pixel=[0.0] * 6,
                matrix_unrotated_to_pixel=[0.0] * 6,
                status="FAILED",
                error_message=err_msg
            )

        # 2. Rendering complete page (No tiles anywhere)
        try:
            pix = page.get_pixmap(dpi=self.config.dpi, alpha=False)
            actual_w = pix.width
            actual_h = pix.height
            actual_pixels = actual_w * actual_h

            # Stable, collision-safe file naming
            clean_stem = re.sub(r'[^a-zA-Z0-9_\-]', '_', doc_stem)
            ext = self.config.image_format.lower().lstrip('.')
            image_file_name = f"{clean_stem}_page_{page_number:03d}.{ext}"
            image_abs_path = (self.images_dir / image_file_name).resolve()

            # Save full-page image
            pix.save(str(image_abs_path))
            file_size_bytes = image_abs_path.stat().st_size
            logger.info(
                "Rendered Page %d: %dx%d px (%d bytes) saved to %s",
                page_number, actual_w, actual_h, file_size_bytes, image_file_name
            )

            # Compute relative path from project root
            try:
                rel_image_path = str(image_abs_path.relative_to(self.project_root)).replace('\\', '/')
            except ValueError:
                rel_image_path = str(image_abs_path).replace('\\', '/')

            # 3. Coordinate Transformations
            transformer = CoordinateTransformer(
                page=page,
                image_width_px=actual_w,
                image_height_px=actual_h
            )
            matrices = transformer.get_matrices_dict()

            return PageRenderResult(
                page_index=page_idx,
                page_number=page_number,
                image_path=rel_image_path,
                image_file_name=image_file_name,
                file_size_bytes=file_size_bytes,
                image_width_px=actual_w,
                image_height_px=actual_h,
                total_pixels=actual_pixels,
                dpi=self.config.dpi,
                image_format=ext,
                color_space=self.config.color_space,
                original_rotation_deg=rotation,
                original_mediabox=mediabox_list,
                original_cropbox=cropbox_list,
                visual_rect=visual_rect_list,
                visual_width_pt=width_pt,
                visual_height_pt=height_pt,
                scale_x=round(transformer.scale_x, 6),
                scale_y=round(transformer.scale_y, 6),
                matrix_pixel_to_visual=matrices["matrix_pixel_to_visual"],
                matrix_pixel_to_unrotated=matrices["matrix_pixel_to_unrotated"],
                matrix_visual_to_pixel=matrices["matrix_visual_to_pixel"],
                matrix_unrotated_to_pixel=matrices["matrix_unrotated_to_pixel"],
                coordinate_conventions=CoordinateConventions(),
                status="WARNING" if warnings else "SUCCESS",
                warnings=warnings
            )

        except Exception as e:
            err_msg = f"Failed to render page {page_number}: {str(e)}"
            logger.error(err_msg, exc_info=True)
            return PageRenderResult(
                page_index=page_idx,
                page_number=page_number,
                image_path="",
                image_file_name="",
                file_size_bytes=0,
                image_width_px=0,
                image_height_px=0,
                total_pixels=0,
                dpi=self.config.dpi,
                image_format=self.config.image_format,
                color_space=self.config.color_space,
                original_rotation_deg=rotation,
                original_mediabox=mediabox_list,
                original_cropbox=cropbox_list,
                visual_rect=visual_rect_list,
                visual_width_pt=width_pt,
                visual_height_pt=height_pt,
                scale_x=0.0,
                scale_y=0.0,
                matrix_pixel_to_visual=[0.0] * 6,
                matrix_pixel_to_unrotated=[0.0] * 6,
                matrix_visual_to_pixel=[0.0] * 6,
                matrix_unrotated_to_pixel=[0.0] * 6,
                status="FAILED",
                error_message=err_msg
            )
