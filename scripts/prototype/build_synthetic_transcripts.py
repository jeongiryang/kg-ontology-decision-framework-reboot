"""Author reproducible, identifier-free PDF fixtures; not a runtime dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.project.resolve()
    facts = json.loads((root / "knowledge/course-catalogue.json").read_text(encoding="utf-8"))["courses"]
    by_code = {f["course_code"]: f for f in facts}
    pdfmetrics.registerFont(TTFont("ExampleKorean", str(args.font)))
    target = root / "src/academic_assistant/web/examples"
    target.mkdir(parents=True, exist_ok=True)
    def course(code, grade="A0", **extra):
        f = by_code[code]
        return {"course_code": code, "course_name": f["course_name"], "credits": f["credits"],
                "grade": grade, "category": f["category"], **extra}
    def generic(name, credits, category, **extra):
        return {"course_code": None, "course_name": name, "credits": credits,
                "grade": "B+", "category": category, **extra}
    early = [generic("가상기초교양", 3, "foundation"),
             generic("가상균형교양", 3, "balanced", balanced_area="nature-science-technology"),
             course("CDA0163"), course("CDA0008"), course("CDA0143"), course("CDA0088", "S")]
    near = [course(f["course_code"], "S" if f["credits"] == 0 else "A0")
            for f in facts if f["category"] == "major_required" and f["course_code"] != "CDA0034"]
    near += [course(f["course_code"]) for f in [f for f in facts if f["category"] == "major_elective" and f["credits"] == 3][:19]]
    near += [generic(f"가상기초교양{i}", 3, "foundation") for i in range(1, 4)]
    areas = ["digital-communication", "humanities-arts", "society-culture", "nature-science-technology"]
    near += [generic(f"가상균형교양{i}", 3, "balanced", balanced_area=area) for i, area in enumerate(areas, 1)]
    near += [generic(f"가상확대교양{i}", 3 if i < 5 else 1, "expanded") for i in range(1, 6)]
    near += [generic(f"가상자유선택{i}", 3, "free") for i in range(1, 7)]
    retake = [course("CDA0143", "F", excluded=True), course("CDA0143", "A0"),
              course("CDA0016", "F"), course("CDA0088", "U"), course("CDA0034", "U"),
              course("CDA0163", "B0"), generic("가상기초교양", 3, "foundation")]
    scenarios = [("early", "1. 이수 초기", "학점과 필수과목이 많이 남은 예제", early),
                 ("near-graduation", "2. 130학점 · 졸업논문 미이수", "학점 합계만으로 졸업을 확정하지 않는 예제", near),
                 ("retake", "3. 재수강 · F/U", "삭제된 이전 기록 제외와 0학점 필수과목 확인", retake)]
    labels = {"foundation": "기초교양", "balanced": "균형교양", "expanded": "확대교양",
              "major_required": "전공필수", "major_elective": "전공선택", "free": "자유선택"}
    manifest = {"schema_version": "1.0.0", "synthetic": True, "examples": {}}
    width, height = A4
    for identifier, title, description, rows in scenarios:
        path = target / f"{identifier}.pdf"
        doc = canvas.Canvas(str(path), pagesize=A4, invariant=1, pageCompression=1)
        doc.setTitle(f"2026 가상 성적표 — {title}")
        doc.setAuthor("Academic Assistant synthetic fixture")
        doc.setFillColor(HexColor("#164b3c")); doc.rect(0, height - 110, width, 110, fill=1, stroke=0)
        doc.setFillColor(HexColor("#ffffff")); doc.setFont("ExampleKorean", 19)
        doc.drawString(35, height - 44, title)
        doc.setFont("ExampleKorean", 10); doc.drawString(35, height - 67, "2026학번 컴퓨터공학과 · 단일전공 · 가상 전체 이수내역")
        doc.drawString(35, height - 88, "실제 학생 정보가 없는 인식 테스트용 PDF입니다.")
        doc.setFillColor(HexColor("#173e34")); doc.setFont("ExampleKorean", 10)
        doc.drawString(35, height - 135, "입학년도: 2026년 / 학과: 컴퓨터공학과")
        doc.drawString(35, height - 154, description)
        columns = [35, 94, 169, 420, 460, 492]
        y = height - 188
        doc.setFillColor(HexColor("#e5efea")); doc.rect(30, y - 6, width - 60, 23, fill=1, stroke=0)
        doc.setFillColor(HexColor("#173e34")); doc.setFont("ExampleKorean", 9)
        for x, text in zip(columns, ["이수구분", "코드", "과목명", "학점", "성적", "비고"]):
            doc.drawString(x, y, text)
        row_height = 12 if len(rows) > 25 else 25
        doc.setFont("ExampleKorean", 8 if len(rows) > 25 else 9)
        for index, row in enumerate(rows, 1):
            y -= row_height
            if index % 2 == 0:
                doc.setFillColor(HexColor("#f4f7f5")); doc.rect(30, y - 3, width - 60, row_height, fill=1, stroke=0)
            doc.setFillColor(HexColor("#222d28"))
            texts = [labels[row["category"]], row["course_code"] or "", row["course_name"], str(row["credits"]), row["grade"], "이전기록 제외" if row.get("excluded") else ""]
            for x, text in zip(columns, texts):
                doc.drawString(x, y, text)
            row["row_id"] = f"p1-r{index}"
        total = sum(row["credits"] for row in rows if not row.get("excluded") and row["grade"] not in {"F", "F0", "U", "W"})
        doc.setFont("ExampleKorean", 9); doc.drawString(35, 62, f"취득학점: {total} / 예제 조건: 영역·이전기록 제외는 검증된 예제 메타데이터 적용")
        doc.setFillColor(HexColor("#56685f")); doc.drawString(35, 43, "가상 성적표 · 공식 증명서가 아니며 최종 졸업 판정을 제공하지 않습니다.")
        doc.drawRightString(width - 35, 25, "1 / 1")
        doc.save()
        manifest["examples"][identifier] = {"title": title, "description": description,
            "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "input_pass_credits": total, "rows": rows}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"examples": {key: {"rows": len(value["rows"]), "credits": value["input_pass_credits"]} for key, value in manifest["examples"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
