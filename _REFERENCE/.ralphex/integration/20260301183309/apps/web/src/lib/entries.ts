import { ApiError, getAccessToken, refreshSession } from '@/lib/auth';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export type EntryStatusResponse = {
  entryId: string;
  status: string | null;
};

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
}

function parseErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string') {
    return payload;
  }

  if (!payload || typeof payload !== 'object') {
    return fallback;
  }

  const record = payload as Record<string, unknown>;
  if (typeof record.message === 'string') {
    return record.message;
  }
  if (typeof record.detail === 'string') {
    return record.detail;
  }

  return fallback;
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return response.json();
  }

  return response.text();
}

async function authorizedFetch(path: string, init: RequestInit, retry = true): Promise<unknown> {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path), {
    ...init,
    headers,
    credentials: 'include'
  });

  if (response.status === 401 && retry) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return authorizedFetch(path, init, false);
    }
  }

  const payload = await parseResponsePayload(response);
  if (!response.ok) {
    throw new ApiError(parseErrorMessage(payload, 'Request failed.'), response.status);
  }

  return payload;
}

function pickEntryId(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const data = payload as Record<string, unknown>;
  const value = data.entry_id ?? data.entryId ?? data.id;
  return typeof value === 'string' ? value : null;
}

function pickEntryStatus(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const data = payload as Record<string, unknown>;
  const value = data.status;
  return typeof value === 'string' ? value : null;
}

export async function createEntry(): Promise<EntryStatusResponse> {
  const payload = await authorizedFetch('/api/v1/entries', { method: 'POST' });
  const entryId = pickEntryId(payload);
  if (!entryId) {
    throw new ApiError('Entry creation response did not include an entry id.', 500);
  }

  return {
    entryId,
    status: pickEntryStatus(payload)
  };
}

export async function uploadEntryAudio(entryId: string, audioFile: File): Promise<void> {
  const mimeType = audioFile.type || 'audio/webm';
  await authorizedFetch(`/api/v1/entries/${entryId}/audio`, {
    method: 'POST',
    headers: {
      'Content-Type': mimeType,
      'x-audio-filename': audioFile.name
    },
    body: audioFile
  });
}

export async function fetchEntryStatus(entryId: string): Promise<EntryStatusResponse> {
  const payload = await authorizedFetch(`/api/v1/entries/${entryId}`, { method: 'GET' });
  const resolvedEntryId = pickEntryId(payload) ?? entryId;

  return {
    entryId: resolvedEntryId,
    status: pickEntryStatus(payload)
  };
}
