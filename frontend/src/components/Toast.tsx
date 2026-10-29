import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

type ToastKind = "ok" | "err" | "info";

interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
}

interface ToastContextValue {
  show: (message: string, error?: boolean, kindOverride?: ToastKind) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, error = false, kindOverride?: ToastKind) => {
      const id = nextId++;
      const kind: ToastKind = kindOverride ?? (error ? "err" : "ok");
      setItems((prev) => [...prev.slice(-3), { id, message, kind }]);
      setTimeout(() => {
        setItems((prev) => prev.filter((t) => t.id !== id));
      }, 4200);
    },
    []
  );

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {items.length > 0 && (
        <div className="toast-stack" role="status" aria-live="polite">
          {items.map((t) => (
            <div key={t.id} className={`toast-item ${t.kind}`}>
              {t.kind === "err" ? (
                <AlertCircle size={17} color="#f87171" style={{ flexShrink: 0 }} />
              ) : t.kind === "info" ? (
                <Info size={17} color="#22d3ee" style={{ flexShrink: 0 }} />
              ) : (
                <CheckCircle2 size={17} color="#34d399" style={{ flexShrink: 0 }} />
              )}
              <span style={{ flex: 1 }}>{t.message}</span>
              <button
                type="button"
                className="ghost sm"
                style={{ padding: "2px 4px", border: "none" }}
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
