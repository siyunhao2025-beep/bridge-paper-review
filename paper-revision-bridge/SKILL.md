---
name: paper-revision-bridge
description: "协调两个或多个智能体对科研论文进行闭环审稿与修改：开工前强制确认论文路径、协作目录许可、全部 AI、主力/秘书和定时检索设置；随后接收本机 PDF、LaTeX、Word，组织五位跨维度模拟审稿人（全部检查防御性写作），合并意见、跟踪问题、监测交接标记，并迭代复审到严格的可投稿门槛。用户提到 Workbuddy、GPT、多智能体协作改论文、协同审稿、反复打分、论文文件夹监听或 PDF/TeX/DOCX 修改闭环时使用。"
---

# 多智能体论文修改桥接器

把“主力—秘书—五位审稿人”协作落实为可恢复、可审计的文件协议。用 `scripts/bridge.py` 管理状态与交接；用智能体完成需要学术判断的阅读、审稿和修改。

## 先守住边界

- 把“主力”和“秘书”视为逻辑角色，不假设产品天然互通。外部智能体只有在人工转交、轮询器、定时任务或 API 适配器实际存在时才会被唤醒。
- 把 9/10 解释为“达到本技能的模拟可投稿门槛”，绝不承诺期刊录用。
- 不靠文字润色掩盖缺失实验、统计错误、伦理问题、数据冲突或无法核验的结果。遇到这类阻断项，暂停并请用户提供证据或作决定。
- 不编造引文、数据、实验、审稿人共识或期刊要求。需要最新期刊规范时，从期刊官方来源核验并记录访问日期。
- 保留原稿。编辑版本化副本或用户明确指定的工作副本；禁止静默覆盖唯一原件。
- 只允许秘书维护审稿状态和汇总；主力只修改论文并提交完成标记，避免两个智能体同时改写状态。

## 1. 先问清楚，再做任何工作

每次新工作流开始时，必须先发送一轮集中问询并等待用户明确确认。即使用户已在首条消息里给出部分答案，也要把识别到的值列出来让用户核对。确认完成前，禁止读取论文、创建目录、初始化状态、派审稿人、修改文件或安装定时任务。

至少问清楚以下项目：

1. 论文存放在哪里：论文/项目的绝对路径、包含哪些 PDF/TeX/DOCX 文件、哪一个是权威可编辑源。
2. 是否允许在论文同一目录下创建 `协同审稿+优化`；若不允许，询问获准的自定义父目录。
3. 参与协作的全部 AI 智能体及其产品/版本；哪个是主修改智能体，哪个是秘书；两角色必须不同。
4. 目标期刊、稿件类型、写作语言，以及五审是真实独立智能体、单模型隔离五审还是人机混合。
5. 是否启用自动定时检索；若启用，询问轮询间隔、时区、运行宿主、持续期限、由谁接收事件、是否授权创建系统定时任务，以及用于真正唤醒秘书/主力的适配器或 API。
6. 若没有唤醒适配器，询问用户是否接受“自动扫描并记录事件，但仍需人工打开对应 AI”的降级模式。

优先使用下面的启动问卷，不要把问题散落到多轮里：

```text
开始前请一次确认：
1) 论文绝对路径及权威可编辑源是什么？
2) 可以在论文同目录创建“协同审稿+优化”吗？若不可以，请给自定义目录。
3) 将使用哪些 AI（名称+产品/版本）？谁是主力？谁是秘书？
4) 目标期刊、稿件类型、语言、五审实现方式是什么？
5) 是否启用自动定时检索？若启用：间隔、时区、持续期限、运行宿主、唤醒适配器/API 是什么？是否授权创建定时任务？
6) 若当前只能自动扫描、不能自动唤醒 AI，是否接受先采用事件记录+人工交接？
```

收到回复后，把所有答案整理成一张确认清单，并要求用户回复明确的“确认开始”。只有得到确认，才把 `--intake-confirmed yes` 传给初始化脚本。

处理多格式时确定唯一事实源：

