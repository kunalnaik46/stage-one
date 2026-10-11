import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class PipelineMetaConfig(BaseModel):
    name: str = "stage1_pid_document_scanner"
    version: str = "1.0.0"


class PathsConfig(BaseModel):
    input_pdf_path: str = "project.pdf"
    output_dir: str = "outputs"
    artifacts_dir: str = "outputs/artifacts"
    images_dir: str = "outputs/images"
    logs_dir: str = "outputs/logs"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: str = "outputs/logs/pipeline.log"
    console_output: bool = True


class PDFReaderConfig(BaseModel):
    max_text_preview_chars: int = 300
    extract_drawing_counts: bool = True
    extract_image_counts: bool = True


class PageRendererConfig(BaseModel):
    enabled: bool = True
    dpi: int = 150
    image_format: str = "png"
    max_pixels_per_page: int = 150000000
    color_space: str = "rgb"


class LayoutDetectionConfig(BaseModel):
    enabled: bool = True
    score_threshold: float = 0.50
    detect_drawing_area: bool = True
    detect_outer_margins: bool = True


class OCRConfig(BaseModel):
    enabled: bool = True
    force_ocr: bool = False
    min_native_words_threshold: int = 10
    min_native_chars_threshold: int = 50
    granularity: str = "word"
    prefer_native: bool = True


class AppConfig(BaseModel):
    pipeline: PipelineMetaConfig = Field(default_factory=PipelineMetaConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    pdf_reader: PDFReaderConfig = Field(default_factory=PDFReaderConfig)
    page_renderer: PageRendererConfig = Field(default_factory=PageRendererConfig)
    layout_detection: LayoutDetectionConfig = Field(default_factory=LayoutDetectionConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)

    @classmethod
    def load(cls, config_path: Optional[str or Path] = None, project_root: Optional[Path] = None) -> "AppConfig":
        """Loads configuration from YAML file and resolves project root paths."""
        if project_root is None:
            # Stage one/app/core/config.py -> Stage one/
            project_root = Path(__file__).resolve().parent.parent.parent

        if config_path is None:
            config_env = os.environ.get("CONFIG_PATH")
            if config_env:
                config_path = Path(config_env)
            else:
                config_path = project_root / "config" / "settings.yaml"
        else:
            config_path = Path(config_path)

        data = {}
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if loaded and isinstance(loaded, dict):
                    data = loaded

        return cls(**data)

    def resolve_path(self, relative_or_absolute_path: str, project_root: Optional[Path] = None) -> Path:
        """Resolves a path relative to project root if it is not already absolute."""
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        p = Path(relative_or_absolute_path)
        if p.is_absolute():
            return p
        return (project_root / p).resolve()
