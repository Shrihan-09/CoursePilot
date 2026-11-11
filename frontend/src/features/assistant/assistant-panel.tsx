"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, History, MoreHorizontal, Plus, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, ToastProvider, Tooltip, useToast } from "@/components/ui/overlay";
import { AssistantChat } from "@/features/assistant/assistant-chat";
import { api } from "@/lib/api";
import { termLabel } from "@/lib/academic/vocabulary";
import { APP_NAME } from "@/lib/brand";
import type { Conversation } from "@/types/assistant";

export type AssistantContext = { term?: string; course?: string; draft?: string; builderTerm?: string; selection?: string[]; onAddCourse?: (course: string) => void };
type PanelState = {
  headerButton: React.ReactNode;
  open: (context?: AssistantContext) => void;
  close: () => void;
  context: AssistantContext | null;
  setContext: (context: AssistantContext | null) => void;
  setPlanTerm: (term: string | undefined) => void;
  conversation: string | undefined;
  select: (id: string) => void;
  isOpen: boolean;
  busy: boolean;
  setBusy: (busy: boolean) => void;
};
const Panel = createContext<PanelState | null>(null);
export function useAssistantPanel() { return useContext(Panel); }
export function AssistantHeaderButton() { return useAssistantPanel()?.headerButton ?? null; }

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["assistant"], queryFn: () => api.assistantStatus() });
  const userKey = status.data?.user_key;
  const [isOpen, setOpen] = useState(false);
  const [seen, setSeen] = useState(true);
  const [hasOpened, setHasOpened] = useState(false);
  const [context, setContext] = useState<AssistantContext | null>(null);
  const [planTerm, setPlanTerm] = useState<string>();
  const [conversation, setConversation] = useState<string>();
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const fullPage = pathname === "/assistant";
  const storageKey = userKey ? `cp:assistant:${userKey}` : null;

  useEffect(() => {
    if (!storageKey) return;
    try {
      setSeen(hasOpened || localStorage.getItem(`${storageKey}:seen`) === "1");
      if (hasOpened) localStorage.setItem(`${storageKey}:seen`, "1");
      const restored = sessionStorage.getItem(`${storageKey}:open`) === "1";
      setOpen(restored);
      setHasOpened(restored);
      setConversation(sessionStorage.getItem(`${storageKey}:conversation`) || undefined);
    } catch { /* Storage may be disabled; this session still works. */ }
  }, [storageKey]);
  useEffect(() => {
    if (!fullPage || !storageKey) return;
    setSeen(true);
    try { localStorage.setItem(`${storageKey}:seen`, "1"); } catch {}
  }, [fullPage, storageKey]);
  const open = useCallback((ctx?: AssistantContext) => {
    setOpen(true);
    setHasOpened(true);
    setSeen(true);
    setContext((previous) => ctx ?? (previous?.builderTerm ? previous : pathname === "/plan" && planTerm ? { term: planTerm } : null));
    try {
      if (storageKey) {
        localStorage.setItem(`${storageKey}:seen`, "1");
        sessionStorage.setItem(`${storageKey}:open`, "1");
      }
    } catch { /* Best effort preferences only. */ }
  }, [pathname, planTerm, storageKey]);
  const close = useCallback(() => {
    setOpen(false);
    try { if (storageKey) sessionStorage.setItem(`${storageKey}:open`, "0"); } catch {}
    trigger.current?.focus();
  }, [storageKey]);
  const select = useCallback((id: string) => {
    if (busy) return;
    setConversation(id);
    try { if (storageKey) sessionStorage.setItem(`${storageKey}:conversation`, id); } catch {}
  }, [busy, storageKey]);
  useEffect(() => {
    const toggle = (ev: KeyboardEvent) => {
      if (ev.ctrlKey && (ev.key === "/" || ev.code === "Slash")) {
        ev.preventDefault();
        if (isOpen) close(); else open();
      }
    };
    window.addEventListener("keydown", toggle);
    return () => window.removeEventListener("keydown", toggle);
  }, [isOpen, open, close]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (isOpen && !el.open) el.showModal();
    else if (!isOpen && el.open) el.close();
  }, [isOpen, fullPage]);
  // The active id is shared by the drawer and the full Assistant page.
  const active = conversation ?? status.data?.conversation_id;
  const headerButton = (<div className="absolute right-4 top-2 z-40" data-testid="assistant-header-button">
      <Tooltip content={`Ask ${APP_NAME} (Ctrl+/)`} below>
        <button ref={trigger} type="button" aria-label={`Ask ${APP_NAME} (Ctrl+/)`}
                aria-haspopup="dialog" aria-expanded={isOpen}
                className="assistant-glow flex size-10 items-center justify-center rounded-lg border"
                data-pulse={!seen ? "true" : "false"} title={`Ask ${APP_NAME} (Ctrl+/)`} onClick={() => isOpen ? close() : open()}>
          <Sparkles aria-hidden className="size-5" />
        </button>
      </Tooltip>
    </div>);
  return <Panel.Provider value={{ headerButton, open, close, context, setContext, setPlanTerm, conversation: active,
                                  select, isOpen, busy, setBusy }}>
    {children}

    <dialog ref={ref} aria-label={`${APP_NAME} assistant`} onCancel={(e) => { e.preventDefault(); close(); }}
            className="assistant-drawer fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-none border-l border-border bg-surface-elevated p-0 text-fg shadow-elevated backdrop:bg-overlay/30 sm:w-[400px]">
      {(isOpen || (hasOpened && !fullPage)) && <ToastProvider contained><div className="flex h-full min-w-0 flex-col">
        <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
          <h2 className="text-sm font-semibold">{APP_NAME}</h2>
          <div className="flex items-center gap-2">
            <Link href="/assistant" className="inline-flex items-center gap-1 text-xs" aria-disabled={busy} onClick={(e) => {
              if (busy) { e.preventDefault(); return; }
              close();
              void qc.invalidateQueries({ queryKey: ["assistant"] });
            }}><ExternalLink className="size-3" aria-hidden /> Expand</Link>
            <Button size="sm" variant="ghost" aria-label="Close assistant" onClick={close}><X className="size-4" /></Button>
          </div>
        </div>
        <AssistantWorkspace compact />
      </div></ToastProvider>}
    </dialog>
  </Panel.Provider>;
}

