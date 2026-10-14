"""Regenerate original, synthetic, offline Web Evidence evaluation snapshots.

No third-party page content or external URLs are downloaded. All hosts use
reserved example domains and are intercepted by the offline benchmark.
"""

from __future__ import annotations

import json
from pathlib import Path

DESTINATION = Path(__file__).parents[1] / "tests/fixtures/web_evidence_v2/cases.json"
DESTINATION_100 = Path(__file__).parents[1] / "tests/fixtures/web_evidence_v2/cases_100.json"
AS_OF = "2026-10-14T00:00:00+07:00"
# The 40-case file is the historical PR1/PR2/PR3 replay set: it is generated
# from the first entries of each group, so extending the groups leaves it
# byte-identical and keeps those reports comparable.
LEGACY_SIZE = {"fact": 10, "buried": 8, "multi_source": 8, "fresh": 7, "unknown_conflicting": 7}
FULL_SIZE = {"fact": 30, "buried": 20, "multi_source": 20, "fresh": 15, "unknown_conflicting": 15}

# ID, question, verified value, document subject. Values are intentionally
# synthetic; they are not statements about real organisations or events.
FACTS = [
    ("fact-01", "Thư viện giả lập Mây Xanh khai trương năm nào?", "2018", "Thư viện Mây Xanh"),
    ("fact-02", "How many stations does the fictional Alder line have?", "14", "Alder line"),
    ("fact-03", "Dự án giả lập Sao Khuê có mấy nhóm tham gia?", "9", "Dự án Sao Khuê"),
    ("fact-04", "What is the capacity of the fictional Moss Hall?", "420", "Moss Hall"),
    ("fact-05", "Vườn tưởng tượng Bờ Nam rộng bao nhiêu hecta?", "26", "Vườn Bờ Nam"),
    ("fact-06", "How many specimens were catalogued in Project Quartz?", "137", "Project Quartz"),
    ("fact-07", "Kênh thử nghiệm Lục Lam dài bao nhiêu km?", "18", "Kênh Lục Lam"),
    ("fact-08", "How many teams attended the imaginary Cloud Cup?", "32", "Cloud Cup"),
    ("fact-09", "Trạm nghiên cứu hư cấu Hải Yến đặt ở độ cao nào?", "840", "Trạm Hải Yến"),
    ("fact-10", "What year was the fictional Cedar Archive founded?", "2009", "Cedar Archive"),
    ("fact-11", "Học viện giả lập Sao Băng có bao nhiêu lớp?", "47", "Học viện Sao Băng"),
    (
        "fact-12",
        "How many bridges does the fictional Larkspur ring road cross?",
        "55",
        "Larkspur ring road",
    ),
    ("fact-13", "Bảo tàng tưởng tượng Đá Quý lưu bao nhiêu hiện vật?", "512", "Bảo tàng Đá Quý"),
    (
        "fact-14",
        "How many seats does the fictional Fern auditorium hold?",
        "634",
        "Fern auditorium",
    ),
    ("fact-15", "Trại giả lập Suối Mơ có bao nhiêu lều?", "78", "Trại Suối Mơ"),
    ("fact-16", "How many volunteers joined the fictional Beacon survey?", "219", "Beacon survey"),
    ("fact-17", "Đài quan sát hư cấu Bắc Cực đo bao nhiêu ngày nắng?", "121", "Đài Bắc Cực"),
    ("fact-18", "What depth does the fictional Tidal gauge report?", "365", "Tidal gauge"),
    ("fact-19", "Xưởng giả lập Gốm Xanh sản xuất bao nhiêu mẫu?", "84", "Xưởng Gốm Xanh"),
    (
        "fact-20",
        "How many citations does the fictional Harbor review contain?",
        "156",
        "Harbor review",
    ),
    ("fact-21", "Thư viện giả lập Nam Phong có bao nhiêu bản thảo?", "933", "Thư viện Nam Phong"),
    ("fact-22", "How many stops are on the fictional Sycamore loop?", "23", "Sycamore loop"),
    ("fact-23", "Phòng thí nghiệm tưởng tượng Vân Hà chạy bao nhiêu thử nghiệm?", "407", "Vân Hà"),
    ("fact-24", "What is the fictional Granite dam height?", "118", "Granite dam"),
    ("fact-25", "Làng giả lập Cát Biển có mấy hộ dân?", "259", "Làng Cát Biển"),
    ("fact-26", "How many hours does the fictional Ember battery last?", "46", "Ember battery"),
    ("fact-27", "Cầu tưởng tượng Trúc Lam dài bao nhiêu mét?", "742", "Cầu Trúc Lam"),
    (
        "fact-28",
        "What member count does the fictional Thistle guild report?",
        "68",
        "Thistle guild",
    ),
    ("fact-29", "Kho giả lập Mộc Tuyết chứa bao nhiêu thùng?", "315", "Kho Mộc Tuyết"),
    (
        "fact-30",
        "How many records did the fictional Driftway survey publish?",
        "902",
        "Driftway survey",
    ),
]

