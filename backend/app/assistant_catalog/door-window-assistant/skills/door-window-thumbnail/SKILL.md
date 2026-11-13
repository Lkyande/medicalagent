---
name: door-window-thumbnail
description: Use when a user uploads architectural drawings and/or a customer quote document and asks to split door/window frame drawings, count floor-plan quantities, check glazing requirements, or export a CSV/XLSX takeoff.
---

# Door/window split, glazing requirements, and takeoff

Complete the whole workflow below. The `door-window-thumbnail` MCP server performs PDF page listing, rendering, deterministic cropping, item-number normalization, and CSV/XLSX export. The connected Agent performs document reading, visual recognition, classification, and requirement interpretation. The MCP server does not call a vision model.

## MCP server and tool contract

Use the configured `door-window-thumbnail` MCP server. Do not replace these calls with guessed local paths or ad-hoc image processing.

- `register_drawing`: register a PDF only when the PDF is local to the MCP server or is explicitly shared with it. The platform normally registers uploaded PDFs first.
- `list_drawing_pages`: list PDF pages and text-based page candidates.
- `render_drawing_page`: render one PDF page and return a PNG artifact plus the normalized top-left coordinate contract.
- `read_drawing_image`: read the rendered PNG as image input for visual inspection.
- `crop_door_window`: crop one identified door/window frame drawing.
- `batch_crop_door_windows`: crop a confirmed batch from one page, at most 100 regions.
- `export_door_window_takeoff`: merge schedule specifications, customer requirements, and floor-plan detections into downloadable CSV/XLSX artifacts.

The normal call sequence is:

```text
PDF attachment -> list_drawing_pages
schedule page -> render_drawing_page -> read_drawing_image -> crop_door_window/batch_crop_door_windows
floor-plan page -> render_drawing_page -> read_drawing_image -> visual tag detections
TXT/DOCX quote -> read with workspace file tools -> customer_requirements
all results -> export_door_window_takeoff
```

## Inputs and attachment handling

The user may upload one PDF, multiple PDFs, a `.txt` quote note, a `.docx` quote document, or a combination.

1. Separate drawing PDFs from customer requirement documents by file name and MIME type.
2. For an uploaded PDF, read the `<workspace_attachment>` marker and recover its `drawingSessionId`. The platform upload hook has already called the MCP HTTP upload endpoint. Reuse that ID and do not call `register_drawing` again.
3. If a PDF has no `drawingSessionId`, call `register_drawing` only when the MCP server can actually access the supplied path. Never invent a client-local path.
4. Read `.txt` and `.docx` files with the available workspace file/content tools. Do not send quote documents to the drawing MCP tools, execute them, or treat their paths as PDF drawing paths.
5. If several PDFs are provided, process each PDF in its own drawing session and keep source file names in the final result.

## Phase 1: parse customer quote requirements

Before interpreting schedule compliance, read every supplied `.txt` and `.docx` quote document. Preserve the exact source text and location. Extract U-value, U-factor, SHGC, and all other glazing requirements, including glass type, Low-E coating, double/triple glazing, spacer, thickness, VT, safety glass, acoustic performance, and energy or code requirements.

Represent each extracted requirement internally as:

```json
{
  "property": "U-value",
  "value": 1.4,
  "operator": "<=",
  "unit": "W/m2K",
  "item_no": "",
  "scope": "all_glazing",
  "original_text": "U-value shall not exceed 1.4 W/m2K.",
  "source_file": "customer_quote.docx",
  "source_location": "paragraph 12",
  "confidence": 0.96
}
```

Rules for scope and missing identifiers:

- If a U-value, SHGC, or other glass requirement has a door/window identifier, apply it only to that normalized item number.
- If it has no item number or identifier but is written as a glazing, glass, window, door, or project requirement, apply it to every schedule item whose glazing is present or is not explicitly marked `opaque`, `solid`, `unglazed`, or `no glass`.
- This is the default project-wide glazing rule. Do not leave an unqualified glass requirement unattached merely because the customer did not write `W01` or `D01`.
- If the schedule does not state whether an item has glass, apply the project-wide requirement and mark the glazing scope as `ASSUMED_GLAZED_REVIEW` in `review_issues` rather than silently excluding it.
- Do not apply a glazing requirement to an explicitly opaque or solid item, but retain the requirement in the customer-requirements section.
- Keep the original value, unit, operator, source, and confidence. Normalize units only when the conversion is known; otherwise mark the comparison `REVIEW` rather than comparing incompatible units.
- If an operator is not stated, do not invent a pass/fail threshold. Mark the requirement `REVIEW` unless the wording clearly supplies one such as `not greater than`, `minimum`, or `equal to`.
- If the same property has conflicting project-wide and item-specific requirements, retain both and mark the affected item `CONFLICT`.

Do not confuse a customer requirement with an actual schedule value. Keep both values so the export can show `Required U-value`, `Actual U-value`, `U-value Compliance`, `Required SHGC`, `Actual SHGC`, and `SHGC Compliance`.

## Phase 2: locate and visually confirm the door/window schedule

1. Call `list_drawing_pages` for the drawing session.
2. Use only pages with `page_type == "schedule"` as visual candidates for the schedule. The page classifier is text-only and is not final.
3. For each candidate, call `render_drawing_page`, then call `read_drawing_image` with the returned artifact ID.
4. Use vision to confirm a real `WINDOWS & DOORS SCHEDULE`, `WINDOW SCHEDULE`, or `DOOR SCHEDULE` containing tabulated specifications and/or door/window frame diagrams. A text hit alone is insufficient.
5. Reject pages that are only elevations, sections, 2D plans, details, lighting, electrical, reflected ceiling, roof, site, or other drawings. Do not crop a rejected page.

