import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List
import pymupdf

from app.core.artifact_writer import ArtifactWriter
from app.core.config import AppConfig
from app.agents.pdf_reader.extractor import PDFExtractor
from .renderer import PageRenderer
from .models import RenderManifest, PageRenderResult

logger = logging.getLogger("stage1_pipeline.page_renderer.agent")


class PageRendererAgent:
    """Agent 2: Page Renderer Agent.

    Responsibilities:
    - Open input PDF using PyMuPDF.
    - Discover pages dynamically and optionally validate against Agent 1 metadata.
    - Render each PDF page as ONE complete page image (strictly no tiles).
    - Save rendered images under outputs/images/.
    - Generate outputs/artifacts/render_manifest.json with complete rendering and coordinate metadata.
    - Record errors, warnings, and resource limits accurately.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        artifacts_dir = self.config.resolve_path(self.config.paths.artifacts_dir)
        images_dir = self.config.resolve_path(self.config.paths.images_dir)
        project_root = self.config.resolve_path(".")

        self.artifact_writer = ArtifactWriter(artifacts_dir)
        self.renderer = PageRenderer(
            config=self.config.page_renderer,
            images_dir=images_dir,
            project_root=project_root
        )

    def run(self, pdf_path: Optional[Path] = None, password: Optional[str] = None) -> Tuple[RenderManifest, Path]:
        """Executes Page Renderer Agent workflow and outputs render_manifest.json.

        Args:
            pdf_path: Optional path to PDF file. If None, uses configured input_pdf_path.
            password: Optional password for encrypted documents.

        Returns:
            Tuple of (RenderManifest, Path to render_manifest.json).
        """
        if pdf_path is None:
            resolved_pdf_path = self.config.resolve_path(self.config.paths.input_pdf_path)
        else:
            resolved_pdf_path = Path(pdf_path).resolve()

        now_utc = datetime.now(timezone.utc).isoformat()
        logger.info("PageRendererAgent: Beginning rendering for %s", resolved_pdf_path)

        if not resolved_pdf_path.exists() or not resolved_pdf_path.is_file():
            err_msg = f"Target PDF does not exist or is not a file: {resolved_pdf_path}"
            logger.error(err_msg)
            manifest = RenderManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256="",
                total_pages=0,
                rendered_pages_count=0,
                render_config=self.config.page_renderer.model_dump(),
                processing_status="FAILED",
                errors=[err_msg],
                rendered_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("render_manifest.json", manifest)
            return manifest, artifact_path

        file_hash = PDFExtractor.compute_sha256(resolved_pdf_path)

        # Optional cross-validation against Agent 1 metadata if present
        self._cross_validate_with_agent1(resolved_pdf_path, file_hash)

        doc = None
        try:
            doc = pymupdf.open(str(resolved_pdf_path))
        except Exception as e:
            err_msg = f"Failed to open PDF document: {str(e)}"
            logger.error(err_msg, exc_info=True)
            manifest = RenderManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=0,
                rendered_pages_count=0,
                render_config=self.config.page_renderer.model_dump(),
                processing_status="FAILED",
                errors=[err_msg],
                rendered_at_utc=now_utc
            )
            artifact_path = self.artifact_writer.write_json("render_manifest.json", manifest)
            return manifest, artifact_path

        try:
            if doc.is_encrypted:
                auth = False
                if password:
                    auth = bool(doc.authenticate(password) > 0)
                if not auth:
                    err_msg = "PDF is password-protected or authentication failed."
                    logger.error(err_msg)
                    manifest = RenderManifest(
                        file_path=str(resolved_pdf_path),
                        file_name=resolved_pdf_path.name,
                        file_hash_sha256=file_hash,
                        total_pages=0,
                        rendered_pages_count=0,
                        render_config=self.config.page_renderer.model_dump(),
                        processing_status="FAILED",
                        errors=[err_msg],
                        rendered_at_utc=now_utc
                    )
                    artifact_path = self.artifact_writer.write_json("render_manifest.json", manifest)
                    return manifest, artifact_path

            total_pages = doc.page_count
            logger.info("PageRendererAgent: Rendering %d page(s) at %d DPI", total_pages, self.config.page_renderer.dpi)

            page_results: List[PageRenderResult] = []
            doc_warnings: List[str] = []
            doc_errors: List[str] = []

            for page_idx in range(total_pages):
                page = doc[page_idx]
                result = self.renderer.render_page(page, page_idx, resolved_pdf_path.stem)
                page_results.append(result)
                if result.warnings:
                    doc_warnings.extend(result.warnings)
                if result.status == "FAILED" and result.error_message:
                    doc_errors.append(result.error_message)

            rendered_count = sum(1 for r in page_results if r.status in ("SUCCESS", "WARNING"))

            if doc_errors:
                proc_status = "PARTIAL" if rendered_count > 0 else "FAILED"
            else:
                proc_status = "SUCCESS"

            manifest = RenderManifest(
                file_path=str(resolved_pdf_path),
                file_name=resolved_pdf_path.name,
                file_hash_sha256=file_hash,
                total_pages=total_pages,
                rendered_pages_count=rendered_count,
                render_config=self.config.page_renderer.model_dump(),
                pages=page_results,
                processing_status=proc_status,
                errors=doc_errors,
                warnings=doc_warnings,
                rendered_at_utc=now_utc
            )

            artifact_path = self.artifact_writer.write_json("render_manifest.json", manifest)
            logger.info(
                "PageRendererAgent: Finished rendering (%d/%d pages rendered). Manifest: %s",
                rendered_count, total_pages, artifact_path
            )
            return manifest, artifact_path

        finally:
            if doc is not None:
                doc.close()

    def _cross_validate_with_agent1(self, pdf_path: Path, current_hash: str):
        """Cross-validates against Agent 1's pages.json if available."""
        pages_artifact = self.config.resolve_path(self.config.paths.artifacts_dir) / "pages.json"
        if not pages_artifact.exists():
            logger.debug("pages.json artifact not found; skipping Agent 1 cross-validation.")
            return

        try:
            with open(pages_artifact, "r", encoding="utf-8") as f:
                data = json.load(f)
            prev_hash = data.get("file_hash_sha256")
            if prev_hash and prev_hash != current_hash:
                logger.warning(
                    "Cross-validation warning: SHA-256 in pages.json (%s) does not match current PDF hash (%s)",
                    prev_hash, current_hash
                )
            else:
                logger.info("Agent 1 cross-validation verified: SHA-256 matches pages.json.")
        except Exception as e:
            logger.warning("Could not read pages.json for cross-validation: %s", str(e))
