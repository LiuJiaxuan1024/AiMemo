# 精灵图片生成提示词模板

本文档记录 AiMemo 原创精灵 Memo 的图片生成提示词模板。目标是让贡献者可以基于同一套角色设定继续扩展表情、动作和未来 Live2D 素材。

使用原则：

```text
保持原创角色身份一致。
优先生成正面、半身、透明背景 PNG。
新增表情不要覆盖已有文件，先放入候选目录检查。
不要使用任何现有动漫、游戏、学校、组织或品牌的可识别元素。
```

## 角色核心设定

所有提示词都应该保留这段身份描述：

```text
Keep the same original character identity:
coral-pink short-to-medium hair,
warm amber eyes,
cozy cream and pale blue loose jacket,
a small bookmark-shaped memory accessory,
gentle slightly sleepy personality,
warm personal memory assistant theme.
```

中文理解：

```text
珊瑚粉中短发
暖琥珀色眼睛
米白和浅蓝色宽松外套
书签形记忆配饰
温柔、轻微慵懒、可靠
个人记忆助理主题
```

## 基准图模板

用途：

```text
生成新的角色基准图。
用于后续所有表情图的身份参考。
适合未来进入 Live2D 拆层。
```

建议文件名：

```text
frontend/public/elf/memo/00_reference_v2_candidate.png
```

提示词：

```text
Create an original Live2D-ready front-facing half-body anime desktop assistant character.

Keep the same original character identity:
coral-pink short-to-medium hair,
warm amber eyes,
cozy cream and pale blue loose jacket,
a small bookmark-shaped memory accessory,
gentle slightly sleepy personality,
warm personal memory assistant theme.

The character should face forward with a symmetrical, clean posture.
Both hands should be simple and relaxed, either naturally down or gently holding a small notebook at chest level.
Keep the face clearly visible and unobstructed.
Make hair parts, eyes, eyebrows, mouth, jacket, sleeves, and accessories visually separable for future Live2D rigging.

Expression: neutral gentle idle smile.
Style: polished 2D anime character concept art, clean linework, soft colors.
Background: transparent PNG if supported. If transparent background is not supported, use a plain pure white background for later background removal.

Important:
Do not change the character identity.
Do not create a new hairstyle, new outfit, or new color palette.
Do not copy any existing anime/game character.
No halo, no weapon, no school badge, no copyrighted emblem, no recognizable franchise costume.
No text, no watermark, no logo.
Avoid complex hand gestures near the face.
Avoid tilted face, extreme perspective, crossed arms, complicated props.
```

## 表情图通用模板

用途：

```text
基于基准图扩展单张表情。
当前桌面精灵和 Web 精灵都使用这类 PNG 表情。
```

建议尺寸：

```text
1024x1536
```

建议文件名：

```text
frontend/public/elf/memo/NN_expression_name.png
desktop/public/elf/memo/NN_expression_name.png
```

模板：

```text
Use the provided reference image as the identity reference.

Keep the same original character identity:
coral-pink short-to-medium hair,
warm amber eyes,
cozy cream and pale blue loose jacket,
a small bookmark-shaped memory accessory,
gentle slightly sleepy personality,
warm personal memory assistant theme.

Create one front-facing half-body transparent PNG expression image for this character.

Expression name: {{expression_name}}
Emotion: {{emotion_description}}
Pose / action: {{pose_or_action}}

Keep the same hairstyle, outfit, color palette, face shape, body proportion, and bookmark accessory.
The face must be clear and readable.
The silhouette should remain suitable for a small desktop assistant.
Use clean linework, soft colors, polished 2D anime character art.
Background: transparent PNG if supported. If transparent background is not supported, use a plain pure white background for later background removal.

Important:
Do not change the character identity.
Do not create a new outfit, hairstyle, or color palette.
Do not copy any existing anime/game character.
No halo, no weapon, no school badge, no copyrighted emblem, no recognizable franchise costume.
No text, no watermark, no logo.
Avoid heavy props, complex background, extreme perspective, cropped face, hidden eyes.
```

字段说明：

```text
{{expression_name}}
  表情的英文标识，建议和文件名保持一致，例如 shy_blush。

{{emotion_description}}
  情绪描述，例如 shy and softly embarrassed, cheeks blushing。

{{pose_or_action}}
  姿态或小动作，例如 slight head tilt, one hand near chest。
```

## 已接入表情清单

当前代码已接入这些表情名，生成新图时优先沿用这些命名：

| 文件名 | emoji / expression | 用途 |
| --- | --- | --- |
| `01_idle_soft.png` | `idle_soft` | 默认温柔待机 |
| `02_thinking.png` | `thinking` | 思考、规划 |
| `03_working_focus.png` | `working_focus` | 专注处理任务 |
| `04_success_smile.png` | `success_smile` | 成功、完成 |
| `05_error_worried.png` | `error_worried` | 担心、失败 |
| `06_sleepy.png` | `sleepy` | 困倦、低打扰 |
| `07_curious.png` | `curious` | 好奇、查看 |
| `08_memory_glow.png` | `memory_glow` | 记忆、回想 |
| `09_shy_blush.png` | `shy_blush` | 害羞 |
| `10_angry_pout.png` | `angry_pout` | 生气、鼓脸 |
| `11_surprised.png` | `surprised` | 惊讶 |
| `12_sad_teary.png` | `sad_teary` | 难过、含泪 |
| `13_wronged_pout.png` | `wronged_pout` | 委屈 |
| `14_confused.png` | `confused` | 困惑 |
| `15_proud.png` | `proud` | 骄傲、得意 |
| `16_playful_wink.png` | `playful_wink` | 调皮眨眼 |
| `17_serious.png` | `serious` | 认真 |
| `18_relaxed.png` | `relaxed` | 放松 |
| `19_encouraging.png` | `encouraging` | 鼓励 |
| `20_speechless.png` | `speechless` | 无语、被噎住 |

