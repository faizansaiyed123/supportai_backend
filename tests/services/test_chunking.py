from app.services.chunking import _count_tokens, chunk_document


def test_empty_text_returns_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []


def test_short_plain_text_is_a_single_chunk():
    text = "This is a short FAQ answer. It should not be split."
    chunks = chunk_document(text)
    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].heading_path is None
    assert chunks[0].order == 0


def test_markdown_headings_produce_heading_paths():
    text = (
        "# Billing\n\n"
        "Some intro text about billing.\n\n"
        "## Refunds\n\n"
        "Refunds are processed within 5 business days.\n\n"
        "## Invoices\n\n"
        "Invoices are emailed monthly.\n"
    )
    chunks = chunk_document(text)
    heading_paths = [c.heading_path for c in chunks]
    assert "Billing" in heading_paths
    assert "Billing > Refunds" in heading_paths
    assert "Billing > Invoices" in heading_paths

    refunds_chunk = next(c for c in chunks if c.heading_path == "Billing > Refunds")
    assert "5 business days" in refunds_chunk.content


def test_oversized_section_is_split_with_paragraph_boundaries_preferred():
    # Build a section well over the default 800-token max, made of many small paragraphs,
    # so the splitter should break on paragraph boundaries rather than mid-sentence.
    paragraphs = [f"This is paragraph number {i} with some extra padding words to add length." for i in range(120)]
    text = "# Long Section\n\n" + "\n\n".join(paragraphs)

    chunks = chunk_document(text, max_tokens=200, overlap_tokens=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 200 + 5  # small tolerance for boundary rounding
        # Each chunk should consist of whole paragraphs (no sentence cut mid-word at the
        # start/end, given our paragraphs each start with "This is paragraph").
    assert all(c.heading_path == "Long Section" for c in chunks)


def test_single_paragraph_longer_than_max_tokens_falls_back_to_sliding_window():
    huge_paragraph = " ".join(["word"] * 3000)  # no paragraph breaks at all
    chunks = chunk_document(huge_paragraph, max_tokens=200, overlap_tokens=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 200


def test_chunk_order_is_sequential_and_starts_at_zero():
    text = "# A\n\npara one\n\n# B\n\npara two\n\n# C\n\npara three\n"
    chunks = chunk_document(text)
    orders = [c.order for c in chunks]
    assert orders == list(range(len(chunks)))


def test_token_counting_is_consistent_with_chunk_results():
    text = "# Heading\n\nSome content that will be measured."
    chunks = chunk_document(text)
    for c in chunks:
        assert c.token_count == _count_tokens(c.content)

