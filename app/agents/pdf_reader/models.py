from typing import List, Optional
from pydantic import BaseModel, Field


class PageBox(BaseModel):
    """Bounding box coordinates in PDF points (72 points = 1 inch)."""
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    height: float


class PageDimensions(BaseModel):
    """Visual page dimensions considering orientation and rotation."""
    width_pt: float
    height_pt: float
    orientation: str = Field(description="Orientation: 'landscape', 'portrait', or 'square'")
    aspect_ratio: float = Field(description="Width divided by height")


class PageMetadata(BaseModel):
    """Comprehensive page-level metadata extracted from PDF internals."""
    page_index: int = Field(description="0-based page index")
    page_number: int = Field(description="1-based human page number")
    visual_dimensions: PageDimensions
    mediabox: PageBox
    cropbox: PageBox
    rotation: int = Field(description="PDF rotation angle in degrees (0, 90, 180, 270)")
    has_native_text: bool
    native_text_char_count: int
    native_text_word_count: int
    text_sample: str = Field(default="", description="Sanitized preview snippet of native text")
    vector_drawings_count: int = Field(default=0, description="Count of native vector drawing paths")
    raster_images_count: int = Field(default=0, description="Count of embedded raster image objects")
    status: str = Field(default="SUCCESS", description="Page extraction status: SUCCESS, WARNING, FAILED")
    warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class DocumentMetadata(BaseModel):
    """Top-level document metadata and collection of page records for pages.json."""
    file_path: str
    file_name: str
    file_size_bytes: int
    file_hash_sha256: str
    pdf_format: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    keywords: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    creation_date: Optional[str] = None
    mod_date: Optional[str] = None
    is_encrypted: bool = False
    total_pages: int
    pages: List[PageMetadata] = Field(default_factory=list)
    processing_status: str = Field(default="SUCCESS", description="SUCCESS, PARTIAL, or FAILED")
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    processed_at_utc: str
