# PixelAgent

[English](README.md) · [完整使用指南](docs/USER_GUIDE.zh-CN.md) · [下载 Windows 预览版](https://github.com/crackthedream/pixelagent/releases/tag/v0.1.0-alpha.1)

PixelAgent 可以从文字或参考图制作游戏像素角色。你可以先审核角色设定和候选图，再框选局部要求修改，为同一角色制作表情，最后导出透明 PNG、图层或游戏资源包。

| 站立 | 微笑 | 眨眼 |
| :---: | :---: | :---: |
| <img src="docs/examples/stand.png" width="120" alt="提灯信使站立图"> | <img src="docs/examples/happy.png" width="120" alt="提灯信使笑脸"> | <img src="docs/examples/blink.png" width="120" alt="提灯信使眨眼"> |

图中的提灯信使来自发布验证时的实际生成结果。三帧共用画布和定位点，可以直接切换表情。[下载示例游戏资源包](https://github.com/crackthedream/pixelagent/releases/download/v0.1.0-alpha.1/Lantern_Courier_game_pack.zip)。

## 第一次使用

1. 打开[预览版下载页](https://github.com/crackthedream/pixelagent/releases/tag/v0.1.0-alpha.1)，在 **Assets** 中下载 **PixelAgent-Windows-x64.zip**。
2. 解压并双击 **PixelAgent.exe**。程序自带 Python 和绘图引擎，无需安装 Docker。应用界面会在本地浏览器窗口中打开。
3. 首次启动默认选择免费的 **Test run**。新建项目和角色，输入设定，按提示确认各个环节，再从 **Game pack** 导出。测试模式用占位图演示整个流程。
4. 要生成正式作品，在 **Settings** 填入自己的 OpenAI API key，选择 **OpenAI** 模式，检查预算并保存。此模式会将任务所需的文字和图片发送至 OpenAI。

## 绘图与导出

**Soft** 模式由图像模型绘制像素风角色，程序识别像素网格、调整颜色并生成表情。**Classic** 模式由 GPT-6.2 Sol 观察参考图，通过 PixelLiner MCP 引擎逐步绘制。游戏资源包里有 PNG 图集、Aseprite 风格的帧数据以及 Godot SpriteFrames 资源。

该版通过了 **245 项离线回归测试**。提灯信使的整轮真实验证覆盖两种绘图方式、表情和评审，共 40 次 API 调用，按返回用量估算 **0.57905 美元**。生成不同角色的费用会随尺寸、修改次数和重试变化。

[使用指南](docs/USER_GUIDE.zh-CN.md) · [验证与费用明细](docs/WINDOWS_VERIFICATION_2026-11-18.md) · [反馈与贡献](CONTRIBUTING.md) · [安全问题](SECURITY.md)

PixelAgent 自有应用源码使用 [MIT 许可证](LICENSE)。随包绘图引擎和第三方组件有[单独的分发条款](DISTRIBUTION.md)。
