import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { toast } from "sonner";
import { errMsg, useApi } from "@/lib/api";
import PageHeader from "@/components/adam/PageHeader";

const field = "w-full px-4 py-3 rounded-xl bg-[#181A26] border border-white/10 outline-none focus:border-[#F4A261]/50 text-sm";

export default function SettingsPage() {
  const api = useApi();
  const [s, setS] = useState(null);
  useEffect(() => { api.get("/settings").then((r) => setS(r.data)); }, [api]);
  const save = async () => {
    try { await api.put("/settings", s); toast.success("Configurações salvas"); } catch (e) { toast.error(errMsg(e)); }
  };
  if (!s) return null;
  const set = (k) => (e) => setS({ ...s, [k]: e.target.value });

  return (
    <div className="flex-1 overflow-y-auto scroll-thin">
      <div className="max-w-3xl mx-auto px-5 sm:px-8 py-14">
        <PageHeader eyebrow="Configurações & Motores" title="Como o Adam pensa" />

        <section className="p-6 rounded-2xl bg-[#12141D] border border-white/[0.07] mb-5">
          <h2 className="font-display text-lg">Servidor local (Ollama)</h2>
          <p className="text-sm text-zinc-500 mt-1 mb-5">Rode um modelo aberto (Llama, Qwen, Gemma) no seu servidor com GPU e conecte aqui. O motor "Servidor Local" usará este endereço — sem nenhuma API de terceiros.</p>
          <label className="text-xs text-zinc-400">URL do servidor</label>
          <input data-testid="settings-ollama-url-input" className={`${field} mt-1.5 mb-4`} value={s.ollama_url} onChange={set("ollama_url")} placeholder="http://seu-servidor:11434" />
          <label className="text-xs text-zinc-400">Modelo</label>
          <input data-testid="settings-ollama-model-input" className={`${field} mt-1.5`} value={s.ollama_model} onChange={set("ollama_model")} placeholder="llama3.1, qwen2.5, gemma2…" />
        </section>

        <section className="p-6 rounded-2xl bg-[#12141D] border border-white/[0.07] mb-5">
          <h2 className="font-display text-lg">Estilo de resposta</h2>
          <p className="text-sm text-zinc-500 mt-1 mb-4">Instrução aplicada em todas as conversas.</p>
          <textarea data-testid="settings-style-input" rows={3} className={field} value={s.response_style} onChange={set("response_style")} placeholder="Ex: Seja direto, use exemplos práticos e emojis com moderação." />
        </section>

        <button onClick={save} data-testid="settings-save-button" className="flex items-center gap-2 px-5 py-3 rounded-xl bg-gradient-to-r from-[#F4A261] to-[#E07A5F] text-sm font-medium hover:opacity-90">
          <Save className="w-4 h-4" /> Salvar
        </button>

        <section className="mt-12 relative overflow-hidden rounded-2xl border border-white/[0.07]">
          <img src="https://images.unsplash.com/photo-1543958871-ccfa180a0c39?crop=entropy&cs=srgb&fm=jpg&q=85&w=1200" alt="Belo Horizonte" className="absolute inset-0 w-full h-full object-cover opacity-25" />
          <div className="relative p-6 bg-gradient-to-r from-[#090A0F] to-transparent">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-500">Sobre</p>
            <p className="font-display text-xl mt-2">Adam foi criado por Felipe Hudson</p>
            <p className="text-sm text-zinc-400 mt-1">19 anos · Belo Horizonte, Minas Gerais, Brasil</p>
          </div>
        </section>
      </div>
    </div>
  );
}
