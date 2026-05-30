import { Brain, Hammer, Volume2 } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { PanelHeader } from "../../shared/ui";

export function WorkshopPage() {
  return (
    <section className="module-page workshop-page">
      <PanelHeader
        subtitle="集中查看后台任务、LangGraph 流程图、长期记忆和后续精灵配置"
        title="精灵工坊"
      />

      <nav className="workshop-subnav" aria-label="精灵工坊子模块">
        <NavLink to="/app/workshop/jobs">
          <Hammer aria-hidden="true" size={16} />
          任务
        </NavLink>
        <NavLink to="/app/workshop/memories">
          <Brain aria-hidden="true" size={16} />
          记忆
        </NavLink>
        <NavLink to="/app/workshop/voice">
          <Volume2 aria-hidden="true" size={16} />
          语音
        </NavLink>
      </nav>

      <Outlet />
    </section>
  );
}
