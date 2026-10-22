#!/usr/bin/env python3
"""File-based orchestration for iterative multi-agent manuscript revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


WORKSPACE_NAME = "协同审稿+优化"
STATE_NAME = "bridge-state.json"
EVENTS_NAME = "events.jsonl"
MONITOR_NAME = "monitor-config.json"
LOCK_NAME = ".bridge.lock"
SCHEMA_VERSION = 1

PHASES = {"awaiting_review", "awaiting_revision", "attention_required", "complete"}
DIMENSIONS = (
    "novelty_significance",
    "methods_rigor",
    "evidence_consistency",
    "clarity_journal_fit",
    "defensive_writing",
)
REVIEWERS = (
    ("R1", "reviewer-01-domain-novelty.md", "领域、问题价值与创新性"),
    ("R2", "reviewer-02-methods-statistics.md", "方法、统计与可复现性"),
    ("R3", "reviewer-03-evidence-consistency.md", "证据链与内部一致性"),
    ("R4", "reviewer-04-journal-presentation.md", "期刊适配、结构与呈现"),
    ("R5", "reviewer-05-writing-integrity.md", "学术表达、完整性与质疑压力测试"),
)
REQUIRED_REVIEW_HEADINGS = (
    "## 摘要结论",
    "## 硬伤与矛盾",
    "## 逐项审稿意见",
    "## 防御性写作审查",
    "## 上轮问题复核",
    "## 评分",
    "## 投稿建议",
)
ISSUE_ID_RE = re.compile(r"^R\d{2}-[A-Z][A-Z0-9_-]*-\d{3}$")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def fail(message: str, code: int = 2) -> "NoReturn":
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(code)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_nonempty(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        fail(f"{label}不能为空。")
    return cleaned


def resolve_file(raw: str, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        fail(f"{label}不存在或不是文件：{path}")
    return path


def resolve_workspace(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        fail(f"协作目录不存在：{root}")
    if not (root / STATE_NAME).is_file():
        fail(f"目录中缺少 {STATE_NAME}：{root}")
    return root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "sha256": sha256_file(path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".bpr-{uuid.uuid4().hex[:12]}.tmp"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"缺少{label}：{path}")
    except json.JSONDecodeError as exc:
        fail(f"{label}不是有效 JSON（{exc}）：{path}")


def load_state(root: Path) -> dict[str, Any]:
    state = load_json(root / STATE_NAME, "状态文件")
    if not isinstance(state, dict):
        fail("状态文件顶层必须是对象。")
    if state.get("schema_version") != SCHEMA_VERSION:
        fail(
            f"不支持的状态版本 {state.get('schema_version')!r}；"
            f"当前脚本只支持 {SCHEMA_VERSION}。"
        )
    if state.get("phase") not in PHASES:
        fail(f"状态阶段无效：{state.get('phase')!r}")
    return state


@contextmanager
def workspace_lock(root: Path):
    lock_path = root / LOCK_NAME
    payload = json.dumps(
        {"pid": os.getpid(), "created_at": now_utc()}, ensure_ascii=False
    ).encode("utf-8")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        fail(
            f"协作目录正被另一进程写入，或上次异常退出留下锁：{lock_path}。"
            "先确认没有活动进程，再人工处理锁文件。"
        )
    try:
        os.write(fd, payload)
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def phase_next_action(state: dict[str, Any]) -> str:
    phase = state["phase"]
    if phase == "awaiting_review":
        return "秘书组织五位审稿人审阅当前稿，完成本轮报告、汇总、任务单和评分卡。"
    if phase == "awaiting_revision":
        return "主力按本轮任务单修改论文，核验输出后生成完成标记。"
    if phase == "attention_required":
        return "向用户报告缺失证据、审稿冲突、停滞或轮次上限，取得决定后再恢复。"
    return "流程已达到模拟可投稿门槛；保留最终报告和所有版本，不再自动修改。"


def status_markdown(state: dict[str, Any]) -> str:
    last = state.get("last_event") or {}
    open_issues = state.get("open_issue_ids", [])
    monitor = state.get("monitor") or {}
    return f"""# 当前状态

- 工作流 ID：`{state['workflow_id']}`
- 论文：{state['paper']['title']}
- 目标期刊：{state['paper']['target_journal']}
- 主力：{state['roles']['lead']}
- 秘书：{state['roles']['secretary']}
- 当前轮次：R{state['current_round']:02d}
- 当前阶段：`{state['phase']}`
- 最新事件：`{last.get('event_id', 0)}` / `{last.get('type', 'none')}`
- 未关闭问题数：{len(open_issues)}
- 连续停滞计数：{state.get('stagnation_count', 0)}
- 定时检索：`{monitor.get('status', 'not_configured')}` / `{monitor.get('method', 'unknown')}`
- 轮询间隔：{monitor.get('interval_minutes') if monitor.get('interval_minutes') is not None else '人工'} 分钟
- 唤醒适配器：`{monitor.get('wake_adapter', 'unknown')}`
- 最近扫描：{monitor.get('last_scan_at') or '尚未扫描'}
- 更新时间：{state['updated_at']}

## 下一步

{phase_next_action(state)}

## 未关闭问题

{chr(10).join(f'- `{item}`' for item in open_issues) if open_issues else '- 无'}
"""


def append_event_file(root: Path, event: dict[str, Any]) -> None:
    path = root / EVENTS_NAME
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    atomic_write_text(path, existing + line)


def commit(
    root: Path,
    state: dict[str, Any],
    event_type: str,
    audience: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state["event_seq"] = int(state.get("event_seq", 0)) + 1
    state["updated_at"] = now_utc()
    event = {
        "event_id": state["event_seq"],
        "type": event_type,
        "audience": audience,
        "created_at": state["updated_at"],
        "workflow_id": state["workflow_id"],
        "round": state["current_round"],
        "phase": state["phase"],
        "details": details or {},
    }
    state["last_event"] = event
    atomic_write_json(root / STATE_NAME, state)
    if state.get("monitor") is not None:
        atomic_write_json(root / MONITOR_NAME, state["monitor"])
    atomic_write_text(root / "00_当前状态.md", status_markdown(state))
    append_event_file(root, event)
    return event


def save_without_event(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc()
    atomic_write_json(root / STATE_NAME, state)
    if state.get("monitor") is not None:
        atomic_write_json(root / MONITOR_NAME, state["monitor"])
    atomic_write_text(root / "00_当前状态.md", status_markdown(state))


def safe_filename(value: str, fallback: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return (value[:80].rstrip(" .") or fallback)


def round_dir(root: Path, round_number: int) -> Path:
    return root / f"round-{round_number:02d}"


def reviewer_template(round_number: int, reviewer_id: str, role: str) -> str:
    return f"""# {reviewer_id} 审稿报告：{role}

<!-- TEMPLATE: incomplete；完成后删除本行。 -->

- 轮次：R{round_number:02d}
- 审稿职责：{role}
- 当前稿件路径：待填写
- 审阅时间：待填写
- 实现方式：待填写（独立智能体 / 单模型隔离审阅 / 人机混合）

## 摘要结论

待填写。

## 硬伤与矛盾

按 P0/P1/P2/P3 分级；无则明确写“未发现”。

## 逐项审稿意见

| ID | 级别 | 精确位置 | 证据或推理 | 风险 | 修改动作 | 验收条件 |
|---|---|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