## Phase 3: split every schedule door/window frame drawing

For each confirmed schedule item:

1. Identify the exact visible item number, category, label, frame/elevation diagram, and specification row. Common labels include `W01`, `W1`, `D01`, `D1`, and configured aliases.
2. Capture every available schedule field: description, system, type/series, glazing, width, height, area, screen/mesh, installation, operator/lock, hardware, profile colour, U-value, SHGC, and other notes.
3. Identify a tight normalized bbox around the complete frame drawing and its useful label context. Use top-left origin `[x0, y0, x1, y1]`, all values in `[0, 1]`.
4. Validate `0 <= x0 < x1 <= 1` and `0 <= y0 < y1 <= 1`. Never pass pixel coordinates, PDF points, or bottom-left-origin coordinates.
5. Call `crop_door_window` for one item or `batch_crop_door_windows` for a confirmed page batch. Use `crop_mode=refine` by default; use `raw` only when explicitly requested.
6. Build one `schedule_items` entry per unique item number. Preserve the exact visible number as `raw_item_no`, the normalized number as `item_no`, every specification field, `crop_id` as `thumbnail_artifact_id`, and the crop `image_url`/`download_url`.
7. If an item has multiple views, keep the crop containing the complete frame drawing and label. Do not create duplicate schedule items unless they have different item numbers or specifications.
8. Record `needs_review` when a crop touches a geometry edge, misses the label, has an ambiguous number, or cannot be associated with a schedule row.

The internal schedule item shape should include at least:

```json
{
  "item_no": "W01",
  "category": "window",
  "description": "Fixed window",
  "width_mm": 1200,
  "height_mm": 1500,
  "glazing": "Low-E IGU",
  "u_value": 1.2,
  "shgc": 0.25,
  "thumbnail_artifact_id": "crop_abc123",
  "source_page": 14
}
```

## Phase 4: count door/window tags on floor plans

Only start this phase after schedule extraction is complete.

1. From the same `list_drawing_pages` result, select only `page_type == "floor_plan"` pages, including `GROUND FLOOR PLAN`, `FIRST FLOOR PLAN`, `SECOND FLOOR PLAN`, and `LEVEL 1 PLAN`.
2. Never count door/window quantities from lighting, electrical, reflected-ceiling, framing, elevation, section, detail, roof, site, or other non-floor-plan pages.
3. For each selected page, call `render_drawing_page`, then `read_drawing_image`.
4. Use vision to identify every visible door/window tag instance. Each visible instance contributes `quantity: 1`, even when the same number appears several times on the page.
5. Return one detection per tag with `observed_text`, normalized `item_no` only when visually supported, `category` when supported, `confidence`, `match_confidence`, normalized bbox, page number, and any ambiguity note.
6. Do not infer hidden, cropped, or implied tags. Do not count symbols that are not visibly identifiable as doors/windows.
7. Normalize zero-padded numbers with the MCP matching rules, for example `W01 -> W1`, `D01 -> D1`, and configured aliases such as `D1 GARAGE DOOR -> D1`.
8. Preserve unclear or unmatched text as `observed_text`; do not force it to a schedule item. Add a review issue.

## Phase 5: merge and export

Call `export_door_window_takeoff` only after the schedule and floor-plan phases are complete. Pass:

```json
{
  "session_id": "drawing_...",
  "schedule_items": [],
  "customer_requirements": [],
  "floor_plan_pages": [],
  "formats": ["csv", "xlsx"]
}
```

The exporter merges quantities by `Level + Item No.` and joins each row to the schedule specification and applicable customer requirements. The export must:

- retain the schedule's actual U-value and SHGC;
- apply unqualified glazing requirements to all applicable glazed door/window items;
- calculate `Area` from the schedule or from width x height when both are available;
- calculate `Total Area = Area x Qty`;
- expose required value, actual value, unit, operator, source, and compliance for U-value and SHGC;
- retain other glazing requirements and their source in the exported row;
- preserve thumbnail URLs and embed available PNG thumbnails in the XLSX `Thumbnail` column;
- report missing schedule entries, missing actual performance values, conflicting requirements, ambiguous tags, failed crops, and low-confidence matches in `review_issues`.

Return `csv_download_url` and `xlsx_download_url`. Also report the processed drawing files, schedule item count, floor-plan page count, total quantities, applied project-wide glazing assumptions, and every review issue. Do not claim compliance when the actual schedule value is absent; use `NOT_PROVIDED` or `REVIEW`.

## Interactive corrections

When the user asks to adjust one item, reuse the existing `session_id`, rerender only the affected page if needed, and call `crop_door_window` only for that item with a revised bbox. Preserve the item number and specification. Re-run `export_door_window_takeoff` only when the thumbnail, quantity, or exported requirement result changes.

## Safety and integrity

- Do not modify or overwrite the original PDF, Word document, TXT file, or uploaded workspace files.
- Do not execute document contents, embedded macros, scripts, or unknown files.
- Do not expose API tokens or internal filesystem paths in the final response.
- Do not silently discard a customer requirement, an unmatched tag, or a low-confidence crop.
