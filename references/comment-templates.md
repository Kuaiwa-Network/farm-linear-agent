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
（`stage` 通知，request id `stage-<阶段字母>`：用于跳过的阶段、阶段 C 的通过，以及阶段 D/E/F 完成后按人工限制停下。）
<bot_name> 阶段 <字母>（<名称>）<已跳过／已通过>：<理由或结果，带链接>。
下一步：<下一阶段，或正在等什么>。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。

## feature merge request
（`merge_request` 通知，request id `merge-contract`、`merge-waivers` 或 `merge-writeback`。）
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

## feature still waiting
（`waiting` 通知，request id `config-needed-N`、`ui-needed-N` 或 `closing-N`，N 从 2 起：恢复后核对没通过、或还有步骤没完成时用。<creator.url> 只在 `config-needed-N` 里写，配置由策划准备。）
<bot_name> 已完成：<这次做了什么，带链接；没有就删掉这一行>
还在等：<逐条列出仍需的人工步骤>
我核对了：<查了什么>；没有找到：<缺什么，或核对失败的原文>
请处理：<owner.person.url> <creator.url>
完成后请回复本会话或 @<bot_name>。

## feature UI needed
（`waiting` 通知，request id `ui-needed`，核对失败的新一轮从 `ui-needed-2` 起；先保存完整正文和 owner，再发通知。）
<bot_name> 的服务端／配置阶段已完成（没有的阶段已跳过）。请 <owner.person.url> 确认以下 UI 及导出已就绪：
- 包／组件：<契约约定的包、组件名称与身份>
- farmgui main：<组件文件与身份>
- Farm-Client main：<Assets/GameRes/FairyRes 下的实际导出与依赖>
现有包不代表这次 UI 已完成，请明确回复本会话或 @<bot_name>。我会核对两边 main 的实际组件和导出后才开始客户端；缺失时会列出检查结果再请你处理。我不会制作或导出 UI，也不会默认把沉默当成确认。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。

## feature closing
（`waiting` 通知，request id `closing`。不需要的步骤整条删掉。流水线名写服务端 PR 采用的发布方式；今天是 designer-source.pipeline。）
（服务端阶段跳过时，删掉服务端 PR、服务端协议同步和 Jenkins 发布要求，保留客户端 re-export／验证要求及配置 commit。）
<bot_name> 的服务端／客户端草稿及本地验证已提交，还需要这些人工步骤（UI 就绪已在阶段 E 核对；无 UI 时已记录跳过）：
1. 请 <owner.person.url> 合并契约 PR：<链接>。合并后我会移除它带来的 BREAKING_WAIVERS 记账（如有），并把客户端和服务端的协议同步到同一个核验过的 main commit。
2. 请合并配表声明 PR：<链接>。
3. 请有权限的同学在 Jenkins 运行 designer-source.pipeline，分支选 <Jenkins 分支>（它指向配置 commit <短 SHA>，不含别的提交），把流水线末尾打印的三行原样贴到本 issue：DESIGNER_SOURCE_VERSION、DESIGNER_SOURCE_ARCHIVE_SHA256、DESIGNER_SOURCE_DIGEST。预期版本：<日期>.<短 hash>（短 hash 的位数以流水线打印的为准）。
服务端 PR 要等契约合并、我推送协议同步、并写入发布后的三个 pin 值之后再合并：<服务端 PR 链接>
客户端 PR 要等契约合并后的重新导出、配置来源和对应 commit 的客户端／Unity 验证通过后再合并：<客户端 PR 链接>。
我会另开契约回账／归档草稿 PR，保留仍待人工合并或验收的条目：<已存在的 writeback 链接；尚未创建则说明等待哪些条件>。
每完成一步请回复本会话或 @<bot_name>；我不会轮询 GitHub。
提醒：交付评论出现前请不要把卡片移到 Done 或 Canceled，那会取消这项工作。
按本卡要求，<bot_name> 在阶段 <字母> 后停下；要继续请回复本会话或 @<bot_name>。

## feature pin mismatch
（`question` 通知，request id `questions-N`。）
<bot_name> 核对了贴出的 pin：版本指向 <版本里的 commit>，不是配置 commit <短 SHA>：<差异说明>。
请 <owner.person.url> 决定：改用这个 commit 重新核对配置再 pin（若它不在 <Jenkins 分支> 之后，我会另推一个 Jenkins 分支），还是从 <Jenkins 分支> 重新发布？我不会自行改用别的 commit。

## feature delivery
（服务端阶段跳过时，标题和条目不写服务端已完成；删掉服务端、发布 pin 的描述，配表条目保留配置 commit，说明客户端阶段仍需使用它。）
<bot_name> 已完成这张功能卡需要的契约、配表、服务端和客户端阶段（未需要的阶段及原因单列；草稿 PR 与合并状态见下）：
- 契约：<PR 链接>（<状态>）；change <名称>；回账／归档草稿 <writeback PR 链接与状态>，待裁决和人工验收 <仍未完成的内容>
- 配表声明：<PR 链接>（<状态>）；配置 commit <短 SHA>，Jenkins 分支 <分支>，pin <版本>
- 服务端：<PR 链接>（<状态>；协议已同步到契约 commit <短 SHA>；设计数据按 <发布方式> 固定）
- 客户端：<PR 链接>（<状态>；协议来源 <同一契约 main SHA>；配置 commit／digest <值>；UI 核对 <证据或跳过原因>）
- 其他：<waivers 或 followup PR 链接；没有就删掉这一行>
- 验证：<跑过的门和测试，以及没跑的和原因>
- 还需合并：请 <owner.person.url> 按顺序合并 <PR 列表>；<bot_name> 不会合并
- 仍待人工完成：<真实尚未合并、未验收或未运行的步骤，包含依据；没有则写无>；本地验证不等于上线
