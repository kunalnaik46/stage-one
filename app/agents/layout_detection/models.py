from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

RegionCategory = Literal[
    "drawing_area",
    "title_block",
    "legend",
    "table",
    "general_notes",
    "revision_block",
    "header",
    "footer",
    "other",
    "unknown"
]

ClassificationStatus = Literal["detected", "uncertain", "unclassified"]


class RegionEvidence(BaseModel):
    """Documented evidence signals supporting a region classification."""
    keywords_matched: List[str] = Field(default_factory=list, description="Keywords found within region")
    has_vector_boundary: bool = Field(default=False, description="Enclosed by vector border lines/rectangles")
    grid_rows_count: int = Field(default=0, description="Number of detected internal horizontal grid rows")
    grid_cols_count: int = Field(default=0, description="Number of detected internal vertical grid columns")
    text_blocks_count: int = Field(default=0, description="Count of text blocks inside region")
    text_density: float = Field(default=0.0, description="Characters per 10,000 sq pt")
    spatial_position: str = Field(default="", description="Qualitative location (e.g. 'bottom_right', 'main_workspace')")
    notes: Optional[str] = None


class LayoutRegion(BaseModel):
    """A detected semantic region on a page in visual PDF points."""
    region_id: str = Field(description="Stable unique identifier on page, e.g. 'page_1_reg_001'")
    category: RegionCategory
    bounding_box: List[float] = Field(description="[x0, y0, x1, y1] in visual PDF points")
    bbox_convention: str = Field(default="[x0, y0, x1, y1] where x0<x1, y0<y1, origin at visual page top-left")
    coordinate_space: str = Field(default="visual_space", description="Visual/displayed page coordinates (page.rect)")
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="Composite evidence score (0.0 to 1.0) based on vector borders, keywords, and density"
    )
    status: ClassificationStatus = Field(default="detected")
    evidence: RegionEvidence
    warnings: List[str] = Field(default_factory=list)


class PageLayoutResult(BaseModel):
    """Layout analysis result for a single page."""
    page_index: int = Field(description="0-based page index")
    page_number: int = Field(description="1-based page number")
    width_pt: float
    height_pt: float
    rotation: int
    coordinate_system_ref: str = Field(default="visual_space (origin top-left, units in points)")
    status: str = Field(default="SUCCESS", description="SUCCESS, PARTIAL, FAILED")
    regions_count: int
    regions: List[LayoutRegion] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class LayoutManifest(BaseModel):
    """Top-level document layout manifest for layout.json."""
    file_path: str
    file_name: str
    file_hash_sha256: str
    total_pages: int
    detector_id: str = Field(default="deterministic_multisignal_layout_detector_v1")
    detector_config: Dict[str, Any]
    pages: List[PageLayoutResult] = Field(default_factory=list)
    processing_status: str = Field(default="SUCCESS", description="SUCCESS, PARTIAL, FAILED")
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    detected_at_utc: str
