import { randomUUID } from "node:crypto";
import type { SessionStore } from "./session-store.js";
import type { EventBus } from "./event-bus.js";

export type PendingQuestion = {
  id: string;
  sessionId: string;
  toolUseId: string;
  questions: Array<{
    question: string;
    header: string;
    options: Array<{ label: string; description: string; preview?: string }>;
    multiSelect: boolean;
  }>;
  resolve: (answers: Record<string, string>) => void;
  createdAt: Date;
};

export class QuestionManager {
  private pending = new Map<string, PendingQuestion>();
  private timeoutMs: number;

  constructor(
    private store: SessionStore,
    private bus: EventBus,
    timeoutMs = 300_000, // 5 min default
  ) {
    this.timeoutMs = timeoutMs;
  }

  /**
   * Called when AskUserQuestion tool is invoked.
   * Emits a question.requested event and blocks until the user answers.
   */
  async waitForAnswer(
    sessionId: string,
    toolUseId: string,
    input: { questions: Array<{
      question: string;
      header: string;
      options: Array<{ label: string; description: string; preview?: string }>;
      multiSelect: boolean;
    }> },
  ): Promise<Record<string, string>> {
    const questionId = randomUUID();

    this.bus.publish(
      this.store.appendEvent(sessionId, "question.requested", {
        question_id: questionId,
        tool_use_id: toolUseId,
        questions: input.questions,
        awaiting_answer: true,
      }),
    );

    const answers = await new Promise<Record<string, string>>((resolve) => {
      this.pending.set(questionId, {
        id: questionId,
        sessionId,
        toolUseId,
        questions: input.questions,
        resolve,
        createdAt: new Date(),
      });

      // Timeout: auto-dismiss after timeoutMs
      setTimeout(() => {
        if (this.pending.has(questionId)) {
          this.pending.delete(questionId);
          // Return empty answers on timeout — the SDK will treat this as dismissed
          resolve({});
        }
      }, this.timeoutMs);
    });

    this.pending.delete(questionId);

    this.bus.publish(
      this.store.appendEvent(sessionId, "question.answered", {
        question_id: questionId,
        tool_use_id: toolUseId,
        answers,
      }),
    );

    return answers;
  }

  /**
   * Called when user answers a question via the API.
   */
  resolveQuestion(questionId: string, answers: Record<string, string>): boolean {
    const pending = this.pending.get(questionId);
    if (!pending) return false;
    pending.resolve(answers);
    return true;
  }

  /**
   * List all pending questions (for debugging / UI display).
   */
  listPending(): PendingQuestion[] {
    return [...this.pending.values()];
  }
}
