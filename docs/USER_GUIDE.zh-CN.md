# PixelAgent 使用指南

[English](USER_GUIDE.md) · Windows 预览版 · 更新于 2026 年 11 月 18 日

PixelAgent 可以把角色描述或参考图制作成像素角色。你会依次确认设计方案、在需要时选择概念图，并批准最终像素图；随后可以局部修改、制作表情，或导出图片与游戏素材包。

当前界面为英文。本指南保留按钮的英文原名，方便对照。主要支持的流程是**角色与表情**。物品功能仍可使用，但属于保留流程；动画、场景和地形套件属于实验功能，默认隐藏。

## 1. 选择运行方式

| 下载或安装方式 | 需要准备什么 |
| --- | --- |
| 发布附件 **PixelAgent-Windows-x64.zip** | 将整个 ZIP 解压到可写入的文件夹，再双击 **PixelAgent.exe**。已包含 Python 和绘图引擎。 |
| **Source code** 源码 ZIP 或代码仓库 | 需要安装 Python 和依赖，另行取得匹配的 PixelLiner MCP 引擎，并配置路径。见[从源码运行](#11-从源码运行)。源码 ZIP 不是 Windows 应用安装包。 |

界面会通过 Edge、Chrome 的应用窗口或默认浏览器打开，连接到本机上的小型服务。Windows 应用通常使用 `127.0.0.1:8765`；该端口被占用时，可能改用其他本地端口。

项目和设置保存在应用解压目录之外。只移动可执行文件，不会同时移动项目。独立的 **PixelLiner 编辑器**是可选工具，只有 **Open in PixelLiner** 按钮需要它；它与 PixelAgent 已包含的绘图引擎不同。

## 2. 先免费体验完整流程

1. 打开侧栏的 **Settings**（设置）。
2. 在 **Mode** 中选择 **Test run**。保留 **Look → Soft** 和 **Default height → 160 px**，点击 **Save settings**。
3. 点击 **New project**，输入名称，再点击 **Create project**。
4. 在 **Characters** 中点击 **New character**，选择 **Describe**，填写描述后点击 **Create character**。
5. 到达 **Check the plan** 时，检查设计方案并点击 **Continue**。
6. 如果出现 **Pick a concept**，选择一张缩略图，再点击 **Use this concept**。
7. 到达 **Approve the sprite** 时，点击 **Approve and export**。
8. 在 **Expressions** 中点击 **Make expressions**，选择需要的表情，再点击弹窗中的 **Make expressions**。
9. 在 **Exports** 中，点击 **Game pack (ZIP)** 旁的 **Save**。

**Test run 使用免费替代模型和占位图片。** 它适合熟悉界面、检查保存与导出，不代表 OpenAI 的实际绘图质量，也不需要 API key。切换为 OpenAI 不会自动替换已经生成的测试图片；首次真实生成时，请新建一个角色。

## 3. 启用 OpenAI 生成

OpenAI 模式会将提示词和相关图片发送给 OpenAI，并产生 API 费用。你需要联网、自己的 OpenAI API key，以及有可用额度或计费方式、能够访问所配置模型的 API 账户。

1. 打开 **Settings**，在 **API key** 中粘贴密钥。
2. 点击 **Save key**。它与 **Save settings** 是两个不同的保存操作。
3. 选择 **Mode → OpenAI**。
4. 检查 **Monthly limit** 和 **Limit per item**，再点击 **Save settings**。

密钥保存在 Windows 用户环境变量中，不写入项目文件。**Found** 只表示找到了密钥，不代表已经验证密钥有效、模型可用或账户有额度。分享项目或反馈问题时，不要附带密钥。

## 4. 制作一个角色

点击 **New character**，选择输入方式：

- **Describe**：描述一个角色，包括服装、配色、姿态和身份特征。例如：“森林信使，短绿色披风、红围巾、皮质挎包，神情平静。全身，四分之三视角。”
- **Reference image**：拖入或选择 PNG、JPG、WEBP 图片，可补充“保留红围巾”等要求。清晰的全身图更容易表达设计意图；带场景或身体不完整的图片，可能会先被重绘为干净的完整角色概念图。

**Name** 可选填。**Height** 可选 64、96、128、160 px。重视面部和表情时，建议先用 160 px；尺寸越小，可用于表现五官的像素越少。导出画布可能包含角色周围的透明边距。

### 确认方案：Check the plan

检查 **Name**、**Height**、**Description**、**View** 和 **Must keep**。在 **Must keep** 中，每行写一个必须保留的特征。确认内容符合预期后，点击 **Continue**。

### 选择概念图：Pick a concept

出现概念图选择界面时，点击缩略图预览，再点击 **Use this concept**。如果都不合适，在 **Not right? Say what to change** 中说明修改方向，然后点击 **New concepts**。OpenAI 模式下，重新生成概念图会增加费用。部分只有一个概念结果的流程会直接进入下一步。

### 批准像素图：Approve the sprite

同时用 **1×** 和放大视图检查轮廓、五官、服装，以及要求保留的特征。**Quality check** 是模型给出的评价，最终仍需自己判断。

点击 **Approve and export** 完成导出，角色状态变为 **Ready**。列表中的 **Needs you** 会筛选出等待确认、暂停和出错的项目；**Working** 显示正在处理的项目。

### 选择风格

| 设置 | 效果 |
| --- | --- |
| **Soft** | 默认角色流程。图像模型绘制像素画，PixelAgent 再按像素网格读取并调整配色。需要表情功能时使用此风格。 |
| **Classic** | 更严格的调色板和轮廓流程，由 agent 调用 PixelLiner 绘图工具绘制；处理方式和费用与 Soft 不同。当前表情流程不支持 Classic 角色。 |
| **Colours** | 仅作用于 Soft，可选 **None**、**Light**、**Medium**、**Strong**。None 保留绘制时的颜色；更强的调整会降低色彩强度、提亮阴影。 |

建议在创建角色前选好风格。修改 Settings 不会自动重新生成已有角色。

## 5. 修改角色或查看旧版本

要调整当前角色，在 **Revise** 中填写明确的修改要求，再点击 **Revise**。局部修改的方法是：

1. 点击 **Select an area**。
2. 在角色图片上拖出矩形区域。
3. 描述修改，例如“把这里的腰带扣改为金色”。
4. 点击 **Fix this area**，完成后检查结果并重新批准。

区域操作会把最终修改限制在选定的角色像素内。点击区域标签中的 × 可以清除选择。OpenAI 模式下，修改会产生费用。

如果需要从某个阶段重新处理，点击 **Progress** 中对应阶段旁的圆形 **Redo from here** 按钮，再确认 **Redo**。该阶段及后续阶段会重新执行。设计本身需要变化时，可重做 **Concept**；小范围细节问题可使用局部修改。涉及费用时，确认窗口会显示估算。

有多个绘图版本时，**Versions** 会显示旧版本。点击缩略图查看，点击 **Show current** 回到当前版本。只有与当前方案和概念图兼容的版本才会显示 **Use this version**。恢复版本后会重新执行后续检查，并回到批准步骤；OpenAI 模式下，这些检查可能产生费用。变暗的版本仍可查看，但不能用于不兼容的当前方案。

## 6. 制作表情

表情需要一个已完成的 **Soft** 角色，并且面部可见。完全封闭的头盔或无法定位的面部，可能导致表情无法生成。

1. 打开角色，找到 **Expressions**。
2. 点击 **Make expressions**；已有表情时，按钮显示为 **Make more**。
3. 选择 **Happy**、**Sad**、**Angry**、**Surprised**、**Hurt**、**Blink**。首次使用会默认选中全部六种，请取消不需要的表情。
4. 也可以在 **Your own** 中描述自定义表情，并在 **Its name** 中填写简短名称。
5. 检查选中的表情和费用估算，再点击 **Make expressions**。

制作表情时只编辑面部，角色身体保持不变。未通过检查的表情可能自动重绘一次，因此会增加费用。**Check this face** 表示未通过检查；**Note** 表示提示项。请自行检查肤色、眼睛、眼镜、头发等细节。

每个表情旁的 **PNG** 下载完整角色，**Face** 下载面部补丁；点击预览可查看放大的完整角色。再次生成同名表情，会替换该名称的当前结果。如需保留一份可直接使用的旧图，请先保存。

表情与生成时的角色来源绑定。角色修改或来源变化后，不兼容的旧表情会从当前列表隐藏，并排除在新生成的游戏素材包之外；旧文件仍保留在角色文件夹中。看到提示后，请为当前角色重新制作表情。

## 7. 保存与使用素材

批准角色后，**Exports** 提供以下操作：

| 控件 | 内容 |
| --- | --- |
| **Sprite (PNG) → Save** | 原始像素尺寸的透明角色图片。 |
| **Sprite 4× (PNG) → Save** | 像素清晰的四倍放大预览。 |
| **Metadata (JSON) → Save** | 画布尺寸、调色板、图层名称和轴心信息。 |
| **Game pack (ZIP) → Save** | 包含站立角色和兼容表情的图集、帧信息、Godot 4 SpriteFrames 资源和导入说明；已有的已完成动画也可能包含在内。 |
| **Show in folder** | 打开当前导出文件夹，可找到分层 PNG 和图层项目。 |
| **Open in PixelLiner** | 使用另行安装的 PixelLiner 编辑器打开导出的图片。 |

保存已有导出文件、生成 Game pack 都是本地操作，不调用模型。最终像素图确认步骤中的 **Approve and export** 也免费；更早的确认步骤如果会启动生成，在 OpenAI 模式下仍会计费。

解压游戏素材包后，先阅读其中的 `README.txt`，它会列出该包实际使用的单元格尺寸、轴心、文件名和导入设置。JSON 是 Aseprite/TexturePacker 风格的帧数据，不是 `.aseprite` 工程。游戏中应使用原始尺寸图片或图集，并设置最近邻／Point 采样。图集与配套数据应一起保留。角色或表情变化后，需要重新保存 Game pack；已经下载的 ZIP 不会自动更新。

## 8. 费用与停止规则

标为 **About … (estimate)** 的金额是粗略估算，不是固定报价。角色的 **Spent** 和侧栏月度金额，来自 API 返回用量与本地价格配置的计算。重试、新概念图、修改和重复制作表情都会增加费用，最终账单以 OpenAI 计费页面为准。

- **Monthly limit**：应用内项目共用的月度阈值；全新安装默认为 90 美元。
- **Limit per item**：单个素材的阈值，包含重新绘制和该角色的表情；全新安装默认为 3 美元。
- 已记录消费达到阈值后，应用停止发起新请求；正在进行的请求仍可能完成并使总额超过阈值。这不是服务商端的硬性计费上限。
- API 额度与 ChatGPT 订阅属于不同的账户事项。PixelAgent 不会根据订阅名称判断你实际拥有多少 API 额度。

点击 **Cancel** 停止运行。显示 **Stopping…** 时，应用正在等待当前操作结束；取消不能撤回已发出的请求，也不会退还已产生的费用。已完成的工作会保留。界面提供 **Continue** 时可继续；遇到错误应先解决原因，再点击 **Try again**。表情中断后，请重新打开 **Make more** 或 **Make expressions**，只选择仍然需要制作的表情。

处理期间可以离开当前角色页面。关闭窗口不能可靠地取消运行，正在进行的工作可能继续；希望停止时请先点击 **Cancel**。

## 9. 存储、备份与删除

默认项目位置是用户文档目录下的 **Documents\PixelAgent**。**Settings → Projects folder** 指定包含各项目的父文件夹，修改它不会自动搬迁旧项目。需要迁移时，先关闭应用，将项目文件夹复制到新位置，再在 Settings 中选择新的父文件夹。

每个项目包含素材、处理阶段、历史版本、导出文件，以及 `costs.jsonl` 用量日志。通过 **Show in folder** 定位后，备份整个项目文件夹。手动修改内部文件前也应先备份。

**…** 菜单提供 **Rename…**、**Duplicate**、**Show in folder** 和 **Delete…**。删除后，内容会进入 **Recently Deleted**，可用 **Restore** 恢复。**Delete Now** 和 **Delete All** 会永久删除相应内容。改名、复制或删除项目都不会退还 API 费用，也不会清零月度消费记录。

应用设置和跨项目消费记录位于 `%USERPROFILE%\.pixelagent`。窗口版应用的诊断日志是该目录中的 `app.log`。API key 单独保存在 Windows 用户环境变量中。

## 10. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 下载后没有可执行文件 | 选择发布附件 **PixelAgent-Windows-x64.zip**。GitHub 的源码 ZIP 需要按下文配置环境。 |
| 只生成占位图片 | 检查 **Settings → Mode**。Test run 本来就使用占位图；切换为 OpenAI 后，新建角色进行真实生成。 |
| API key 显示 **Not set** | 粘贴自己的密钥并点击 **Save key**。只保存其他设置不会保存密钥。 |
| **Details** 中出现 API 错误或 HTTP 状态码 | 检查网络、密钥有效性、模型访问权限、API 计费与额度。解决原因后再 **Try again**；反复重试不能解决没有额度或权限的问题。 |
| **Stopped at a spending limit** | 查看 **Spent**、**Monthly limit** 和 **Limit per item**。确实愿意继续花费时再提高阈值，保存设置后重试；已有导出仍可保存。 |
| 缺少价格配置或未知模型错误 | 自定义模型需要有效的价格配置和兼容的 API 支持。重试前恢复随附默认配置，或正确更新项目配置。 |
| 用量缺失／无效，或费用未知错误 | 请求可能已经产生费用。主动发起新任务前先核对服务商账单；未知费用不等于零。 |
| **Expressions need the Soft look** | 需要使用 Soft 流程创建的角色。只切换设置不会把现有 Classic 角色转换为 Soft。 |
| 修改角色后旧表情不见了 | 旧表情与当前角色不匹配；文件仍保留。重新制作适用于当前角色的表情后再导出。 |
| 没有 **Use this version** | 该版本来自较早的方案或概念图，只可查看，不能用于当前状态。 |
| 找不到 PixelLiner | 独立编辑器是可选的，可直接保存 PNG 或用 **Show in folder**。熟悉配置的用户可在项目配置中指定编辑器位置。 |
| 源码运行提示缺少引擎 | 检查引擎目录、`[engine] path` 和其中的 `canvas.py`。Windows 发布包已经包含引擎。 |
| 没有打开应用窗口 | 重新启动，并检查 `%USERPROFILE%\.pixelagent\app.log`。源码运行时，可查看终端打印的本地网址。 |

反馈问题时，请提供应用版本、Windows 版本、Test run/OpenAI 模式、复现步骤和可见错误。附件中如有私人参考图或账户信息，应先移除；不要分享 API key。

## 11. 从源码运行

此方式面向开发者。如果只是使用应用，Windows 发布 ZIP 更简单。

1. 准备 PixelAgent 源码和匹配的 PixelLiner MCP 引擎目录。引擎需要另行取得，源码 ZIP 不包含它。此预览版验证的引擎版本为 `0c24db40e84d797fb2418444ecb96184fb53231b`；请与当前源码中的 `packaging/engine-lock.json` 核对。
2. 安装 **Python 3.11 或更新版本**。Windows 预览版使用 Python 3.13 完成验证。在 PixelAgent 源码目录打开终端，创建环境并安装应用：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[openai]"
   ```

3. 编辑源码目录中的 `config.toml`，将 `[engine] path` 指向引擎目录，即包含 `canvas.py` 和 `server.py` 的文件夹。例如：

   ```toml
   [engine]
   path = "C:/dev/pixelliner-mcp"
   ```

   将示例路径换成实际位置。修改已有的 `[engine]` 段落，不要新增重复段落，并保留其中其他配置。

4. 在源码目录启动：

   ```powershell
   .\.venv\Scripts\python.exe -m pixelagent.app
   ```

5. 先按免费 Test run 流程检查。需要付费生成时，再到应用内配置 API key。

源码中的 `requirements-lock.txt` 记录了已验证的 Windows Python 3.13 环境，供复现构建使用。项目自己的 `config.toml` 可以覆盖默认模型和价格；明确指定的旧模型不会被自动替换。这属于高级设置，桌面应用中的模式、风格和预算设置仍会作用于应用运行。

可阅读[Windows 验证记录与实测费用示例](WINDOWS_VERIFICATION_2026-11-18.md)，了解本预览版已完成的检查及尚未验证的部分。从源码可以安装，不等于已经提供或验证了 macOS、Linux 桌面发布版。
