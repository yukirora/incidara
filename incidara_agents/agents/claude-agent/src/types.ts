// src/types.ts

export type SessionStatus =
  | "starting"
  | "running"
  | "busy"
  | "waiting_input"
  | "interrupted"
  | "completed"
  | "error";

export type GatewayEventType =
  | "session.started"
  | "session.state_changed"
  | "session.completed"
  | "session.error"
  | "session.interrupted"
  | "session.waiting_input"
  | "message.user"
  | "message.agent"
  | "message.delta"
  | "tool.started"
  | "tool.stdout"
  | "tool.finished"
  | "permission.requested"
  | "permission.resolved"
  | "artifact.created"
  | "subagent.started"
  | "subagent.progress"
  | "subagent.updated"
  | "subagent.finished"
  | "subagent.delta"
  | "subagent.tool.started"
  | "subagent.tool.finished"
  | "subagent.tool.progress"
  | "mcp.progress"
  | "question.requested"
  | "question.answered";

export type GatewayEvent = {
  seq: number;
  session_id: string;
  event_type: GatewayEventType;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type Session = {
  id: string;
  claudeSessionId?: string;
  status: SessionStatus;
  title?: string;
  workspacePath: string;
  createdAt: Date;
  updatedAt: Date;
  lastMessagePreview?: string;
  currentTool?: string;
  currentToolUseId?: string;
  abortController: AbortController;
};

export type SessionStateSnapshot = {
  session_id: string;
  status: SessionStatus;
  current_tool?: string;
  last_message_preview?: string;
  waiting_for_input: boolean;
  claude_session_id?: string;
  latest_seq: number;
};
