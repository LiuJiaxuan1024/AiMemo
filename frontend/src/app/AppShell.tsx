import type { ReactNode } from "react";
import { Bot, MessageSquareText, NotebookText, Wrench } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { APP_ROUTES, isActiveRoute, type AppRoute } from "./routes";
import { BackgroundTasksDrawer } from "../features/background_tasks/BackgroundTasksDrawer";

const ROUTE_ICONS: Record<AppRoute, ReactNode> = {
  memo: <NotebookText aria-hidden="true" size={17} />,
  chat: <MessageSquareText aria-hidden="true" size={17} />,
  workshop: <Wrench aria-hidden="true" size={17} />,
};

export function AppShell() {
  const location = useLocation();

  return (
    <main className="module-shell">
      <nav className="module-nav" aria-label="AiMemo 模块导航">
        <div className="module-nav-brand">
          <span className="module-nav-mark">
            <Bot aria-hidden="true" size={18} />
          </span>
          <div>
            <strong>AiMemo</strong>
            <small>本地个人知识中心</small>
          </div>
        </div>

        <div className="module-nav-links">
          {APP_ROUTES.map((item) => (
            <NavLink
              className={isActiveRoute(location.pathname, item.key) ? "active" : ""}
              end={item.key === "memo"}
              to={item.path}
              key={item.key}
            >
              {ROUTE_ICONS[item.key]}
              {item.label}
            </NavLink>
          ))}
        </div>
      </nav>

      <div className="module-content">
        <Outlet />
      </div>

      <BackgroundTasksDrawer />
    </main>
  );
}