## 防御性写作审查

| 位置 | 文本功能 | 校准不确定性或回避问题 | 删除/改写/补证据/保留 | 改后过度自信风险 |
|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

## 上轮问题复核

R01 写“无上轮问题”。R02 起逐项写：已解决 / 部分解决 / 未解决 / 复发 / 无法核验。

## 评分

分别报告 novelty_significance、methods_rigor、evidence_consistency、clarity_journal_fit、defensive_writing、overall（0–10），并解释最低分。

## 投稿建议

写明 submission_ready=true/false 及理由。
"""


def summary_template(round_number: int) -> str:
    return f"""# 审稿汇总 R{round_number:02d}

<!-- TEMPLATE: incomplete；完成后删除本行。 -->

## 当前稿件与范围

待填写。

## 五审评分

待填写逐人总分、各维度分和是否可投稿。

## 共识问题

待填写；保留问题 ID、优先级和证据位置。

## 审稿分歧

待填写；不得用平均分抹去分歧。

## 旧问题复核

待填写已解决、部分解决、未解决、复发和无法核验项。

## 防御性写作综合结论

待填写，区分必要的不确定性与回避式辩护。

## 秘书结论

待填写：revise / attention_required / ready。
"""


def tasks_template(round_number: int) -> str:
    return f"""# 修改任务单 R{round_number:02d}

<!-- TEMPLATE: incomplete；完成后删除本行。 -->

## 执行规则

先处理 P0，再处理 P1；每项保留唯一问题 ID。若任务依赖新数据、实验、伦理审批或作者判断，标记为阻断并交还用户。

## 任务

| ID | 优先级 | 来源审稿人 | 文件/位置 | 修改动作 | 依赖 | 验收条件 | 状态 |
|---|---|---|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | open |

## 不采纳的 P2/P3

待填写问题 ID 与证据化理由；不得静默删除。
"""


def scorecard_template(round_number: int) -> dict[str, Any]:
    reviewers = []
    for reviewer_id, _, role in REVIEWERS:
        reviewers.append(
            {
                "id": reviewer_id,
                "role": role,
                "dimensions": {dimension: None for dimension in DIMENSIONS},
                "overall": None,
                "submission_ready": False,
                "blocking_issue_ids": [],
                "must_fix_issue_ids": [],
            }
        )
    return {
        "round": round_number,
        "journal_compliance": False,
        "source_integrity_checked": False,
        "prior_issue_audit_complete": round_number == 1,
        "resolved_prior_issue_ids": [],
        "unresolved_prior_issue_ids": [],
        "recurring_issue_ids": [],
        "conflicts_needing_user": [],
        "missing_evidence_actions": [],
        "secretary_recommendation": "revise",
        "reviewers": reviewers,
    }


def scaffold_round(root: Path, state: dict[str, Any], round_number: int) -> None:
    destination = round_dir(root, round_number)
    if destination.exists():
        fail(f"轮次目录已存在，拒绝覆盖：{destination}")
    destination.mkdir(parents=False)
    for reviewer_id, filename, role in REVIEWERS:
        atomic_write_text(
            destination / filename,
            reviewer_template(round_number, reviewer_id, role),
        )
    atomic_write_text(destination / f"审稿汇总_R{round_number:02d}.md", summary_template(round_number))
    atomic_write_text(destination / f"修改任务单_R{round_number:02d}.md", tasks_template(round_number))
    atomic_write_json(destination / f"scorecard_R{round_number:02d}.json", scorecard_template(round_number))
    state.setdefault("rounds", {})[str(round_number)] = {
        "status": "awaiting_review",
        "directory": str(destination),
        "created_at": now_utc(),
    }


def config_markdown(state: dict[str, Any]) -> str:
    editable = state["paper"].get("editable_source") or "未提供（PDF-only，修改前必须解决）"
    thresholds = state["thresholds"]
    monitor = state["monitor"]
    return f"""# 协作配置

- 工作流 ID：`{state['workflow_id']}`
- 论文标题：{state['paper']['title']}
- 基线稿：`{state['paper']['manuscript']}`
- 权威可编辑源：`{editable}`
- 目标期刊：{state['paper']['target_journal']}
- 稿件类型：{state['paper']['article_type']}
- 写作语言：{state['paper']['language']}
- 全部 AI：{', '.join(state['roles']['agents'])}
- 主修改智能体：{state['roles']['lead']}
- 秘书智能体：{state['roles']['secretary']}
- 五审实现方式：`{state['review_mode']}`
- 协作目录位置：`{state['intake']['workspace_placement']}` / `{state['workspace']}`
- 用户启动确认时间：{state['intake']['confirmed_at']}
- 用户启动确认说明：{state['intake']['confirmation_note']}
- 定时检索模式：`{monitor['mode']}`
- 监测方法：`{monitor['method']}`
- 监测状态：`{monitor['status']}`
- 轮询间隔：{monitor['interval_minutes'] if monitor['interval_minutes'] is not None else '人工'} 分钟
- 时区：{monitor['timezone']}
- 持续期限：{monitor['duration']}
- 系统定时任务授权：{monitor['scheduler_authorized']}
- 唤醒适配器：`{monitor['wake_adapter']}`
- 每位审稿人最低总分：{thresholds['min_reviewer_overall']}
- 每个维度最低分：{thresholds['min_dimension']}
- 自主轮次上限：{thresholds['max_rounds']}
- 连续停滞判据：提升小于 {thresholds['min_progress']} 分达到 2 次

## 责任边界

