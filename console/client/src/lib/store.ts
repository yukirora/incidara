import { create } from "zustand";

interface UIState {
  newTaskModalOpen: boolean;
  newTaskPrefilledAgentId: string | undefined;
  commandPaletteOpen: boolean;

  openNewTaskModal: (agentId?: string) => void;
  closeNewTaskModal: () => void;

  openCommandPalette: () => void;
  closeCommandPalette: () => void;

  closeAll: () => void;
}

export const useUIState = create<UIState>((set) => ({
  newTaskModalOpen: false,
  newTaskPrefilledAgentId: undefined,
  commandPaletteOpen: false,

  openNewTaskModal: (agentId?: string) =>
    set({ newTaskModalOpen: true, newTaskPrefilledAgentId: agentId, commandPaletteOpen: false }),
  closeNewTaskModal: () =>
    set({ newTaskModalOpen: false, newTaskPrefilledAgentId: undefined }),

  openCommandPalette: () =>
    set({ commandPaletteOpen: true, newTaskModalOpen: false }),
  closeCommandPalette: () =>
    set({ commandPaletteOpen: false }),

  closeAll: () =>
    set({ newTaskModalOpen: false, commandPaletteOpen: false }),
}));
