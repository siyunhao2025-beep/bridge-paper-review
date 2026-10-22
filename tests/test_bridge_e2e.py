from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE = REPO_ROOT / "paper-revision-bridge" / "scripts" / "bridge.py"
TEST_ROOT = Path(tempfile.mkdtemp(prefix=f"bpr-{uuid.uuid4().hex[:6]}-"))
REVIEW_FILES = (
    "reviewer-01-domain-novelty.md",
    "reviewer-02-methods-statistics.md",
    "reviewer-03-evidence-consistency.md",
    "reviewer-04-journal-presentation.md",
    "reviewer-05-writing-integrity.md",
)
HEADINGS = (
    "## 摘要结论",
    "## 硬伤与矛盾",
    "## 逐项审稿意见",
    "## 防御性写作审查",
    "## 上轮问题复核",
    "## 评分",
    "## 投稿建议",
)


def run(*args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(BRIDGE), *args],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ok and result.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
    if not ok and result.returncode == 0:
        raise AssertionError(f"command unexpectedly passed: {args}\nstdout={result.stdout}")
    return result


def review_text(reviewer: int, round_number: int, passing: bool) -> str:
    bodies = []
    for heading in HEADINGS:
        bodies.append(heading)
        bodies.append(
            f"审稿人 R{reviewer} 对第 {round_number} 轮稿件执行了证据定位、交叉一致性和防御性写作检查。"
            "这里记录足够详细的测试性审稿内容，包括方法、数据、结论、图表、引用、局限与目标期刊适配。"
            "防御性措辞被区分为必要的不确定性校准和回避证据责任的辩护；所有判断均给出修改动作与验收条件。"
            * 3
        )
    if passing:
        bodies.append("submission_ready=true；未发现 P0/P1，旧问题均已解决。")
    else:
        bodies.append(f"submission_ready=false；问题 R01-TEST-00{reviewer} 必须修改。")
    return "\n\n".join(bodies) + "\n"


