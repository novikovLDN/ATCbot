import { create } from "zustand";

export type Toast = {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
};

interface ToastStore {
  items: Toast[];
  push: (kind: Toast["kind"], text: string) => void;
  dismiss: (id: number) => void;
}

let counter = 0;

export const useToasts = create<ToastStore>((set) => ({
  items: [],
  push: (kind, text) => {
    const id = ++counter;
    set((s) => ({ items: [...s.items, { id, kind, text }] }));
    window.setTimeout(() => {
      set((s) => ({ items: s.items.filter((t) => t.id !== id) }));
    }, 4500);
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
}));

export const toast = {
  success: (text: string) => useToasts.getState().push("success", text),
  error: (text: string) => useToasts.getState().push("error", text),
  info: (text: string) => useToasts.getState().push("info", text),
};
