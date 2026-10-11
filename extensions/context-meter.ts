import { Text, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const WIDGET_KEY = "context-meter";
const BAR_CELLS = 24;
const ROLE_BUCKETS = [
  { key: "system", label: "Instruções", color: "muted" },
  { key: "user", label: "Você", color: "accent" },
  { key: "assistant", label: "Pi", color: "success" },
  { key: "tools", label: "Ferramentas", color: "warning" },
  { key: "other", label: "Outros", color: "borderMuted" },
] as const;

interface TokenBuckets {
  system: number;
  user: number;
  assistant: number;
  tools: number;
  other: number;
}

function safeCount(text: string): number {
  // A transparent chars/4 estimate; image payloads and provider overhead are
  // accounted for in the residual "Outros" category where possible.
  return Math.ceil(text.length / 4);
}

function messageTextTokens(message: any): number {
  if (typeof message?.content === "string") return safeCount(message.content);
  if (!Array.isArray(message?.content)) return 0;

  let characters = 0;
  for (const part of message.content) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && typeof part.text === "string") {
      characters += part.text.length;
    } else if (part.type === "toolCall") {
      characters += String(part.name ?? "").length;
      try {
        characters += JSON.stringify(part.arguments ?? {}).length;
      } catch {
        // Ignore malformed extension-provided tool arguments.
      }
    } else if (part.type === "image") {
      // Images have no reliable local token count. Treat them as a modest
      // estimate and keep the overall estimate explicitly labeled as such.
      characters += 1024;
    }
    // Deliberately do not inspect or display private thinking blocks.
  }
  return Math.ceil(characters / 4);
}

function getBuckets(ctx: any, contextTokens: number | null): TokenBuckets {
  const buckets: TokenBuckets = { system: 0, user: 0, assistant: 0, tools: 0, other: 0 };
  buckets.system = safeCount(String(ctx.getSystemPrompt?.() ?? ""));

  try {
    const messages = ctx.sessionManager.buildSessionProjection().messages;
    for (const message of messages) {
      const estimate = messageTextTokens(message);
      switch (message.role) {
        case "user": buckets.user += estimate; break;
        case "assistant": buckets.assistant += estimate; break;
        case "toolResult": buckets.tools += estimate; break;
        case "system": buckets.system += estimate; break;
        default: buckets.other += estimate; break;
      }
    }
  } catch {
    // Keep a useful system-prompt estimate if a session projection is not ready.
  }

  const estimated = Object.values(buckets).reduce((sum, count) => sum + count, 0);
  if (contextTokens !== null && contextTokens >= 0) {
    if (estimated < contextTokens) {
      buckets.other += contextTokens - estimated;
    } else if (estimated > contextTokens && estimated > 0) {
      // Scale role estimates to the Pi-reported context total. This avoids
      // presenting character-based estimates as exact provider token counts.
      const scale = contextTokens / estimated;
      for (const key of Object.keys(buckets) as (keyof TokenBuckets)[]) {
        buckets[key] *= scale;
      }
    }
  }
  return buckets;
}

function allocateCells(values: number[], count: number): number[] {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0 || count <= 0) return values.map(() => 0);
  const exact = values.map((value) => value / total * count);
  const cells = exact.map(Math.floor);
  let remainder = count - cells.reduce((sum, value) => sum + value, 0);
  const byRemainder = exact.map((value, index) => ({ index, fraction: value - cells[index] }))
    .sort((left, right) => right.fraction - left.fraction);
  for (const item of byRemainder) {
    if (remainder <= 0) break;
    cells[item.index]++;
    remainder--;
  }
  return cells;
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1_000)}k`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

class ModelFooter extends Text {
  constructor(private readonly ctx: any, private readonly theme: any) {
    super("", 0, 0);
  }

  render(width: number): string[] {
    const cwd = this.theme.fg("dim", this.ctx.sessionManager.getCwd());
    const model = this.ctx.model;
    const modelName = model ? `${model.provider}/${model.id}` : "sem modelo";
    const effort = this.ctx.thinkingLevel ?? "off";
    const right = truncateToWidth(
      this.theme.fg("dim", `${modelName} · effort ${effort}`),
      width,
      "…",
    );
    const rightWidth = visibleWidth(right);
    const left = truncateToWidth(cwd, Math.max(0, width - rightWidth - 1), "…");
    const gap = " ".repeat(Math.max(0, width - visibleWidth(left) - rightWidth));
    this.setText(`${left}${gap}${right}`);
    return super.render(width);
  }
}

class ContextMeter extends Text {
  constructor(private readonly ctx: any, private readonly theme: any) {
    super("", 0, 0);
  }

  render(width: number): string[] {
    const usage = this.ctx.getContextUsage?.();
    if (!usage || usage.tokens === null || !Number.isFinite(usage.tokens)) {
      this.setText(this.theme.fg("dim", "CTX indisponível (aguardando uso do modelo)"));
      return super.render(width);
    }

    const tokens = Math.max(0, usage.tokens);
    const capacity = Math.max(0, Number(this.ctx.model?.contextWindow) || usage.contextWindow || 0);
    const percent = capacity > 0 ? Math.min(100, tokens / capacity * 100) : usage.percent;
    const buckets = getBuckets(this.ctx, tokens);
    const values = ROLE_BUCKETS.map(({ key }) => buckets[key]);
    const usedCells = percent === null ? 0 : Math.round(BAR_CELLS * Math.min(100, Math.max(0, percent)) / 100);
    const cells = allocateCells(values, usedCells);
    const remaining = BAR_CELLS - usedCells;
    const graph = ROLE_BUCKETS.map(({ key, color }, index) => {
      const count = cells[index];
      return count ? this.theme.fg(color, "█".repeat(count)) : "";
    }).join("") + (remaining ? this.theme.fg("borderMuted", "█".repeat(remaining)) : "");
    const percentLabel = percent === null ? "?%" : `${Math.round(percent)}%`;
    const capacityLabel = capacity > 0 ? `${formatTokens(tokens)}/${formatTokens(capacity)}` : `${formatTokens(tokens)} tokens`;
    this.setText(`${this.theme.fg("accent", `CTX ${percentLabel}`)} ${graph} ${capacityLabel}`);
    return super.render(width);
  }
}

export default function (pi: ExtensionAPI) {
  let widget: ContextMeter | undefined;
  let footer: ModelFooter | undefined;

  const installWidget = (ctx: any) => {
    ctx.ui.setWidget(WIDGET_KEY, (_tui: any, theme: any) => {
      widget = new ContextMeter(ctx, theme);
      return widget;
    }, { placement: "belowEditor" });

    // Keep the working directory on the left and show the active model and
    // effort on the far right. Context usage is already shown above, so Pi's
    // built-in token/cost row stays hidden.
    ctx.ui.setFooter((_tui: any, theme: any) => {
      footer = new ModelFooter(ctx, theme);
      return footer;
    });
  };

  pi.on("session_start", (_event, ctx) => installWidget(ctx));
  pi.on("turn_end", () => widget?.invalidate());
  pi.on("session_compact", () => widget?.invalidate());
  pi.on("model_select", () => {
    footer?.invalidate();
    widget?.invalidate();
  });
  pi.on("thinking_level_select", () => footer?.invalidate());
  pi.on("session_shutdown", (_event, ctx) => {
    ctx.ui.setWidget(WIDGET_KEY, undefined);
    ctx.ui.setFooter(undefined);
    footer = undefined;
  });
}
