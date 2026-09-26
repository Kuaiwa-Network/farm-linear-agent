# Linear comment templates (zh-CN, concise)

The ledger appends the marker line; do not write it yourself. Replace `<bot_name>` with `bot_name`
from your launch message, exactly as given: it is the Linear app this instance speaks as.

Replace `<owner.person.url>` with `owner.person.url` from `issue-context`; Linear renders a
profile URL as a mention. When `owner` is null, write 负责人 instead. When the comment needs a
decision from 策划 and `creator` is not null, add `creator.url` after it. Add no other person's URL.

## started
👀 <bot_name> 已开始处理：正在复现与定位问题，验证结果和草稿 PR 会补充在本 issue。

## blocker
<bot_name> 暂停处理。
- 已确认：<一到三条有证据的事实>
- 已尝试：<做过的检查>
- 需要：<具体缺少的信息或需要哪位负责人的决定>
- 请处理：<owner.person.url>

## delivery
<bot_name> 已提交修复（草稿 PR，待 review）：
- 问题：<观察到的现象>
- 改动：<改了什么>
- 验证：<跑过的检查，以及没跑的和原因>
- PR：<链接，每个仓一行>
- 合并：请 <owner.person.url> review 后合并，<bot_name> 不会合并

## delivery (no change)
用于结论是「不需要改代码」的交付：已在主干修复、与已合并工单重复、或无法复现。不要用上面的
delivery 模板，它声称提交了草稿 PR。
<bot_name> 已确认无需改动：
- 问题：<工单描述的现象>
- 结论：<已在主干修复／与哪个工单重复／无法复现>
- 依据：<提交、PR 或配置对比，给出可核对的链接>
- 验证：<跑过的检查，以及没跑的和原因>
