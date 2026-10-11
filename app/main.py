import argparse
import sys
from pathlib import Path

from app.core.config import AppConfig
from app.core.logging_config import setup_logging
from app.agents.pdf_reader import PDFReaderAgent
from app.agents.page_renderer import PageRendererAgent
from app.agents.layout_detection import LayoutDetectionAgent
from app.agents.ocr import OCRAgent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stage 1: P&ID Document Scanner Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="Path to target engineering PDF file (overrides settings.yaml)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom settings.yaml configuration file"
    )
    parser.add_argument(
        "--agent",
        type=str,
        choices=["pdf_reader", "page_renderer", "layout_detection", "ocr", "all"],
        default="pdf_reader",
        help="Target agent to execute"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load configuration
    config = AppConfig.load(config_path=args.config)

    # Setup centralized logging
    log_file_path = config.resolve_path(config.logging.log_file)
    logger = setup_logging(
        level=config.logging.level,
        log_file=log_file_path,
        console_output=config.logging.console_output
    )

    logger.info("Starting Stage 1 Pipeline execution [Agent: %s]", args.agent)

    target_pdf = Path(args.pdf) if args.pdf else config.resolve_path(config.paths.input_pdf_path)

    if args.agent in ("pdf_reader", "all"):
        reader_agent = PDFReaderAgent(config=config)
        doc_meta, pages_artifact = reader_agent.run(pdf_path=target_pdf)

        print("\n" + "=" * 60)
        print("AGENT 1: PDF READER EXECUTION SUMMARY")
        print("=" * 60)
        print(f"File Name:        {doc_meta.file_name}")
        print(f"File Size:        {doc_meta.file_size_bytes} bytes")
        print(f"File SHA-256:     {doc_meta.file_hash_sha256[:16]}...")
        print(f"Total Pages:      {doc_meta.total_pages}")
        print(f"Status:           {doc_meta.processing_status}")
        print(f"Artifact Saved:   {pages_artifact}")
        
        for p in doc_meta.pages:
            print(f"  Page {p.page_number}: {p.visual_dimensions.width_pt}x{p.visual_dimensions.height_pt} pt "
                  f"({p.visual_dimensions.orientation}), Rotation: {p.rotation} deg, "
                  f"Native Text: {'Yes' if p.has_native_text else 'No'} ({p.native_text_word_count} words), "
                  f"Drawings: {p.vector_drawings_count}, Images: {p.raster_images_count}")
        print("=" * 60 + "\n")

        if doc_meta.processing_status == "FAILED":
            logger.error("Agent 1 processing failed: %s", doc_meta.errors)
            sys.exit(1)

    if args.agent in ("page_renderer", "all"):
        renderer_agent = PageRendererAgent(config=config)
        manifest, render_artifact = renderer_agent.run(pdf_path=target_pdf)

        print("\n" + "=" * 60)
        print("AGENT 2: PAGE RENDERER EXECUTION SUMMARY")
        print("=" * 60)
        print(f"File Name:        {manifest.file_name}")
        print(f"File SHA-256:     {manifest.file_hash_sha256[:16]}...")
        print(f"Rendered Pages:   {manifest.rendered_pages_count}/{manifest.total_pages}")
        print(f"Render DPI:       {manifest.render_config.get('dpi')} DPI")
        print(f"Status:           {manifest.processing_status}")
        print(f"Artifact Saved:   {render_artifact}")

        for p in manifest.pages:
            print(f"  Page {p.page_number}: {p.image_width_px}x{p.image_height_px} px ({p.total_pixels:,} px, {p.file_size_bytes:,} bytes)")
            print(f"    Image:        {p.image_path}")
            print(f"    Scale (X, Y): ({p.scale_x:.4f}, {p.scale_y:.4f}) px/pt")
            print(f"    Status:       {p.status}")
        print("=" * 60 + "\n")

        if manifest.processing_status == "FAILED":
            logger.error("Agent 2 processing failed: %s", manifest.errors)
            sys.exit(1)

    if args.agent in ("layout_detection", "all"):
        layout_agent = LayoutDetectionAgent(config=config)
        layout_manifest, layout_artifact = layout_agent.run(pdf_path=target_pdf)

        print("\n" + "=" * 60)
        print("AGENT 3: LAYOUT DETECTION EXECUTION SUMMARY")
        print("=" * 60)
        print(f"File Name:        {layout_manifest.file_name}")
        print(f"File SHA-256:     {layout_manifest.file_hash_sha256[:16]}...")
        print(f"Detector ID:      {layout_manifest.detector_id}")
        print(f"Status:           {layout_manifest.processing_status}")
        print(f"Artifact Saved:   {layout_artifact}")

        for p in layout_manifest.pages:
            print(f"  Page {p.page_number} ({p.regions_count} regions detected):")
            for r in p.regions:
                print(f"    [{r.category:<14}] {r.bounding_box} (score: {r.confidence_score:.2f}, status: {r.status})")
        print("=" * 60 + "\n")

        if layout_manifest.processing_status == "FAILED":
            logger.error("Agent 3 processing failed: %s", layout_manifest.errors)
            sys.exit(1)

    if args.agent in ("ocr", "all"):
        ocr_agent = OCRAgent(config=config)
        ocr_manifest, ocr_artifact = ocr_agent.run(pdf_path=target_pdf)

        print("\n" + "=" * 60)
        print("AGENT 4: OCR / TEXT EXTRACTION EXECUTION SUMMARY")
        print("=" * 60)
        print(f"File Name:        {ocr_manifest.file_name}")
        print(f"File SHA-256:     {ocr_manifest.file_hash_sha256[:16]}...")
        print(f"Method Summary:   {ocr_manifest.extraction_method_summary}")
        print(f"Total Records:    {ocr_manifest.total_records_count}")
        print(f"Status:           {ocr_manifest.processing_status}")
        print(f"Artifact Saved:   {ocr_artifact}")

        for p in ocr_manifest.pages:
            print(f"  Page {p.page_number} ({p.records_count} records extracted):")
            print(f"    Native words: {p.native_stats.word_count}, chars: {p.native_stats.character_count}")
            print(f"    OCR Decision: {'Skipped' if p.ocr_decision.ocr_skipped else 'Executed'} - {p.ocr_decision.reason}")
            sample_tags = [r.text for r in p.records if any(c.isdigit() for c in r.text) or len(r.text) >= 4][:8]
            print(f"    Sample tags:  {', '.join(sample_tags)}")
        print("=" * 60 + "\n")

        if ocr_manifest.processing_status == "FAILED":
            logger.error("Agent 4 processing failed: %s", ocr_manifest.errors)
            sys.exit(1)

    logger.info("Pipeline step completed successfully.")


if __name__ == "__main__":
    main()
