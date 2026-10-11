# Industrial P&ID Extraction Platform — Stage 1: Document Scanner

Industrial document ingestion and analysis pipeline for engineering Piping & Instrumentation Diagrams (P&IDs).

## Pipeline Sequence

1. **PDF Reader Agent** (`app/agents/pdf_reader/`) -> Generates `pages.json` *(Active & Validated)*
2. **Page Renderer Agent** (`app/agents/page_renderer/`) -> Generates full-page images & `render_manifest.json` *(Active & Validated)*
3. **Layout Detection Agent** (`app/agents/layout_detection/`) -> Generates `layout.json` *(Active & Validated)*
4. **OCR / Text Extraction Agent** (`app/agents/ocr/`) -> Generates `ocr.json` *(Active & Validated)*
5. **Geometry Agent** (`app/agents/geometry/`) -> Generates `geometry.json` *(Pending)*
6. **Symbol Candidate Agent** (`app/agents/symbol_candidate/`) -> Generates `symbols.json` *(Pending)*
7. **Metadata Builder Agent** (`app/agents/metadata_builder/`) -> Generates `document_metadata.json` *(Pending)*

## Agent 1: PDF Reader

### Responsibilities
- Safe opening of engineering PDF files.
- Dynamic page count discovery.
- Extraction of document metadata (format, title, author, creator, producer, dates, hash, encryption).
- Extraction of page dimensions (points), orientation, rotation, mediabox, cropbox.
- Detection of native text existence and word/character statistics.
- Vector drawings and raster image counts.
- Generation of structured `outputs/artifacts/pages.json`.

### Execution
Run via CLI:
```bash
python -m app.main --agent pdf_reader --pdf project.pdf
python -m app.main --agent page_renderer --pdf project.pdf
python -m app.main --agent layout_detection --pdf project.pdf
python -m app.main --agent ocr --pdf project.pdf
```
Or run the complete pipeline sequence:
```bash
python -m app.main --agent all --pdf project.pdf
```

