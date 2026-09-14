import { useEffect } from "react";
import { useUIState } from "../lib/store.ts";

export function useKeyboardShortcuts() {
  const { openNewTaskModal, openCommandPalette, closeAll } = useUIState();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;

      // ⌘K / Ctrl+K → command palette
      if (isMeta && e.key === "k") {
        e.preventDefault();
        openCommandPalette();
        return;
      }

      // ⌘N / Ctrl+N → new task modal
      if (isMeta && e.key === "n") {
        e.preventDefault();
        openNewTaskModal();
        return;
      }

      // Escape → close all modals
      if (e.key === "Escape") {
        closeAll();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [openNewTaskModal, openCommandPalette, closeAll]);
}
