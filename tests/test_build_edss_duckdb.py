import csv
import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_edss_duckdb.py"
SPEC = importlib.util.spec_from_file_location("build_edss_duckdb", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class BuildEdssDuckDBTests(unittest.TestCase):
    def test_table_names_are_source_scoped(self):
        higher = builder.table_key({"source": "고등교육통계", "catalog_code": "0101"})
        disclosure = builder.table_key({"source": "대학정보공시", "catalog_code": "0101"})
        self.assertEqual(higher, ("higher_education", "panel_0101"))
        self.assertEqual(disclosure, ("university_disclosure", "panel_0101"))

    def test_rejects_unsafe_catalog_code(self):
        with self.assertRaisesRegex(RuntimeError, "unsafe catalog code"):
            builder.table_name("01-01")

    def test_quote_identifier_doubles_quotes(self):
        self.assertEqual(builder.quote_identifier('a"b'), '"a""b"')

    def test_catalog_rejects_duplicate_table_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = root / "panel.csv.gz"
            with gzip.open(panel, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["_panel_year", "개방ID"])
                writer.writerow(["2024", "0000000001"])
            catalog = root / "catalog.csv"
            fields = [
                "source",
                "catalog_code",
                "dataset",
                "access_tier",
                "row_count",
                "column_count",
                "output_path",
                "output_bytes",
                "output_sha256",
            ]
            row = {
                "source": "고등교육통계",
                "catalog_code": "0101",
                "dataset": "테스트",
                "access_tier": "panel",
                "row_count": "1",
                "column_count": "2",
                "output_path": "panel.csv.gz",
                "output_bytes": str(panel.stat().st_size),
                "output_sha256": "0" * 64,
            }
            with catalog.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows([row, row])
            with self.assertRaisesRegex(RuntimeError, "duplicate DuckDB table key"):
                builder.read_catalog(catalog, root)


if __name__ == "__main__":
    unittest.main()