- LaTeX：以根 `.tex` 和项目目录为权威源，PDF 仅作渲染核验。
- Word：以 `.docx` 为权威源，导出的 PDF 仅作版面核验。
- PDF-only：可以完整审稿，但不能声称能无损修改原稿；先索取 TeX/DOCX，或取得用户对转换损失的明确同意。
- 同时存在 TeX 和 DOCX：让用户二选一；禁止平行修改后再猜测如何合并。

读取 [workflow-protocol.md](references/workflow-protocol.md) 了解文件契约、状态机与适配器约束。读取 [review-rubric.md](references/review-rubric.md) 了解五位审稿人、打分门槛和防御性写作标准。

## 2. 建立协作空间

从本技能目录运行：

```powershell
py -3 scripts/bridge.py init `
  --manuscript "D:\papers\paper.pdf" `
  --editable-source "D:\papers\paper.tex" `
  --agent "GPT" `
  --agent "Workbuddy" `
  --lead "GPT" `
  --secretary "Workbuddy" `
  --journal "Target Journal" `
  --article-type "Research Article" `
  --language "English" `
  --review-mode "independent_agents" `
  --workspace-placement "same" `
  --monitor-mode "scheduled" `
  --monitor-method "windows_task_scheduler" `
  --poll-interval-minutes 5 `
  --timezone "Asia/Shanghai" `
  --monitor-duration "until_complete" `
  --scheduler-authorized yes `
  --wake-adapter "event_only" `
  --intake-confirmed yes `
  --intake-note "用户已确认论文路径、同目录建文件夹、GPT 为主力、Workbuddy 为秘书及每 5 分钟扫描"
```

`--workspace-placement same` 表示用户已同意在论文所在目录创建文件夹；若用户不同意，使用 `custom --workspace-parent <获准目录>`。脚本写入协作配置、监测配置、状态、事件日志和第一轮模板。若目录已存在，先运行 `status` 或 `verify`，不要重建或覆盖。

若已获授权使用 Windows 任务计划程序，在初始化后安装真实定时扫描：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows_monitor.ps1 `
  -Action Install `
  -Workspace "D:\papers\协同审稿+优化" `
  -IntervalMinutes 5 `
  -ConfirmedByUser yes
```

安装后运行 `status`，确认监测状态为 `active`。若选择 Codex 自动化、Workbuddy 计划任务或外部 API，使用对应宿主的正式调度能力，然后调用 `monitor-activate` 记录调度器 ID。仅写入 `monitor-config.json` 不等于定时任务已经启用。

## 3. 由秘书组织五审

让五位审稿角色相互独立地先读全文，再填写 `round-NN` 中各自的报告。可使用真实子智能体、产品内代理或同一模型的五次隔离审阅；必须如实记录实现方式，不能把一次生成伪装成五个独立代理。

要求每位审稿人：

- 给出可定位到页、行、节、图、表、公式或引用的证据；不能定位时明确标为“全局”。
- 区分 `P0 硬伤`、`P1 必改`、`P2 应改`、`P3 可选`。
- 单列“防御性写作审查”，同时保留必要的科学不确定性与局限性。
- 从第二轮起逐条判定旧问题为 `已解决/部分解决/未解决/复发/无法核验`。
- 提供 0–10 分以及是否可投稿；分数必须与列出的问题一致。

让秘书合并同义问题但保留分歧，生成：

- `审稿汇总_RNN.md`：五审结论、证据、冲突和总评分。
- `修改任务单_RNN.md`：唯一问题 ID、责任、依赖、验收条件和优先级。
- `scorecard_RNN.json`：供脚本执行硬门槛校验的结构化评分。

完成后运行：

```powershell
py -3 scripts/bridge.py record-review --workspace "D:\papers\协同审稿+优化" --round 1
```

若存在用户决策冲突、缺实验/数据或连续停滞，状态进入 `attention_required`。先向用户说明具体阻断项，得到决定后再运行：

```powershell
py -3 scripts/bridge.py resolve-attention --workspace "D:\papers\协同审稿+优化" --note "用户决定与证据路径"
```

## 4. 由主力修改并交接

让主力严格按任务单修改权威源文件，编译或导出对应 PDF，并记录每个问题 ID 的处理位置。不得只在完成标记里宣称“已改”。

完成一轮后运行：

```powershell
py -3 scripts/bridge.py lead-done `
  --workspace "D:\papers\协同审稿+优化" `
  --agent "GPT" `
  --output "D:\papers\revisions\paper-r1.tex" `
  --output "D:\papers\revisions\paper-r1.pdf" `
  --change-log "D:\papers\revisions\change-log-r1.md" `
  --resolved "R01-METH-001" `
  --resolved "R01-WRIT-002" `
  --check "LaTeX 编译成功，引用、公式和交叉引用无错误" `
  --check "逐项核对正文、图表与导出 PDF"
```

