import { inject, Injectable } from '@angular/core';
import { Config } from './config';

/** One LangGraph node's partial state update, streamed while the agents work. */
export interface AgentStep {
  node: string;
  fields: Record<string, string | string[]>;
}

export interface PlanFacility {
  name: string;
  lat: number;
  lon: number;
  distance_km: number;
  [key: string]: unknown;
}

/** Mirrors the Python EmergencyPlan schema (models/schemas.py). */
export interface EmergencyPlan {
  summary: string;
  nearest_shelter: PlanFacility | null;
  nearest_hospital: PlanFacility | null;
  distance_to_shelter_km: number | null;
  distance_to_hospital_km: number | null;
  instructions: string[];
  emergency_contacts: string[];
  warnings: string[];
}

export interface ResolvedLocation {
  name: string;
  lat: number;
  lon: number;
  source: string;
  confidence: number;
}

export interface TurnTrace {
  intent: string | null;
  router_rationale: string | null;
  nodes: string[];
  tools: string[];
  search_radius_km: number;
  effective_radius_km: number | null;
  radius_expanded: boolean;
  shelter_to_hospital_km: number | null;
  turn: number;
}

export interface ChatResult {
  type: 'plan' | 'clarification' | 'error' | 'text';
  message: string | null;
  plan: EmergencyPlan | null;
  location: ResolvedLocation | null;
  candidates: string[];
  shelters: PlanFacility[];
  hospitals: PlanFacility[];
  trace: TurnTrace;
}

@Injectable({
  providedIn: 'root',
})
export class ChatService {
  private readonly configService = inject(Config);

  /**
   * Runs one turn of the multi-agent graph. `threadId` is the LangGraph checkpoint
   * thread, so answering a clarification on the same thread resumes the request.
   * The server streams newline-delimited JSON; `onStep` fires as each agent finishes.
   */
  async sendMessage(message: string, threadId: string, onStep: (step: AgentStep) => void): Promise<ChatResult> {
    const response = await fetch(this.configService.configurations.chatApiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
    if (!response.ok || !response.body) {
      throw new Error(`Chat request failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });

      let newline: number;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;

        const event = JSON.parse(line);
        if (event.event === 'node') {
          onStep({ node: event.node, fields: event.fields });
        } else if (event.event === 'result') {
          return event as ChatResult;
        } else if (event.event === 'error') {
          throw new Error(event.message);
        }
      }
      if (done) {
        throw new Error('The assistant ended the response without a result.');
      }
    }
  }
}
