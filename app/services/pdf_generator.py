"""
PDF Generator Service — Phase B3

Responsibilities:
  1. Render Jinja2 HTML template using structured resume JSON.
  2. Compile ATS-friendly A4 PDF binary stream (using xhtml2pdf with WeasyPrint fallback).
"""

import io
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# Template directory path
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


def render_resume_html(resume_data: dict[str, Any]) -> str:
    """
    Render ATS resume Jinja2 HTML template with structured resume data.
    """
    template = _env.get_template("resume_ats.html")

    # Extract sections with safe defaults
    personal_info = resume_data.get("personal_info", {})
    work_experiences = resume_data.get("work_experiences", [])
    education = resume_data.get("education", [])
    skill_categories = resume_data.get("skill_categories", [])
    certifications = resume_data.get("certifications", [])
    projects = resume_data.get("projects", [])

    return template.render(
        personal_info=personal_info,
        work_experiences=work_experiences,
        education=education,
        skill_categories=skill_categories,
        certifications=certifications,
        projects=projects,
    )


def generate_resume_pdf(resume_data: dict[str, Any]) -> bytes:
    """
    Compile rendered resume HTML string into an ATS-friendly A4 PDF binary.

    Uses xhtml2pdf for pure-python cross-platform compilation, with
    WeasyPrint fallback if available on the host environment.

    Returns:
        bytes of the compiled PDF file.
    """
    rendered_html = render_resume_html(resume_data)
    logger.info("Compiling PDF (html_len=%d)", len(rendered_html))

    # 1. Try WeasyPrint if system libraries exist
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=rendered_html).write_pdf()
        logger.info("Compiled PDF via WeasyPrint (%d bytes)", len(pdf_bytes))
        return pdf_bytes
    except Exception as exc:
        logger.debug("WeasyPrint unavailable (%s); falling back to xhtml2pdf", exc)

    # 2. Pure Python fallback via xhtml2pdf
    from xhtml2pdf import pisa
    out_stream = io.BytesIO()
    pisa_status = pisa.CreatePDF(src=rendered_html, dest=out_stream)

    if pisa_status.err:
        logger.error("xhtml2pdf encountered errors during PDF rendering.")

    pdf_bytes = out_stream.getvalue()
    logger.info("Compiled PDF via xhtml2pdf (%d bytes)", len(pdf_bytes))
    return pdf_bytes
