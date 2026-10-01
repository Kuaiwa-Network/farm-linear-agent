# Linear comment templates (zh-CN, concise)

The ledger appends the marker line; do not write it yourself. Replace `<bot_name>` with `bot_name`
from your launch message, exactly as given: it is the Linear app this instance speaks as.

Replace `<owner.person.url>` with `owner.person.url` from `issue-context`; Linear renders a
profile URL as a mention. When `owner` is null, write 负责人 instead. When the comment needs a
decision from 策划 and `creator` is not null, add `creator.url` after it. Add no other person's URL.

## started
👀 <bot_name> 已开始处理：正在定位问题或要改动的位置，验证结果和草稿 PR 会补充在本 issue。

## blocker
<bot_name> 暂停处理。
- 已确认：<一到三条有证据的事实>
- 已尝试：<做过的检查>
- 需要：<具体缺少的信息或需要哪位负责人的决定>
- 请处理：<owner.person.url>

## delivery
<bot_name> 已提交修复或改动（草稿 PR，待 review）：
- 问题或需求：<观察到的现象，或要做的改动>
- 改动：<改了什么>
- 验证：<跑过的检查，以及没跑的和原因>
- PR：<链接，每个仓一行>
- 合并：请 <owner.person.url> review 后合并，<bot_name> 不会合并

## delivery (no change)
用于结论是「不需要改代码」的交付：已在主干修复、与已合并工单重复、无法复现，或要做的改动已在主干。不要用上面的
delivery 模板，它声称提交了草稿 PR。
<bot_name> 已确认无需改动：
- 问题或需求：<工单描述的现象或改动>
- 结论：<已在主干修复／与哪个工单重复／无法复现／改动已在主干>
- 依据：<提交、PR 或配置对比，给出可核对的链接>
- 验证：<跑过的检查，以及没跑的和原因>

## Code worker (`feature`)

The sections below are the `feature` worker's. Each notice's kind and request id go to `prepare-notice`, and the
ledger appends the marker line to notices too. Replace `<creator.url>` with `creator.url` from `issue-context`;
leave it out when `creator` is null or the comment asks 策划 nothing. Lines in （） are instructions to you, not
part of the comment. The line that begins 「按本卡要求」 is written only when a stage limit stops the job after that
stage (the skill's "A stage limit"); leave it out otherwise.

## feature started
👀 <bot_name> 已开始处理这张功能卡：先读策划案、整理契约缺口，问题、阶段进展和草稿 PR 会发在本 issue。

## feature questions
（`question` 通知，request id `questions-N`。按收件人分节，每节低置信度在前；没有问题的收件人整节删掉。）
<bot_name> 需要以下裁决才能继续（第 N 轮）。请在本 issue 用评论逐条回答，高置信度的也请回一句（例如「默认的照此」），答完后回复本会话或 @<bot_name>。我只记录答题人自己的回答，没人回答的不会默认成立。
请处理：<owner.person.url> <creator.url>
### 主策
- 真要你拍板的：Q1 <情境>：候选 A <做法与代价>；候选 B <做法与代价>；其他（请说）。我倾向 <…>，但依据不足，因为 <…>。
- 看一眼就行：Q2 <建议>；另一边的代价：<…>。
- 我打算这样做（也请回一句）：Q3 <结论>（依据：<位置>）。
### 服务端
（同上三类）
### 客户端
（同上三类）

## feature stage
（`stage` 通知，request id `stage-<阶段字母>`：只用于跳过的阶段和阶段 C 的通过。）
<bot_name> 阶段 <字母>（<名称>）<已跳过／已通过>：<理由或结果，带链接>。
下一步：<下一阶段，或正在等什么>。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。

## feature merge request
（`merge_request` 通知，request id `merge-contract` 或 `merge-waivers`。）
<bot_name> 请 <owner.person.url> review 并合并：<PR 链接>（<仓库>：<一句说明>）。
- 合并前提：<无；或需要先合并的 PR>
- 合并后：<合并带来的后续，例如契约合并后它加的 BREAKING_WAIVERS 记账随即失效，我会开 PR 移除>
- 下一步：<不等合并就开始的阶段，或合并后才做的事>
<bot_name> 不会合并；合并后请回复本会话或 @<bot_name>，我会去 GitHub 核对。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。

## feature config needed
（`waiting` 通知，request id `config-needed`。）
<bot_name> 已提交配表声明（草稿 PR，待 review）：<PR 链接>
请 <owner.person.url> review 后合并：合并即确认这些表名、列名和字段名。<creator.url>
需要策划填写的配置（表头文字必须完全一致，不一致或没声明的列会被静默丢弃）：
- <文件> / <sheet>
  - 「<表头原文>」：位置由策划定；类型 <类型>；取值 <含义与范围>；默认值 <仅当需要非中性默认值时写>
- 新枚举 <枚举名>：<新增标签>
「配置就绪」指：策划的数据和这些声明都已提交到 farm-common 的同一个 commit 或分支（不必是 main）。就绪后请在本 issue 写出那个 commit 或分支名，再回复本会话或 @<bot_name>；我会按表头逐列核对、重新导表，然后开始服务端。
提醒：交付评论出现前请不要把卡片移到 Done 或 Canceled，那会取消这项工作。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。