export function AssistantWorkspace({ compact = false }: { compact?: boolean }) {
  const panel = useAssistantPanel();
  const qc = useQueryClient();
  const toast = useToast();
  const [showHistory, setShowHistory] = useState(false);
  const [editing, setEditing] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [title, setTitle] = useState("");
  const [mutating, setMutating] = useState(false);
  const chats = useQuery({ queryKey: ["assistant"], queryFn: () => api.assistantStatus() });
  const conversation = panel?.conversation ?? chats.data?.conversation_id;
  const refresh = () => qc.invalidateQueries({ queryKey: ["assistant"] });
  const mutate = async (fn: () => Promise<unknown>) => {
    setMutating(true);
    try { await fn(); await refresh(); }
    catch { toast("Couldn't update that chat. Try again.", "danger"); }
    finally { setMutating(false); }
  };
  return <div className={`flex min-h-0 min-w-0 flex-col ${compact ? "flex-1" : "h-[calc(100dvh-14rem)] min-h-96"}`}>
    <div className="flex shrink-0 flex-wrap gap-1 border-b border-border p-2">
      <Button size="sm" variant="ghost" disabled={mutating || panel?.busy} onClick={() => void mutate(async () => {
        const chat = await api.newAssistantConversation();
        panel?.select(chat.id);
        panel?.setContext(null);
        setShowHistory(false);
      })}><Plus className="size-4" aria-hidden /> New chat</Button>
      <Button size="sm" variant="ghost" aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
        <History className="size-4" aria-hidden /> History
      </Button>
    </div>
    {showHistory && <ul aria-label="Chat history" className="max-h-52 shrink-0 space-y-1 overflow-y-auto border-b border-border p-2">
      {(chats.data?.conversations ?? []).map((c) => <li key={c.id} className="flex min-w-0 items-center gap-1 rounded border border-border p-1">
        <button disabled={panel?.busy} className="min-w-0 flex-1 text-left text-xs" aria-current={conversation === c.id ? "true" : undefined}
                onClick={() => { panel?.select(c.id); panel?.setContext(null); setShowHistory(false); }}>
          <span className="block truncate font-medium">{c.title}{c.archived ? " (archived)" : ""}</span>
          <time className="text-fg-muted" dateTime={c.updated_at}>{new Date(c.updated_at).toLocaleString()}</time>
        </button>
        <details className="shrink-0">
          <summary aria-label={`Chat menu: ${c.title}`} className="cursor-pointer list-none p-2"><MoreHorizontal className="size-4" /></summary>
          <div className="w-28 rounded border border-border bg-surface-elevated p-1 shadow-elevated">
            <button className="block w-full p-2 text-left text-xs" onClick={() => { setEditing(c); setTitle(c.title); }}>Rename</button>
            <button className="block w-full p-2 text-left text-xs" disabled={panel?.busy} onClick={() => setDeleting(c)}>Delete</button>
          </div>
        </details>
      </li>)}
    </ul>}
    {panel?.context && <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2 text-xs" data-testid="assistant-context">
      <span className="min-w-0 flex-1 truncate">{panel.context.builderTerm ? `Builder draft (${panel.context.selection?.length ?? 0} courses)` : panel.context.course ?? "Your plan"}{panel.context.term ? ` · ${termLabel(panel.context.term)}` : ""}</span>
      <button aria-label="Remove assistant context" onClick={() => panel.setContext(null)}><X className="size-3" /></button>
    </div>}
    <AssistantChat key={conversation ?? "default"} compact={compact} conversationId={conversation}
                   contextTerm={panel?.context?.term} builderSelection={panel?.context?.selection} onBuilderAdd={panel?.context?.onAddCourse} draft={panel?.context?.draft} onBusy={panel?.setBusy}
                   onDraftUsed={() => { if (panel?.context?.draft) panel.setContext({ ...panel.context, draft: undefined }); }} />
    <Dialog open={!!editing} onClose={() => setEditing(null)} title="Rename chat" footer={<Button disabled={mutating || !title.trim()}
      onClick={() => void mutate(async () => { await api.editAssistantConversation(editing!.id, { title: title.trim() }); setEditing(null); })}>Save</Button>}>
      <label className="text-sm">Chat title<input className="mt-2 block w-full rounded border border-border bg-surface p-2" value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} /></label>
    </Dialog>
    <Dialog open={!!deleting} onClose={() => setDeleting(null)} title="Delete this chat?" description="This chat and all its messages will be removed."
      footer={<Button disabled={mutating} onClick={() => void mutate(async () => {
        await api.deleteAssistantConversation(deleting!.id);
        qc.removeQueries({ queryKey: ["assistant-turns", deleting!.id] });
        if (conversation === deleting!.id) { const next = await api.newAssistantConversation(); panel?.select(next.id); }
        setDeleting(null);
      })}>Delete chat</Button>} />
  </div>;
}
