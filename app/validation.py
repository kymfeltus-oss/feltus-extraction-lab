from __future__ import annotations

from .extractor import Extraction


def validate_extraction(extraction: Extraction) -> tuple[list[str], list[str], float]:
    """Step 1 validates page coverage only. Interpretation belongs to Step 2."""
    if extraction.page_count == 0:
        return [], ["The PDF contained no readable pages."], 0.0
    missing = [page.page_number for page in extraction.pages if not page.raw_text.strip()]
    warnings = []
    if missing:
        warnings.append(
            "No text was extracted from page(s) " + ", ".join(map(str, missing)) +
            ". These pages require OCR or professional review."
        )
    extracted_count = extraction.page_count - len(missing)
    errors = ["No text could be extracted from any page."] if extracted_count == 0 else []
    return warnings, errors, round(extracted_count / extraction.page_count, 3)