BURIED = [
    ("buried-01", "Báo cáo giả lập Ngân Hà ghi bao nhiêu mẫu ở phụ lục?", "288", "Ngân Hà"),
    ("buried-02", "What is the final audit count in the Orion dossier?", "73", "Orion dossier"),
    ("buried-03", "Biên bản Mộc Miên chốt bao nhiêu hạng mục?", "61", "Mộc Miên"),
    (
        "buried-04",
        "How many units appear in the late section of the Elm ledger?",
        "509",
        "Elm ledger",
    ),
    ("buried-05", "Tài liệu giả lập Bình Minh xác nhận mấy tuyến?", "11", "Bình Minh"),
    (
        "buried-06",
        "What is the recorded batch size in the Willow appendix?",
        "760",
        "Willow appendix",
    ),
    ("buried-07", "Kết luận chương cuối của hồ sơ Hồng Sa là bao nhiêu điểm?", "94", "Hồng Sa"),
    (
        "buried-08",
        "What is the inspection total at the end of the Maple report?",
        "215",
        "Maple report",
    ),
    ("buried-09", "Hồ sơ giả lập Tuyết Sơn ghi bao nhiêu mục ở phụ lục?", "341", "Tuyết Sơn"),
    ("buried-10", "What is the closing tally in the Juniper memo?", "128", "Juniper memo"),
    ("buried-11", "Báo cáo giả lập Rêu Xanh chốt bao nhiêu chỉ tiêu?", "57", "Rêu Xanh"),
    (
        "buried-12",
        "How many entries are listed late in the Poppy register?",
        "613",
        "Poppy register",
    ),
    ("buried-13", "Tài liệu giả lập Đông Phong xác nhận mấy điều khoản?", "36", "Đông Phong"),
    ("buried-14", "What batch count appears in the Quarry annex?", "489", "Quarry annex"),
    ("buried-15", "Kết luận cuối hồ sơ giả lập Mưa Ngâu là bao nhiêu điểm?", "71", "Mưa Ngâu"),
    ("buried-16", "What total does the Sable inventory list at the end?", "855", "Sable inventory"),
    ("buried-17", "Phụ lục giả lập Lam Ngọc ghi bao nhiêu bản?", "144", "Lam Ngọc"),
    (
        "buried-18",
        "How many checks are recorded at the end of the Rowan dossier?",
        "262",
        "Rowan dossier",
    ),
    ("buried-19", "Biên bản giả lập Sương Lam thống kê bao nhiêu ca?", "38", "Sương Lam"),
    ("buried-20", "What is the final count in the Amethyst summary?", "574", "Amethyst summary"),
]

MULTI = [
    (
        "multi-01",
        "Hai đơn vị giả lập Bắc và Nam công bố bao nhiêu hồ sơ?",
        "42",
        "58",
        "Hồ sơ Bắc Nam",
    ),
    (
        "multi-02",
        "What are the fictional East and West trial counts?",
        "17",
        "29",
        "East West trials",
    ),
    ("multi-03", "Thống kê hai nguồn Sơn và Thủy lần lượt là bao nhiêu?", "88", "91", "Sơn Thủy"),
    ("multi-04", "How many permits do the Brook and Hill archives show?", "65", "23", "Brook Hill"),
    ("multi-05", "Hai kho dữ liệu Lam và Tím ghi bao nhiêu bản ghi?", "300", "240", "Lam Tím"),
    ("multi-06", "What totals do the Pine and River registries report?", "12", "34", "Pine River"),
    ("multi-07", "Hãy đối chiếu số máy ở trạm Đỏ và Vàng?", "7", "13", "Trạm Đỏ Vàng"),
    ("multi-08", "What are the two fictional Nova and Vega scores?", "81", "79", "Nova Vega"),
    ("multi-09", "Hai nguồn giả lập Đông và Tây báo bao nhiêu ca?", "63", "90", "Ca Đông Tây"),
    ("multi-10", "What do the fictional Ridge and Vale portals report?", "148", "96", "Ridge Vale"),
    ("multi-11", "Hai bảng giả lập Nắng và Mưa ghi bao nhiêu ngày?", "24", "31", "Nắng Mưa"),
    (
        "multi-12",
        "What totals do the fictional Harbor and Quay ledgers show?",
        "275",
        "209",
        "Harbor Quay",
    ),
    ("multi-13", "Hai trạm giả lập Bắc và Nam đo bao nhiêu milimét?", "402", "377", "Trạm Bắc Nam"),
    (
        "multi-14",
        "How many signups did the fictional Sol and Luna pages record?",
        "332",
        "286",
        "Sol Luna",
    ),
    (
        "multi-15",
        "Hai nhóm giả lập Kim và Mộc hoàn thành bao nhiêu nhiệm vụ?",
        "59",
        "84",
        "Kim Mộc",
    ),
    (
        "multi-16",
        "What are the fictional Drift and Cliff survey counts?",
        "167",
        "198",
        "Drift Cliff",
    ),
    ("multi-17", "Hai hồ sơ giả lập Cam và Chanh ghi bao nhiêu đơn?", "925", "713", "Cam Chanh"),
    (
        "multi-18",
        "What do the fictional Ember and Frost trackers report?",
        "111",
        "87",
        "Ember Frost",
    ),
    (
        "multi-19",
        "Hai bản tổng hợp giả lập Trúc và Tùng nêu bao nhiêu mục?",
        "154",
        "176",
        "Trúc Tùng",
    ),
    (
        "multi-20",
        "What totals do the fictional Ivory and Onyx registers publish?",
        "508",
        "431",
        "Ivory Onyx",
    ),
]

