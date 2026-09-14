import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar.tsx";
import { Header } from "./Header.tsx";
import { CommandPalette } from "./CommandPalette.tsx";
import { NewTaskModal } from "./NewTaskModal.tsx";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts.ts";

export function AppLayout() {
  useKeyboardShortcuts();

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-zinc-50">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <CommandPalette />
      <NewTaskModal />
    </div>
  );
}