## 常用扩展表情 Prompt

### 傲娇

```text
Expression name: tsundere_pout
Emotion: cute tsundere embarrassment, pretending not to care but clearly flustered, small pout, slightly blushing cheeks.
Pose / action: slight head turn to the side, arms lightly crossed or one hand near the chest, eyes looking sideways.
```

### 托腮思考

```text
Expression name: chin_rest_thinking
Emotion: thoughtful and curious, quietly analyzing something, soft focused eyes.
Pose / action: one hand gently supporting the chin, slight head tilt, relaxed shoulders.
```

### 坏笑

```text
Expression name: mischievous_smirk
Emotion: playful little mischief, clever smile, eyes slightly narrowed but still friendly.
Pose / action: slight lean forward, one finger raised near the face as if having a small idea.
```

### 求夸

```text
Expression name: praise_me
Emotion: proud but cute, expecting praise, sparkling eyes, soft happy smile.
Pose / action: hands lightly held near chest, posture slightly lifted.
```

### 心虚

```text
Expression name: guilty_smile
Emotion: guilty and awkward, forced small smile, nervous eyes, tiny sweat drop feeling without exaggerated symbols.
Pose / action: slight shoulder shrink, one hand touching hair or sleeve.
```

### 撒娇

```text
Expression name: soft_pleading
Emotion: gentle pleading, slightly watery eyes, soft shy smile, cute but not overly childish.
Pose / action: hands close to chest, slight forward lean.
```

### 灵感来了

```text
Expression name: idea_spark
Emotion: sudden inspiration, bright eyes, delighted small smile.
Pose / action: one finger raised, slight upward gaze, energetic but still gentle.
```

### 害怕

```text
Expression name: scared_small
Emotion: small frightened reaction, worried eyes, mouth slightly open, still cute and restrained.
Pose / action: shoulders slightly raised, hands close to body.
```

## 全身设定图模板

用途：

```text
确定完整服装和未来桌面精灵站姿。
当前应用主要用半身图，全身图作为长期设定参考。
```

建议文件名：

```text
live2d/memo-concept/00_reference_full_body.png
```

提示词：

```text
Use the provided image as the identity reference.

Keep the same original character identity:
coral-pink short-to-medium hair,
warm amber eyes,
cozy cream and pale blue loose jacket,
a small bookmark-shaped memory accessory,
gentle slightly sleepy personality,
warm personal memory assistant theme.

Create a full-body front-facing character design sheet for Memo.
Show the complete outfit from head to shoes.
Keep the same upper-body design, jacket, color palette, hairstyle, face, and bookmark accessory.
Design the lower body as original and simple: comfortable pale-blue shorts or a soft cream skirt, cozy socks, simple practical shoes, matching the warm personal memory assistant theme.
Pose should be relaxed and symmetrical, standing naturally, arms simple and visible.
Clean silhouette, readable clothing layers, suitable as a future Live2D full-body reference.

Style: polished 2D anime character concept art, clean linework, soft colors, simple neutral background.

Important:
Do not change the character identity.
Do not copy any existing anime/game character.
No halo, no weapon, no school badge, no copyrighted emblem, no recognizable franchise costume.
No text, no watermark.
Avoid extreme perspective, dynamic pose, crossed legs, hidden feet, complex props.
```

## 批量生成建议

批量生成时建议使用候选目录，不要直接覆盖正式资源：

```text
live2d/memo-concept/expression_inventory_raw/
```

确认透明背景和角色一致性后，再复制到：

```text
frontend/public/elf/memo/
desktop/public/elf/memo/
```

提交前检查：

```text
1. 文件名是否和 emoji / expression 一致。
2. 背景是否已经移除。
3. 角色发色、眼睛、服装是否一致。
4. 有没有水印、文字、logo、版权角色特征。
5. 桌面端和前端 public 目录是否同步。
6. 后端 generate_elf_bubble_answer 的 emoji 白名单是否同步。
7. frontend/src/features/elf/memoExpressionRenderer.tsx 是否同步。
```

## 提示词调参经验

如果角色变得不像：

```text
加强 Keep the same original character identity。
明确 Do not create a new hairstyle, new outfit, or new color palette。
使用上一张最满意的基准图作为 reference。
```

如果表情太平：

```text
把 Emotion 写得更具体。
增加 eyes, eyebrows, mouth, cheeks 的细节。
允许轻微 pose / action，但不要破坏半身构图。
```

如果背景不好抠：

```text
优先要求 transparent PNG。
如果接口不稳定，就要求 plain pure white background。
后续用专业抠图工具处理，再放入正式 public 目录。
```

如果边缘发虚：

```text
使用 clean linework。
避免 glow-heavy, blurry, painterly。
抠图后检查头发封闭区域是否也被移除。
```