FRESH = [
    (
        "fresh-01",
        "Bản cập nhật giả lập Atlas ngày 14/10/2026 ghi giá trị nào?",
        "101",
        "Atlas update",
    ),
    ("fresh-02", "What is the 2026-10-14 snapshot value for Helix?", "202", "Helix update"),
    (
        "fresh-03",
        "Lúc chốt sổ 14/10/2026, chỉ số giả lập Cánh Buồm là bao nhiêu?",
        "303",
        "Cánh Buồm",
    ),
    (
        "fresh-04",
        "What was the synthetic Pearl reading on October 14, 2026?",
        "404",
        "Pearl update",
    ),
    ("fresh-05", "Số liệu giả lập Gió Mới ngày 14/10/2026 là bao nhiêu?", "505", "Gió Mới"),
    (
        "fresh-06",
        "What number does the 2026-10-14 fake Solstice bulletin state?",
        "606",
        "Solstice",
    ),
    ("fresh-07", "Bảng tổng hợp giả lập Sao Mai ngày 14/10/2026 ghi gì?", "707", "Sao Mai"),
    (
        "fresh-08",
        "Bản tin giả lập Hồng Ngọc ngày 14/10/2026 ghi chỉ số nào?",
        "808",
        "Hồng Ngọc",
    ),
    (
        "fresh-09",
        "What value did the 2026-10-14 synthetic Willow bulletin publish?",
        "909",
        "Willow bulletin",
    ),
    (
        "fresh-10",
        "Chốt ngày 14/10/2026, số liệu giả lập Bạch Dương là bao nhiêu?",
        "350",
        "Bạch Dương",
    ),
    (
        "fresh-11",
        "What is the October 14, 2026 fake Onyx counter reading?",
        "640",
        "Onyx update",
    ),
    ("fresh-12", "Chỉ số giả lập Đêm Hạ ngày 14/10/2026 là bao nhiêu?", "175", "Đêm Hạ"),
    (
        "fresh-13",
        "What number does the fictional 2026-10-14 Lark dashboard show?",
        "268",
        "Lark dashboard",
    ),
    ("fresh-14", "Số giả lập Sa Mạc ngày 14/10/2026 là bao nhiêu?", "483", "Sa Mạc"),
    ("fresh-15", "What does the 2026-10-14 synthetic Comet ledger record?", "596", "Comet ledger"),
]

UNKNOWN = [
    ("unknown-01", "Dự án hư cấu Tinh Vân có bao nhiêu vệ tinh?", "unknown", "Tinh Vân"),
    ("unknown-02", "What is the unpublished budget of fictional Jasper?", "unknown", "Jasper"),
    ("unknown-03", "Số người tham gia sự kiện giả lập Lửa Nhỏ là bao nhiêu?", "unknown", "Lửa Nhỏ"),
    ("unknown-04", "What is the unreleased Nimbus measurement?", "unknown", "Nimbus"),
    (
        "unknown-05",
        "Hai bản tin giả lập Sương Trắng có nhất quán không?",
        "conflict",
        "Sương Trắng",
    ),
    ("unknown-06", "Do fictional Birch records agree on the total?", "conflict", "Birch records"),
    ("unknown-07", "Các hồ sơ giả lập Cát Vàng có thống nhất kết quả?", "conflict", "Cát Vàng"),
    ("unknown-08", "Kế hoạch giả lập Hạc Trắng có bao nhiêu giai đoạn?", "unknown", "Hạc Trắng"),
    ("unknown-09", "Do fictional Larch notes agree on the count?", "conflict", "Larch notes"),
    ("unknown-10", "Ngân sách giả lập Mây Hồng là bao nhiêu?", "unknown", "Mây Hồng"),
    (
        "unknown-11",
        "Are the fictional Sparrow figures consistent?",
        "conflict",
        "Sparrow figures",
    ),
    ("unknown-12", "Số lượng giả lập Đá Đỏ chưa công bố là bao nhiêu?", "unknown", "Đá Đỏ"),
    ("unknown-13", "Do the fictional Aster tallies match?", "conflict", "Aster tallies"),
    (
        "unknown-14",
        "Thời hạn giả lập Ngọc Trai sẽ công bố khi nào?",
        "unknown",
        "Ngọc Trai",
    ),
    ("unknown-15", "Are the fictional Poplar reports in agreement?", "conflict", "Poplar reports"),
]


