import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from 'react';

import { ApiError, clearTokens, getMe, login, logout, refreshSession, signup, type AuthUser } from '@/lib/auth';

type Credentials = {
  email: string;
  password: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  loginWithCredentials: (payload: Credentials) => Promise<void>;
  signupWithCredentials: (payload: Credentials) => Promise<void>;
  logoutCurrentUser: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'Something went wrong.';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const bootstrapSession = useCallback(async () => {
    setIsLoading(true);

    try {
      try {
        const me = await getMe();
        setUser(me);
        return;
      } catch {
        const refreshed = await refreshSession();
        if (!refreshed) {
          setUser(null);
          return;
        }
      }

      try {
        const me = await getMe();
        setUser(me);
      } catch {
        clearTokens();
        setUser(null);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void bootstrapSession();
  }, [bootstrapSession]);

  const loginWithCredentials = useCallback(async (payload: Credentials) => {
    await login(payload);
    const me = await getMe();
    setUser(me);
  }, []);

  const signupWithCredentials = useCallback(async (payload: Credentials) => {
    await signup(payload);
    const me = await getMe();
    setUser(me);
  }, []);

  const logoutCurrentUser = useCallback(async () => {
    await logout();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const me = await getMe();
      setUser(me);
    } catch (error) {
      throw new Error(toErrorMessage(error));
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: Boolean(user),
      isLoading,
      loginWithCredentials,
      signupWithCredentials,
      logoutCurrentUser,
      refreshUser
    }),
    [isLoading, loginWithCredentials, logoutCurrentUser, refreshUser, signupWithCredentials, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider.');
  }

  return context;
}

export function getAuthErrorMessage(error: unknown): string {
  return toErrorMessage(error);
}
