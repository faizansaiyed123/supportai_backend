"""
Text extraction from uploaded documents (Sec. 13). Deliberately narrow: given raw bytes + a
mime type, return plain text. No mime type is trusted from the client-supplied header alone -
services/knowledge.py sniffs the actual content type via python-magic before calling this
(Sec. 10 - MIME validation).
"""
import io


class ExtractionError(Exception):
    pass


SUPPORTED_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "application/pdf",
}


def extract_text(*, content: bytes, mime_type: str) -> str:
    if mime_type in ("text/plain", "text/markdown"):
        return content.decode("utf-8", errors="replace")

    if mime_type == "text/html":
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(separator="\n")

    if mime_type == "application/pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(content))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ExtractionError(f"Failed to parse PDF: {exc}") from exc

    raise ExtractionError(f"Unsupported mime type: {mime_type}")


def sniff_mime_type(content: bytes, *, declared_filename: str) -> str:
    """Sniffs actual content type from file bytes rather than trusting the client's
    Content-Type header or the file extension alone (Sec. 10)."""
    try:
        import magic

        detected = magic.from_buffer(content, mime=True)
    except Exception:
        # python-magic depends on libmagic being installed; fall back to a conservative
        # extension-based guess rather than failing upload entirely if it's unavailable.
        ext = declared_filename.lower().rsplit(".", 1)[-1] if "." in declared_filename else ""
        fallback = {
            "txt": "text/plain",
            "md": "text/markdown",
            "html": "text/html",
            "htm": "text/html",
            "pdf": "application/pdf",
        }
        detected = fallback.get(ext, "application/octet-stream")
    return detected
