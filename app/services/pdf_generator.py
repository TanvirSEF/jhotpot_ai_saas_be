"""ATS-friendly resume HTML rendering, PDF compilation, and validation."""

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pypdf import PdfReader

from app.schemas.resume import ResumeContent

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


class PdfGenerationError(RuntimeError):
    """The renderer could not produce a valid ATS PDF."""


@dataclass(frozen=True)
class PdfValidationResult:
    page_count: int
    extracted_text: str


def render_resume_html(resume_data: dict[str, Any]) -> str:
    """Validate canonical data and render the resume HTML template."""
    content = ResumeContent.model_validate(resume_data)
    return _env.get_template("resume_ats.html").render(
        **content.model_dump(mode="json")
    )


def generate_resume_pdf(resume_data: dict[str, Any]) -> bytes:
    """Compile canonical resume data into an A4 PDF byte stream."""
    rendered_html = render_resume_html(resume_data)
    logger.info("Compiling resume PDF html_length=%d", len(rendered_html))

    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=rendered_html).write_pdf()
        if pdf_bytes:
            logger.info("Compiled resume PDF renderer=weasyprint bytes=%d", len(pdf_bytes))
            return pdf_bytes
    except Exception as exc:
        logger.info(
            "WeasyPrint unavailable; using fallback error_type=%s",
            type(exc).__name__,
        )

    try:
        from xhtml2pdf import pisa

        out_stream = io.BytesIO()
        status = pisa.CreatePDF(src=rendered_html, dest=out_stream)
        if status.err:
            raise PdfGenerationError("Fallback PDF renderer reported an error.")
        pdf_bytes = out_stream.getvalue()
        if not pdf_bytes:
            raise PdfGenerationError("Fallback PDF renderer returned no content.")
        logger.info("Compiled resume PDF renderer=xhtml2pdf bytes=%d", len(pdf_bytes))
        return pdf_bytes
    except PdfGenerationError:
        raise
    except Exception as exc:
        raise PdfGenerationError("No PDF renderer could compile the resume.") from exc


def validate_resume_pdf(
    pdf_bytes: bytes,
    *,
    expected_text: tuple[str, ...] = (),
) -> PdfValidationResult:
    """Prove that a PDF has pages and selectable, non-empty text on every page."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        pages = list(reader.pages)
        page_text = [(page.extract_text() or "").strip() for page in pages]
    except Exception as exc:
        raise PdfGenerationError("Generated file is not a readable PDF.") from exc

    if not page_text:
        raise PdfGenerationError("Generated PDF contains no pages.")
    if any(not text for text in page_text):
        raise PdfGenerationError("Generated PDF contains a page without selectable text.")
    for page in pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if abs(width - 595.28) > 5 or abs(height - 841.89) > 5:
            raise PdfGenerationError("Generated PDF contains a non-A4 page.")

    extracted_text = "\n".join(page_text)
    normalized_text = " ".join(extracted_text.casefold().split())
    for anchor in expected_text:
        normalized_anchor = " ".join(anchor.casefold().split())
        if normalized_anchor and normalized_anchor not in normalized_text:
            raise PdfGenerationError(
                "Generated PDF is missing expected selectable resume text."
            )

    return PdfValidationResult(
        page_count=len(page_text),
        extracted_text=extracted_text,
    )


def generate_validated_resume_pdf(
    resume_data: dict[str, Any],
) -> tuple[bytes, PdfValidationResult]:
    """Compile and validate a PDF before it can be marked ready."""
    content = ResumeContent.model_validate(resume_data)
    pdf_bytes = generate_resume_pdf(content.model_dump(mode="json"))
    validation = validate_resume_pdf(
        pdf_bytes,
        expected_text=(content.personal_info.full_name, content.personal_info.email),
    )
    return pdf_bytes, validation
