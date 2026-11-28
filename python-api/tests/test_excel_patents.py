"""The patents sheet in the enriched Excel workbook.

The enriched workbook was the one download with no patent information in it:
SMILES, activities and publications, but not a single patent number, assignee
or link — so "patent information" lived only in the engine's own workbook.
"""

import sys
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API_DIR))

import openpyxl  # noqa: E402
import enrich_exports  # noqa: E402

NODES = {
    "COMPOUND:C1": {
        "uid:ID": "COMPOUND:C1",
        ":LABEL": "Entity;Compound",
        "name": "Atrazine",
        "canonical_id": "MXWJVTOOROXGIU-UHFFFAOYSA-N",
        "queried_compound": "",
    },
    "PATENT:US-20190021252-A1": {
        "uid:ID": "PATENT:US-20190021252-A1",
        ":LABEL": "Entity;Patent",
        "name": "US Patent US-20190021252-A1 (Atrazine)",
        "patent_number": "US-20190021252-A1",
        "canonical_id": "US-20190021252-A1",
        "publication_year": "2019",
        "assignee": "Example Assignee",
        "queried_compound": "Atrazine",
        "google_patents_url": "",
    },
}

EDGES = [
    {
        ":START_ID": "COMPOUND:C1",
        ":END_ID": "PATENT:US-20190021252-A1",
        ":TYPE": "PATENTED_IN",
    }
]


class PatentsSheet(unittest.TestCase):
    def test_sheet_lists_every_patent_with_its_link(self):
        register = enrich_exports._patent_register(NODES, EDGES)
        self.assertIsNotNone(register)
        self.assertEqual(register["patent_count"], 1)

        wb = openpyxl.Workbook()
        enrich_exports.write_patents_sheet(wb, register)

        self.assertIn("Patents", wb.sheetnames)
        ws = wb["Patents"]
        self.assertEqual(ws.cell(row=1, column=1).value, "Patent Number")
        self.assertEqual(ws.cell(row=1, column=6).value, "Google Patents Link")
        self.assertEqual(ws.cell(row=2, column=1).value, "US-20190021252-A1")
        self.assertEqual(ws.cell(row=2, column=3).value, "Example Assignee")
        self.assertEqual(ws.cell(row=2, column=5).value, "Atrazine")
        # The link is the canonical document URL, the same one the results
        # screen uses, and it is a live hyperlink in the cell.
        self.assertEqual(
            ws.cell(row=2, column=6).hyperlink.target,
            "https://patents.google.com/patent/US20190021252A1",
        )

    def test_a_search_without_patents_still_produces_a_readable_sheet(self):
        register = enrich_exports._patent_register({"COMPOUND:C1": NODES["COMPOUND:C1"]}, [])
        wb = openpyxl.Workbook()
        enrich_exports.write_patents_sheet(wb, register)
        ws = wb["Patents"]
        self.assertEqual(ws.max_row, 1)


if __name__ == "__main__":
    unittest.main()
