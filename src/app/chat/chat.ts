import { CommonModule } from '@angular/common';
import {
  AfterViewChecked,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  inject,
  Output,
  ViewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AgentStep, ChatResult, ChatService } from '../services/chat.service';
import { ChatHistoryService, ChatSession, SavedChatMessage } from '../services/chat-history.service';
import { PlanOverlay } from '../map/map';

const NODE_LABELS: Record<string, string> = {
  router: 'Router agent',
  location_resolver: 'Location Resolver agent',
  facility_finder: 'Facility Finder agent',
  response_formatter: 'Response Formatter agent',
  clarification: 'Clarification',
  resolution_error: 'Location not found',
};

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chat.html',
  styleUrl: './chat.css',
})
export class ChatComponent implements AfterViewChecked {
  @ViewChild('chatMessages') private chatMessagesRef!: ElementRef<HTMLDivElement>;

  /** Emits when a plan should be drawn on the map. */
  @Output() showPlan = new EventEmitter<PlanOverlay>();

  messages: SavedChatMessage[] = [];
  chats: ChatSession[] = [];
  currentChat!: ChatSession;

  input = '';
  loading = false;
  liveSteps: AgentStep[] = [];

  /** Message whose agent trace is expanded (index into `messages`). */
  openTraceIndex: number | null = null;

  private shouldScroll = false;
  private readonly cdr = inject(ChangeDetectorRef);

  constructor(
    private readonly chatService: ChatService,
    private readonly chatHistoryService: ChatHistoryService,
  ) {
    this.loadChats();
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll) {
      this.scrollToBottom();
      this.shouldScroll = false;
    }
  }

  // =========================
  // CHAT SESSION MANAGEMENT
  // =========================

  loadChats(): void {
    this.chats = this.chatHistoryService.getChats();
    this.chats.length ? this.openChat(this.chats[0]) : this.newChat();
  }

  newChat(): void {
    this.currentChat = this.chatHistoryService.createChat();
    this.messages = [...this.currentChat.messages];
    this.openTraceIndex = null;
    this.saveCurrentChat();
    this.shouldScroll = true;
  }

  openChat(chat: ChatSession): void {
    this.currentChat = chat;
    this.messages = [...chat.messages];
    this.openTraceIndex = null;
    this.shouldScroll = true;
  }

  deleteChat(id: string, event: Event): void {
    event.stopPropagation();
    this.chatHistoryService.deleteChat(id);
    this.chats = this.chatHistoryService.getChats();

    if (this.currentChat?.id === id) {
      this.newChat();
    }
  }

  saveCurrentChat(): void {
    if (!this.currentChat) return;

    this.currentChat.messages = this.messages;
    if (this.messages.length > 1) {
      this.currentChat.title = this.messages[1].text?.slice(0, 35) || 'New Chat';
    }

    this.chatHistoryService.saveChat(this.currentChat);
    this.chats = this.chatHistoryService.getChats();
  }

  // =========================
  // SENDING MESSAGES
  // =========================

  async send(text?: string): Promise<void> {
    const message = (text ?? this.input).trim();
    if (!message || this.loading) return;

    this.messages.push({ role: 'user', text: message });
    this.input = '';
    this.loading = true;
    this.liveSteps = [];
    this.saveCurrentChat();
    this.shouldScroll = true;

    try {
      const result = await this.chatService.sendMessage(message, this.currentChat.id, (step) => {
        this.liveSteps = [...this.liveSteps, step];
        this.shouldScroll = true;
        this.cdr.detectChanges();
      });
      this.appendAssistantResponse(result);
    } catch (error) {
      console.error('Chat error:', error);
      this.messages.push({
        role: 'assistant',
        text: `Something went wrong reaching the assistant. ${error instanceof Error ? error.message : ''}`.trim(),
        steps: this.liveSteps,
        error: true,
      });
    } finally {
      this.loading = false;
      this.liveSteps = [];
      this.saveCurrentChat();
      this.shouldScroll = true;
      this.cdr.detectChanges();
    }
  }

  private appendAssistantResponse(result: ChatResult): void {
    const base: SavedChatMessage = { role: 'assistant', steps: this.liveSteps, trace: result.trace };

    if (result.type === 'plan' && result.plan) {
      this.messages.push({ ...base, text: result.plan.summary, plan: result.plan, location: result.location ?? undefined });
      this.drawPlan(this.messages[this.messages.length - 1]);
    } else if (result.type === 'clarification') {
      this.messages.push({ ...base, text: 'I found more than one place with that name. Which one do you mean?', candidates: result.candidates });
    } else {
      this.messages.push({ ...base, text: result.message || 'No answer available.', error: result.type === 'error' });
    }
  }

  /** Answer a clarification question by clicking one of the offered places. */
  pickCandidate(candidate: string): void {
    this.send(candidate);
  }

  drawPlan(msg: SavedChatMessage): void {
    if (!msg.plan || !msg.location) return;
    this.showPlan.emit({
      location: msg.location,
      shelter: msg.plan.nearest_shelter,
      hospital: msg.plan.nearest_hospital,
    });
  }

  // =========================
  // AGENT TRACE
  // =========================

  nodeLabel(node: string): string {
    return NODE_LABELS[node] ?? node;
  }

  toggleTrace(index: number): void {
    this.openTraceIndex = this.openTraceIndex === index ? null : index;
  }

  fieldEntries(step: AgentStep): { key: string; value: string | string[] }[] {
    return Object.entries(step.fields).map(([key, value]) => ({ key, value }));
  }

  isList(value: string | string[]): value is string[] {
    return Array.isArray(value);
  }

  // =========================
  // SCROLLING
  // =========================

  private scrollToBottom(): void {
    try {
      const el = this.chatMessagesRef?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    } catch {
      // container not ready yet, ignore
    }
  }
}
