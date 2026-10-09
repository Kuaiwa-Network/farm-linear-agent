# <bot_name> UI 近似预览：第 <N> 轮

草稿 PR：<verified PR URL>；HEAD：<full SHA>。
以下是根据源 XML / 原图生成的**近似预览**，不是 FairyGUI 编辑器或 Unity 截图。

| 实际稿名 / 状态 | 原始上传稿 | 近似预览 | 测量差异 / 已知缺口 |
|---|---|---|---|
| <actual mockup name; selected state> | <unsigned original asset> | <unsigned preview asset> | <measured deviations and explicit gaps> |

客户端目标：`Farm-Client` / `<validated same-issue branch>`；本次安装范围：
`Assets/GameRes/FairyRes/<actually changed package>`（逐一列出）。
<state whether Client target is already pinned; use its recorded branch/baseline if so>

<owner.person.url> 请在本会话回复“确认”或 `approved`，或指出需要修改的地方。
确认本轮即同意继续导出，并在上述分支的专属客户端 worktree 中安装和检查。
只想确认视觉时，请明确回复“仅确认视觉，暂不导出”。
本轮身份：<round/source/art/preview digest>。输入或 PR HEAD 变化需重新出图确认。
评论本身不会自动继续工作；请在本会话回复或 @<bot_name> 明确继续。
导出先写入私有临时目录并核验，再交给控制器安装到本卡的客户端 worktree。
尚未选定客户端基线时，由控制器在首次交接时选定；不会写入客户端 main 工作目录。
确认表示允许继续，不表示导出、客户端检查、Unity 加载或实际界面验收已经通过。
缺少已配置的导出工具、许可或主机能力时会保留本轮并报告具体缺口。
