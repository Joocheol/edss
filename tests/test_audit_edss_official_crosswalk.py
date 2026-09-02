import csv
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_edss_official_crosswalk as audit


WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="취업(1종), 평생(2종)" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="worksheets/sheet1.xml"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
</Relationships>"""


def inline_cell(reference: str, value: str) -> str:
    return (
        f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
    )


def write_test_workbook(path: Path) -> None:
    rows = {
        4: ["구분", "대영역", "번호", "영역", "주요제공항목", "제공년도", "제공수준",
            "학교코드 제공여부", "비고"],
        5: ["취업통계", "취업현황", "0001", "학생인적취업정보", "조사년도,개방ID", "2010~2022",
            "", "Y", ""],
        6: ["", "", "", "", "조사년도,학교명", "2023~2024", "", "Y", ""],
    }
    row_xml = []
    for row_number, values in rows.items():
        cells = []
        for index, value in enumerate(values):
            column = chr(ord("A") + index)
            cells.append(inline_cell(f"{column}{row_number}", value))
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("xl/workbook.xml", WORKBOOK_XML)
        workbook.writestr("xl/_rels/workbook.xml.rels", RELS_XML)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def write_nested_csv(path: Path, encoding: str) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["조사년도", "학교명", "학과명"])
    writer.writerow(["2024", "테스트대학교", "테스트학과"])
    inner_bytes = io.BytesIO()
    with zipfile.ZipFile(inner_bytes, "w") as inner:
        inner.writestr("employment.csv", buffer.getvalue().encode(encoding))
    with zipfile.ZipFile(path, "w") as outer:
        outer.writestr("employment.zip", inner_bytes.getvalue())


class OfficialCrosswalkAuditTests(unittest.TestCase):
    def test_reads_provider_list_school_code_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "provider.xlsx"
            write_test_workbook(workbook)

            evidence = audit.provider_employment_evidence(workbook)

            self.assertEqual(evidence["period"], "2023~2024")
            self.assertEqual(evidence["listed_school_code_provided"], "Y")
            self.assertTrue(evidence["listed_has_school_name"])
            self.assertFalse(evidence["listed_has_open_id"])

    def test_reads_nested_archive_without_inventing_school_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "employment.zip"
            write_nested_csv(archive, "cp949")

            evidence = audit.nested_csv_header(archive)

            self.assertEqual(evidence["archive_depth"], 2)
            self.assertTrue(evidence["has_school_name"])
            self.assertFalse(evidence["has_open_id"])
            self.assertEqual(evidence["school_code_fields"], [])


if __name__ == "__main__":
    unittest.main()
