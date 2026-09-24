import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** Mirrors backend/app/schemas.py. `source` is always null in Milestone 1. */
export interface Claim {
  text: string;
  source: string | null;
}

export interface AssistantResponse {
  answer: string;
  claims: Claim[];
}

export interface ChatResponse {
  conversation_id: string;
  message_id: number;
  response: AssistantResponse;
  declined: boolean;
  decline_category: string | null;
  guard_stage: 'input' | 'output' | null;
  model: string | null;
}

export interface StoredMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  response: AssistantResponse | null;
  declined: boolean;
  created_at: string;
}

export interface Conversation {
  id: string;
  created_at: string;
  messages: StoredMessage[];
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private http = inject(HttpClient);

  send(message: string, conversationId: string | null): Observable<ChatResponse> {
    return this.http.post<ChatResponse>('/api/chat', { message, conversation_id: conversationId });
  }

  load(conversationId: string): Observable<Conversation> {
    return this.http.get<Conversation>(`/api/conversations/${conversationId}`);
  }
}
