# Linear comment templates (zh-CN, concise)

The ledger appends the marker line; do not write it yourself.

## started
👀 FarmBot 已开始处理：正在复现与定位问题，验证结果和草稿 PR 会补充在本 issue。

## blocker
FarmBot 暂停处理。
- 已确认：<一到三条有证据的事实>
- 已尝试：<做过的检查>
- 需要：<具体缺少的信息或需要哪位负责人的决定>

## delivery
FarmBot 已提交修复（草稿 PR，待 review）：
- 问题：<观察到的现象>
- 改动：<改了什么>
- 验证：<跑过的检查，以及没跑的和原因>
- PR：<链接，每个仓一行>

## delivery (no change)
用于结论是「不需要改代码」的交付：已在主干修复、与已合并工单重复、或无法复现。不要用上面的
delivery 模板，它声称提交了草稿 PR。
FarmBot 已确认无需改动：
- 问题：<工单描述的现象>
- 结论：<已在主干修复／与哪个工单重复／无法复现>
- 依据：<提交、PR 或配置对比，给出可核对的链接>
- 验证：<跑过的检查，以及没跑的和原因>
