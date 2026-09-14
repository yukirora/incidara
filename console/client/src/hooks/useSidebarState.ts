import { useState } from "react";

const STORAGE_KEY = "chatui.sidebar.collapsed";

export function useSidebarState(): {
  collapsed: boolean;
  toggle: () => void;
} {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  function toggle() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // ignore
      }
      return next;
    });
  }

  return { collapsed, toggle };
}
