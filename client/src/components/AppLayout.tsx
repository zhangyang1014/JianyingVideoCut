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
  Github,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  href: string;
}

const NAV_ITEMS: NavItem[] = [
  { icon: LayoutGrid, label: "任务看板", href: "/" },
  { icon: Settings, label: "配置管理", href: "/config" },
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

        {/* Bottom: GitHub link */}
        <div className="flex flex-col items-center gap-1">
          <Tooltip delayDuration={0}>
            <TooltipTrigger asChild>
              <a
                href="https://github.com/zhangyang1014/JianyingVideoCut"
                target="_blank"
                rel="noopener noreferrer"
                className="w-10 h-10 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary transition-all duration-150"
              >
                <Github className="w-5 h-5" />
              </a>
            </TooltipTrigger>
            <TooltipContent side="right" className="bg-card border-border text-foreground">
              GitHub 仓库
            </TooltipContent>
          </Tooltip>
        </div>
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
