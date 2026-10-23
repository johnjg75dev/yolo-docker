import { createContext, useContext, useState, type ReactNode } from "react";

interface ToastContextValue {
  show: (message: string, error?: boolean) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null);

  const show = (message: string, error = false) => {
    setToast({ message, error });
    setTimeout(() => setToast(null), 3000);
  };

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {toast && (
        <div className={`toast ${toast.error ? "err" : "ok"}`}>{toast.message}</div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}