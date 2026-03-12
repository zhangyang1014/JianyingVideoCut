/**
 * GoldenClip App Router
 * Design: 暗金剪辑台 · 编导美学
 * Routes: / (Dashboard) | /tasks/:id (Review Workbench) | /config (Settings)
 */

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Route, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Dashboard from "./pages/Dashboard";
import ReviewWorkbench from "./pages/ReviewWorkbench";
import ConfigPage from "./pages/ConfigPage";
import NotFound from "./pages/NotFound";
import AppLayout from "./components/AppLayout";

function Router() {
  return (
    <AppLayout>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/tasks/:id" component={ReviewWorkbench} />
        <Route path="/config" component={ConfigPage} />
        <Route component={NotFound} />
      </Switch>
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
