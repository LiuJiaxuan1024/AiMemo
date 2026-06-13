import { Suspense, lazy } from "react";
import type { ReactNode } from "react";
import { Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";

import { AppShell } from "./app/AppShell";

const MemoPage = lazy(() =>
  import("./pages/memo/MemoPage").then((module) => ({ default: module.MemoPage })),
);
const ChatPage = lazy(() =>
  import("./pages/chat/ChatPage").then((module) => ({ default: module.ChatPage })),
);
const KnowledgePage = lazy(() =>
  import("./pages/knowledge/KnowledgePage").then((module) => ({ default: module.KnowledgePage })),
);
const WorkshopPage = lazy(() =>
  import("./pages/workshop/WorkshopPage").then((module) => ({ default: module.WorkshopPage })),
);
const WorkshopJobsPage = lazy(() =>
  import("./pages/workshop/WorkshopJobsPage").then((module) => ({
    default: module.WorkshopJobsPage,
  })),
);
const WorkshopElfPage = lazy(() =>
  import("./pages/workshop/WorkshopElfPage").then((module) => ({
    default: module.WorkshopElfPage,
  })),
);
const WorkshopMemoriesPage = lazy(() =>
  import("./pages/workshop/WorkshopMemoriesPage").then((module) => ({
    default: module.WorkshopMemoriesPage,
  })),
);
const WorkshopVoicePage = lazy(() =>
  import("./pages/workshop/WorkshopVoicePage").then((module) => ({
    default: module.WorkshopVoicePage,
  })),
);
const WorkshopCloudSyncPage = lazy(() =>
  import("./pages/workshop/WorkshopCloudSyncPage").then((module) => ({
    default: module.WorkshopCloudSyncPage,
  })),
);

function withPageSuspense(element: ReactNode) {
  return <Suspense fallback={<div className="module-loading">正在加载模块...</div>}>{element}</Suspense>;
}

const router = createBrowserRouter([
  {
    path: "/app",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/app/memo" replace /> },
      { path: "memo", element: withPageSuspense(<MemoPage />) },
      { path: "chat", element: withPageSuspense(<ChatPage />) },
      { path: "knowledge", element: withPageSuspense(<KnowledgePage />) },
      {
        path: "workshop",
        element: withPageSuspense(<WorkshopPage />),
        children: [
          { index: true, element: <Navigate to="/app/workshop/jobs" replace /> },
          { path: "elf", element: withPageSuspense(<WorkshopElfPage />) },
          { path: "jobs", element: withPageSuspense(<WorkshopJobsPage />) },
          { path: "memories", element: withPageSuspense(<WorkshopMemoriesPage />) },
          { path: "voice", element: withPageSuspense(<WorkshopVoicePage />) },
          { path: "sync", element: withPageSuspense(<WorkshopCloudSyncPage />) },
        ],
      },
      { path: "*", element: <Navigate to="/app/memo" replace /> },
    ],
  },
  { path: "*", element: <Navigate to="/app/memo" replace /> },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
