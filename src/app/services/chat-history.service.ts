import { Injectable } from '@angular/core';
import { AgentStep, EmergencyPlan, ResolvedLocation, TurnTrace } from './chat.service';

export interface SavedChatMessage {
  role: 'user' | 'assistant';
  text?: string;
  plan?: EmergencyPlan;
  location?: ResolvedLocation;
  candidates?: string[];
  steps?: AgentStep[];
  trace?: TurnTrace;
  error?: boolean;
}

export interface ChatSession {
  /** Also used as the LangGraph thread_id, so each chat keeps its own checkpointed state. */
  id: string;
  title: string;
  messages: SavedChatMessage[];
  createdAt: number;
}

@Injectable({
  providedIn: 'root',
})
export class ChatHistoryService {
  private storageKey = 'lebanon_emergency_chat_history';

  getChats(): ChatSession[] {
    const data = localStorage.getItem(this.storageKey);
    if (!data) {
      return [];
    }
    return JSON.parse(data);
  }

  saveChat(chat: ChatSession): void {
    const chats = this.getChats();
    const index = chats.findIndex((c) => c.id === chat.id);
    if (index >= 0) {
      chats[index] = chat;
    } else {
      chats.unshift(chat);
    }
    localStorage.setItem(this.storageKey, JSON.stringify(chats));
  }

  createChat(): ChatSession {
    return {
      id: crypto.randomUUID(),
      title: 'New Chat',
      messages: [
        {
          role: 'assistant',
          text:
            'Tell me where you are in Lebanon and what you need, e.g. "There is shelling near Achrafieh, ' +
            'I need a full emergency plan" or "nearest hospital to Tyre". I will find the nearest shelter and hospital.',
        },
      ],
      createdAt: Date.now(),
    };
  }

  deleteChat(id: string): void {
    const chats = this.getChats().filter((c) => c.id !== id);
    localStorage.setItem(this.storageKey, JSON.stringify(chats));
  }
}
