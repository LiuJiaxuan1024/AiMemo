export type AppRoute = "memo" | "chat" | "workshop";

export interface AppRouteItem {
  key: AppRoute;
  label: string;
  path: string;
}

export const APP_ROUTES: AppRouteItem[] = [
  { key: "memo", label: "Ai 记", path: "/app/memo" },
  { key: "chat", label: "对话", path: "/app/chat" },
  { key: "workshop", label: "工坊", path: "/app/workshop" },
];

export function routeFromPath(pathname: string): AppRoute {
  if (pathname.startsWith("/app/chat")) {
    return "chat";
  }
  if (pathname.startsWith("/app/workshop")) {
    return "workshop";
  }
  return "memo";
}

export function pathForRoute(route: AppRoute): string {
  return APP_ROUTES.find((item) => item.key === route)?.path ?? "/app/memo";
}

export function isActiveRoute(pathname: string, route: AppRoute): boolean {
  if (route === "memo") {
    return pathname === "/app" || pathname === "/app/" || pathname.startsWith("/app/memo");
  }
  return pathname.startsWith(pathForRoute(route));
}
