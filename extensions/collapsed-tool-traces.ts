import { stripVTControlCharacters } from "node:util";
import { Text } from "@earendil-works/pi-tui";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PREVIEW_LIMIT = 96;

function safeText(value: unknown): string {
  // Tool output is untrusted: do not let escape sequences control the terminal.
  return stripVTControlCharacters(String(value ?? "")).replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
}

function stringify(value: unknown): string {
  try {
    return safeText(JSON.stringify(value, null, 2) ?? String(value));
  } catch {
    return safeText(value);
  }
}

function oneLine(value: unknown, limit = PREVIEW_LIMIT): string {
  const chars = [...safeText(value).replace(/\s+/g, " ").trim()];
  return chars.length > limit ? `${chars.slice(0, limit - 1).join("")}…` : chars.join("");
}

function callSummary(args: Record<string, unknown>): string {
  const value = [args.command, args.file_path, args.path, args.pattern, args.query,
    args.url, args.uri, args.name, args.script, args.text]
    .find((item) => typeof item === "string" && item.trim());
  return value === undefined ? "" : oneLine(value);
}

function toolIcon(name: string): string {
  const leaf = name.toLowerCase().split(/[./:]/).pop() ?? name;
  if (/bash|shell|exec|command|powershell/.test(leaf)) return "❯";
  if (/read|open/.test(leaf)) return "≡";
  if (/write|edit|patch/.test(leaf)) return "✎";
  if (/search|grep|find|query/.test(leaf)) return "⌕";
  if (/web|fetch|browse|http/.test(leaf)) return "↗";
  return "◇";
}

function resultText(content: Array<{ type: string; text?: string; mimeType?: string }>): string {
  return content.map((item) => {
    if (item.type === "text") return safeText(item.text);
    if (item.type === "image") return `[imagem${item.mimeType ? `: ${safeText(item.mimeType)}` : ""}]`;
    return `[${safeText(item.type)}]`;
  }).filter(Boolean).join("\n");
}

function durationLabel(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) return "";
  return ms < 1000 ? `${Math.max(0, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export default function (pi: ExtensionAPI) {
  // Keep Pi's shell/background and mouse handling. Execution and stored results
  // remain untouched; all decoration belongs exclusively to the presentation.
  pi.registerToolRenderer((toolName, _next) => ({
    renderCall(args, theme, context) {
      const text = context.lastComponent instanceof Text ? context.lastComponent : new Text("", 0, 0);
      const params = (args && typeof args === "object" ? args : {}) as Record<string, unknown>;
      const running = context.executionStarted && context.isPartial;
      // Running tool calls stay visually neutral. Only a finished result gets
      // a green success marker or a red error marker.
      text.setText((() => {
        const marker = context.expanded ? "▾" : "▸";
        const title = theme.fg("muted", theme.bold(`${toolIcon(toolName)} ${safeText(toolName)}`));
        const hint = running ? "" : theme.fg("dim", context.expanded ? "recolher" : "clique para inspecionar");
        const summary = callSummary(params);
        // Use foreground-only neutral styling for the tool card; never apply
        // success/error background colors to the call or its result.
        const header = `${theme.fg("borderMuted", `╭─ ${marker}`)} ${title}${hint ? `  ${hint}` : ""}`;
        if (!context.expanded) return `${header}${summary ? `\n${theme.fg("borderMuted", "│")}  ${theme.fg("muted", summary)}` : ""}`;
        const rail = theme.fg("borderMuted", "│");
        const body = stringify(params).split("\n").map((line) => `${rail}  ${theme.fg("muted", line)}`).join("\n");
        return `${header}\n${rail}  ${theme.fg("accent", "ARGUMENTOS")}\n${body}`;
      })());
      return text;
    },

    renderResult(result, options, theme, context) {
      const text = context.lastComponent instanceof Text ? context.lastComponent : new Text("", 0, 0);
      const output = resultText(result.content as Array<{ type: string; text?: string; mimeType?: string }>);
      const lines = output ? output.split("\n").length : 0;
      const duration = durationLabel(context.durationMs);
      const metadata = [lines ? `${lines} linha${lines === 1 ? "" : "s"}` : "sem saída", duration].filter(Boolean).join(" · ");
      const footer = options.isPartial
        ? theme.fg("dim", metadata)
        : `${theme.fg(context.isError ? "error" : "success", `${context.isError ? "✗" : "✓"} ${context.isError ? "falha" : "concluído"}`)}  ${theme.fg("dim", metadata)}`;
      if (options.expanded) {
        const rail = theme.fg("borderMuted", "│");
        const body = (output || "(sem saída)").split("\n").map((line) => `${rail}  ${theme.fg("muted", line)}`).join("\n");
        const details = result.details === undefined ? "" : `\n${rail}  ${theme.fg("accent", "DETALHES")}\n${stringify(result.details).split("\n").map((line) => `${rail}  ${theme.fg("muted", line)}`).join("\n")}`;
        text.setText(`${rail}  ${theme.fg("accent", "RESULTADO")}\n${body}${details}\n${footer}`);
      } else {
        text.setText(`${footer}${output ? `\n   ${theme.fg("muted", oneLine(output))}` : ""}`);
      }
      return text;
    },
  }));
}
