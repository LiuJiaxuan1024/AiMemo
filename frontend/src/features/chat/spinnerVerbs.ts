// 灵感来自 Claude Code 的 spinner 动词词典：
// submodules/Claude-Code/src/constants/spinnerVerbs.ts
// 这里改为中文风格的"思考动词"，保持节奏感、轻幽默。
export const SPINNER_VERBS: readonly string[] = [
  "思考中",
  "梳理中",
  "推演中",
  "回想中",
  "斟酌中",
  "权衡中",
  "查阅中",
  "比对中",
  "拼接中",
  "盘点中",
  "搜寻中",
  "翻找中",
  "演算中",
  "归纳中",
  "提炼中",
  "校对中",
  "复盘中",
  "拆解中",
  "归档中",
  "酝酿中",
  "勾勒中",
  "推敲中",
  "凝神中",
  "整队中",
  "校准中",
  "对焦中",
  "串联中",
  "审视中",
  "孵化中",
  "解构中",
  "演绎中",
  "构想中",
  "梳辫子",
  "翻笔记",
  "拨算盘",
  "理思路",
  "打草稿",
  "翻档案",
  "找线索",
];

export function pickVerb(seed: number): string {
  if (SPINNER_VERBS.length === 0) {
    return "思考中";
  }
  const index = ((seed % SPINNER_VERBS.length) + SPINNER_VERBS.length) % SPINNER_VERBS.length;
  return SPINNER_VERBS[index] ?? "思考中";
}