def fill_round(workspace: Path, round_number: int, passing: bool, prior_ids: list[str]) -> list[str]:
    directory = workspace / f"round-{round_number:02d}"
    current_ids = [] if passing else [f"R{round_number:02d}-TEST-00{i}" for i in range(1, 6)]
    for index, filename in enumerate(REVIEW_FILES, 1):
        (directory / filename).write_text(
            review_text(index, round_number, passing), encoding="utf-8"
        )
    summary = (
        f"# 审稿汇总 R{round_number:02d}\n\n"
        + "五位审稿人分别检查了创新性、方法统计、证据一致性、期刊呈现与学术表达。"
        + "所有审稿人均单列防御性写作审查，并对上轮问题逐项复核。"
        + ("本轮达到所有硬门槛。" if passing else "本轮存在五项 P1，须由主力修改。")
        + "\n\n"
        + ("证据链、源文件与渲染件已经交叉核验。" * 20)
    )
    (directory / f"审稿汇总_R{round_number:02d}.md").write_text(summary, encoding="utf-8")
    tasks = (
        f"# 修改任务单 R{round_number:02d}\n\n"
        + ("本轮没有 P0/P1；无需新的修改任务。" if passing else "\n".join(current_ids))
        + "\n\n"
        + ("每项任务均给出位置、动作、依赖与验收条件。" * 20)
    )
    (directory / f"修改任务单_R{round_number:02d}.md").write_text(tasks, encoding="utf-8")
    scorecard_path = directory / f"scorecard_R{round_number:02d}.json"
    card = json.loads(scorecard_path.read_text(encoding="utf-8"))
    card["journal_compliance"] = True
    card["source_integrity_checked"] = True
    card["prior_issue_audit_complete"] = True
    card["resolved_prior_issue_ids"] = prior_ids if passing else []
    card["unresolved_prior_issue_ids"] = []
    card["recurring_issue_ids"] = []
    card["conflicts_needing_user"] = []
    card["missing_evidence_actions"] = []
    card["secretary_recommendation"] = "ready" if passing else "revise"
    for index, reviewer in enumerate(card["reviewers"], 1):
        score = 9.3 if passing else 8.0
        reviewer["dimensions"] = {key: score for key in reviewer["dimensions"]}
        reviewer["overall"] = score
        reviewer["submission_ready"] = passing
        reviewer["blocking_issue_ids"] = []
        reviewer["must_fix_issue_ids"] = [] if passing else [current_ids[index - 1]]
    scorecard_path.write_text(
        json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return current_ids


def main() -> None:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    manuscript = TEST_ROOT / "paper.tex"
    editable = TEST_ROOT / "paper-working.tex"
    manuscript.write_text("\\documentclass{article}\n\\begin{document}Baseline\\end{document}\n", encoding="utf-8")
    editable.write_text(manuscript.read_text(encoding="utf-8"), encoding="utf-8")

    init = run(
        "init",
        "--manuscript", str(manuscript),
        "--editable-source", str(editable),
        "--agent", "GPT",
        "--agent", "Workbuddy",
        "--lead", "GPT",
        "--secretary", "Workbuddy",
        "--journal", "Journal of Reproducible Tests",
        "--article-type", "Research Article",
        "--language", "English",
        "--review-mode", "independent_agents",
        "--workspace-placement", "same",
        "--monitor-mode", "scheduled",
        "--monitor-method", "windows_task_scheduler",
        "--poll-interval-minutes", "5",
        "--timezone", "Asia/Shanghai",
        "--monitor-duration", "until_complete",
        "--scheduler-authorized", "yes",
        "--wake-adapter", "event_only",
        "--intake-confirmed", "yes",
        "--intake-note", "User confirmed paths, roles, same-folder placement, and five-minute monitoring",
    )
    workspace = Path(json.loads(init.stdout)["workspace"])
    state = json.loads((workspace / "bridge-state.json").read_text(encoding="utf-8"))
    assert ".initializing-" not in state["rounds"]["1"]["directory"]

    verification = json.loads(run("verify", "--workspace", str(workspace)).stdout)
    assert verification["valid"] is True

    incomplete = run(
        "record-review", "--workspace", str(workspace), "--round", "1", ok=False
    )
    assert "未完成" in incomplete.stderr

    round_one_ids = fill_round(workspace, 1, passing=False, prior_ids=[])
    first_review = json.loads(
        run("record-review", "--workspace", str(workspace), "--round", "1").stdout
    )
    assert first_review["phase"] == "awaiting_revision"

    activated = json.loads(
        run(
            "monitor-activate",
            "--workspace", str(workspace),
            "--scheduler-id", "test-scheduler:001",
            "--authorization-confirmed", "yes",
            "--note", "End-to-end test scheduler",
        ).stdout
    )
    assert activated["monitor"]["status"] == "active"
    fake_external_marker = workspace / "GPT_1修paper.md"
    fake_external_marker.write_text(
        "# GPT 第 1 次修改完成\n\nExternal lead reports completion; secretary must verify outputs.\n",
        encoding="utf-8",
    )
    detected = json.loads(run("scan", "--workspace", str(workspace)).stdout)
    assert detected["event"]["type"] == "completion_marker_detected"
    repeated_scan = json.loads(run("scan", "--workspace", str(workspace)).stdout)
    assert repeated_scan["new_markers"] == []
    assert repeated_scan["event"] is None

    wrong_agent = run(
        "lead-done",
        "--workspace", str(workspace),
        "--agent", "Workbuddy",
        "--output", str(editable),
        "--change-log", str(manuscript),
        "--check", "test",
        ok=False,
    )
    assert "已登记主力" in wrong_agent.stderr

    revised = TEST_ROOT / "paper-r1.tex"
    revised_pdf = TEST_ROOT / "paper-r1.pdf"
    change_log = TEST_ROOT / "change-log-r1.md"
    revised.write_text("\\documentclass{article}\n\\begin{document}Revised\\end{document}\n", encoding="utf-8")
    revised_pdf.write_bytes(b"%PDF-1.4\n% test fixture\n")
    change_log.write_text("# Change log\n\nResolved all five test issues with exact locations.\n", encoding="utf-8")
    lead_args = [
        "accept-marker", "--workspace", str(workspace),
        "--marker", str(fake_external_marker), "--agent", "GPT",
        "--output", str(revised), "--output", str(revised_pdf),
        "--change-log", str(change_log),
        "--check", "LaTeX compile and cross-reference checks passed",
        "--check", "Rendered PDF matches the editable source",
    ]
    for issue_id in round_one_ids:
        lead_args.extend(["--resolved", issue_id])
    handoff = json.loads(run(*lead_args).stdout)
    assert Path(handoff["marker"]).name == "GPT_1修paper.md"
    assert handoff["next_round"] == 2
    accepted_state = json.loads((workspace / "bridge-state.json").read_text(encoding="utf-8"))
    assert accepted_state["rounds"]["1"]["revision"]["marker_origin"] == "external_agent"

    fill_round(workspace, 2, passing=True, prior_ids=round_one_ids)
    second_review = json.loads(
        run("record-review", "--workspace", str(workspace), "--round", "2").stdout
    )
    assert second_review["phase"] == "complete"
    assert (workspace / "最终可投稿报告.md").is_file()

    final_verification = json.loads(run("verify", "--workspace", str(workspace)).stdout)
    assert final_verification["valid"] is True
    waited = json.loads(
        run("wait", "--workspace", str(workspace), "--after-event", "0", "--timeout", "0").stdout
    )
    assert waited["timed_out"] is False
    assert len(waited["events"]) >= 6
    deactivated = json.loads(
        run(
            "monitor-deactivate",
            "--workspace", str(workspace),
            "--confirmed", "yes",
            "--note", "End-to-end test completed",
        ).stdout
    )
    assert deactivated["monitor"]["status"] == "configured"

    pdf_case = TEST_ROOT / "pdf-only-case"
    pdf_case.mkdir()
    pdf_manuscript = pdf_case / "only.pdf"
    pdf_manuscript.write_bytes(b"%PDF-1.4\n% pdf-only test fixture\n")
    pdf_init = json.loads(
        run(
            "init",
            "--manuscript", str(pdf_manuscript),
            "--agent", "GPT",
            "--agent", "Workbuddy",
            "--lead", "GPT",
            "--secretary", "Workbuddy",
            "--journal", "Journal of Boundary Tests",
            "--article-type", "Research Article",
            "--language", "English",
            "--review-mode", "single_model_isolated",
            "--workspace-placement", "same",
            "--monitor-mode", "manual",
            "--monitor-method", "manual",
            "--timezone", "Asia/Shanghai",
            "--monitor-duration", "until_complete",
            "--scheduler-authorized", "no",
            "--wake-adapter", "manual_handoff",
            "--intake-confirmed", "yes",
            "--intake-note", "User confirmed PDF-only path, roles, folder placement, and manual monitoring",
        ).stdout
    )
    pdf_workspace = Path(pdf_init["workspace"])
    fill_round(pdf_workspace, 1, passing=False, prior_ids=[])
    pdf_card_path = pdf_workspace / "round-01" / "scorecard_R01.json"
    pdf_card = json.loads(pdf_card_path.read_text(encoding="utf-8"))
    pdf_card["missing_evidence_actions"] = ["Provide editable TeX or DOCX source"]
    pdf_card_path.write_text(
        json.dumps(pdf_card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pdf_review = json.loads(
        run("record-review", "--workspace", str(pdf_workspace), "--round", "1").stdout
    )
    assert pdf_review["phase"] == "attention_required"
    assert any("PDF" in item for item in pdf_review["event"]["details"]["attention_reasons"])
    no_source_resume = run(
        "resolve-attention",
        "--workspace", str(pdf_workspace),
        "--note", "Use the supplied source",
        ok=False,
    )
    assert "权威可编辑源" in no_source_resume.stderr
    supplied_source = pdf_case / "only.tex"
    supplied_source.write_text("\\documentclass{article}\n", encoding="utf-8")
    run("set-source", "--workspace", str(pdf_workspace), "--editable-source", str(supplied_source))
    resumed = json.loads(
        run(
            "resolve-attention",
            "--workspace", str(pdf_workspace),
            "--note", "User supplied the canonical TeX source and approved revision",
        ).stdout
    )
    assert resumed["phase"] == "awaiting_revision"
    pdf_verification = json.loads(run("verify", "--workspace", str(pdf_workspace)).stdout)
    assert pdf_verification["valid"] is True

    print(
        json.dumps(
            {
                "test_root": str(TEST_ROOT),
                "workspace": str(workspace),
                "events": len(waited["events"]),
                "final_phase": "complete",
                "verified": True,
                "pdf_only_pause_and_resume": True,
                "wrong_agent_rejected": True,
                "scheduled_scan_idempotent": True,
                "external_marker_accepted": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
