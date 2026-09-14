export interface GatewayEvent {
  seq: number;
  session_id: string;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface CreateSessionOptions {
  prompt: string;
  workspace_path?: string;
  title?: string;
}

export interface GatewaySession {
  id: string;
  status: string;
  [key: string]: unknown;
}

export class GatewayClient {
  constructor(private readonly baseUrl: string) {}

  private async request(
    method: string,
    path: string,
    body?: unknown
  ): Promise<unknown> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Gateway error ${res.status}: ${text}`);
    }

    if (res.status === 204) {
      return undefined;
    }

    return res.json();
  }

  async createSession(opts: CreateSessionOptions): Promise<GatewaySession> {
    return this.request("POST", "/sessions", opts) as Promise<GatewaySession>;
  }

  async sendMessage(id: string, content: string): Promise<unknown> {
    return this.request("POST", `/sessions/${id}/messages`, { content });
  }

  async interrupt(id: string): Promise<unknown> {
    return this.request("POST", `/sessions/${id}/interrupt`);
  }

  async resume(id: string, content?: string): Promise<unknown> {
    return this.request("POST", `/sessions/${id}/resume`, content ? { content } : undefined);
  }

  async getState(id: string): Promise<unknown> {
    return this.request("GET", `/sessions/${id}/state`);
  }

  async getRounds(id: string): Promise<{ rounds: Array<{ seq: number; preview: string }>; total: number }> {
    return this.request("GET", `/sessions/${id}/rounds`) as Promise<{ rounds: Array<{ seq: number; preview: string }>; total: number }>;
  }

  async getEvents(id: string, afterSeq?: number, beforeSeq?: number): Promise<unknown> {
    const params = new URLSearchParams();
    if (afterSeq != null) params.set("after_seq", String(afterSeq));
    if (beforeSeq != null) params.set("before_seq", String(beforeSeq));
    const qs = params.toString();
    return this.request("GET", `/sessions/${id}/events${qs ? "?" + qs : ""}`);
  }

  /** Load ALL events from a gateway session, paginating as needed.
   *  Returns the full events array (no pagination metadata). */
  async getAllEvents(id: string, afterSeq?: number, beforeSeq?: number): Promise<any[]> {
    const allEvents: any[] = [];
    let cursor = afterSeq;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const params = new URLSearchParams();
      if (cursor != null) params.set("after_seq", String(cursor));
      if (beforeSeq != null) params.set("before_seq", String(beforeSeq));
      const qs = params.toString();
      const resp: any = await this.request("GET", `/sessions/${id}/events${qs ? "?" + qs : ""}`);
      const events: any[] = resp?.events ?? [];
      if (events.length === 0) break;
      allEvents.push(...events);
      const nextSeq = resp?.next_after_seq;
      if (!nextSeq) break;
      cursor = nextSeq;
    }
    return allEvents;
  }

  async deleteSession(id: string): Promise<void> {
    await this.request("DELETE", `/sessions/${id}`);
  }

  async approvePermission(sessionId: string, permId: string): Promise<unknown> {
    return this.request("POST", `/sessions/${sessionId}/permissions/${permId}/approve`);
  }

  async denyPermission(sessionId: string, permId: string, reason?: string): Promise<unknown> {
    return this.request("POST", `/sessions/${sessionId}/permissions/${permId}/deny`, reason ? { reason } : undefined);
  }

  async answerQuestion(sessionId: string, questionId: string, answers: Record<string, string>): Promise<unknown> {
    return this.request("POST", `/sessions/${sessionId}/questions/${questionId}/answer`, { answers });
  }

  async getSubagentTranscript(sessionId: string, taskId: string): Promise<unknown> {
    return this.request("GET", `/sessions/${sessionId}/subagents/${taskId}/transcript`);
  }

  /**
   * Get per-turn usage with timestamps for a specific session (for task-level splitting).
   */
  async getUsageTurns(sessionId: string): Promise<any> {
    return this.request("GET", `/usage/sessions/${sessionId}/turns`);
  }

  /**
   * Get usage data for a specific session from the gateway.
   */
  async getUsage(sessionId: string): Promise<any> {
    return this.request("GET", `/usage/sessions/${sessionId}?include_subagents=true`);
  }

  /**
   * Get usage data for all sessions from the gateway, including sub-agents merged into parent totals.
   */
  async getAllUsage(): Promise<any> {
    return this.request("GET", `/usage?include_subagents=true`);
  }

  /**
   * Opens a raw SSE stream from the gateway.
   * Returns the raw Response so callers can pipe bytes.
   */
  async openEventStream(id: string, afterSeq?: number, lastEventId?: string): Promise<Response> {
    const qs = afterSeq != null ? `?after_seq=${afterSeq}` : "";
    const url = `${this.baseUrl}/sessions/${id}/events/stream${qs}`;
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
    };
    if (lastEventId) {
      headers["Last-Event-ID"] = lastEventId;
    }

    const res = await fetch(url, { headers });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Gateway error ${res.status}: ${text}`);
    }

    return res;
  }
}
