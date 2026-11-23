/**
 * GoldenClip App Router
 * Design: 暗金剪辑台 · 编导美学
 * Routes: / (Dashboard) | /tasks/:id (Review Workbench) | /config/api (AI 配置)
 */

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Route, Switch, Redirect } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Dashboard from "./pages/Dashboard";
import ReviewWorkbench from "./pages/ReviewWorkbench";
import ApiConfigPage from "./pages/ApiConfigPage";
import LearningPage from "./pages/LearningPage";
import NotFound from "./pages/NotFound";
import AppLayout from "./components/AppLayout";
import { lazy, Suspense } from "react";

const ProductionWorkbench = lazy(() => import("./pages/ProductionWorkbench"));
const PresentersPage = lazy(() => import("./pages/PresentersPage"));

function Router() {
  return (
    <AppLayout>
      <Suspense fallback={<div className="p-8 text-muted-foreground">正在打开工作台…</div>}>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/tasks/:id" component={ReviewWorkbench} />
        <Route path="/productions/:id" component={ProductionWorkbench} />
        <Route path="/productions" component={ProductionWorkbench} />
        <Route path="/presenters" component={PresentersPage} />
        <Route path="/learning" component={LearningPage} />
        <Route path="/config/api" component={ApiConfigPage} />
        <Route path="/config/prompts">
          <Redirect to="/config/api" />
        </Route>
        {/* 旧配置入口统一重定向到 AI 配置页 */}
        <Route path="/config">
          <Redirect to="/config/api" />
        </Route>
        <Route component={NotFound} />
      </Switch>
      </Suspense>
    </AppLayout>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="dark">
        <TooltipProvider>
          <Toaster
            theme="dark"
            toastOptions={{
              style: {
                background: "#1A1D24",
                border: "1px solid rgba(240,180,41,0.2)",
                color: "#F1F5F9",
              },
            }}
          />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
