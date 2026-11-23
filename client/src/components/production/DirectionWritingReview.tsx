import type { ProductionWritingReview } from "@/lib/productions-api";

const roleLabels: Record<ProductionWritingReview["beats"][number]["role"], string> = {
  opening: "开场",
  first_answer: "首答",
  demonstration: "演示",
  explanation: "解释",
  objection: "回应顾虑",
  action: "行动引导",
};

export function DirectionWritingReview({ review, intentLabel }: {
  review: ProductionWritingReview;
  intentLabel?: string;
}) {
  const rules = [
    { name: "结构规则", hash: review.structure_prompt_sha256, model: review.structure_model },
    { name: "语气规则", hash: review.polish_prompt_sha256, model: review.polish_model },
  ];

  return (
    <details className="mt-4 rounded-md border border-border p-3 text-sm">
      <summary className="cursor-pointer font-medium">
        口播提纲与审查
        {review.issues.length > 0 && (
          <span className="ml-2 text-xs text-amber-300">{review.issues.length} 项待核对</span>
        )}
      </summary>
      {intentLabel && <p className="mt-3 text-xs text-muted-foreground">写稿目标 · {intentLabel}</p>}
      <ol className="mt-3 list-decimal space-y-3 pl-5">
        {review.beats.map((beat, index) => (
          <li key={index}>
            <p className="font-medium">
              {beat.purpose}<span className="ml-2 text-xs font-normal text-muted-foreground">{roleLabels[beat.role]}</span>
            </p>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-xs leading-relaxed">
              <dt className="text-muted-foreground">观众问题</dt>
              <dd>{beat.audience_question}</dd>
              <dt className="text-muted-foreground">回答</dt>
              <dd>{beat.answer}</dd>
            </dl>
            {beat.shot_ids.length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">镜头 {beat.shot_ids.join("、")}</p>
            )}
          </li>
        ))}
      </ol>
      {review.issues.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-border pt-3 text-xs leading-relaxed text-amber-300">
          {review.issues.map((issue, index) => (
            <li key={`${issue.code}:${index}`}>
              {issue.message}{issue.shot_ids.length > 0 && ` · 镜头 ${issue.shot_ids.join("、")}`}
            </li>
          ))}
        </ul>
      )}
      <dl className="mt-3 space-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
        {rules.map(({ name, hash, model }) => (
          <div key={name}>
            <dt>{name}{model && ` · ${model}`}</dt>
            <dd className="break-all"><code>{hash}</code></dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