秘书维护本目录和评分；主力修改论文并提交完成标记；用户裁决缺证据、审稿冲突和学术真实性问题。`configured` 只表示监测参数已保存；只有 `active` 才表示调度器已登记，只有 `wake_verified` 才表示真实唤醒链路已测试。模拟审稿通过不等于期刊录用保证。
"""


def command_init(args: argparse.Namespace) -> None:
    if args.intake_confirmed != "yes":
        fail("尚未记录用户的明确启动确认；先完成启动问卷并取得“确认开始”。")
    intake_note = ensure_nonempty(args.intake_note, "启动确认说明")
    manuscript = resolve_file(args.manuscript, "基线稿")
    editable = resolve_file(args.editable_source, "权威可编辑源") if args.editable_source else None
    agents: list[str] = []
    seen_agents: set[str] = set()
    for raw_agent in args.agent:
        agent = ensure_nonempty(raw_agent, "AI 智能体名称")
        key = agent.casefold()
        if key in seen_agents:
            fail(f"AI 智能体列表含重复项：{agent!r}")
        seen_agents.add(key)
        agents.append(agent)
    if len(agents) < 2:
        fail("至少登记两个不同的 AI 智能体。")
    requested_lead = ensure_nonempty(args.lead, "主力名称")
    requested_secretary = ensure_nonempty(args.secretary, "秘书名称")
    agent_by_key = {item.casefold(): item for item in agents}
    if requested_lead.casefold() not in agent_by_key:
        fail("主力必须出现在 --agent 列表中。")
    if requested_secretary.casefold() not in agent_by_key:
        fail("秘书必须出现在 --agent 列表中。")
    lead = agent_by_key[requested_lead.casefold()]
    secretary = agent_by_key[requested_secretary.casefold()]
    if lead.casefold() == secretary.casefold():
        fail("主力与秘书必须是不同的逻辑角色。")
    journal = ensure_nonempty(args.journal, "目标期刊")
    article_type = ensure_nonempty(args.article_type, "稿件类型")
    language = ensure_nonempty(args.language, "写作语言")
    title = ensure_nonempty(args.title or manuscript.stem, "论文标题")
    timezone_name = ensure_nonempty(args.timezone, "时区")
    duration = ensure_nonempty(args.monitor_duration, "监测持续期限")
    wake_adapter = ensure_nonempty(args.wake_adapter, "唤醒适配器")

    if args.workspace_placement == "same":
        if args.workspace_parent:
            fail("workspace-placement=same 时不要再提供 --workspace-parent。")
        workspace_parent = manuscript.parent
    else:
        if not args.workspace_parent:
            fail("workspace-placement=custom 时必须提供已获用户许可的 --workspace-parent。")
        workspace_parent = Path(args.workspace_parent).expanduser().resolve()
        if not workspace_parent.is_dir():
            fail(f"自定义协作父目录不存在或不是目录：{workspace_parent}")

    if args.monitor_mode == "scheduled":
        if args.poll_interval_minutes is None:
            fail("启用定时检索时必须提供 --poll-interval-minutes。")
        if args.monitor_method == "manual":
            fail("monitor-mode=scheduled 不能使用 monitor-method=manual。")
        monitor_status = "configured"
    else:
        if args.poll_interval_minutes is not None:
            fail("monitor-mode=manual 时不要提供轮询间隔。")
        if args.monitor_method != "manual":
            fail("monitor-mode=manual 时 monitor-method 必须为 manual。")
        monitor_status = "manual"

    root = workspace_parent / WORKSPACE_NAME
    if root.exists():
        fail(f"协作目录已存在，拒绝覆盖：{root}。请运行 status 或 verify。")

    staging = workspace_parent / f".bpr-init-{uuid.uuid4().hex[:12]}"
    staging.mkdir()
    try:
        created = now_utc()
        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "workflow_id": str(uuid.uuid4()),
            "workspace": str(root),
            "created_at": created,
            "updated_at": created,
            "phase": "awaiting_review",
            "current_round": 1,
            "event_seq": 0,
            "last_event": None,
            "review_mode": args.review_mode,
            "intake": {
                "confirmed": True,
                "confirmed_at": created,
                "confirmation_note": intake_note,
                "workspace_placement": args.workspace_placement,
                "workspace_parent": str(workspace_parent),
            },
            "roles": {"agents": agents, "lead": lead, "secretary": secretary},
            "paper": {
                "title": title,
                "manuscript": str(manuscript),
                "manuscript_baseline": file_record(manuscript),
                "editable_source": str(editable) if editable else None,
                "editable_source_baseline": file_record(editable) if editable else None,
                "target_journal": journal,
                "article_type": article_type,
                "language": language,
            },
            "monitor": {
                "mode": args.monitor_mode,
                "method": args.monitor_method,
                "status": monitor_status,
                "interval_minutes": args.poll_interval_minutes,
                "timezone": timezone_name,
                "duration": duration,
                "scheduler_authorized": args.scheduler_authorized,
                "scheduler_id": None,
                "wake_adapter": wake_adapter,
                "activated_at": None,
                "last_scan_at": None,
                "scan_count": 0,
                "detected_markers": [],
            },
            "thresholds": {
                "min_reviewer_overall": args.min_overall,
                "min_dimension": args.min_dimension,
                "min_progress": args.min_progress,
                "max_rounds": args.max_rounds,
            },
            "open_issue_ids": [],
            "stagnation_count": 0,
            "rounds": {},
            "latest_outputs": [],
            "attention_history": [],
        }
        scaffold_round(staging, state, 1)
        state["rounds"]["1"]["directory"] = str(root / "round-01")
        atomic_write_text(staging / "00_协作配置.md", config_markdown(state))
        commit(
            staging,
            state,
            "review_requested",
            secretary,
            {"reason": "initial_review", "round_directory": str(root / "round-01")},
        )
        os.replace(staging, root)
    except Exception:
        # Keep a failed staging directory for diagnosis; never erase user-visible data silently.
        raise

    print(
        json.dumps(
            {
                "workspace": str(root),
                "phase": "awaiting_review",
                "round": 1,
                "monitor_status": monitor_status,
                "scheduler_authorized": args.scheduler_authorized,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def require_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} 必须是数组。")
        return []
    return value


def validate_issue_ids(values: Iterable[Any], label: str, errors: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not ISSUE_ID_RE.fullmatch(value):
            errors.append(f"{label} 含无效问题 ID：{value!r}")
        else:
            result.append(value)
    if len(result) != len(set(result)):
        errors.append(f"{label} 含重复问题 ID。")
    return result


def numeric_score(value: Any, label: str, errors: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} 必须是 0–10 数字。")
        return -1.0
    result = float(value)
    if not 0.0 <= result <= 10.0:
        errors.append(f"{label} 超出 0–10：{result}")
    return result


def validate_completed_markdown(path: Path, headings: Iterable[str], minimum: int, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"缺少文件：{path}")
        return
    text = path.read_text(encoding="utf-8")
    if "TEMPLATE: incomplete" in text:
        errors.append(f"文件仍标记为未完成：{path.name}")
    if len(text.strip()) < minimum:
        errors.append(f"文件内容过短，无法视为完成：{path.name}")
    for heading in headings:
        if heading not in text:
            errors.append(f"{path.name} 缺少标题：{heading}")


def validate_scorecard(
    card: Any,
    state: dict[str, Any],
    round_number: int,
    root: Path,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(card, dict):
        return {}, ["评分卡顶层必须是对象。"]
    if card.get("round") != round_number:
        errors.append(f"评分卡 round 必须等于 {round_number}。")

    for key in ("journal_compliance", "source_integrity_checked", "prior_issue_audit_complete"):
        if not isinstance(card.get(key), bool):
            errors.append(f"{key} 必须是布尔值。")

    recommendation = card.get("secretary_recommendation")
    if recommendation not in {"revise", "attention_required", "ready"}:
        errors.append("secretary_recommendation 必须是 revise、attention_required 或 ready。")

    top_issue_lists: dict[str, list[str]] = {}
    for key in (
        "resolved_prior_issue_ids",
        "unresolved_prior_issue_ids",
        "recurring_issue_ids",
    ):
        values = require_list(card.get(key), key, errors)
        top_issue_lists[key] = validate_issue_ids(values, key, errors)
    for key in ("conflicts_needing_user", "missing_evidence_actions"):
        require_list(card.get(key), key, errors)

    reviewers_raw = require_list(card.get("reviewers"), "reviewers", errors)
    if len(reviewers_raw) != len(REVIEWERS):
        errors.append(f"reviewers 必须恰好有 {len(REVIEWERS)} 位。")
    expected_ids = [item[0] for item in REVIEWERS]
    seen_ids: list[str] = []
    normalized_reviewers: list[dict[str, Any]] = []
    all_current_issues: set[str] = set()
    all_scores: list[float] = []
    all_dimensions: list[float] = []

    for index, raw in enumerate(reviewers_raw):
        if not isinstance(raw, dict):
            errors.append(f"reviewers[{index}] 必须是对象。")
            continue
        reviewer_id = raw.get("id")
        if reviewer_id not in expected_ids:
            errors.append(f"reviewers[{index}].id 无效：{reviewer_id!r}")
        else:
            seen_ids.append(reviewer_id)
        dimensions = raw.get("dimensions")
        dimension_scores: dict[str, float] = {}
        if not isinstance(dimensions, dict):
            errors.append(f"{reviewer_id}.dimensions 必须是对象。")
            dimensions = {}
        missing_dims = set(DIMENSIONS) - set(dimensions)
        extra_dims = set(dimensions) - set(DIMENSIONS)
        if missing_dims:
            errors.append(f"{reviewer_id} 缺少维度：{sorted(missing_dims)}")
        if extra_dims:
            errors.append(f"{reviewer_id} 含未知维度：{sorted(extra_dims)}")
        for dimension in DIMENSIONS:
            score = numeric_score(dimensions.get(dimension), f"{reviewer_id}.{dimension}", errors)
            dimension_scores[dimension] = score
            all_dimensions.append(score)
        overall = numeric_score(raw.get("overall"), f"{reviewer_id}.overall", errors)
        all_scores.append(overall)
        ready = raw.get("submission_ready")
        if not isinstance(ready, bool):
            errors.append(f"{reviewer_id}.submission_ready 必须是布尔值。")
            ready = False
        blocker_values = require_list(raw.get("blocking_issue_ids"), f"{reviewer_id}.blocking_issue_ids", errors)
        must_values = require_list(raw.get("must_fix_issue_ids"), f"{reviewer_id}.must_fix_issue_ids", errors)
        blockers = validate_issue_ids(blocker_values, f"{reviewer_id}.blocking_issue_ids", errors)
        must_fix = validate_issue_ids(must_values, f"{reviewer_id}.must_fix_issue_ids", errors)
        all_current_issues.update(blockers)
        all_current_issues.update(must_fix)
        thresholds = state["thresholds"]
        low_score = overall < thresholds["min_reviewer_overall"] or any(
            value < thresholds["min_dimension"] for value in dimension_scores.values()
        )
        if (low_score or not ready) and not (blockers or must_fix):
            errors.append(f"{reviewer_id} 未达门槛却没有给出 P0/P1 问题 ID，评分与意见不一致。")
        normalized_reviewers.append(
            {
                "id": reviewer_id,
                "overall": overall,
                "dimensions": dimension_scores,
                "submission_ready": ready,
                "blocking_issue_ids": blockers,
                "must_fix_issue_ids": must_fix,
            }
        )

    if sorted(seen_ids) != sorted(expected_ids):
        errors.append(f"审稿人 ID 必须恰好为 {expected_ids}，实际为 {seen_ids}。")

    prior_open = set(state.get("open_issue_ids", [])) if round_number > 1 else set()
    resolved = set(top_issue_lists.get("resolved_prior_issue_ids", []))
    unresolved = set(top_issue_lists.get("unresolved_prior_issue_ids", []))
    recurring = set(top_issue_lists.get("recurring_issue_ids", []))
    if (resolved & unresolved) or (resolved & recurring) or (unresolved & recurring):
        errors.append("旧问题的已解决、未解决、复发列表不能重叠。")
    classified = resolved | unresolved | recurring
    if prior_open != classified:
        missing = sorted(prior_open - classified)
        unknown = sorted(classified - prior_open)
        if missing:
            errors.append(f"以下上轮问题未分类：{missing}")
        if unknown:
            errors.append(f"旧问题分类含并非上轮开放的问题：{unknown}")
    if round_number > 1 and card.get("prior_issue_audit_complete") is not True:
        errors.append("第二轮起必须完成旧问题逐项复核。")

    normalized = {
        "reviewers": normalized_reviewers,
        "all_scores": all_scores,
        "all_dimensions": all_dimensions,
        "current_issue_ids": sorted(all_current_issues),
        "resolved_prior_issue_ids": sorted(resolved),
        "unresolved_prior_issue_ids": sorted(unresolved),
        "recurring_issue_ids": sorted(recurring),
    }
    return normalized, errors


def final_report_text(state: dict[str, Any], evaluation: dict[str, Any]) -> str:
    outputs = state.get("latest_outputs") or [state["paper"]["manuscript_baseline"]]
    output_lines = []
    for item in outputs:
        output_lines.append(
            f"- `{item['path']}` — {item['bytes']} bytes — SHA-256 `{item['sha256']}`"
        )
    score_lines = []
    for reviewer in evaluation["reviewers"]:
        score_lines.append(
            f"- {reviewer['id']}：overall={reviewer['overall']:.2f}，submission_ready=true"
        )
    return f"""# 最终可投稿报告

