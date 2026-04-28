/**
 * GoldenClip AppLayout
 * Design: 暗金剪辑台 · 编导美学
 * Left 56px icon sidebar + main content area
 */

import { Link, useLocation } from "wouter";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  LayoutGrid,
  Settings,
  Scissors,
  Zap,
  Key,
  GraduationCap,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  href: string;
}

const NAV_ITEMS: NavItem[] = [
  { icon: LayoutGrid,    label: "任务看板",   href: "/" },
  { icon: GraduationCap, label: "学习",       href: "/learning" },
  { icon: Key,           label: "API 配置",   href: "/config/api" },
  { icon: Settings,      label: "Prompt 管理", href: "/config/prompts" },
];

interface AppLayoutProps {
  children: React.ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  const [location] = useLocation();

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Left Sidebar — 56px icon rail */}
      <aside className="flex flex-col items-center w-14 shrink-0 border-r border-border bg-sidebar py-4 z-50">
        {/* Logo */}
        <div className="mb-6 flex flex-col items-center">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #F0B429 0%, #C8921A 100%)" }}>
            <Scissors className="w-4 h-4 text-black" />
          </div>
        </div>

        {/* Nav Items */}
        <nav className="flex flex-col items-center gap-1 flex-1">
          {NAV_ITEMS.map((item) => {
            const isActive = item.href === "/"
              ? location === "/"
              : location.startsWith(item.href);
            return (
              <Tooltip key={item.href} delayDuration={0}>
                <TooltipTrigger asChild>
                  <Link href={item.href}>
                    <div
                      className={cn(
                        "w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-150",
                        isActive
                          ? "bg-accent text-primary shadow-sm"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                      )}
                    >
                      <item.icon className="w-5 h-5" />
                    </div>
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="right" className="bg-card border-border text-foreground">
                  {item.label}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </nav>

      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Top bar */}
        <header className="h-11 shrink-0 border-b border-border flex items-center px-4 gap-3">
          <div className="flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs font-medium text-muted-foreground tracking-wider uppercase">
              GoldenClip
            </span>
            <span className="text-xs text-muted-foreground/50">·</span>
            <span className="text-xs text-muted-foreground">智能视频工作站 v3.0</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs text-muted-foreground font-mono">API :8000</span>
            </div>
          </div>
        </header>

        {/* Page Content */}
        <div className="flex-1 overflow-hidden">
          {children}
        </div>
      </main>
    </div>
  );
}
