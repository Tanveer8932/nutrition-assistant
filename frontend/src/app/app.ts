import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { ChatService, Claim } from './chat/chat.service';

interface UiMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  claims: Claim[];
  declined: boolean;
  error?: boolean;
}

const STORAGE_KEY = 'nutrition-assistant.conversation';

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredId(id: string | null): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: conversation just won't survive a reload */
  }
}

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private chat = inject(ChatService);
  private scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected readonly messages = signal<UiMessage[]>([]);
  protected readonly draft = signal('');
  protected readonly sending = signal(false);
  protected readonly conversationId = signal<string | null>(readStoredId());
  protected readonly selectedId = signal<number | null>(null);

  /** Assistant message whose claims/sources the side panel shows. */
  protected readonly selected = computed(() => {
    const msgs = this.messages().filter((m) => m.role === 'assistant' && !m.error);
    const id = this.selectedId();
    return msgs.find((m) => m.id === id) ?? msgs.at(-1) ?? null;
  });
  protected readonly sources = computed(() =>
    (this.selected()?.claims ?? []).filter((c) => c.source !== null),
  );

  protected readonly suggestions = [
    'How much protein does a vegetarian adult need?',
    'How long can cooked rice stay in the fridge?',
    'Does steaming keep more vitamins than boiling?',
  ];

  constructor() {
    const id = this.conversationId();
    if (id) {
      this.chat.load(id).subscribe({
        next: (conv) =>
          this.messages.set(
            conv.messages.map((m) => ({
              id: m.id,
              role: m.role,
              text: m.content,
              claims: m.response?.claims ?? [],
              declined: m.declined,
            })),
          ),
        error: () => this.newConversation(),
      });
    }
  }

  protected send(text = this.draft()): void {
    const message = text.trim();
    if (!message || this.sending()) return;
    this.draft.set('');
    this.sending.set(true);
    this.push({ id: -Date.now(), role: 'user', text: message, claims: [], declined: false });

    this.chat.send(message, this.conversationId()).subscribe({
      next: (res) => {
        this.conversationId.set(res.conversation_id);
        writeStoredId(res.conversation_id);
        this.push({
          id: res.message_id,
          role: 'assistant',
          text: res.response.answer,
          claims: res.response.claims,
          declined: res.declined,
        });
        this.selectedId.set(res.message_id);
        this.sending.set(false);
      },
      error: (err: HttpErrorResponse) => {
        const detail = typeof err.error?.detail === 'string' ? err.error.detail : 'Something went wrong.';
        this.push({ id: -Date.now(), role: 'assistant', text: detail, claims: [], declined: false, error: true });
        this.sending.set(false);
      },
    });
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  protected newConversation(): void {
    this.conversationId.set(null);
    writeStoredId(null);
    this.messages.set([]);
    this.selectedId.set(null);
  }

  private push(msg: UiMessage): void {
    this.messages.update((list) => [...list, msg]);
    queueMicrotask(() => {
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }
}
