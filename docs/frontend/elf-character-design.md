# AiMemo 原创精灵第一版设计

本文档记录 AiMemo 精灵的原创角色方向。目标不是复刻任何现有角色，而是做一个适合个人知识库产品的陪伴型精灵：温柔、慵懒、可靠，能通过表情和小动作表达后台任务状态。

如果需要生成或扩展精灵图片，请优先查看：

```text
docs/frontend/elf-image-prompts.md
```

该文档保存了可直接复制使用的基准图、表情图、全身设定图和批量扩展提示词模板。

## 角色定位

暂定名：Memo

关键词：

```text
午后感
软萌但不吵闹
轻微困倦
认真帮用户整理记忆
像陪你写笔记的小助理
```

角色不需要一开始就做复杂动作。第一版重点是：

```text
一套主体 Live2D 模型
多个 expression
少量 idle / thinking / working motion
根据 app 状态切换表情
```

## 视觉方向

整体风格：

```text
原创二次元桌面精灵
少女感但不过度幼态
柔和线条
低攻击性
适合长期停留在应用右下角
```

建议外观：

```text
浅粉偏珊瑚色短发或中短发
略微凌乱的发梢，表达慵懒感
暖金或浅琥珀色眼睛
小披肩 / 宽松外套 / 学院风但不使用任何现有作品的校服符号
胸口或发饰带一个小小的书签、便签或星形记忆标记
整体配色以米白、浅蓝、珊瑚粉、暖金为主
```

避免：

```text
不要复制现有角色的发型、服装、光环、徽章、武器、学校标志
不要使用明确可识别的蓝色档案风格符号
不要做成纯工具图标，要保留“陪伴感”
```

## 表情设计

第一版建议做 8 个 expression：

| 状态 | 表情名 | 用途 | 表现 |
| --- | --- | --- | --- |
| idle | `idle_soft` | 默认待机 | 轻微微笑，半睁眼 |
| thinking | `thinking` | AI 正在规划或检索 | 眼睛微眯，嘴角收起，像在认真想 |
| working | `working_focus` | job 正在执行 | 睁眼，轻微专注，嘴巴小小抿住 |
| success | `success_smile` | 任务完成 | 温柔笑，眼睛更亮 |
| error | `error_worried` | 任务失败 | 眉毛下压，嘴巴微张，担心但不夸张 |
| sleepy | `sleepy` | 无事发生 / 夜间氛围 | 困倦眯眼，小哈欠 |
| curious | `curious` | 用户打开工坊或查看图 | 歪头，好奇眼神 |
| memory | `memory_glow` | 记忆写入 / 检索到证据 | 轻笑，眼睛高光更明显 |

## App 状态映射

当前前端已有 job 状态和 elf mood，可先这样映射：

| App 状态 | Expression | Motion |
| --- | --- | --- |
| 无任务 | `idle_soft` | `idle_breathe` |
| 有 pending/running job | `working_focus` | `working_loop` |
| Chat graph 正在思考 | `thinking` | `thinking_loop` |
| 任务完成短提醒 | `success_smile` | `success_once` |
| 任务失败 | `error_worried` | `error_once` |
| 打开精灵工坊 | `curious` | `look_up_once` |
| Memory 写入完成 | `memory_glow` | `memory_once` |

第一版可以没有 motion，只切 expression。这样实现成本最低。

## Live2D 拆层建议

如果要进入 Cubism Editor，建议至少拆这些层：

```text
head_base
face_base
hair_back
hair_side_left
hair_side_right
hair_front_1
hair_front_2
eye_left_white
eye_left_iris
eye_left_highlight
eye_left_lid
eye_right_white
eye_right_iris
eye_right_highlight
eye_right_lid
eyebrow_left
eyebrow_right
mouth_base
mouth_smile
mouth_open
body_base
outer_jacket
collar
arm_left
arm_right
hand_left
hand_right
accessory_bookmark
accessory_star
```

第一版可以简化：

```text
头发：前发 / 侧发 / 后发
眼睛：左右眼、眼皮、眉毛
嘴巴：闭嘴、微笑、张嘴
身体：身体、外套、双手
装饰：记忆书签
```

## 第一版制作路线

### 1. 概念图

先生成或绘制一张半身角色概念图，确认：

```text
脸型
发色
服装轮廓
整体气质
是否适合停留在右下角
```

### 2. 表情表

基于同一角色画 8 个表情头像：

```text
idle
thinking
working
success
error
sleepy
curious
memory
```

### 3. Live2D 拆层图

确认角色后，再画一张可拆层 PSD 风格底图。

### 4. Cubism 绑定

在 Cubism Editor 中绑定：

```text
眨眼
嘴巴开合
头部轻微 XY
呼吸
头发轻微物理
expression 参数
```

### 5. 前端接入

导出 `.model3.json` 后放入：

```text
frontend/public/live2d/memo/
```

前端配置：

```text
Live2D 模型路径：/live2d/memo/model.model3.json
状态映射：elf mood -> expression / motion
```

## 概念图 Prompt 草案

用于生成概念图时的提示词：

```text
Original anime-style desktop assistant character for a personal memory notebook app.
Design a gentle, slightly sleepy young assistant with a warm, reliable presence.
She has soft coral-pink short-to-medium hair with relaxed messy tips, warm amber eyes, a cozy cream and pale blue loose jacket, and a small bookmark-shaped accessory that symbolizes memory.
Half-body front-facing character design, clean silhouette, suitable for Live2D rigging, separated readable hair parts, expressive eyes, simple outfit without copyrighted school emblems or recognizable existing character details.
Mood: soft afternoon, calm, helpful, lightly playful.
Style: polished 2D anime character concept art, clean linework, soft colors, no background, no text, no watermark.
Avoid copying any existing anime/game character, avoid halos, logos, weapons, school badges, or exact franchise costume elements.
```

用于生成表情表时的提示词：

```text
Create an expression sheet for the same original anime-style desktop assistant character.
Eight head-and-shoulder expressions in a clean grid: idle soft smile, thinking, focused working, success smile, worried error, sleepy, curious, memory glow.
Keep identity, hair, outfit, and colors consistent across all expressions.
Designed for Live2D expression reference, clean linework, soft color, no background, no text, no watermark.
```

## 代码接入预留

## 当前 PNG 表情版本

目前已经先接入透明 PNG 版本，用来验证精灵状态切换体验。资源放在：

```text
frontend/public/elf/memo/
```

对应文件：

```text
01_idle_soft.png
02_thinking.png
03_working_focus.png
04_success_smile.png
05_error_worried.png
06_sleepy.png
07_curious.png
08_memory_glow.png
```

渲染入口：

```text
frontend/src/features/elf/memoExpressionRenderer.tsx
```

这一版不做骨骼绑定和动作，只根据 `ElfMood` 切换图片。这样可以先确认：

```text
角色尺寸是否适合右下角停留
透明背景是否自然
不同任务状态是否能被用户一眼感知
气泡和角色是否遮挡主操作区域
```

后续进入 Live2D 时，建议继续沿用当前的状态映射，不要把状态逻辑写进模型渲染层。

后续建议把当前 `live2dAdapter.ts` 里的模型配置抽到独立文件：

```text
frontend/src/features/elf/elfModelConfig.ts
```

并提供：

```ts
export interface ElfModelConfig {
  name: string;
  modelPath: string;
  scale: number;
  position: [number, number];
  expressions: Record<string, string>;
  motions: Record<string, string>;
}
```

这样未来可以支持：

```text
默认远程模型
本地 Memo 原创模型
用户自定义模型
轻量 fallback 模型
```