## 结论

当前版本已通过本工作流的五审硬门槛，达到面向 **{state['paper']['target_journal']}** 的模拟可投稿状态。这不是编辑部或真实同行评审的录用保证。

## 稿件信息

- 论文：{state['paper']['title']}
- 稿件类型：{state['paper']['article_type']}
- 语言：{state['paper']['language']}
- 完成轮次：R{state['current_round']:02d}
- 完成时间：{now_utc()}

## 最终文件

{chr(10).join(output_lines)}

## 五审评分

{chr(10).join(score_lines)}

## 已通过的硬门槛

- 五位审稿人的总分均不低于 {state['thresholds']['min_reviewer_overall']}。
- 每位审稿人的所有维度均不低于 {state['thresholds']['min_dimension']}。
- P0、P1、未解决旧问题和复发问题均为零。
- 五位审稿人均确认 submission_ready=true。
- 目标期刊合规与源文件/渲染件一致性检查均已确认。
- 无待用户裁决的冲突，无缺失证据行动。

## 残余风险

作者仍需在真实投稿前核对作者顺序、利益冲突、伦理声明、数据/代码可得性、投稿信、最新期刊格式和投稿系统生成的最终 PDF。模拟审稿不能替代真实领域专家、统计审查或编辑决定。
"""


def command_record_review(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    with workspace_lock(root):
        state = load_state(root)
        if state["phase"] != "awaiting_review":
            fail(f"当前阶段为 {state['phase']}，不能登记审稿。")
        round_number = args.round or state["current_round"]
        if round_number != state["current_round"]:
            fail(f"只能登记当前轮 R{state['current_round']:02d}。")

        directory = round_dir(root, round_number)
        scorecard_path = Path(args.scorecard).expanduser().resolve() if args.scorecard else directory / f"scorecard_R{round_number:02d}.json"
        summary_path = Path(args.summary).expanduser().resolve() if args.summary else directory / f"审稿汇总_R{round_number:02d}.md"
        tasks_path = Path(args.tasks).expanduser().resolve() if args.tasks else directory / f"修改任务单_R{round_number:02d}.md"

        errors: list[str] = []
        for _, filename, _ in REVIEWERS:
            validate_completed_markdown(directory / filename, REQUIRED_REVIEW_HEADINGS, 500, errors)
        validate_completed_markdown(summary_path, (), 350, errors)
        validate_completed_markdown(tasks_path, (), 250, errors)
        card = load_json(scorecard_path, "评分卡")
        evaluation, score_errors = validate_scorecard(card, state, round_number, root)
        errors.extend(score_errors)
        if errors:
            fail("本轮交付物未通过校验：\n- " + "\n- ".join(errors))

        thresholds = state["thresholds"]
        reviewers = evaluation["reviewers"]
        gate_failures: list[str] = []
        if any(item["overall"] < thresholds["min_reviewer_overall"] for item in reviewers):
            gate_failures.append("至少一位审稿人总分低于门槛")
        if any(
            value < thresholds["min_dimension"]
            for item in reviewers
            for value in item["dimensions"].values()
        ):
            gate_failures.append("至少一个评分维度低于门槛")
        if any(not item["submission_ready"] for item in reviewers):
            gate_failures.append("至少一位审稿人未确认可投稿")
        if evaluation["current_issue_ids"]:
            gate_failures.append("存在 P0/P1 问题")
        if evaluation["unresolved_prior_issue_ids"]:
            gate_failures.append("存在未解决旧问题")
        if evaluation["recurring_issue_ids"]:
            gate_failures.append("存在复发问题")
        if card["journal_compliance"] is not True:
            gate_failures.append("目标期刊合规检查未通过")
        if card["source_integrity_checked"] is not True:
            gate_failures.append("源文件与渲染件一致性未确认")
        if card["prior_issue_audit_complete"] is not True:
            gate_failures.append("旧问题复核未确认完成")
        if card["conflicts_needing_user"]:
            gate_failures.append("存在待用户裁决的审稿冲突")
        if card["missing_evidence_actions"]:
            gate_failures.append("存在缺失证据行动")

        overall_average = sum(evaluation["all_scores"]) / len(evaluation["all_scores"])
        previous_averages = [
            record.get("overall_average")
            for key, record in sorted(state["rounds"].items(), key=lambda pair: int(pair[0]))
            if int(key) < round_number and isinstance(record.get("overall_average"), (int, float))
        ]
        improvement = None
        if previous_averages:
            improvement = overall_average - previous_averages[-1]
            if improvement < thresholds["min_progress"]:
                state["stagnation_count"] = int(state.get("stagnation_count", 0)) + 1
            else:
                state["stagnation_count"] = 0

        passed = not gate_failures
        open_issues = sorted(
            set(evaluation["current_issue_ids"])
            | set(evaluation["unresolved_prior_issue_ids"])
            | set(evaluation["recurring_issue_ids"])
        )
        record = state["rounds"][str(round_number)]
        record.update(
            {
                "status": "passed" if passed else "revision_required",
                "recorded_at": now_utc(),
                "scorecard": str(scorecard_path),
                "summary": str(summary_path),
                "tasks": str(tasks_path),
                "overall_average": round(overall_average, 4),
                "minimum_overall": min(evaluation["all_scores"]),
                "minimum_dimension": min(evaluation["all_dimensions"]),
                "improvement": round(improvement, 4) if improvement is not None else None,
                "gate_failures": gate_failures,
                "open_issue_ids": open_issues,
            }
        )
        state["open_issue_ids"] = open_issues

        attention_reasons: list[str] = []
        if card["conflicts_needing_user"]:
            attention_reasons.append("审稿冲突需要用户裁决")
        if card["missing_evidence_actions"]:
            attention_reasons.append("需要新增或提供证据")
        if not state["paper"].get("editable_source") and not passed:
            attention_reasons.append("只有 PDF，缺少权威可编辑源")
        if state.get("stagnation_count", 0) >= 2 and not passed:
            attention_reasons.append("连续两轮提升低于进展阈值")
        if round_number >= thresholds["max_rounds"] and not passed:
            attention_reasons.append("达到自主轮次上限")

        if passed:
            state["phase"] = "complete"
            atomic_write_text(root / "最终可投稿报告.md", final_report_text(state, evaluation))
            event_type = "workflow_completed"
            audience = "all"
        elif attention_reasons:
            state["phase"] = "attention_required"
            state.setdefault("attention_history", []).append(
                {"round": round_number, "created_at": now_utc(), "reasons": attention_reasons}
            )
            event_type = "attention_required"
            audience = "user"
        else:
            state["phase"] = "awaiting_revision"
            event_type = "revision_requested"
            audience = state["roles"]["lead"]

        event = commit(
            root,
            state,
            event_type,
            audience,
            {
                "passed": passed,
                "overall_average": round(overall_average, 4),
                "gate_failures": gate_failures,
                "attention_reasons": attention_reasons,
                "summary": str(summary_path),
                "tasks": str(tasks_path),
            },
        )
    print(json.dumps({"phase": state["phase"], "event": event, "gate_failures": gate_failures}, ensure_ascii=False, indent=2))


def complete_revision(args: argparse.Namespace, existing_marker: Path | None = None) -> None:
    root = resolve_workspace(args.workspace)
    with workspace_lock(root):
        state = load_state(root)
        if state["phase"] != "awaiting_revision":
            fail(f"当前阶段为 {state['phase']}，不能提交主力完成标记。")
        if args.agent.casefold() != state["roles"]["lead"].casefold():
            fail(f"提交者必须是已登记主力 {state['roles']['lead']!r}。")
        if not state["paper"].get("editable_source"):
            fail("尚无权威可编辑源。先用 set-source 登记 TeX/DOCX 等源文件。")
        outputs = [resolve_file(item, "修改后输出") for item in args.output]
        change_log = resolve_file(args.change_log, "变更日志")
        checks = [ensure_nonempty(item, "核验说明") for item in args.check]

        open_issues = set(state.get("open_issue_ids", []))
        resolved = set(args.resolved or [])
        declared_unresolved = set(args.unresolved or [])
        unknown = (resolved | declared_unresolved) - open_issues
        if unknown:
            fail(f"完成标记引用了当前不存在的问题 ID：{sorted(unknown)}")
        overlap = resolved & declared_unresolved
        if overlap:
            fail(f"问题不能同时标为已解决和未解决：{sorted(overlap)}")
        unresolved = sorted(open_issues - resolved)
        round_number = state["current_round"]
        agent_name = safe_filename(state["roles"]["lead"], "lead")
        paper_name = safe_filename(state["paper"]["title"], "paper")
        expected_prefix = f"{agent_name}_{round_number}修"
        generated_marker = root / f"{expected_prefix}{paper_name}.md"
        if existing_marker is None:
            marker = generated_marker
            if marker.exists():
                fail(f"完成标记已存在，拒绝覆盖：{marker}。若它由外部主力生成，请使用 accept-marker。")
        else:
            marker = existing_marker.resolve()
            if marker.parent != root:
                fail("外部完成标记必须直接位于协作目录根部。")
            if marker.suffix.lower() != ".md" or not marker.name.startswith(expected_prefix):
                fail(f"外部完成标记应以 {expected_prefix!r} 开头并使用 .md 扩展名。")
            if marker.stat().st_size < 20:
                fail("外部完成标记内容过短，无法可靠交接。")

        output_records = [file_record(path) for path in outputs]
        change_record = file_record(change_log)
        marker_text = f"""# {state['roles']['lead']} 第 {round_number} 次修改完成