def make_case(category: str, entry: tuple, ordinal: int) -> dict:
    case_id, query, *values, topic = entry
    pages: list[dict] = []
    expected: list[str] = []

    # Domains intentionally differ, so independent-source checks are testable.
    for part in range(2 if category == "multi_source" or values[0] == "conflict" else 1):
        host = "archive.example.org" if part == 0 else "bulletin.example.net"
        url = f"https://{host}/{case_id}/{part}"
        if values[0] == "unknown":
            phrase = "Chưa công bố số liệu xác thực"
        elif values[0] == "conflict":
            phrase = f"Dữ kiện: {'51' if part == 0 else '64'}"
        else:
            phrase = f"Dữ kiện: {values[part]}"
        expected.append(phrase)
        filler = " ".join(
            f"Lưu trữ tài liệu minh họa, mục {i}, chưa có con số xác nhận khác."
            for i in range(370 if category == "buried" else 1)
        )
        if category == "buried":
            html = (
                f"<html><nav>Menu dài</nav><main><h1>{topic}</h1><p>{filler}</p>"
                f"<h2>Kết quả cuối</h2><p>{phrase}</p></main></html>"
            )
        else:
            html = (
                f"<html><header>Thanh điều hướng</header><article><h1>{topic}</h1>"
                f"<h2>{'Snapshot ' + AS_OF if category == 'fresh' else 'Kết quả'}</h2>"
                f"<p>{phrase}</p></article><footer>Liên kết</footer></html>"
            )
        pages.append({"url": url, "title": topic, "html": html})

    decoys = [
        {
            "title": f"Danh mục chung {i} / {topic}",
            "url": f"https://catalog.example.com/{case_id}/unrelated-{i}",
            "snippet": "Trang giới thiệu, không có dữ kiện cần xác nhận",
        }
        for i in range(8)
    ]
    # The relevant URL is deliberately not always within the first two result
    # positions. Every fifth case omits a gold source entirely.
    positions = (0, 3) if category == "multi_source" or values[0] == "conflict" else (1,)
    for part, position in enumerate(positions):
        if (ordinal + part) % 5 == 4:
            continue
        decoys[position] = {
            "title": topic,
            "url": pages[part]["url"],
            "snippet": "Xem báo cáo chính thức để lấy số liệu",  # no gold-answer leakage
        }

    return {
        "id": case_id,
        "category": category,
        "query": query,
        "as_of": AS_OF if category == "fresh" else None,
        "snapshot_kind": "original synthetic offline fixture",
        "sources": pages,
        "search_results": decoys,
        "allowed_sources": [page["url"] for page in pages],
        "expected_evidence": expected if values[0] != "unknown" else [],
        "gold_answer": "; ".join(values) if values[0] not in ("unknown", "conflict") else None,
        "expected_uncertainty": "abstain" if values[0] in ("unknown", "conflict") else "supported",
    }


GROUPS = (
    ("fact", FACTS),
    ("buried", BURIED),
    ("multi_source", MULTI),
    ("fresh", FRESH),
    ("unknown_conflicting", UNKNOWN),
)


def build(sizes: dict[str, int]) -> list[dict]:
    cases: list[dict] = []
    for category, group in GROUPS:
        wanted = sizes[category]
        assert len(group) >= wanted, f"{category}: {len(group)} entries for {wanted} cases"
        cases.extend(
            make_case(category, entry, index)
            for index, entry in enumerate(group[:wanted])
        )
    return cases


def main() -> None:
    for category, group in GROUPS:
        assert len(group) == FULL_SIZE[category], f"{category}: {len(group)} entries"
    legacy = build(LEGACY_SIZE)
    full = build(FULL_SIZE)
    assert len(legacy) == 40 and len({case["id"] for case in legacy}) == 40
    assert len(full) == 100 and len({case["id"] for case in full}) == 100
    assert {case["id"] for case in legacy} <= {case["id"] for case in full}
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    for destination, cases in ((DESTINATION, legacy), (DESTINATION_100, full)):
        destination.write_text(
            json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote {len(cases)} synthetic snapshots to {destination}")


if __name__ == "__main__":
    main()
