import { QueryClient } from "@tanstack/react-query";

/**
 * 前端统一的服务端状态缓存入口。
 * 目前先接管 jobs / memories 这类普通 HTTP 请求；聊天 SSE 仍保留自定义流式逻辑。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 1000,
    },
  },
});
