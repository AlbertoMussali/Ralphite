const ACCESS_TOKEN_KEY = 'voicevault.accessToken';
const REFRESH_TOKEN_KEY = 'voicevault.refreshToken';
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

type LoginPayload = {
  email: string;
  password: string;
};

type SignupPayload = {
  email: string;
  password: string;
};

type TokenResponse = {
  access_token?: string;
  refresh_token?: string;
  accessToken?: string;
  refreshToken?: string;
};

export type AuthUser = {
  id?: string;
  email?: string;
  [key: string]: unknown;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
}

function normalizeTokens(tokens: TokenResponse): { accessToken?: string; refreshToken?: string } {
  return {
    accessToken: tokens.access_token ?? tokens.accessToken,
    refreshToken: tokens.refresh_token ?? tokens.refreshToken
  };
}

function parseErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string') {
    return payload;
  }

  if (payload && typeof payload === 'object') {
    const data = payload as Record<string, unknown>;

    if (typeof data.message === 'string') {
      return data.message;
    }

    if (typeof data.detail === 'string') {
      return data.detail;
    }

    if (Array.isArray(data.detail)) {
      const first = data.detail[0];
      if (first && typeof first === 'object' && 'msg' in first) {
        const msg = (first as { msg?: unknown }).msg;
        if (typeof msg === 'string') {
          return msg;
        }
      }
    }
  }

  return fallback;
}

async function request<T>(path: string, init: RequestInit, fallbackError: string): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {})
    },
    credentials: 'include'
  });

  const isJson = response.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    throw new ApiError(parseErrorMessage(payload, fallbackError), response.status);
  }

  return payload as T;
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

function setAccessToken(token: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

function setRefreshToken(token: string): void {
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

function saveTokens(payload: TokenResponse): void {
  const { accessToken, refreshToken } = normalizeTokens(payload);

  if (accessToken) {
    setAccessToken(accessToken);
  }

  if (refreshToken) {
    setRefreshToken(refreshToken);
  }
}

export async function signup(payload: SignupPayload): Promise<void> {
  const tokenResponse = await request<TokenResponse>(
    '/api/v1/auth/signup',
    {
      method: 'POST',
      body: JSON.stringify(payload)
    },
    'Signup failed.'
  );

  saveTokens(tokenResponse);
}

export async function login(payload: LoginPayload): Promise<void> {
  const tokenResponse = await request<TokenResponse>(
    '/api/v1/auth/login',
    {
      method: 'POST',
      body: JSON.stringify(payload)
    },
    'Login failed.'
  );

  saveTokens(tokenResponse);
}

export async function logout(): Promise<void> {
  try {
    await request<unknown>(
      '/api/v1/auth/logout',
      {
        method: 'POST',
        body: JSON.stringify({ refresh_token: getRefreshToken() ?? undefined })
      },
      'Logout failed.'
    );
  } finally {
    clearTokens();
  }
}

let refreshPromise: Promise<boolean> | null = null;

export async function refreshSession(): Promise<boolean> {
  if (refreshPromise) {
    return refreshPromise;
  }

  refreshPromise = (async () => {
    try {
      const refreshToken = getRefreshToken();
      const tokenResponse = await request<TokenResponse>(
        '/api/v1/auth/refresh',
        {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken ?? undefined })
        },
        'Session refresh failed.'
      );

      saveTokens(tokenResponse);
      return Boolean(getAccessToken());
    } catch {
      clearTokens();
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

export async function getMe(): Promise<AuthUser> {
  const token = getAccessToken();

  return request<AuthUser>(
    '/api/v1/me',
    {
      method: 'GET',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined
    },
    'Failed to load user profile.'
  );
}

type ApiFetchOptions = Omit<RequestInit, 'body'> & {
  body?: unknown;
};

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const token = getAccessToken();

    const headers = new Headers(options.headers ?? {});
    if (options.body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    return fetch(buildUrl(path), {
      ...options,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: 'include'
    });
  };

  let response = await doFetch();

  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      response = await doFetch();
    }
  }

  const isJson = response.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    throw new ApiError(parseErrorMessage(payload, 'Request failed.'), response.status);
  }

  return payload as T;
}