脚本在协作目录根部生成 `<主力>_1修<论文名>.md`，记录输出文件、散列值、已解决项和仍未解决项，然后创建下一轮复审模板。文件存在只证明交接标记已生成；秘书仍须读取修改后的论文，而不是只读标记。

若外部主力不能运行本脚本、只能自行写入符合命名规则的完成标记，定时 `scan` 会生成 `completion_marker_detected` 事件。秘书核验标记、修改稿和变更日志后，用 `accept-marker` 登记，而不是让 `lead-done` 覆盖现有文件：

```powershell
py -3 scripts/bridge.py accept-marker `
  --workspace "D:\papers\协同审稿+优化" `
  --marker "D:\papers\协同审稿+优化\GPT_1修paper.md" `
  --agent "GPT" `
  --output "D:\papers\revisions\paper-r1.pdf" `
  --change-log "D:\papers\revisions\change-log-r1.md" `
  --resolved "R01-METH-001" `
  --check "秘书已打开修改稿并核对标记、变更日志与文件散列"
```

## 5. 在上一轮基础上复审

让同一五个审稿角色维持职责与问题 ID 连续性：

1. 读取当前论文、上一轮五份报告、汇总、任务单、完成标记和变更日志。
2. 先复核旧问题，再发现新问题；禁止因措辞变化而默认旧问题已解决。
3. 检查新修改是否造成交叉矛盾、引用错位、数字不一致、图表/正文不一致或新防御性措辞。
4. 重新独立评分，再由秘书合并并运行 `record-review`。

重复“审稿 → 修改 → 完成标记 → 复审”，但不要机械循环。若需新增实验、数据、伦理审批或作者学术判断，立即暂停；若连续两轮提升小于 0.2 分或到第五轮仍未通过，必须升级给用户。

## 6. 只按硬门槛收口

仅当脚本同时确认以下条件时结束：

- 五位审稿人的总分分别不低于 9.0；不能用平均分掩盖低分。
- 每位审稿人的每个评分维度均不低于 8.5，且防御性写作维度存在。
- `P0`、`P1`、旧问题未解决项、复发项均为零。
- 每位审稿人均明确标记 `submission_ready=true`。
- 期刊格式/范围核验、源文件与渲染件一致性核验均通过。
- 不存在待用户裁决的审稿冲突，也不存在缺失证据行动。

通过后由脚本生成 `最终可投稿报告.md`。把结论表述为“模拟审稿达到目标期刊的可投稿门槛”，并保留残余风险说明。

## 7. 监测和恢复

查看下一步动作：

```powershell
py -3 scripts/bridge.py status --workspace "D:\papers\协同审稿+优化"
py -3 scripts/bridge.py verify --workspace "D:\papers\协同审稿+优化"
py -3 scripts/bridge.py scan --workspace "D:\papers\协同审稿+优化"
```

长驻宿主可每次最多等待 60 秒的新事件：

```powershell
py -3 scripts/bridge.py wait --workspace "D:\papers\协同审稿+优化" --after-event 3 --timeout 60
```

把 `wait/status` 的事件交给实际的产品适配器，再由适配器唤醒对应智能体。未配置适配器时，明确提示用户手动把协作目录路径交给下一角色。

自动监测分三层报告，不得混淆：

- `configured`：轮询参数已写入配置，但尚未验证调度器。
- `active`：宿主调度器已登记，并至少能定时调用 `scan`。
- `wake_verified`：已实际测试适配器能唤醒目标智能体并传递事件。只有完成端到端测试才可使用此状态。

Windows 用户可用同一个脚本执行 `-Action Status` 或 `-Action Remove`。删除定时任务前再次取得用户确认；完成工作流后提醒用户决定是否保留监测。
