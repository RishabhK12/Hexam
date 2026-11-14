"""Georgia Tech Institutional Research & Planning (IRP) adapters.

* ``parse_coe_enrollment`` — the College of Engineering "Enrollment by Major"
  dashboard page (HTML tables). Gives undergraduate headcounts by major and
  year-in-program (1-5) per academic year (Fall terms). Engineering majors only.
* ``parse_factbook_ug_by_college`` — the Fact Book PDF table "Undergraduate
  Enrollment by College, Fall Term History". Gives college-level totals, used
  for majors the engineering dashboard does not cover (e.g. CS).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


@dataclass(frozen=True)
class MajorEnrollment:
    major_name: str
    academic_year: str  # "2026-2027"
    undergrad_by_year: tuple[int, int, int, int, int]  # years 1..5 (5 = fifth year and beyond)
    undergrad_total: int


class _TableRows(HTMLParser):
    """Collects every table as a list of rows; each row is a list of cell texts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _int(s: str) -> int:
    s = s.replace(",", "").replace("\xa0", "").strip()
    return int(s) if s else 0


_AY = re.compile(r"^\d{4}-\d{4}$")


def parse_coe_enrollment(path: Path) -> list[MajorEnrollment]:
    """Return one record per (major, academic year) from the COE dashboard HTML.

    Table layout per major: ``[Major (rowspan), AY (rowspan=2), FT, y1..y5, UG total, MS, PhD, G total, grand]``,
    then a ``PT`` row, then ``[Total, y1..y5, UG total, ...]`` for that academic year.
    """
    p = _TableRows()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    out: list[MajorEnrollment] = []
    for table in p.tables:
        major: str | None = None
        ay: str | None = None
        for row in table:
            cells = [html.unescape(c) for c in row]
            if not cells:
                continue
            # A row that opens a major starts with the major name, then the academic year.
            if len(cells) > 2 and _AY.match(cells[1]) and not _AY.match(cells[0]):
                major, ay = cells[0], cells[1]
            elif _AY.match(cells[0]):
                ay = cells[0]
            if cells[0] == "Total" and major and ay:
                nums = [_int(c) for c in cells[1:7]]
                years, total = tuple(nums[:5]), nums[5]
                if sum(years) != total:
                    raise ValueError(f"IRP {major} {ay}: years {years} do not sum to total {total}")
                out.append(MajorEnrollment(major, ay, years, total))  # type: ignore[arg-type]
    if not out:
        raise ValueError(f"no enrollment tables parsed from {path}")
    return out


def parse_factbook_ug_by_college(pdf_path: Path) -> dict:
    """Extract "Undergraduate Enrollment by College, Fall Term History" from a Fact Book PDF.

    Returns ``{"title": ..., "page": n, "years": [...], "colleges": {name: {year: count}}}``.
    """
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if "Undergraduate Enrollment by College, Fall Term History" not in text:
            continue
        lines = text.splitlines()
        header = next((ln for ln in lines if ln.strip().startswith("College ") and re.search(r"\b20\d\d\b", ln)), None)
        if header is None:
            continue
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", header)]
        colleges: dict[str, dict[int, int]] = {}
        row = re.compile(r"^(?P<name>[A-Z][A-Za-z/&\-\* ]+?)\s+(?P<nums>(?:[\d,]+\s+){%d})" % len(years))
        for ln in lines[lines.index(header) + 1 :]:
            m = row.match(ln.strip() + " ")
            if not m:
                if colleges:
                    break
                continue
            nums = [_int(n) for n in m.group("nums").split()]
            colleges[m.group("name").strip().rstrip("*").strip()] = dict(zip(years, nums))
        if colleges:
            return {"title": "Undergraduate Enrollment by College, Fall Term History", "page": i + 1, "years": years, "colleges": colleges}
    raise ValueError(f"undergraduate-by-college table not found in {pdf_path}")
