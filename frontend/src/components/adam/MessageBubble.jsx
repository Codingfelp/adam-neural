import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, Download, Paperclip } from "lucide-react";
import { ENGINES } from "@/lib/api";
import { FileCard, GeneratedImage, useBlobUrl } from "@/components/adam/FileCard";

const EXT = { python: "py", javascript: "js", js: "js", typescript: "ts", html: "html", css: "css", json: "json", bash: "sh", sql: "sql", jsx: "jsx", java: "java" };

function CodeBlock({ lang, code }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  const save = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([code], { type: "text/plain" }));
    a.download = `adam.${EXT[lang] || "txt"}`; a.click();
  };
  return (
    <div className="rounded-xl overflow-hidden border border-white/10 bg-[#0c0d14] my-3">
      <div className="flex items-center justify-between px-4 py-2 bg-white/[0.04] border-b border-white/5 text-xs text-zinc-400 font-mono">
        <span>{lang || "código"}</span>
        <div className="flex gap-3">
          <button onClick={copy} data-testid="code-copy-button" className="flex items-center gap-1 hover:text-white">
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}{copied ? "Copiado" : "Copiar"}
          </button>
          <button onClick={save} data-testid="code-download-button" className="flex items-center gap-1 hover:text-white"><Download className="w-3.5 h-3.5" />Baixar</button>
        </div>
      </div>
      <pre className="p-4 overflow-x-auto scroll-thin text-[13px] leading-relaxed text-zinc-200"><code>{code}</code></pre>
    </div>
  );
}

const mdComponents = {
  code({ inline, className, children }) {
    const lang = /language-(\w+)/.exec(className || "")?.[1];
    const text = String(children).replace(/\n$/, "");
    if (inline || (!lang && !text.includes("\n"))) return <code>{children}</code>;
    return <CodeBlock lang={lang} code={text} />;
  },
  pre: ({ children }) => <>{children}</>,
};

function Attachment({ f }) {
  const url = useBlobUrl(f.mime?.startsWith("image/") ? f.id : null);
  if (f.mime?.startsWith("image/")) return url ? <img src={url} alt={f.name} className="h-28 rounded-lg border border-white/10 object-cover" /> : <div className="h-28 w-28 rounded-lg bg-white/5 animate-pulse" />;
  return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 text-xs"><Paperclip className="w-3 h-3" />{f.name}</span>;
}

export default function MessageBubble({ m, streaming }) {
  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end gap-2 rise" data-testid="message-user">
        {m.attachments?.length > 0 && <div className="flex flex-wrap gap-2 justify-end">{m.attachments.map((f) => <Attachment key={f.id} f={f} />)}</div>}
        <div className="max-w-[85%] px-4 py-2.5 rounded-2xl rounded-br-md bg-[#181A26] border border-white/5 text-[0.95rem] whitespace-pre-wrap break-words">{m.content}</div>
      </div>
    );
  }
  const eng = ENGINES[m.engine] || ENGINES.pro;
  return (
    <div className="flex gap-3 sm:gap-4 rise" data-testid="message-assistant">
      <div className="w-7 h-7 shrink-0 rounded-lg bg-gradient-to-br from-[#E07A5F] to-[#3A86FF] flex items-center justify-center font-display text-xs font-bold">A</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium">Adam</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${eng.cls}`}>{eng.label}</span>
        </div>
        {m.tool && streaming && <p className="text-xs font-mono text-[#F4A261] animate-pulse mb-1" data-testid="tool-status">{m.tool}…</p>}
        {!m.content && streaming && !m.tool && <p className="text-xs font-mono text-zinc-500 animate-pulse">Pensando…</p>}
        <div className={`prose-adam ${streaming ? "caret" : ""}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{m.content || ""}</ReactMarkdown>
        </div>
        {m.files?.map((f) => (f.kind === "image" ? <GeneratedImage key={f.id} file={f} /> : <FileCard key={f.id} file={f} />))}
      </div>
    </div>
  );
}
