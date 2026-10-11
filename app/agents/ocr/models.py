from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

ExtractionSource = Literal["native_pdf", "ocr"]
GranularityType = Literal["word", "span", "line"]


class ExtractedTextRecord(BaseModel):
    """A single extracted text record with visual bounding box and provenance."""
    record_id: str = Field(description="Unique record identifier, e.g. 'p1_w001'")
    text: str = Field(description="Exact extracted text string without synthetic modification")
    bounding_box: List[float] = Field(description="[x0, y0, x1, y1] in visual PDF points (page.rect space)")
    bbox_convention: str = Field(
        default="[x0, y0, x1, y1] with origin at top-left of displayed page; units in PDF points"
    )
    coordinate_space: str = Field(default="visual_space")
    granularity: GranularityType = Field(default="word")
    source: ExtractionSource = Field(default="native_pdf")
    confidence: Optional[float] = Field(
        default=None,
        description="Confidence score provided by OCR engine; None for native PDF text"
    )
    font_name: Optional[str] = Field(default=None, description="Native font name if available")
    font_size: Optional[float] = Field(default=None, description="Native font size in points if available")
    flags: Optional[int] = Field(default=None, description="Font flags (e.g. bold, italic) from PDF internals")
    direction: Optional[List[float]] = Field(default=None, description="Text writing direction vector [dx, dy]")
    block_index: int = Field(default=0, description="0-based block index")
    line_index: int = Field(default=0, description="0-based line index within block")
    word_index: int = Field(default=0, description="0-based word index within line")


class NativeTextStats(BaseModel):
    """Statistics on native PDF text stream content."""
    character_count: int
    word_count: int
    block_count: int
    is_sufficient: bool = Field(
        description="Whether native text meets configured adequacy thresholds"
    )


class OCRDecision(BaseModel):
    """Audit record explaining whether OCR was executed, skipped, or failed."""
    ocr_attempted: bool
    ocr_skipped: bool
    reason: str
    backend_available: bool
    backend_name: Optional[str] = None


class PageTextResult(BaseModel):
    """Complete text extraction results for a single page."""
    page_index: int
    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    coordinate_space: str = Field(default="visual_space (origin top-left, units in PDF points)")
    native_stats: NativeTextStats
    ocr_decision: OCRDecision
    records_count: int
    records: List[ExtractedTextRecord] = Field(default_factory=list)
    status: str = Field(default="SUCCESS", description="SUCCESS, WARNING, FAILED")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class TextExtractionManifest(BaseModel):
    """Top-level document text extraction manifest for ocr.json."""
    file_path: str
    file_name: str
    file_hash_sha256: str
    total_pages: int
    processing_status: str = Field(default="SUCCESS", description="SUCCESS, PARTIAL, FAILED")
    extraction_config: Dict[str, Any]
    extraction_method_summary: str
    total_records_count: int
    pages: List[PageTextResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    extracted_at_utc: str
