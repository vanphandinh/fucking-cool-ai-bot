"""Regenerate original, synthetic, offline Web Evidence evaluation snapshots.

No third-party page content or external URLs are downloaded. All hosts use
reserved example domains and are intercepted by the offline benchmark.
"""

from __future__ import annotations

import json
from pathlib import Path

DESTINATION = Path(__file__).parents[1] / "tests/fixtures/web_evidence_v2/cases.json"
AS_OF = "2026-10-14T00:00:00+07:00"

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


def main() -> None:
    groups = (
        ("fact", FACTS),
        ("buried", BURIED),
        ("multi_source", MULTI),
        ("fresh", FRESH),
        ("unknown_conflicting", UNKNOWN),
    )
    cases = [
        make_case(category, entry, index)
        for category, group in groups
        for index, entry in enumerate(group)
    ]
    assert len(cases) == 40 and len({case["id"] for case in cases}) == 40
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} synthetic snapshots to {DESTINATION}")


if __name__ == "__main__":
    main()
