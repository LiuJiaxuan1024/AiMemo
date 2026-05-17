import type { Oml2dMethods, Oml2dProperties, Oml2dEvents } from "oh-my-live2d";

export type Live2DInstance = Oml2dProperties & Oml2dMethods & Oml2dEvents;

const DEFAULT_MODEL_URL = "https://model.hacxy.cn/HK416-1-normal/model.json";
const DESKTOP_STAGE_STYLE = {
  bottom: "0px",
  height: 320,
  left: "auto",
  position: "absolute",
  right: "0px",
  top: "auto",
  width: 240,
} as const;
const MOBILE_STAGE_STYLE = {
  bottom: "0px",
  height: 260,
  left: "auto",
  position: "absolute",
  right: "0px",
  top: "auto",
  width: 200,
} as const;

/**
 * 创建 OhMyLive2D 实例。
 * 第三方库的初始化细节集中在这里，业务组件只需要关心“精灵容器”和“状态消息”。
 * 这里使用动态导入，避免 Live2D runtime 进入首屏主包。
 */
export async function createLive2DElf(
  parentElement: HTMLElement,
  isCanceled?: () => boolean,
): Promise<Live2DInstance> {
  // 先确认模型配置可访问。模型 URL 异常时不启动 Pixi/Live2D，避免第三方 runtime 半初始化后抛内部错误。
  const modelResponse = await fetch(DEFAULT_MODEL_URL, { cache: "no-store" });
  if (!modelResponse.ok) {
    throw new Error(`Live2D model request failed: ${modelResponse.status}`);
  }
  if (isCanceled?.()) {
    throw new Error("Live2D initialization canceled");
  }

  const { loadOml2d } = await import("oh-my-live2d");
  if (isCanceled?.()) {
    throw new Error("Live2D initialization canceled");
  }

  return loadOml2d({
    dockedPosition: "right",
    mobileDisplay: true,
    parentElement,
    primaryColor: "#1f6feb",
    sayHello: false,
    transitionTime: 500,
    menus: {
      disable: true,
    },
    statusBar: {
      disable: true,
    },
    // OhMyLive2D 默认会根据 dockedPosition 给舞台加浏览器停靠定位。
    // 我们需要让舞台固定在 React 精灵容器内部，这样拖拽外层容器时模型才会一起移动。
    stageStyle: DESKTOP_STAGE_STYLE,
    tips: {
      idleTips: {
        message: ["我会帮你盯着后台任务。"],
        interval: 12000,
        duration: 4000,
      },
      messageLine: 2,
      style: {
        background: "rgba(255, 255, 255, 0.94)",
        border: "1px solid #d9dde5",
        borderRadius: "8px",
        boxShadow: "0 12px 32px rgba(15, 23, 42, 0.14)",
        color: "#1d2433",
        fontSize: "13px",
        minHeight: "56px",
        width: "220px",
      },
    },
    models: [
      {
        name: "HK416",
        // model.oml2d.com 在部分网络环境会 SSL 握手失败，使用项目作者备用域名更稳定。
        path: DEFAULT_MODEL_URL,
        position: [0, 70],
        scale: 0.075,
        stageStyle: DESKTOP_STAGE_STYLE,
        mobilePosition: [0, 55],
        mobileScale: 0.06,
        mobileStageStyle: MOBILE_STAGE_STYLE,
      },
    ],
  });
}
