import datetime as dt
import io
import json
import zipfile
import tempfile
import unittest
from pathlib import Path

import build_consensus_data as C


def make_xlsx(rows):
    """문자열은 sharedStrings, 숫자는 값 그대로 넣은 최소 xlsx."""
    strings, sheet = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            if value is None:
                continue
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet)}</sheetData></worksheet>')
    return buffer.getvalue()


SCREENER = [
    [], ["스크리너"], ["시장 : 전체"], [],
    ["종목코드", "종목명", "시장", "매출액(FY1)(억원)", "영업이익(FY1)(억원)", "영업이익(FY2)(억원)", "영업이익(Fwd.12M)(억원)"],
    ["005930", "삼성전자", "KOSPI", "7207350.32", "3830971.64", 6165286.27, "597076005.3"],
    ["204270", "제이앤티씨", "KOSDAQ", "2000", "-432", None, "1"],
    ["", "합계", "", "1", "1", "1", "1"],
]


class ConsensusDataTest(unittest.TestCase):
    def test_fnguide_screener_xlsx_is_read_per_fiscal_period(self):
        rows = C.parse_screener(C.read_xlsx(make_xlsx(SCREENER)))
        self.assertEqual(rows, [["005930", "삼성전자", "FY1", 3830971.64], ["005930", "삼성전자", "FY2", 6165286.27], ["204270", "제이앤티씨", "FY1", -432.0]])
        self.assertEqual(C.normalize_period("fy1"), "FY1")
        with self.assertRaisesRegex(ValueError, "종목코드"):
            C.parse_screener([["이름", "값"]])
        with self.assertRaisesRegex(ValueError, "추정치 열"):
            C.parse_screener([["종목코드", "종목명", "영업이익(FY0)(억원)", "영업이익(Fwd.12M)(억원)"]])

    def test_xlsx_in_inbox_builds_annual_metric_and_skips_broken_files(self):
        later = [row[:] for row in SCREENER]
        later[5][4] = "4000000"
        with tempfile.TemporaryDirectory() as folder:
            inbox, history, out = Path(folder, "inbox"), Path(folder, "history"), Path(folder, "out.json")
            inbox.mkdir()
            (inbox / "FnGuide_FnScreener_스크리너_20261123115316.xlsx").write_bytes(make_xlsx(SCREENER))
            (inbox / "FnGuide_FnScreener_스크리너_20261125090000.XLSX").write_bytes(make_xlsx(later))
            (inbox / "2026-11-24.xlsx").write_bytes(b"not a zip")
            (inbox / "README.md").write_text("안내", encoding="utf-8")
            self.assertEqual(C.main(["--inbox", str(inbox), "--history", str(history), "--output", str(out)]), 0)
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((data["asof"], data["previous"], data["observations"], data["metric"]), ("2026-11-25", "2026-11-23", 2, "연간 영업이익 예상치"))
        rows = {r["code"]: r for r in data["rows"]}
        self.assertEqual((rows["005930"]["period"], rows["005930"]["current"], rows["005930"]["prev_pct"], rows["005930"]["class"]), ("FY1", 4000000.0, 4.41, "상향"))
        self.assertEqual((rows["204270"]["prev_pct"], rows["204270"]["class"]), (0.0, "유지"))

    def test_normalizers(self):
        self.assertEqual(C.date_from_name("2026-11-20.csv"), "2026-11-20")
        self.assertEqual(C.date_from_name("20261120_fn.csv"), "2026-11-20")
        self.assertIsNone(C.date_from_name("consensus.csv"))
        self.assertIsNone(C.date_from_name("20261340.csv"))
        self.assertEqual(C.normalize_code("A005930"), "005930")
        self.assertEqual(C.normalize_code("5930"), "005930")
        self.assertEqual(C.normalize_code("0150K0"), "0150K0")
        self.assertIsNone(C.normalize_code("삼성전자"))
        self.assertEqual(C.normalize_period("2026.12"), "202612")
        self.assertEqual(C.normalize_period(""), "")
        self.assertEqual(C.to_number("1,234.5"), 1234.5)
        self.assertEqual(C.to_number("-30"), -30.0)
        self.assertIsNone(C.to_number(""))
        self.assertIsNone(C.to_number("N/A"))

    def test_parse_csv_accepts_aliases_encodings_and_skips_blanks(self):
        text = "종목코드,종목명,대상분기,영업이익\nA005930,삼성전자,2026.12,\"123,456\"\n000660,SK하이닉스,2026.12,\n000660,SK하이닉스,2027.03,90000\n005930,중복,2026.12,1\n"
        rows = C.parse_csv(text.encode("cp949"))
        self.assertEqual(rows, [["005930", "삼성전자", "202612", 123456.0], ["000660", "SK하이닉스", "202703", 90000.0]])
        self.assertEqual(C.parse_csv("code,op\n5930,10\n".encode("utf-8-sig")), [["005930", "", "", 10.0]])
        with self.assertRaises(ValueError):
            C.parse_csv("name,value\nA,1\n".encode("utf-8"))

    def test_change_and_class_rules(self):
        self.assertEqual(C.change_pct(71, 30), 136.67)
        self.assertEqual(C.change_pct(-50, -100), 50.0)
        self.assertIsNone(C.change_pct(10, 0))
        self.assertIsNone(C.change_pct(10, None))
        self.assertEqual(C.classify(None, None, None, 10, None), "비교 불가")
        self.assertEqual(C.classify(50.0, None, None, -50, -100), "적자 축소")
        self.assertEqual(C.classify(0.0, 5.0, None, 10, 10), "상향")
        self.assertEqual(C.classify(-1.0, 0.0, -3.0, 9, 10), "하향")
        self.assertEqual(C.classify(2.0, -1.0, None, 10, 9), "엇갈림")
        self.assertEqual(C.classify(0.0, 0.0, None, 10, 10), "유지")

    def test_pipeline_accumulates_and_compares_same_period_only(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox, history, out = Path(folder, "inbox"), Path(folder, "history"), Path(folder, "out.json")
            inbox.mkdir()
            head = "종목코드,종목명,대상분기,영업이익\n"
            (inbox / "2026-10-20.csv").write_text(head + "000001,가,202612,100\n000002,나,202612,-200\n", encoding="utf-8")
            (inbox / "2026-11-13.csv").write_text(head + "000001,가,202612,110\n000002,나,202612,-150\n000003,다,202612,50\n", encoding="utf-8")
            (inbox / "2026-11-19.csv").write_text(head + "000001,가,202612,110\n000002,나,202612,-150\n000003,다,202609,40\n", encoding="utf-8")
            (inbox / "2026-11-20.csv").write_text(head + "000001,가,202612,121\n000002,나,202612,-120\n000003,다,202612,50\n000004,라,202612,7\n000004,라,202703,9\n", encoding="utf-8")
            (inbox / "메모.csv").write_text(head, encoding="utf-8")
            (inbox / "2026-11-21.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(C.main(["--inbox", str(inbox), "--history", str(history), "--output", str(out)]), 0)
            self.assertEqual(sorted(p.name for p in history.glob("*.json")), ["2026-10-20.json", "2026-11-13.json", "2026-11-19.json", "2026-11-20.json"])
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((data["asof"], data["previous"], data["week_base"], data["month_base"]), ("2026-11-20", "2026-11-19", "2026-11-13", "2026-10-20"))
        self.assertEqual(data["observations"], 4)
        self.assertEqual(data["summary"], {"up": 2, "down": 0, "flat": 0, "na": 2})
        rows = {r["code"]: r for r in data["rows"]}
        self.assertEqual((rows["000001"]["prev_pct"], rows["000001"]["week_pct"], rows["000001"]["month_pct"]), (10.0, 10.0, 21.0))
        self.assertEqual(rows["000001"]["series"], [100.0, 110.0, 110.0, 121.0])
        self.assertEqual((rows["000002"]["class"], rows["000002"]["prev_pct"], rows["000002"]["month_pct"]), ("적자 축소", 20.0, 40.0))
        self.assertIsNone(rows["000003"]["prev_pct"], "직전 관측의 대상분기가 다르면 비교하지 않는다")
        self.assertEqual(rows["000003"]["week_pct"], 0.0)
        self.assertEqual(rows["000003"]["series"], [None, 50.0, None, 50.0])
        self.assertEqual((rows["000004"]["period"], rows["000004"]["class"]), ("202612", "비교 불가"))
        self.assertEqual(data["rows"][0]["code"], "000002", "누적 주간 상향순")

    def test_no_history_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder, "out.json")
            self.assertEqual(C.main(["--inbox", str(Path(folder, "none")), "--history", str(Path(folder, "h")), "--output", str(out)]), 0)
            self.assertFalse(out.exists())
        with self.assertRaises(ValueError):
            C.build([], dt.datetime(2026, 11, 20, tzinfo=C.KST))

    def test_old_snapshots_are_pruned(self):
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder)
            for date in ("2025-01-02", "2026-11-20"):
                (history / f"{date}.json").write_text(json.dumps({"date": date, "rows": [["000001", "가", "202612", 1.0]]}), encoding="utf-8")
            snapshots = C.load_history(history, dt.date(2026, 11, 23))
            self.assertEqual([s["date"] for s in snapshots], ["2026-11-20"])
            self.assertFalse((history / "2025-01-02.json").exists())


if __name__ == "__main__":
    unittest.main()