- 工作流 ID：`{state['workflow_id']}`
- 修改轮次：{round_number}
- 主力：{state['roles']['lead']}
- 生成时间：{now_utc()}
- 变更日志：`{change_record['path']}`
- 变更日志 SHA-256：`{change_record['sha256']}`

## 修改后文件

{chr(10).join(f"- `{item['path']}` — {item['bytes']} bytes — SHA-256 `{item['sha256']}`" for item in output_records)}

## 已解决问题

{chr(10).join(f'- `{item}`' for item in sorted(resolved)) if resolved else '- 无'}

## 未解决问题

{chr(10).join(f'- `{item}`' for item in unresolved) if unresolved else '- 无'}

## 主力核验

{chr(10).join(f'- {item}' for item in checks)}

## 复审要求

秘书必须打开以上修改后文件，并在上一轮问题清单基础上逐项复核；本标记不能替代论文正文或变更日志。
"""
        if existing_marker is None:
            atomic_write_text(marker, marker_text)
        state["latest_outputs"] = output_records
        state["rounds"][str(round_number)]["revision"] = {
            "completed_at": now_utc(),
            "agent": state["roles"]["lead"],
            "marker": str(marker),
            "marker_record": file_record(marker),
            "marker_origin": "bridge_generated" if existing_marker is None else "external_agent",
            "outputs": output_records,
            "change_log": change_record,
            "resolved_issue_ids": sorted(resolved),
            "unresolved_issue_ids": unresolved,
            "checks": checks,
        }
        next_round = round_number + 1
        state["current_round"] = next_round
        state["phase"] = "awaiting_review"
        scaffold_round(root, state, next_round)
        event = commit(
            root,
            state,
            "review_requested",
            state["roles"]["secretary"],
            {
                "reason": "revision_completed",
                "completed_revision": round_number,
                "marker": str(marker),
                "outputs": [item["path"] for item in output_records],
                "carried_open_issue_ids": unresolved,
            },
        )
    print(json.dumps({"marker": str(marker), "next_round": next_round, "event": event}, ensure_ascii=False, indent=2))


def command_lead_done(args: argparse.Namespace) -> None:
    complete_revision(args)


def command_accept_marker(args: argparse.Namespace) -> None:
    marker = resolve_file(args.marker, "外部主力完成标记")
    complete_revision(args, existing_marker=marker)


def command_set_source(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    source = resolve_file(args.editable_source, "权威可编辑源")
    with workspace_lock(root):
        state = load_state(root)
        if state["phase"] == "complete":
            fail("流程已完成，不能更改权威源。")
        old = state["paper"].get("editable_source")
        state["paper"]["editable_source"] = str(source)
        state["paper"]["editable_source_baseline"] = file_record(source)
        atomic_write_text(root / "00_协作配置.md", config_markdown(state))
        event = commit(
            root,
            state,
            "editable_source_updated",
            state["roles"]["secretary"],
            {"old": old, "new": str(source)},
        )
    print(json.dumps({"editable_source": str(source), "event": event}, ensure_ascii=False, indent=2))


def command_resolve_attention(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    note = ensure_nonempty(args.note, "用户决定或证据说明")
    with workspace_lock(root):
        state = load_state(root)
        if state["phase"] != "attention_required":
            fail(f"当前阶段为 {state['phase']}，无需解除注意状态。")
        if not state["paper"].get("editable_source"):
            fail("仍缺少权威可编辑源；先运行 set-source。")
        state["attention_history"][-1]["resolved_at"] = now_utc()
        state["attention_history"][-1]["resolution_note"] = note
        state["phase"] = "awaiting_revision"
        event = commit(
            root,
            state,
            "revision_requested",
            state["roles"]["lead"],
            {"attention_resolution": note},
        )
    print(json.dumps({"phase": state["phase"], "event": event}, ensure_ascii=False, indent=2))


def known_revision_markers(state: dict[str, Any]) -> set[str]:
    markers: set[str] = set()
    for record in state.get("rounds", {}).values():
        revision = record.get("revision") if isinstance(record, dict) else None
        if isinstance(revision, dict) and revision.get("marker"):
            markers.add(str(Path(revision["marker"]).resolve()))
    return markers


def command_scan(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    with workspace_lock(root):
        state = load_state(root)
        monitor = state.get("monitor")
        if not isinstance(monitor, dict):
            fail("状态中缺少监测配置。")
        monitor["last_scan_at"] = now_utc()
        monitor["scan_count"] = int(monitor.get("scan_count", 0)) + 1

        expected_prefix = (
            f"{safe_filename(state['roles']['lead'], 'lead')}_"
            f"{state['current_round']}修"
        )
        known = known_revision_markers(state)
        already_detected = {
            str(Path(item).resolve()) for item in monitor.get("detected_markers", [])
        }
        candidates: list[Path] = []
        if state["phase"] == "awaiting_revision":
            for path in sorted(root.glob(f"{expected_prefix}*.md")):
                resolved = str(path.resolve())
                if path.is_file() and path.stat().st_size > 0 and resolved not in known and resolved not in already_detected:
                    candidates.append(path.resolve())

        if candidates:
            records = [file_record(path) for path in candidates]
            monitor.setdefault("detected_markers", []).extend(item["path"] for item in records)
            monitor["last_detection_at"] = now_utc()
            event = commit(
                root,
                state,
                "completion_marker_detected",
                state["roles"]["secretary"],
                {
                    "markers": records,
                    "warning": "检测只证明文件出现；秘书仍须核验标记和修改后论文。",
                },
            )
        else:
            save_without_event(root, state)
            event = None
    print(
        json.dumps(
            {
                "scanned_at": monitor["last_scan_at"],
                "phase": state["phase"],
                "expected_prefix": expected_prefix,
                "new_markers": [str(item) for item in candidates],
                "event": event,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_monitor_activate(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    if args.authorization_confirmed != "yes":
        fail("安装或登记持久化调度器前必须取得用户明确授权。")
    scheduler_id = ensure_nonempty(args.scheduler_id, "调度器 ID")
    with workspace_lock(root):
        state = load_state(root)
        monitor = state.get("monitor")
        if not isinstance(monitor, dict) or monitor.get("mode") != "scheduled":
            fail("当前工作流没有配置定时检索。")
        if args.wake_verified and monitor.get("wake_adapter") == "event_only":
            fail("event_only 只能自动扫描和记录事件，不能标记为 wake_verified。")
        monitor["scheduler_authorized"] = "yes"
        monitor["scheduler_id"] = scheduler_id
        monitor["status"] = "wake_verified" if args.wake_verified else "active"
        monitor["activated_at"] = now_utc()
        if args.note:
            monitor["activation_note"] = ensure_nonempty(args.note, "激活说明")
        event = commit(
            root,
            state,
            "monitor_activated",
            state["roles"]["secretary"],
            {
                "scheduler_id": scheduler_id,
                "status": monitor["status"],
                "method": monitor["method"],
                "wake_adapter": monitor["wake_adapter"],
            },
        )
    print(json.dumps({"monitor": monitor, "event": event}, ensure_ascii=False, indent=2))


def command_monitor_deactivate(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    if args.confirmed != "yes":
        fail("停用持久化监测前必须取得用户确认。")
    note = ensure_nonempty(args.note, "停用说明")
    with workspace_lock(root):
        state = load_state(root)
        monitor = state.get("monitor")
        if not isinstance(monitor, dict):
            fail("状态中缺少监测配置。")
        previous_scheduler = monitor.get("scheduler_id")
        monitor["status"] = "configured" if monitor.get("mode") == "scheduled" else "manual"
        monitor["scheduler_id"] = None
        monitor["deactivated_at"] = now_utc()
        monitor["deactivation_note"] = note
        event = commit(
            root,
            state,
            "monitor_deactivated",
            state["roles"]["secretary"],
            {"previous_scheduler_id": previous_scheduler, "note": note},
        )
    print(json.dumps({"monitor": monitor, "event": event}, ensure_ascii=False, indent=2))


def read_events(root: Path) -> list[dict[str, Any]]:
    path = root / EVENTS_NAME
    if not path.exists():
        return []
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"事件日志第 {line_number} 行损坏：{exc}")
        if not isinstance(item, dict):
            fail(f"事件日志第 {line_number} 行不是对象。")
        result.append(item)
    return result


def command_status(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    state = load_state(root)
    payload = {
        "workflow_id": state["workflow_id"],
        "workspace": str(root),
        "phase": state["phase"],
        "round": state["current_round"],
        "lead": state["roles"]["lead"],
        "secretary": state["roles"]["secretary"],
        "open_issue_ids": state.get("open_issue_ids", []),
        "monitor": state.get("monitor"),
        "last_event": state.get("last_event"),
        "next_action": phase_next_action(state),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(status_markdown(state))


def command_wait(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    if not 0 <= args.timeout <= 60:
        fail("timeout 必须在 0–60 秒之间。")
    if not 0.1 <= args.poll <= 10:
        fail("poll 必须在 0.1–10 秒之间。")
    deadline = time.monotonic() + args.timeout
    while True:
        state = load_state(root)
        if state.get("event_seq", 0) > args.after_event:
            events = [item for item in read_events(root) if item.get("event_id", 0) > args.after_event]
            print(json.dumps({"timed_out": False, "events": events, "status": state["phase"]}, ensure_ascii=False, indent=2))
            return
        if time.monotonic() >= deadline:
            print(json.dumps({"timed_out": True, "events": [], "status": state["phase"]}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        time.sleep(min(args.poll, max(0.0, deadline - time.monotonic())))


def command_verify(args: argparse.Namespace) -> None:
    root = resolve_workspace(args.workspace)
    state = load_state(root)
    errors: list[str] = []
    warnings: list[str] = []
    if Path(state.get("workspace", "")).resolve() != root:
        errors.append("状态文件中的 workspace 与当前目录不一致。")
    if state.get("intake", {}).get("confirmed") is not True:
        errors.append("缺少用户启动确认记录。")
    if not str(state.get("intake", {}).get("confirmation_note", "")).strip():
        errors.append("缺少用户启动确认说明。")
    agents = state.get("roles", {}).get("agents", [])
    if not isinstance(agents, list) or len({str(item).casefold() for item in agents}) < 2:
        errors.append("全部 AI 列表无效或少于两个。")
    if state.get("roles", {}).get("lead") not in agents:
        errors.append("主力不在全部 AI 列表中。")
    if state.get("roles", {}).get("secretary") not in agents:
        errors.append("秘书不在全部 AI 列表中。")
    if state["roles"]["lead"].casefold() == state["roles"]["secretary"].casefold():
        errors.append("主力和秘书角色相同。")
    for key in ("manuscript",):
        if not Path(state["paper"].get(key, "")).is_file():
            errors.append(f"论文路径不存在：{state['paper'].get(key)}")
    editable = state["paper"].get("editable_source")
    if editable and not Path(editable).is_file():
        errors.append(f"权威可编辑源不存在：{editable}")
    if not editable:
        warnings.append("未登记权威可编辑源；只能审稿，不能可靠进入修改闭环。")
    for required in ("00_协作配置.md", "00_当前状态.md", STATE_NAME, MONITOR_NAME, EVENTS_NAME):
        if not (root / required).is_file():
            errors.append(f"缺少协作文件：{required}")
    monitor = state.get("monitor")
    if not isinstance(monitor, dict):
        errors.append("状态中缺少有效监测配置。")
    else:
        monitor_file = root / MONITOR_NAME
        if monitor_file.is_file() and load_json(monitor_file, "监测配置") != monitor:
            errors.append("monitor-config.json 与主状态中的监测配置不一致。")
        if monitor.get("status") not in {"manual", "configured", "active", "wake_verified"}:
            errors.append(f"监测状态无效：{monitor.get('status')!r}")
        if monitor.get("mode") == "scheduled":
            if not isinstance(monitor.get("interval_minutes"), int):
                errors.append("定时检索缺少整数轮询间隔。")
            if monitor.get("status") == "configured":
                warnings.append("定时检索仅完成配置，尚未验证宿主调度器已激活。")
            if monitor.get("status") in {"active", "wake_verified"} and not monitor.get("scheduler_id"):
                errors.append("监测已标为激活，但缺少调度器 ID。")
        if monitor.get("status") == "wake_verified" and monitor.get("wake_adapter") == "event_only":
            errors.append("event_only 不能标记为 wake_verified。")
    events = read_events(root)
    event_ids = [item.get("event_id") for item in events]
    if event_ids != list(range(1, len(event_ids) + 1)):
        errors.append(f"事件 ID 不连续：{event_ids}")
    if event_ids and event_ids[-1] != state.get("event_seq"):
        errors.append("事件日志末尾 ID 与状态 event_seq 不一致。")
    for number_text, record in state.get("rounds", {}).items():
        try:
            number = int(number_text)
        except ValueError:
            errors.append(f"轮次键不是整数：{number_text!r}")
            continue
        directory = round_dir(root, number)
        if not directory.is_dir():
            errors.append(f"缺少轮次目录：{directory}")
            continue
        for _, filename, _ in REVIEWERS:
            if not (directory / filename).is_file():
                errors.append(f"R{number:02d} 缺少审稿文件：{filename}")
        revision = record.get("revision")
        if revision:
            if not Path(revision.get("marker", "")).is_file():
                errors.append(f"R{number:02d} 的完成标记不存在。")
            for item in revision.get("outputs", []):
                path = Path(item.get("path", ""))
                if not path.is_file():
                    errors.append(f"R{number:02d} 的修改后文件不存在：{path}")
                elif sha256_file(path) != item.get("sha256"):
                    warnings.append(f"R{number:02d} 的文件在提交完成标记后发生变化：{path}")
    if state["phase"] == "complete" and not (root / "最终可投稿报告.md").is_file():
        errors.append("状态为 complete，但缺少最终可投稿报告。")
    payload = {"valid": not errors, "errors": errors, "warnings": warnings}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理多智能体科研论文审稿—修改—复审的文件交接状态。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="创建协同审稿+优化目录和第一轮模板。")
    init_parser.add_argument("--manuscript", required=True, help="基线稿 PDF/DOCX/TEX 的绝对路径。")
    init_parser.add_argument("--editable-source", help="唯一权威可编辑源的绝对路径。")
    init_parser.add_argument("--agent", action="append", required=True, help="参与协作的 AI 名称；至少重复两次。")
    init_parser.add_argument("--lead", required=True, help="主修改智能体名称。")
    init_parser.add_argument("--secretary", required=True, help="秘书智能体名称。")
    init_parser.add_argument("--journal", required=True, help="目标期刊。")
    init_parser.add_argument("--article-type", required=True, help="稿件类型。")
    init_parser.add_argument("--language", required=True, help="写作语言。")
    init_parser.add_argument("--title", help="论文显示标题；默认取文件名。")
    init_parser.add_argument(
        "--review-mode",
        required=True,
        choices=("independent_agents", "single_model_isolated", "human_mixed"),
        help="五审的真实实现方式。",
    )
    init_parser.add_argument(
        "--workspace-placement",
        required=True,
        choices=("same", "custom"),
        help="same 表示用户同意在论文目录创建；custom 需要 --workspace-parent。",
    )
    init_parser.add_argument("--workspace-parent", help="用户批准的自定义协作父目录。")
    init_parser.add_argument(
        "--monitor-mode",
        required=True,
        choices=("scheduled", "manual"),
        help="是否启用定时检索。",
    )
    init_parser.add_argument(
        "--monitor-method",
        required=True,
        choices=(
            "windows_task_scheduler",
            "codex_automation",
            "external_adapter",
            "long_running_host",
            "manual",
        ),
        help="执行定时扫描的真实宿主。",
    )
    init_parser.add_argument("--poll-interval-minutes", type=int, help="定时检索间隔；manual 模式省略。")
    init_parser.add_argument("--timezone", required=True, help="IANA 或宿主可识别的时区。")
    init_parser.add_argument("--monitor-duration", required=True, help="例如 until_complete 或用户指定的截止时间。")
    init_parser.add_argument(
        "--scheduler-authorized",
        required=True,
        choices=("yes", "no"),
        help="用户是否授权创建持久化系统定时任务。",
    )
    init_parser.add_argument(
        "--wake-adapter",
        required=True,
        help="真实唤醒适配器名称；若只能记录事件，明确填 event_only。",
    )
    init_parser.add_argument(
        "--intake-confirmed",
        required=True,
        choices=("yes",),
        help="仅在用户核对启动清单并明确确认后传 yes。",
    )
    init_parser.add_argument(
        "--intake-note",
        required=True,
        help="记录用户确认的摘要，不得用脚本默认值替代。",
    )
    init_parser.add_argument("--min-overall", type=float, default=9.0)
    init_parser.add_argument("--min-dimension", type=float, default=8.5)
    init_parser.add_argument("--min-progress", type=float, default=0.2)
    init_parser.add_argument("--max-rounds", type=int, default=5)
    init_parser.set_defaults(func=command_init)

    status_parser = subparsers.add_parser("status", help="显示当前状态和下一步。")
    status_parser.add_argument("--workspace", required=True)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=command_status)

    scan_parser = subparsers.add_parser("scan", help="幂等扫描预期完成标记并产生检测事件。")
    scan_parser.add_argument("--workspace", required=True)
    scan_parser.set_defaults(func=command_scan)

    activate_parser = subparsers.add_parser("monitor-activate", help="登记已实际安装的宿主调度器。")
    activate_parser.add_argument("--workspace", required=True)
    activate_parser.add_argument("--scheduler-id", required=True)
    activate_parser.add_argument("--authorization-confirmed", required=True, choices=("yes",))
    activate_parser.add_argument("--wake-verified", action="store_true")
    activate_parser.add_argument("--note")
    activate_parser.set_defaults(func=command_monitor_activate)

    deactivate_parser = subparsers.add_parser("monitor-deactivate", help="记录已停用的宿主调度器。")
    deactivate_parser.add_argument("--workspace", required=True)
    deactivate_parser.add_argument("--confirmed", required=True, choices=("yes",))
    deactivate_parser.add_argument("--note", required=True)
    deactivate_parser.set_defaults(func=command_monitor_deactivate)

    review_parser = subparsers.add_parser("record-review", help="校验并登记当前轮五审结果。")
    review_parser.add_argument("--workspace", required=True)
    review_parser.add_argument("--round", type=int)
    review_parser.add_argument("--scorecard", help="自定义评分卡路径。")
    review_parser.add_argument("--summary", help="自定义审稿汇总路径。")
    review_parser.add_argument("--tasks", help="自定义修改任务单路径。")
    review_parser.set_defaults(func=command_record_review)

    done_parser = subparsers.add_parser("lead-done", help="登记主力修改完成并创建下一轮。")
    done_parser.add_argument("--workspace", required=True)
    done_parser.add_argument("--agent", required=True)
    done_parser.add_argument("--output", action="append", required=True, help="修改后文件；可重复。")
    done_parser.add_argument("--change-log", required=True, help="变更日志 Markdown 路径。")
    done_parser.add_argument("--resolved", action="append", help="确认已解决的问题 ID；可重复。")
    done_parser.add_argument("--unresolved", action="append", help="明确仍未解决的问题 ID；可重复。")
    done_parser.add_argument("--check", action="append", required=True, help="已执行的编译/一致性核验；可重复。")
    done_parser.set_defaults(func=command_lead_done)

    accept_parser = subparsers.add_parser("accept-marker", help="核验并登记外部主力已经写入的完成标记。")
    accept_parser.add_argument("--workspace", required=True)
    accept_parser.add_argument("--marker", required=True, help="协作目录根部的外部完成标记。")
    accept_parser.add_argument("--agent", required=True)
    accept_parser.add_argument("--output", action="append", required=True, help="修改后文件；可重复。")
    accept_parser.add_argument("--change-log", required=True, help="变更日志 Markdown 路径。")
    accept_parser.add_argument("--resolved", action="append", help="确认已解决的问题 ID；可重复。")
    accept_parser.add_argument("--unresolved", action="append", help="明确仍未解决的问题 ID；可重复。")
    accept_parser.add_argument("--check", action="append", required=True, help="已执行的编译/一致性核验；可重复。")
    accept_parser.set_defaults(func=command_accept_marker)

    source_parser = subparsers.add_parser("set-source", help="登记或更换权威可编辑源。")
    source_parser.add_argument("--workspace", required=True)
    source_parser.add_argument("--editable-source", required=True)
    source_parser.set_defaults(func=command_set_source)

    resolve_parser = subparsers.add_parser("resolve-attention", help="记录用户决定并恢复修改。")
    resolve_parser.add_argument("--workspace", required=True)
    resolve_parser.add_argument("--note", required=True)
    resolve_parser.set_defaults(func=command_resolve_attention)

    wait_parser = subparsers.add_parser("wait", help="短轮询等待新事件；不会自行唤醒外部产品。")
    wait_parser.add_argument("--workspace", required=True)
    wait_parser.add_argument("--after-event", type=int, required=True)
    wait_parser.add_argument("--timeout", type=float, default=60.0)
    wait_parser.add_argument("--poll", type=float, default=1.0)
    wait_parser.set_defaults(func=command_wait)

    verify_parser = subparsers.add_parser("verify", help="检查协作目录、事件和交接文件完整性。")
    verify_parser.add_argument("--workspace", required=True)
    verify_parser.set_defaults(func=command_verify)

    return parser


def validate_cli_thresholds(args: argparse.Namespace) -> None:
    if args.command != "init":
        return
    if not 0 <= args.min_dimension <= 10:
        fail("min-dimension 必须在 0–10。")
    if not 0 <= args.min_overall <= 10:
        fail("min-overall 必须在 0–10。")
    if args.min_dimension > args.min_overall:
        fail("min-dimension 不应高于 min-overall。")
    if args.min_progress < 0:
        fail("min-progress 不能为负数。")
    if args.max_rounds < 1:
        fail("max-rounds 必须至少为 1。")
    if args.poll_interval_minutes is not None and not 1 <= args.poll_interval_minutes <= 1440:
        fail("poll-interval-minutes 必须在 1–1440。")


def main() -> None:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    validate_cli_thresholds(args)
    args.func(args)


if __name__ == "__main__":
    main()
