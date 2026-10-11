from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class CoordinateConventions(BaseModel):
    """Explicit definitions of coordinate systems and transformation formulas."""
    pixel_space: str = Field(
        default="Origin at top-left of rendered image; +X points right (0 to width_px), +Y points down (0 to height_px). Units in integer pixels."
    )
    visual_space: str = Field(
        default="Origin at top-left of displayed/oriented page (page.rect); +X points right (0 to width_pt), +Y points down (0 to height_pt). Units in PDF points (1/72 inch)."
    )
    unrotated_space: str = Field(
        default="Raw PDF user space coordinates matching mediabox/cropbox before page rotation is applied. Units in PDF points (1/72 inch)."
    )
    matrix_convention: str = Field(
        default="Row-vector convention: [x', y', 1] = [x, y, 1] * [[a, b, 0], [c, d, 0], [e, f, 1]], where x'=x*a+y*c+e, y'=x*b+y*d+f."
    )
    mapping_formula_pixel_to_visual: str = Field(
        default="P_visual = P_pixel * matrix_pixel_to_visual (maps pixel [0, W_px]x[0, H_px] to page.rect [0, W_pt]x[0, H_pt])"
    )
    mapping_formula_pixel_to_unrotated: str = Field(
        default="P_unrotated = P_pixel * matrix_pixel_to_visual * derotation_matrix * cropbox_translation (maps pixel to raw PDF user coordinates)"
    )


class PageRenderResult(BaseModel):
    """Complete metadata for a single rendered page."""
    page_index: int = Field(description="0-based page index")
    page_number: int = Field(description="1-based page number")
    image_path: str = Field(description="Relative path to generated image file from project root")
    image_file_name: str
    file_size_bytes: int
    image_width_px: int
    image_height_px: int
    total_pixels: int
    dpi: int
    image_format: str
    color_space: str
    original_rotation_deg: int
    original_mediabox: List[float] = Field(description="[x0, y0, x1, y1] in unrotated PDF points")
    original_cropbox: List[float] = Field(description="[x0, y0, x1, y1] in unrotated PDF points")
    visual_rect: List[float] = Field(description="[x0, y0, x1, y1] of visible viewport in points")
    visual_width_pt: float
    visual_height_pt: float
    scale_x: float = Field(description="Pixel-to-point horizontal scale factor (image_width_px / visual_width_pt)")
    scale_y: float = Field(description="Pixel-to-point vertical scale factor (image_height_px / visual_height_pt)")
    matrix_pixel_to_visual: List[float] = Field(description="6-element affine matrix [a, b, c, d, e, f]")
    matrix_pixel_to_unrotated: List[float] = Field(description="6-element affine matrix [a, b, c, d, e, f]")
    matrix_visual_to_pixel: List[float] = Field(description="6-element affine matrix [a, b, c, d, e, f]")
    matrix_unrotated_to_pixel: List[float] = Field(description="6-element affine matrix [a, b, c, d, e, f]")
    coordinate_conventions: CoordinateConventions = Field(default_factory=CoordinateConventions)
    status: str = Field(default="SUCCESS", description="SUCCESS, WARNING, FAILED")
    warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class RenderManifest(BaseModel):
    """Top-level manifest for render_manifest.json."""
    file_path: str
    file_name: str
    file_hash_sha256: str
    total_pages: int
    rendered_pages_count: int
    render_config: Dict[str, Any]
    pages: List[PageRenderResult] = Field(default_factory=list)
    processing_status: str = Field(default="SUCCESS", description="SUCCESS, PARTIAL, or FAILED")
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    rendered_at_utc: str
