# 数据集、事件与安装操作示意图

以下 SVG 均为项目自行绘制的中性示意图，不是截图，也不是平台实时状态：

- `meta-conversion-warning.svg`：识别转化事件设置提示。
- `meta-standard-event-route.svg`：选择标准事件安装路径。
- `meta-add-event-code.svg`：查看事件代码的操作位置。
- `meta-create-pixel.svg`：创建数据集的操作路径。
- `meta-pixel-created.svg`：创建后核对自己的数据集编号。
- `meta-lead-installation.svg`：Lead 事件安装和状态的核对位置。

所有图示源数据在 `fixtures/guide-figures.json`，通过 `scripts/build-guide-figures.mjs` 可重复生成；测试、构建都会生成完整图集。数据集、网址、事件编号和名称均为示例，不包含真实账号绑定。

图示不能证明当前环境已安装或接收到事件。关闭安装向导不等于安装完成；已有追踪应核对后复用，不能重复粘贴代码。Lead 只对应确认成功的留资。
