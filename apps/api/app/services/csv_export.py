"""CSV export helpers.

Activity exports stream straight from a server-side database cursor: the query selects only
the columns the CSV needs (no ORM entities, so no evidence / review / revision collections
are loaded), rows are fetched ``EXPORT_BATCH_SIZE`` at a time, and each batch is written to
the response as soon as it is read. Memory stays bounded however large the export grows.

The request's ``AsyncSession`` (a ``yield`` dependency) stays open until the response has
been sent — FastAPI >= 0.118 runs dependency teardown after a ``StreamingResponse`` finishes.
"""
import csv
import io
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import Row, Select
from sqlalchemy.ext.asyncio import AsyncSession

# Rows fetched from the cursor (and written to the response) per chunk.
EXPORT_BATCH_SIZE = 1000


def safe_csv(value: object) -> object:
    """Prevent spreadsheet applications from interpreting untrusted CSV cells as formulas."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_lines(rows: Iterable[Sequence[Any]], *, guard: bool = True) -> str:
    """Render ``rows`` as CSV text (``csv`` module defaults: minimal quoting, CRLF)."""
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow([safe_csv(value) for value in row] if guard else row)
    return output.getvalue()


async def stream_csv(
    db: AsyncSession,
    header: Sequence[str],
    query: Select,
    to_row: Callable[[Row], Sequence[Any]],
    batch_size: int | None = None,
) -> AsyncIterator[str]:
    """Yield the header, then one CSV chunk per ``batch_size`` rows of ``query``."""
    batch_size = batch_size or EXPORT_BATCH_SIZE
    yield csv_lines([header], guard=False)
    result = await db.stream(query.execution_options(yield_per=batch_size))
    try:
        async for partition in result.partitions(batch_size):
            yield csv_lines(to_row(row) for row in partition)
    finally:
        await result.close()


def streaming_csv_response(
    db: AsyncSession,
    header: Sequence[str],
    query: Select,
    to_row: Callable[[Row], Sequence[Any]],
    filename: str,
) -> StreamingResponse:
    return StreamingResponse(
        stream_csv(db, header, query, to_row),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
