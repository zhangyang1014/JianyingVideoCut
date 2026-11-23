import { useState } from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  getPresenterWritingRules,
  type PresenterWritingRules,
} from "@/lib/productions-api";

const kindLabels = {
  skill: "完整 Skill",
  creator_rule: "专属规则",
  shared_rule: "共用规则",
  style_card: "风格卡",
  evidence: "完整证据索引",
};
const sourceClass =
  "mt-3 whitespace-pre-wrap break-words rounded-lg border border-border bg-background p-4 font-sans text-sm leading-7 text-foreground/90 select-text";

export function WritingRuleViewer({
  stage,
  presenterId,
  projectId,
}: {
  stage: "structure" | "polish";
  presenterId?: string;
  projectId?: string;
}) {
  const [data, setData] = useState<PresenterWritingRules>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const [copyError, setCopyError] = useState("");
  const stageLabel = stage === "structure" ? "结构" : "文字";
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await getPresenterWritingRules(stage, presenterId, projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "完整规则暂时无法读取");
    } finally {
      setLoading(false);
    }
  };
  const copy = async (key: string, content: string) => {
    setCopyError("");
    try {
      await navigator.clipboard.writeText(content);
      setCopied(key);
    } catch {
      setCopyError("暂时无法复制，可以直接选择下方原文复制。");
    }
  };
  return (
    <details
      className="mt-5 rounded-lg border border-primary/25 bg-primary/5 p-4"
      onToggle={(e) => {
        if (e.currentTarget.open && !data && !loading && !error) void load();
      }}
    >
      <summary className="cursor-pointer text-sm font-semibold text-primary">
        查看完整 Skill 与规则 · {stageLabel}
      </summary>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        上方文本框编辑数字人的风格设定。这里展示完整规则原文，包含技能声明、正文和参考资料，可逐份展开及复制。
      </p>
      {loading && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          正在读取完整规则…
        </p>
      )}
      {error && (
        <div className="mt-3 space-y-2">
          <p role="alert" className="text-sm text-amber-300">
            {error}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading}
            onClick={() => void load()}
          >
            重新读取完整规则
          </Button>
        </div>
      )}
      {data && (
        <div className="mt-4 space-y-4">
          <p className="text-xs text-muted-foreground">
            已载入 {data.documents.length} 份规则资料 · Skill 原文来自当前服务器
            {data.documents.some((d) => d.kind === "style_card") &&
              (data.style_source === "project_snapshot"
                ? " · 风格卡使用本项目固定版本"
                : " · 风格卡使用当前启用版本")}
          </p>
          {data.documents.map((doc, index) => (
            <details
              key={doc.path}
              open={index === 0}
              className="rounded-lg border border-border bg-card p-3"
            >
              <summary className="cursor-pointer text-sm font-medium">
                {doc.title}
                <span className="ml-2 text-xs text-muted-foreground">
                  {kindLabels[doc.kind]} · {doc.content.length.toLocaleString()}{" "}
                  字符
                </span>
              </summary>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <code className="min-w-0 break-all text-xs text-muted-foreground">
                  {doc.path}
                </code>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void copy(doc.path, doc.content)}
                >
                  <Copy className="mr-1.5 h-3.5 w-3.5" />
                  {copied === doc.path ? "已复制原文" : "复制完整原文"}
                </Button>
              </div>
              <pre className={sourceClass} tabIndex={0}>
                {doc.content}
              </pre>
            </details>
          ))}
          <details className="rounded-lg border border-border bg-card p-3">
            <summary className="cursor-pointer text-sm font-medium">
              核对本阶段实际加载的固定规则
            </summary>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
              与写稿入口使用同一份规则组装逻辑。Skill
              声明和仅用于回查的参考链接不作为执行指令；本次档案、商品事实和草稿作为项目资料另外传入。
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-3"
              onClick={() => void copy("effective", data.effective_prompt)}
            >
              <Copy className="mr-1.5 h-3.5 w-3.5" />
              {copied === "effective" ? "已复制固定规则" : "复制实际加载规则"}
            </Button>
            <pre className={sourceClass} tabIndex={0}>
              {data.effective_prompt}
            </pre>
          </details>
          {copyError && (
            <p role="alert" className="text-sm text-amber-300">
              {copyError}
            </p>
          )}
        </div>
      )}
    </details>
  );
}
